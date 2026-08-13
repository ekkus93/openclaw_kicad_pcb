from __future__ import annotations

import io
import json
from pathlib import Path

from kicad_pcb.errors import UserError
from kicad_pcb_web import refinement_cli
from kicad_pcb_web.errors import PersistedStateError
from kicad_pcb_web.refinement_cli import RefinementCliContext
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _context(tmp_path: Path, stdout: io.StringIO, stderr: io.StringIO) -> RefinementCliContext:
    data_dir = tmp_path / "data"
    settings = WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="vision-model",
            vision_enabled=True,
        ),
    )
    return RefinementCliContext(
        settings=settings,
        llm_client=object(),  # type: ignore[arg-type]
        config=RefinementFeatureConfig(enabled=True),
        stdout=stdout,
        stderr=stderr,
    )


def test_refinement_cli_does_not_print_user_error_text_or_details(
    monkeypatch, tmp_path: Path
) -> None:
    def fail(**kwargs):
        raise UserError(
            "Candidate validation failed for /tmp/private/design.kicad_sch "
            "using super-secret-api-key.",
            code="REFINEMENT_STALE",
            details={
                "private_path": "/tmp/private/design.kicad_sch",
                "api_key": "super-secret-api-key",
            },
        )

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", fail)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001"],
        _context(tmp_path, stdout, stderr),
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload == {
        "status": "error",
        "code": "REFINEMENT_STALE",
        "message": "Schematic refinement request could not be completed.",
    }
    assert "/tmp/private" not in stderr.getvalue()
    assert "super-secret-api-key" not in stderr.getvalue()


def test_refinement_cli_sanitizes_trusted_resolver_web_service_errors(
    monkeypatch, tmp_path: Path
) -> None:
    def fail(**kwargs):
        raise PersistedStateError(
            "Unsafe persisted path /home/operator/private/board.kicad_sch.",
            code="REFINEMENT_TARGET_PATH_INVALID",
            details={
                "private_path": "/home/operator/private/board.kicad_sch",
                "credential": "provider-secret-token",
            },
        )

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", fail)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001"],
        _context(tmp_path, stdout, stderr),
    )

    assert exit_code == 2
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload == {
        "status": "error",
        "code": "REFINEMENT_TARGET_PATH_INVALID",
        "message": "Schematic refinement request could not be completed.",
    }
    assert "/home/operator" not in stderr.getvalue()
    assert "provider-secret-token" not in stderr.getvalue()
