"""Targeted D1-D7 regressions for wizard/LLM robustness."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from kicad_pcb.errors import ToolError
from kicad_pcb_web.errors import (
    ConflictError,
    LlmCompletionRefusedError,
    LlmCompletionTruncatedError,
    LlmInvalidStructuredOutputError,
    LlmNoUsableContentError,
    PersistenceError,
    WebServiceError,
)
from kicad_pcb_web.schemas import JobDetail
from kicad_pcb_web.services import _wizard_session_io as session_io
from kicad_pcb_web.services import wizard as wizard_service
from kicad_pcb_web.services._wizard_llm import _call_llm_for_json
from kicad_pcb_web.services._wizard_session_io import (
    _make_debug_artifact_writer,
    _persist_session,
    _set_error,
    read_wizard_session,
)
from kicad_pcb_web.services.llm import LlmCompletion, LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.services.wizard import (
    _effective_failure_kind,
    _failure_operation,
    _llm_provenance,
    generate_wizard_ir,
    generate_wizard_project,
)
from kicad_pcb_web.settings import LlmSettings, WebSettings, _validate_llm_settings, load_settings
from kicad_pcb_web.wizard_models import (
    CircuitSpec,
    WizardIrValidation,
    WizardLlmProvenance,
    WizardSessionDetail,
)

_VALID_NETLIST = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "Device:R", "value": "10k"},
        {"ref": "R2", "symbol": "Device:R", "value": "10k"},
    ],
    "nets": [
        {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
        {"name": "N2", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}]},
    ],
}


class _Envelope(BaseModel):
    value: str


class ScriptedClient:
    def __init__(self, responses: list[LlmCompletion | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _completion(content: str, *, finish_reason: str = "stop") -> LlmCompletion:
    return LlmCompletion(
        provider="test", model="test-model", content=content, finish_reason=finish_reason
    )


def _settings(tmp_path: Path, **overrides: object) -> WebSettings:
    values: dict[str, object] = {
        "provider": "llama_server",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8080",
        "ir_max_repair_rounds": 2,
        "spec_max_repair_rounds": 2,
    }
    values.update(overrides)
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(**values),  # type: ignore[arg-type]
    )


def _approved_session(settings: WebSettings, session_id: str = "wiz_robustness") -> None:
    _persist_session(
        settings,
        WizardSessionDetail(
            id=session_id,
            status="spec_approved",
            created_at="2026-08-10T00:00:00Z",
            updated_at="2026-08-10T00:00:00Z",
            spec=CircuitSpec(project_name="Robustness", purpose="Exercise LLM robustness."),
            spec_provenance=_llm_provenance(settings),
            spec_approved=True,
            spec_approved_at="2026-08-10T00:00:00Z",
        ),
    )


def _ir_output(netlist: dict[str, object]) -> str:
    return json.dumps({"assistant_message": "IR", "netlist_json": netlist, "assumptions": []})


def test_d1_ir_structural_then_valid_uses_shared_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    client = ScriptedClient([_completion("{"), _completion(_ir_output(_VALID_NETLIST))])
    result = generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert result.status == "ir_ready_for_generation"
    assert len(client.requests) == 2
    assert "did not match the required JSON contract" in client.requests[1].messages[-1].content


def test_d1_ir_structural_exhaustion_is_operational_and_exactly_bounded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    client = ScriptedClient([_completion("{"), _completion("{"), _completion("{")])
    with pytest.raises(LlmInvalidStructuredOutputError):
        generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert len(client.requests) == settings.llm.ir_max_repair_rounds + 1
    persisted = read_wizard_session(settings, "wiz_robustness")
    assert persisted.status == "failed"
    assert persisted.failure_kind == "operational"
    assert _failure_operation(persisted) == "generate_ir"


def test_d1_ir_semantic_exhaustion_preserves_last_parseable_ir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    invalid = {
        **_VALID_NETLIST,
        "nets": [
            {"name": "N1", "pins": [{"ref": "R1", "pin": "99"}, {"ref": "R2", "pin": "1"}]},
            _VALID_NETLIST["nets"][1],
        ],
    }
    client = ScriptedClient([_completion(_ir_output(invalid)) for _ in range(3)])
    result = generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert len(client.requests) == settings.llm.ir_max_repair_rounds + 1
    assert result.status == "ir_needs_repair"
    assert result.ir_json == invalid
    assert result.ir_validation is not None and result.ir_validation.valid is False


def test_d2_no_content_repairs_then_recovers() -> None:
    client = ScriptedClient([_completion(""), _completion('{"value":"ok"}')])
    result = _call_llm_for_json(
        llm_client=client,
        messages=[LlmMessage(role="user", content="json")],
        response_model=_Envelope,
        max_repairs=1,
    )
    assert result.value == "ok"
    assert len(client.requests) == 2


def test_d2_no_content_exhaustion_is_distinct() -> None:
    client = ScriptedClient([_completion(""), _completion("")])
    with pytest.raises(LlmNoUsableContentError):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=1,
        )
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    ("reason", "error_type"),
    [("length", LlmCompletionTruncatedError), ("content_filter", LlmCompletionRefusedError)],
)
def test_d2_terminal_finish_reasons_do_not_repair(reason: str, error_type: type[Exception]) -> None:
    client = ScriptedClient([_completion('{"value":"ignored"}', finish_reason=reason)])
    with pytest.raises(error_type):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=2,
        )
    assert len(client.requests) == 1


def test_d2_generic_tool_error_is_not_absorbed() -> None:
    client = ScriptedClient([ToolError("transport failed")])
    with pytest.raises(ToolError, match="transport failed"):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=2,
        )
    assert len(client.requests) == 1


def _captured_payload(settings: WebSettings) -> dict[str, object]:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "req",
                "model": settings.llm.model,
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )

    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None
    client.complete(LlmRequest(messages=[LlmMessage(role="user", content="hi")]))
    return captured


@pytest.mark.parametrize("provider", ["openai", "llama_server"])
def test_d3_temperature_mode_exact_payload_contract(tmp_path: Path, provider: str) -> None:
    base_url = None if provider == "openai" else "http://127.0.0.1:8080"
    common = {
        "provider": provider,
        "model": "test-model",
        "base_url": base_url,
        "api_key": "test-key",
        "temperature": 0.2,
        "max_tokens": None,
    }
    send = _settings(tmp_path, **common, temperature_mode="send")
    omit = _settings(tmp_path, **common, temperature_mode="omit")
    assert _captured_payload(send) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
    }
    assert _captured_payload(omit) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_d3_temperature_mode_is_explicit_provenance(tmp_path: Path) -> None:
    send = _llm_provenance(_settings(tmp_path, temperature_mode="send"))
    omit = _llm_provenance(_settings(tmp_path, temperature_mode="omit"))
    assert send.temperature_mode == "send"
    assert omit.temperature_mode == "omit"
    assert send.config_revision != omit.config_revision


def test_d3_unknown_temperature_mode_rejects(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_TEMPERATURE_MODE", "guess")
    with pytest.raises(ValueError, match="temperature_mode"):
        load_settings()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"timeout_s": 0}, "greater than zero"),
        ({"timeout_s": 301}, "300 seconds or less"),
        ({"retry_max_delay_s": 61}, "60 seconds or less"),
        ({"retry_base_delay_s": 5, "retry_max_delay_s": 4}, "at least llm.retry_base_delay_s"),
        ({"retry_max_attempts": 0}, "between 1 and 10"),
        ({"retry_max_attempts": 11}, "between 1 and 10"),
    ],
)
def test_d4_retry_timeout_configuration_caps(values: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_llm_settings(LlmSettings(**values))  # type: ignore[arg-type]


def test_d4_maximum_scheduled_retry_sleep_is_deterministic(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, retry_max_attempts=10, retry_base_delay_s=1.0, retry_max_delay_s=60.0
    )
    client = build_llm_client(settings)
    assert client is not None
    assert client.max_scheduled_retry_sleep_s == 540.0  # type: ignore[attr-defined]
    close = getattr(client, "close", None)
    if callable(close):
        close()


def test_d5_debug_artifact_count_prunes_oldest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE", 2)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_count", stage="spec")
    assert writer is not None
    writer({"attempt": 1})
    writer({"attempt": 2})
    writer({"attempt": 3})
    artifacts = sorted(
        (settings.data_dir / "wizard_sessions/wiz_debug_count/debug_artifacts").glob("spec_*.json")
    )
    assert len(artifacts) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8"))["attempt"] for path in artifacts]
    assert payloads == [2, 3]


def test_d5_byte_cap_keeps_newest_even_when_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_TOTAL_BYTES", 10)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_bytes", stage="ir")
    assert writer is not None
    writer({"attempt": 1})
    writer({"attempt": 2})
    artifacts = list(
        (settings.data_dir / "wizard_sessions/wiz_debug_bytes/debug_artifacts").glob("*.json")
    )
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text(encoding="utf-8"))["attempt"] == 2


def test_d5_prune_delete_failure_warns_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE", 1)
    original_unlink = Path.unlink
    calls = {"count": 0}

    def fail_first(path: Path, *args: object, **kwargs: object) -> None:
        if "debug_artifacts" in path.parts and calls["count"] == 0:
            calls["count"] += 1
            raise OSError("simulated prune failure")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_first)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_failure", stage="spec")
    assert writer is not None
    with caplog.at_level(logging.WARNING):
        writer({"attempt": 1})
        writer({"attempt": 2})
    assert "failed to prune wizard debug artifact" in caplog.text


def test_d6_failure_kind_explicit_and_legacy_interpretation() -> None:
    base = WizardSessionDetail(
        id="wiz_failure_kind",
        status="drafting_ir",
        created_at="2026-08-10T00:00:00Z",
        updated_at="2026-08-10T00:00:00Z",
    )
    operational = _set_error(
        base, {"details": {"operation": "generate_ir"}}, failure_kind="operational"
    )
    generation = _set_error(
        base, {"details": {"operation": "generate_project"}}, failure_kind="generation"
    )
    unsupported = base.model_copy(
        update={"status": "failed", "failure_kind": "unsupported_design", "error": None}
    )
    legacy_soft = base.model_copy(update={"status": "failed", "failure_kind": None, "error": None})
    legacy_error = base.model_copy(
        update={"status": "failed", "failure_kind": None, "error": {"details": {}}}
    )
    assert _effective_failure_kind(operational) == "operational"
    assert _failure_operation(operational) == "generate_ir"
    assert _effective_failure_kind(generation) == "generation"
    assert _failure_operation(generation) == "generate_project"
    assert _effective_failure_kind(unsupported) == "unsupported_design"
    assert _failure_operation(unsupported) is None
    assert _effective_failure_kind(legacy_soft) == "unsupported_design"
    assert _effective_failure_kind(legacy_error) == "operational"


def test_d7_empty_data_dir_env_and_toml_reject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", "")
    with pytest.raises(ValueError, match="web.data_dir must not be empty"):
        load_settings()
    monkeypatch.delenv("KICAD_PCB_WEB_DATA_DIR")
    config = tmp_path / "empty-data.toml"
    config.write_text('[web]\ndata_dir = ""\n', encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config))
    with pytest.raises(ValueError, match="web.data_dir must not be empty"):
        load_settings()


def test_d7_jobs_dir_mkdir_oserror_is_wrapped_as_valueerror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "not-a-directory"
    data_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(data_file))
    with pytest.raises(ValueError, match="Unable to create jobs directory") as caught:
        load_settings()
    assert str(data_file / "jobs") in str(caught.value)


def test_followup_f2_real_ollama_length_is_terminal_before_valid_json_acceptance(
    tmp_path: Path,
) -> None:
    attempts = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"content": '{"value":"schema-valid"}'},
                "done_reason": "length",
            },
        )

    settings = _settings(
        tmp_path,
        provider="ollama",
        model="llama3.1",
        base_url="http://127.0.0.1:11434",
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        with pytest.raises(LlmCompletionTruncatedError):
            _call_llm_for_json(
                llm_client=client,
                messages=[LlmMessage(role="user", content="json")],
                response_model=_Envelope,
                max_repairs=2,
            )
    finally:
        client.close()  # type: ignore[attr-defined]
    assert attempts["count"] == 1


def test_followup_f5_operational_ir_failure_allows_actual_retry(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings, "wiz_retry_ir")

    failing_client = ScriptedClient([LlmCompletionTruncatedError("simulated truncation")])
    with pytest.raises(LlmCompletionTruncatedError):
        generate_wizard_ir(
            settings=settings,
            session_id="wiz_retry_ir",
            llm_client=failing_client,
        )

    failed = read_wizard_session(settings, "wiz_retry_ir")
    assert failed.status == "failed"
    assert failed.failure_kind == "operational"
    assert _failure_operation(failed) == "generate_ir"

    retry_client = ScriptedClient([_completion(_ir_output(_VALID_NETLIST))])
    retried = generate_wizard_ir(
        settings=settings,
        session_id="wiz_retry_ir",
        llm_client=retry_client,
    )
    assert retried.status == "ir_ready_for_generation"
    assert len(retry_client.requests) == 1


def test_followup_f5_generation_failure_blocks_generate_ir_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings, "wiz_generation_gate")
    ready = read_wizard_session(settings, "wiz_generation_gate").model_copy(
        update={
            "status": "ir_ready_for_generation",
            "ir_json": _VALID_NETLIST,
            "ir_validation": WizardIrValidation(
                valid=True,
                component_count=2,
                net_count=2,
            ),
        }
    )
    _persist_session(settings, ready)

    failed_job = JobDetail(
        id="job_followup_failed",
        status="failed",
        project_name="Robustness",
        created_at="2026-08-10T00:00:00Z",
        updated_at="2026-08-10T00:00:01Z",
        request={},
        error={"type": "tool_error", "message": "simulated generation failure"},
    )
    monkeypatch.setattr(
        wizard_service,
        "generate_project_from_netlist_job",
        lambda **_: failed_job,
    )

    with pytest.raises(WebServiceError) as caught:
        generate_wizard_project(settings=settings, session_id="wiz_generation_gate")
    assert caught.value.code == "WIZARD_PROJECT_GENERATION_FAILED"

    failed = read_wizard_session(settings, "wiz_generation_gate")
    assert failed.status == "failed"
    assert failed.failure_kind == "generation"
    assert failed.latest_job_id == failed_job.id
    assert _failure_operation(failed) == "generate_project"

    retry_client = ScriptedClient([_completion(_ir_output(_VALID_NETLIST))])
    with pytest.raises(ConflictError, match="not allowed"):
        generate_wizard_ir(
            settings=settings,
            session_id="wiz_generation_gate",
            llm_client=retry_client,
        )
    assert retry_client.requests == []


def test_followup_f5_legacy_unsupported_failure_is_not_operationally_retryable(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings, "wiz_legacy_gate")
    legacy = read_wizard_session(settings, "wiz_legacy_gate").model_copy(
        update={"status": "failed", "failure_kind": None, "error": None}
    )
    _persist_session(settings, legacy)

    retry_client = ScriptedClient([_completion(_ir_output(_VALID_NETLIST))])
    with pytest.raises(ConflictError, match="not allowed"):
        generate_wizard_ir(
            settings=settings,
            session_id="wiz_legacy_gate",
            llm_client=retry_client,
        )
    assert retry_client.requests == []


def test_followup_f6_debug_write_persistence_error_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    _approved_session(settings, "wiz_debug_write_failure")
    original_atomic_write_json = session_io.atomic_write_json

    def fail_debug_write(
        path: Path,
        payload: object,
        *,
        sort_keys: bool = True,
    ) -> None:
        if "debug_artifacts" in path.parts:
            raise PersistenceError("simulated debug artifact persistence failure")
        original_atomic_write_json(path, payload, sort_keys=sort_keys)

    monkeypatch.setattr(session_io, "atomic_write_json", fail_debug_write)
    client = ScriptedClient([_completion(_ir_output(_VALID_NETLIST))])
    with caplog.at_level(logging.WARNING):
        result = generate_wizard_ir(
            settings=settings,
            session_id="wiz_debug_write_failure",
            llm_client=client,
        )

    assert result.status == "ir_ready_for_generation"
    assert "wizard debug artifact capture failed" in caplog.text


@pytest.mark.parametrize("error_type", [OSError, PersistenceError])
def test_followup_f6_debug_directory_failure_is_best_effort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error_type: type[Exception],
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    _approved_session(settings, "wiz_debug_dir_failure")

    def fail_private_directory(_: Path) -> None:
        raise error_type("simulated debug directory failure")

    monkeypatch.setattr(session_io, "_ensure_private_directory", fail_private_directory)
    client = ScriptedClient([_completion(_ir_output(_VALID_NETLIST))])
    with caplog.at_level(logging.WARNING):
        result = generate_wizard_ir(
            settings=settings,
            session_id="wiz_debug_dir_failure",
            llm_client=client,
        )

    assert result.status == "ir_ready_for_generation"
    assert "wizard debug artifact capture failed" in caplog.text


def test_followup_f7_provenance_temperature_mode_alias_remains_narrow() -> None:
    common = {
        "provider": "openai",
        "model": "test-model",
        "prompt_version": "v1",
        "endpoint_identity": None,
        "config_revision": "0123456789abcdef",
    }
    for mode in ("send", "omit", None):
        provenance = WizardLlmProvenance(temperature_mode=mode, **common)
        assert provenance.temperature_mode == mode

    with pytest.raises(ValidationError):
        WizardLlmProvenance(temperature_mode="auto", **common)
