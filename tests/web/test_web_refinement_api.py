from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kicad_pcb_web.services.refinement_api import (
    RefinementRunRequest,
    build_refinement_run_response,
)
from kicad_pcb_web.services.schematic_refinement import RefinementLoopResult


def _result(tmp_path: Path) -> RefinementLoopResult:
    return RefinementLoopResult(
        status="stopped",
        stop_reason="REFINEMENT_STOP_NO_OPERATIONS",
        starting_hash="a" * 64,
        final_accepted_hash="b" * 64,
        best_accepted_hash="b" * 64,
        latest_attempted_hash=None,
        starting_layout_fingerprint="c" * 64,
        final_layout_fingerprint="d" * 64,
        rounds_attempted=1,
        accepted_rounds=0,
        rejected_rounds=0,
        accepted_operations=0,
        model_calls_made=2,
        model_call_limit=6,
        iterations=(),
        session_evidence_dir=tmp_path / "private" / "evidence" / "session-1",
    )


def test_refinement_request_accepts_only_session_id() -> None:
    request = RefinementRunRequest.model_validate({"session_id": "session-001"})

    assert request.session_id == "session-001"


@pytest.mark.parametrize("session_id", ["../escape", "with/slash", "space id", ""])
def test_refinement_request_rejects_unsafe_session_id(session_id: str) -> None:
    with pytest.raises(ValidationError):
        RefinementRunRequest.model_validate({"session_id": session_id})


def test_refinement_request_rejects_paths_limits_and_provider_overrides() -> None:
    for forbidden in (
        {"accepted_path": "/tmp/design.kicad_sch"},
        {"max_rounds": 99},
        {"provider": "other"},
        {"model": "other-model"},
        {"api_key": "secret"},
    ):
        with pytest.raises(ValidationError):
            RefinementRunRequest.model_validate(
                {"session_id": "session-001", **forbidden}
            )


def test_refinement_response_never_exposes_absolute_evidence_path(tmp_path: Path) -> None:
    response = build_refinement_run_response(
        session_id="session-001",
        result=_result(tmp_path),
    )
    payload = response.model_dump(mode="json")

    assert payload["session_id"] == "session-001"
    assert payload["evidence_available"] is True
    assert "session_evidence_dir" not in payload
    assert str(tmp_path) not in str(payload)
    assert "api_key" not in payload
