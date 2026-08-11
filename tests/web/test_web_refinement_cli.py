from __future__ import annotations

import io
import json
from pathlib import Path

from kicad_pcb_web import refinement_cli
from kicad_pcb_web.services.refinement_api import RefinementRunResponse
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig


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


def test_refinement_cli_forwards_only_sanitized_request(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")
    observed: dict[str, object] = {}

    def fake_run(**kwargs):
        observed.update(kwargs)
        return _response(kwargs["request"].session_id)

    monkeypatch.setattr(refinement_cli, "run_configured_refinement_request", fake_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    runtime = object()
    config = RefinementFeatureConfig(enabled=True)

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001"],
        accepted_path=accepted,
        runtime=runtime,  # type: ignore[arg-type]
        config=config,
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 0
    assert stderr.getvalue() == ""
    assert json.loads(stdout.getvalue())["session_id"] == "session-001"
    assert observed["accepted_path"] == accepted
    assert observed["runtime"] is runtime
    assert observed["config"] is config
    request = observed["request"]
    assert request.model_dump() == {"session_id": "session-001"}  # type: ignore[attr-defined]


def test_refinement_cli_has_no_path_provider_or_limit_flags(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")
    stdout = io.StringIO()
    stderr = io.StringIO()

    def unexpected_run(**kwargs):
        raise AssertionError("invalid CLI arguments must not dispatch")

    monkeypatch.setattr(refinement_cli, "run_configured_refinement_request", unexpected_run)
    for argv in (
        ["--session-id", "s1", "--accepted-path", "/tmp/other.kicad_sch"],
        ["--session-id", "s1", "--max-rounds", "99"],
        ["--session-id", "s1", "--provider", "other"],
        ["--session-id", "s1", "--api-key", "secret"],
    ):
        stdout.seek(0)
        stdout.truncate(0)
        stderr.seek(0)
        stderr.truncate(0)
        exit_code = refinement_cli.execute_refinement_cli(
            argv,
            accepted_path=accepted,
            runtime=object(),  # type: ignore[arg-type]
            config=RefinementFeatureConfig(enabled=True),
            stdout=stdout,
            stderr=stderr,
        )
        payload = json.loads(stderr.getvalue())
        assert exit_code == 2
        assert payload["code"] == "REFINEMENT_CLI_INVALID_ARGUMENTS"
        assert stdout.getvalue() == ""


def test_refinement_cli_rejects_unsafe_session_id_before_dispatch(
    monkeypatch, tmp_path: Path
) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")

    def unexpected_run(**kwargs):
        raise AssertionError("invalid session id must not dispatch")

    monkeypatch.setattr(refinement_cli, "run_configured_refinement_request", unexpected_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "../escape"],
        accepted_path=accepted,
        runtime=object(),  # type: ignore[arg-type]
        config=RefinementFeatureConfig(enabled=True),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == 2
    assert json.loads(stderr.getvalue())["code"] == "REFINEMENT_CLI_INVALID_ARGUMENTS"
    assert stdout.getvalue() == ""
