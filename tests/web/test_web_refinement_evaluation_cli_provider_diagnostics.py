from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web import refinement_evaluation_cli as cli
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def test_phase_n3_cli_preserves_only_allowlisted_provider_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail(*args, **kwargs):
        raise UserError(
            "Refinement evaluation corpus fixture failed.",
            code="REFINEMENT_EVALUATION_FIXTURE_FAILED",
            details={
                "fixture_id": "n1-crowded-power-regulator",
                "cause_code": "TOOL_ERROR",
                "cause_type": "ToolError",
                "cause_message": "ollama request failed with HTTP 400.",
                "cause_provider": "ollama",
                "cause_status_code": 400,
                "cause_endpoint": "/api/chat",
                "cause_retryable": False,
                "secret": "must-not-leak",
            },
        )

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", fail)
    context = cli.RefinementEvaluationCliContext(
        settings=WebSettings(
            data_dir=tmp_path / "data",
            jobs_dir=tmp_path / "jobs",
            llm=LlmSettings(
                provider="openai",
                model="vision-model",
                vision_enabled=True,
            ),
        ),
        llm_client=object(),  # type: ignore[arg-type]
        config=RefinementFeatureConfig(enabled=True),
        adapter=object(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        implementation_sha="a" * 40,
    )

    code = cli.execute_refinement_evaluation_cli(
        [
            "--output-root",
            str(tmp_path / "out"),
            "--work-root",
            str(tmp_path / "work"),
        ],
        context,
    )

    assert code == 2
    payload = json.loads(context.stderr.getvalue())
    assert payload["details"] == {
        "fixture_id": "n1-crowded-power-regulator",
        "cause_code": "TOOL_ERROR",
        "cause_type": "ToolError",
        "cause_message": "ollama request failed with HTTP 400.",
        "cause_provider": "ollama",
        "cause_status_code": 400,
        "cause_endpoint": "/api/chat",
        "cause_retryable": False,
    }
    assert "secret" not in context.stderr.getvalue()
