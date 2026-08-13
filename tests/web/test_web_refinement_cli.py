from __future__ import annotations

import io
import json
from pathlib import Path

from kicad_pcb_web import refinement_cli
from kicad_pcb_web.refinement_cli import RefinementCliContext
from kicad_pcb_web.services.refinement_api import RefinementRunResponse
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _response(session_id: str) -> RefinementRunResponse:
    return RefinementRunResponse(
        status="stopped",
        stop_reason="REFINEMENT_STOP_NO_OPERATIONS",
        session_id=session_id,
        starting_hash="a" * 64,
        final_accepted_hash="a" * 64,
        best_accepted_hash="a" * 64,
        latest_attempted_hash=None,
        starting_layout_fingerprint="b" * 64,
        final_layout_fingerprint="b" * 64,
        rounds_attempted=1,
        accepted_rounds=0,
        rejected_rounds=0,
        accepted_operations=0,
        model_calls_made=1,
        model_call_limit=6,
        evidence_available=True,
    )


def _settings(tmp_path: Path) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="vision-model",
            vision_enabled=True,
        ),
    )


def _context(
    *,
    settings: WebSettings,
    llm_client: object,
    config: RefinementFeatureConfig,
    stdout: io.StringIO,
    stderr: io.StringIO,
) -> RefinementCliContext:
    return RefinementCliContext(
        settings=settings,
        llm_client=llm_client,  # type: ignore[arg-type]
        config=config,
        stdout=stdout,
        stderr=stderr,
    )


def test_refinement_cli_dispatches_through_trusted_wizard_composition(
    monkeypatch, tmp_path: Path
) -> None:
    observed: dict[str, object] = {}

    def fake_run(**kwargs):
        observed.update(kwargs)
        return _response(kwargs["request"].session_id)

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", fake_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    settings = _settings(tmp_path)
    llm_client = object()
    config = RefinementFeatureConfig(enabled=True)

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001"],
        _context(
            settings=settings,
            llm_client=llm_client,
            config=config,
            stdout=stdout,
            stderr=stderr,
        ),
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert json.loads(stdout.getvalue())["session_id"] == "session-001"
    assert observed["settings"] is settings
    assert observed["llm_client"] is llm_client
    assert observed["config"] is config
    assert "accepted_path" not in observed
    assert "runtime" not in observed
    request = observed["request"]
    assert request.model_dump() == {"session_id": "session-001"}  # type: ignore[attr-defined]


def test_refinement_cli_has_no_path_provider_or_limit_flags(monkeypatch, tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def unexpected_run(**kwargs):
        raise AssertionError("invalid CLI arguments must not dispatch")

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", unexpected_run)
    settings = _settings(tmp_path)
    config = RefinementFeatureConfig(enabled=True)
    for argv in (
        ["--session-id", "s1", "--accepted-path", "/tmp/other.kicad_sch"],
        ["--session-id", "s1", "--job-id", "job-other"],
        ["--session-id", "s1", "--max-rounds", "99"],
        ["--session-id", "s1", "--provider", "other"],
        ["--session-id", "s1", "--model", "other-model"],
        ["--session-id", "s1", "--api-key", "secret"],
        ["--session-id", "s1", "--kicad-cli", "/tmp/kicad-cli"],
    ):
        stdout.seek(0)
        stdout.truncate(0)
        stderr.seek(0)
        stderr.truncate(0)
        exit_code = refinement_cli.execute_refinement_cli(
            argv,
            _context(
                settings=settings,
                llm_client=object(),
                config=config,
                stdout=stdout,
                stderr=stderr,
            ),
        )
        payload = json.loads(stderr.getvalue())
        assert exit_code == 2
        assert payload["code"] == "REFINEMENT_CLI_INVALID_ARGUMENTS"
        assert stdout.getvalue() == ""


def test_refinement_cli_rejects_unsafe_session_id_before_dispatch(
    monkeypatch, tmp_path: Path
) -> None:
    def unexpected_run(**kwargs):
        raise AssertionError("invalid session id must not dispatch")

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", unexpected_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "../escape"],
        _context(
            settings=_settings(tmp_path),
            llm_client=object(),
            config=RefinementFeatureConfig(enabled=True),
            stdout=stdout,
            stderr=stderr,
        ),
    )

    assert exit_code == 2
    assert json.loads(stderr.getvalue())["code"] == "REFINEMENT_CLI_INVALID_ARGUMENTS"
    assert stdout.getvalue() == ""


def test_refinement_cli_main_loads_process_owned_dependencies_and_closes_client(
    monkeypatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    config = RefinementFeatureConfig(enabled=True)
    stdout = io.StringIO()
    stderr = io.StringIO()
    observed: dict[str, object] = {}

    class FakeClient:
        closed = False

        def close(self) -> None:
            self.closed = True

    client = FakeClient()

    monkeypatch.setattr(refinement_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(refinement_cli, "load_refinement_feature_config", lambda: config)
    monkeypatch.setattr(refinement_cli, "build_llm_client", lambda loaded: client)
    monkeypatch.setattr(refinement_cli.sys, "stdout", stdout)
    monkeypatch.setattr(refinement_cli.sys, "stderr", stderr)

    def fake_execute(argv, context):
        observed["argv"] = list(argv)
        observed["context"] = context
        return 0

    monkeypatch.setattr(refinement_cli, "execute_refinement_cli", fake_execute)

    assert refinement_cli.main(["--session-id", "session-001"]) == 0
    context = observed["context"]
    assert observed["argv"] == ["--session-id", "session-001"]
    assert context.settings is settings  # type: ignore[attr-defined]
    assert context.llm_client is client  # type: ignore[attr-defined]
    assert context.config is config  # type: ignore[attr-defined]
    assert context.stdout is stdout  # type: ignore[attr-defined]
    assert context.stderr is stderr  # type: ignore[attr-defined]
    assert client.closed is True


def test_refinement_cli_main_configuration_failure_does_not_build_client(
    monkeypatch,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    build_calls = 0

    def fail_settings():
        raise ValueError("/tmp/private/config.toml contains secret-value")

    def unexpected_build(settings):
        nonlocal build_calls
        build_calls += 1
        return object()

    monkeypatch.setattr(refinement_cli, "load_settings", fail_settings)
    monkeypatch.setattr(refinement_cli, "build_llm_client", unexpected_build)
    monkeypatch.setattr(refinement_cli.sys, "stdout", stdout)
    monkeypatch.setattr(refinement_cli.sys, "stderr", stderr)

    assert refinement_cli.main(["--session-id", "session-001"]) == 2
    assert build_calls == 0
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload == {
        "status": "error",
        "code": "REFINEMENT_CLI_CONFIGURATION_INVALID",
        "message": "Schematic refinement CLI configuration is invalid.",
    }
    assert "/tmp/private" not in stderr.getvalue()
    assert "secret-value" not in stderr.getvalue()
