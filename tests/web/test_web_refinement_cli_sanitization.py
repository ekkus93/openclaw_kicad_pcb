from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

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


def test_refinement_cli_sanitizes_unexpected_request_failure(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    def fail(**kwargs):
        raise RuntimeError("secret runtime body /tmp/private/refinement-state.json")

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", fail)
    stdout = io.StringIO()
    stderr = io.StringIO()

    with caplog.at_level("ERROR", logger="uvicorn.error"):
        exit_code = refinement_cli.execute_refinement_cli(
            ["--session-id", "session-001"],
            _context(tmp_path, stdout, stderr),
        )

    assert exit_code == 1
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {
        "status": "error",
        "code": "REFINEMENT_CLI_INTERNAL_ERROR",
        "message": "Schematic refinement CLI failed unexpectedly.",
    }
    assert "secret runtime body" not in stderr.getvalue()
    assert "/tmp/private" not in stderr.getvalue()
    assert "secret runtime body" not in caplog.text
    assert "/tmp/private" not in caplog.text
    assert "unexpected schematic refinement CLI request failure" in caplog.text


def test_refinement_cli_main_sanitizes_unexpected_configuration_failure(
    monkeypatch,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    def fail_settings():
        raise RuntimeError("secret config /home/operator/private/settings.json")

    monkeypatch.setattr(refinement_cli, "load_settings", fail_settings)
    monkeypatch.setattr(refinement_cli.sys, "stdout", stdout)
    monkeypatch.setattr(refinement_cli.sys, "stderr", stderr)

    assert refinement_cli.main(["--session-id", "session-001"]) == 1
    assert stdout.getvalue() == ""
    assert json.loads(stderr.getvalue()) == {
        "status": "error",
        "code": "REFINEMENT_CLI_INTERNAL_ERROR",
        "message": "Schematic refinement CLI failed unexpectedly.",
    }
    assert "secret config" not in stderr.getvalue()
    assert "/home/operator" not in stderr.getvalue()


def test_refinement_cli_client_cleanup_failure_is_warning_visible_and_non_reflecting(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    settings = _context(tmp_path, io.StringIO(), io.StringIO()).settings
    stdout = io.StringIO()
    stderr = io.StringIO()

    class FailingCloseClient:
        def close(self) -> None:
            raise RuntimeError("provider-secret-token /tmp/private/client.sock")

    client = FailingCloseClient()
    monkeypatch.setattr(refinement_cli, "load_settings", lambda: settings)
    monkeypatch.setattr(
        refinement_cli,
        "load_refinement_feature_config",
        lambda: RefinementFeatureConfig(enabled=True),
    )
    monkeypatch.setattr(refinement_cli, "build_llm_client", lambda loaded: client)
    monkeypatch.setattr(refinement_cli, "execute_refinement_cli", lambda argv, context: 2)
    monkeypatch.setattr(refinement_cli.sys, "stdout", stdout)
    monkeypatch.setattr(refinement_cli.sys, "stderr", stderr)

    with caplog.at_level("WARNING", logger="uvicorn.error"):
        assert refinement_cli.main(["--session-id", "session-001"]) == 2

    assert "provider-secret-token" not in caplog.text
    assert "/tmp/private" not in caplog.text
    assert "LLM client cleanup failed" in caplog.text


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--work-dir", "/tmp/private-work"),
        ("--evidence-root", "/tmp/private-evidence"),
        ("--refinement-session-id", "alternate-reservation"),
        ("--max-model-calls", "999"),
        ("--operation-policy", "unsafe"),
    ],
)
def test_refinement_cli_rejects_additional_boundary_override_flags_before_dispatch(
    monkeypatch,
    tmp_path: Path,
    flag: str,
    value: str,
) -> None:
    calls = 0

    def unexpected_run(**kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("forbidden CLI override must not dispatch")

    monkeypatch.setattr(refinement_cli, "run_wizard_refinement_request", unexpected_run)
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = refinement_cli.execute_refinement_cli(
        ["--session-id", "session-001", flag, value],
        _context(tmp_path, stdout, stderr),
    )

    assert exit_code == 2
    assert calls == 0
    assert stdout.getvalue() == ""
    payload = json.loads(stderr.getvalue())
    assert payload["code"] == "REFINEMENT_CLI_INVALID_ARGUMENTS"
    assert value not in stderr.getvalue()
