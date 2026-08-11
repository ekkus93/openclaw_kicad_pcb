from __future__ import annotations

import io
import json
from pathlib import Path

from kicad_pcb.errors import UserError
from kicad_pcb_web import refinement_cli
from kicad_pcb_web.refinement_cli import RefinementCliContext
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig


def test_refinement_cli_does_not_print_user_error_details(monkeypatch, tmp_path: Path) -> None:
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes(b"accepted")

    def fail(**kwargs):
        raise UserError(
            "Candidate validation failed.",
            code="REFINEMENT_STALE",
            details={
                "private_path": "/tmp/private/design.kicad_sch",
                "api_key": "super-secret-api-key",
            },
        )

    monkeypatch.setattr(refinement_cli, "run_configured_refinement_request", fail)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001"],
        RefinementCliContext(
            accepted_path=accepted,
            runtime=object(),  # type: ignore[arg-type]
            config=RefinementFeatureConfig(enabled=True),
            stdout=stdout,
            stderr=stderr,
        ),
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload == {
        "status": "error",
        "code": "REFINEMENT_STALE",
        "message": "Candidate validation failed.",
    }
    assert "/tmp/private" not in stderr.getvalue()
    assert "super-secret-api-key" not in stderr.getvalue()
