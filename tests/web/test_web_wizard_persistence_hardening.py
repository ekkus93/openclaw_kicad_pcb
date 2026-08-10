"""Wizard persistence and provenance hardening regressions."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from kicad_pcb_web.errors import PersistenceError
from kicad_pcb_web.services import _wizard_session_io
from kicad_pcb_web.services._wizard_session_io import _persist_session, read_wizard_session
from kicad_pcb_web.services.wizard import _llm_provenance
from kicad_pcb_web.settings import LlmSettings, WebSettings
from kicad_pcb_web.wizard_models import CircuitSpec, WizardSessionDetail


def _settings(tmp_path: Path, *, debug: bool = False) -> WebSettings:
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="gpt-4.1-mini",
            base_url="https://llm.internal.example:8443/v1",
            api_key="TOP-SECRET-KEY",
            debug_artifact_capture=debug,
        ),
    )


def _session() -> WizardSessionDetail:
    return WizardSessionDetail(
        id="wiz_persistence_test",
        status="spec_ready_for_review",
        created_at="2026-08-09T00:00:00Z",
        updated_at="2026-08-09T00:00:01Z",
        spec=CircuitSpec(purpose="Test persistence semantics."),
    )


def test_derived_export_failure_reports_committed_canonical_state(
    tmp_path: Path, monkeypatch
) -> None:
    settings = _settings(tmp_path)
    session = _session()

    def fail_derived(*_args, **_kwargs) -> None:
        raise PersistenceError("derived export failed")

    monkeypatch.setattr(_wizard_session_io, "_refresh_derived_exports", fail_derived)

    with pytest.raises(PersistenceError) as caught:
        _persist_session(settings, session)

    assert caught.value.code == "DERIVED_WIZARD_EXPORT_FAILED"
    assert caught.value.details["authoritative_committed"] is True
    persisted = read_wizard_session(settings, session.id)
    assert persisted.status == "spec_ready_for_review"
    assert persisted.updated_at == session.updated_at


def test_debug_artifacts_use_private_directory_and_persist_metadata_only(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, debug=True)
    writer = _wizard_session_io._make_debug_artifact_writer(
        settings,
        "wiz_debug_permissions",
        stage="spec",
    )
    assert writer is not None
    sensitive_prompt = "sensitive raw prompt /home/alice/private-design.kicad_sch"
    writer(
        {
            "attempt": 1,
            "response_model": "SpecConversationOutput",
            "messages": [{"role": "user", "content": sensitive_prompt}],
            "completion": {
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "content": "sensitive raw provider body",
                "finish_reason": "stop",
                "request_id": "req_safe_identifier",
            },
            "parse_error": "failure while reading /home/alice/private-design.kicad_sch",
            "parsed": {"purpose": "private user design text"},
        }
    )

    artifact_dir = (
        settings.data_dir / "wizard_sessions" / "wiz_debug_permissions" / "debug_artifacts"
    )
    artifacts = list(artifact_dir.glob("*.json"))
    assert len(artifacts) == 1
    artifact_text = artifacts[0].read_text(encoding="utf-8")
    artifact = json.loads(artifact_text)

    assert sensitive_prompt not in artifact_text
    assert "sensitive raw provider body" not in artifact_text
    assert "private user design text" not in artifact_text
    assert "/home/alice" not in artifact_text
    assert "TOP-SECRET-KEY" not in artifact_text
    assert artifact["attempt"] == 1
    assert artifact["response_model"] == "SpecConversationOutput"
    assert artifact["prompt_message_count"] == 1
    assert artifact["prompt_chars"] == len(sensitive_prompt)
    assert len(artifact["prompt_fingerprint"]) == 16
    assert artifact["provider"] == "openai"
    assert artifact["model"] == "gpt-4.1-mini"
    assert artifact["response_chars"] == len("sensitive raw provider body")
    assert len(artifact["response_fingerprint"]) == 16
    assert artifact["parse_error_present"] is True
    assert artifact["parsed_type"] == "dict"
    assert artifact["parsed_top_level_key_count"] == 1
    if os.name == "posix":
        assert artifact_dir.stat().st_mode & 0o777 == 0o700


def test_debug_artifact_writer_rejects_header_capture_without_writing(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings(tmp_path, debug=True)
    writer = _wizard_session_io._make_debug_artifact_writer(
        settings,
        "wiz_debug_headers",
        stage="spec",
    )
    assert writer is not None

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        writer({"headers": {"Authorization": "Bearer TOP-SECRET-KEY"}})

    artifact_dir = settings.data_dir / "wizard_sessions" / "wiz_debug_headers" / "debug_artifacts"
    assert not list(artifact_dir.glob("*.json"))
    assert "wizard debug artifact rejected unsafe capture payload" in caplog.text
    assert "TOP-SECRET-KEY" not in caplog.text


def test_llm_provenance_is_non_secret_and_stable(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    first = _llm_provenance(settings)
    second = _llm_provenance(settings)
    serialized = json.dumps(first.model_dump(mode="json"), sort_keys=True)

    assert first == second
    assert first.provider == "openai"
    assert first.model == "gpt-4.1-mini"
    assert first.endpoint_identity is not None
    assert len(first.endpoint_identity) == 16
    assert len(first.config_revision) == 16
    assert "TOP-SECRET-KEY" not in serialized
    assert "llm.internal.example" not in serialized
