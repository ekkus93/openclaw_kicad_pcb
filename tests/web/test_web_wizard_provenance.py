"""Wizard LLM provenance continuation regressions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb_web.errors import ConflictError, web_service_error_to_payload
from kicad_pcb_web.services._wizard_session_io import _persist_session, read_wizard_session
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest
from kicad_pcb_web.services.wizard import (
    _llm_provenance,
    generate_wizard_ir,
    post_wizard_message,
)
from kicad_pcb_web.settings import LlmSettings, WebSettings
from kicad_pcb_web.wizard_models import (
    CircuitSpec,
    WizardMessage,
    WizardMessageRequest,
    WizardSessionDetail,
)


class ScriptedClient:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls = 0

    def complete(self, _request: LlmRequest) -> LlmCompletion:
        self.calls += 1
        return LlmCompletion(
            provider="test",
            model="test-model",
            content=self.content,
            finish_reason="stop",
        )


class FailIfCalledClient:
    def complete(self, _request: LlmRequest) -> LlmCompletion:
        raise AssertionError("LLM client must not be called after provenance mismatch")


def _settings(
    tmp_path: Path,
    *,
    provider: str = "openai",
    model: str = "model-a",
    prompt_version: str = "v1",
    base_url: str = "https://provider-a.example/v1",
) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            base_url=base_url,
            api_key="TOP-SECRET-PROVIDER-KEY",
            system_prompt_version=prompt_version,
        ),
    )


def _session(
    settings: WebSettings,
    *,
    session_id: str = "wiz_provenance",
    approved: bool = False,
) -> WizardSessionDetail:
    return WizardSessionDetail(
        id=session_id,
        status="spec_approved" if approved else "spec_ready_for_review",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:01Z",
        llm_provider=settings.llm.provider,
        llm_model=settings.llm.model,
        prompt_version=settings.llm.system_prompt_version,
        messages=[WizardMessage(role="user", content="Build a divider.")],
        spec=CircuitSpec(project_name="Divider", purpose="Divide a voltage."),
        spec_provenance=_llm_provenance(settings),
        spec_approved=approved,
        spec_approved_at="2026-08-09T00:00:01Z" if approved else None,
    )


@pytest.mark.parametrize(
    ("field", "settings_kwargs"),
    [
        ("provider", {"provider": "ollama"}),
        ("model", {"model": "model-b"}),
        ("prompt_version", {"prompt_version": "v2"}),
        ("endpoint_identity", {"base_url": "https://provider-b.example/v1"}),
    ],
)
def test_spec_revision_rejects_changed_llm_provenance(
    tmp_path: Path,
    field: str,
    settings_kwargs: dict[str, str],
) -> None:
    original_settings = _settings(tmp_path)
    original = _persist_session(original_settings, _session(original_settings))
    changed_settings = _settings(tmp_path, **settings_kwargs)

    with pytest.raises(ConflictError) as caught:
        post_wizard_message(
            settings=changed_settings,
            session_id=original.id,
            request=WizardMessageRequest(message="Revise the divider."),
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert field in caught.value.details["mismatch_fields"]
    payload = json.dumps(web_service_error_to_payload(caught.value))
    assert "TOP-SECRET-PROVIDER-KEY" not in payload
    assert "provider-a.example" not in payload

    persisted = read_wizard_session(changed_settings, original.id)
    assert persisted.updated_at == original.updated_at
    assert persisted.messages == original.messages


def test_ir_generation_rejects_changed_spec_provenance(tmp_path: Path) -> None:
    original_settings = _settings(tmp_path)
    original = _persist_session(
        original_settings,
        _session(original_settings, session_id="wiz_ir_provenance", approved=True),
    )
    changed_settings = _settings(tmp_path, model="model-b")

    with pytest.raises(ConflictError) as caught:
        generate_wizard_ir(
            settings=changed_settings,
            session_id=original.id,
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert caught.value.details["operation"] == "generate_ir"
    assert "model" in caught.value.details["mismatch_fields"]


def test_same_provenance_allows_spec_continuation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    original = _persist_session(settings, _session(settings, session_id="wiz_same_provenance"))
    client = ScriptedClient(
        json.dumps(
            {
                "assistant_message": "Updated the reviewable specification.",
                "next_state": "spec_ready_for_review",
                "spec": {
                    "project_name": "Divider",
                    "purpose": "Divide a voltage with two resistors.",
                },
                "assumptions": [],
                "open_questions": [],
                "unsupported_reasons": [],
            }
        )
    )

    updated = post_wizard_message(
        settings=settings,
        session_id=original.id,
        request=WizardMessageRequest(message="Use two equal resistors."),
        llm_client=client,
    )

    assert client.calls == 1
    assert updated.status == "spec_ready_for_review"
    assert updated.spec_provenance == _llm_provenance(settings)


def test_legacy_session_remains_readable_but_missing_model_provenance_blocks_mutation(
    tmp_path: Path,
) -> None:
    original_settings = _settings(tmp_path)
    legacy = _session(original_settings, session_id="wiz_legacy_provenance").model_copy(
        update={"llm_model": None, "spec_provenance": None}
    )
    _persist_session(original_settings, legacy)

    restarted_settings = _settings(tmp_path, model="model-b")
    readable = read_wizard_session(restarted_settings, legacy.id)
    assert readable.spec is not None
    assert readable.spec.project_name == "Divider"

    with pytest.raises(ConflictError) as caught:
        post_wizard_message(
            settings=restarted_settings,
            session_id=legacy.id,
            request=WizardMessageRequest(message="Continue this session."),
            llm_client=FailIfCalledClient(),
        )

    assert caught.value.code == "WIZARD_LLM_PROVENANCE_MISMATCH"
    assert "model" in caught.value.details["mismatch_fields"]
