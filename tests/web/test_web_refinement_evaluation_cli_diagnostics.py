from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web import refinement_evaluation_cli as cli
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _context(tmp_path: Path) -> cli.RefinementEvaluationCliContext:
    settings = WebSettings(
        data_dir=tmp_path / "data",
        jobs_dir=tmp_path / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="vision-model",
            api_key="test-key",
            vision_enabled=True,
        ),
    )
    return cli.RefinementEvaluationCliContext(
        settings=settings,
        llm_client=object(),  # type: ignore[arg-type]
        config=RefinementFeatureConfig(enabled=True),
        adapter=object(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )


def test_phase_n3_cli_reports_only_safe_fixture_failure_details(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def run(*args, **kwargs):
        raise UserError(
            "fixture failed",
            code="REFINEMENT_EVALUATION_FIXTURE_FAILED",
            details={
                "fixture_id": "n1-crowded-power-regulator",
                "cause_code": "TOOL_ERROR",
                "cause_type": "ToolError",
                "raw_provider_payload": "must-not-leak",
            },
        )

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", run)
    context = _context(tmp_path)
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
    assert payload["code"] == "REFINEMENT_EVALUATION_FIXTURE_FAILED"
    assert payload["details"] == {
        "fixture_id": "n1-crowded-power-regulator",
        "cause_code": "TOOL_ERROR",
        "cause_type": "ToolError",
    }
    assert "raw_provider_payload" not in payload["details"]
