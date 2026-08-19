from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kicad_pcb_web import refinement_evaluation_cli as cli
from kicad_pcb_web.services import refinement_evaluation_corpus as corpus
from kicad_pcb_web.services.refinement_config import RefinementFeatureConfig
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _context(
    tmp_path: Path,
    *,
    vision_enabled: bool = True,
    enabled: bool = True,
    llm_client: object | None = None,
) -> cli.RefinementEvaluationCliContext:
    effective_provider = "openai" if enabled else "disabled"
    settings = WebSettings(
        data_dir=tmp_path / "data",
        jobs_dir=tmp_path / "jobs",
        llm=LlmSettings(
            provider=effective_provider,  # type: ignore[arg-type]
            model="vision-model",
            vision_enabled=vision_enabled,
        ),
    )
    return cli.RefinementEvaluationCliContext(
        settings=settings,
        llm_client=(object() if llm_client is None and enabled else llm_client),  # type: ignore[arg-type]
        config=RefinementFeatureConfig(enabled=True),
        adapter=object(),  # type: ignore[arg-type]
        stdout=io.StringIO(),
        stderr=io.StringIO(),
        implementation_sha="a" * 40,
    )


def test_phase_n3_cli_runs_selected_corpus_with_explicit_vision_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, object] = {}

    def run(
        request: corpus.RefinementCorpusEvaluationRequest,
        *,
        output_root: Path,
        work_root: Path,
    ) -> corpus.RefinementCorpusEvaluationResult:
        captured["request"] = request
        captured["output_root"] = output_root
        captured["work_root"] = work_root
        return corpus.RefinementCorpusEvaluationResult(
            output_root=output_root,
            summary_path=output_root / "summary.json",
            fixture_ids=("n1-crowded-power-regulator",),
            results=(),
        )

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", run)
    context = _context(tmp_path)
    code = cli.execute_refinement_evaluation_cli(
        [
            "--repo-root",
            str(tmp_path),
            "--output-root",
            str(tmp_path / "out"),
            "--work-root",
            str(tmp_path / "work"),
            "--fixture-id",
            "n1-crowded-power-regulator",
            "--max-rounds",
            "5",
            "--max-operations-per-round",
            "6",
        ],
        context,
    )

    assert code == 0
    request = captured["request"]
    assert isinstance(request, corpus.RefinementCorpusEvaluationRequest)
    assert request.provenance.provider == "openai"
    assert request.provenance.model == "vision-model"
    assert request.provenance.implementation_sha == "a" * 40
    assert request.fixture_ids == ("n1-crowded-power-regulator",)
    assert request.iteration_limits.max_operations == 6
    assert request.loop_limits.max_rounds == 5
    assert request.loop_limits.max_operations_per_round == 6
    payload = json.loads(context.stdout.getvalue())
    assert payload["status"] == "completed"
    assert payload["fixture_count"] == 1
    assert payload["provider"] == "openai"
    assert context.stderr.getvalue() == ""


@pytest.mark.parametrize(
    ("enabled", "vision_enabled", "expected_code"),
    [
        (False, True, "REFINEMENT_EVALUATION_LLM_REQUIRED"),
        (True, False, "REFINEMENT_EVALUATION_VISION_REQUIRED"),
    ],
)
def test_phase_n3_cli_requires_explicit_vision_model(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    enabled: bool,
    vision_enabled: bool,
    expected_code: str,
) -> None:
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("corpus execution must not start")

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", run)
    context = _context(tmp_path, enabled=enabled, vision_enabled=vision_enabled)
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
    assert called is False
    assert json.loads(context.stderr.getvalue())["code"] == expected_code


def test_phase_n3_cli_requires_refinement_feature_gate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("corpus execution must not start")

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", run)
    base = _context(tmp_path)
    context = cli.RefinementEvaluationCliContext(
        settings=base.settings,
        llm_client=base.llm_client,
        config=RefinementFeatureConfig(enabled=False),
        adapter=base.adapter,
        stdout=base.stdout,
        stderr=base.stderr,
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
    assert called is False
    assert json.loads(context.stderr.getvalue())["code"] == "REFINEMENT_DISABLED"


def test_phase_n3_cli_rejects_out_of_bounds_limits_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    called = False

    def run(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("corpus execution must not start")

    monkeypatch.setattr(cli, "run_refinement_evaluation_corpus", run)
    context = _context(tmp_path)
    code = cli.execute_refinement_evaluation_cli(
        [
            "--output-root",
            str(tmp_path / "out"),
            "--work-root",
            str(tmp_path / "work"),
            "--max-rounds",
            "21",
        ],
        context,
    )

    assert code == 2
    assert called is False
    assert json.loads(context.stderr.getvalue())["code"] == (
        "REFINEMENT_EVALUATION_CLI_INVALID_ARGUMENTS"
    )


def test_phase_n3_cli_rejects_missing_required_output_arguments(tmp_path: Path) -> None:
    context = _context(tmp_path)
    code = cli.execute_refinement_evaluation_cli([], context)

    assert code == 2
    assert json.loads(context.stderr.getvalue())["code"] == (
        "REFINEMENT_EVALUATION_CLI_INVALID_ARGUMENTS"
    )


def test_phase_n3_cli_rejects_provider_without_image_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class Capabilities:
        supports_image_input = False

    monkeypatch.setattr(cli, "get_llm_provider_capabilities", lambda provider: Capabilities())
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
    assert json.loads(context.stderr.getvalue())["code"] == "REFINEMENT_EVALUATION_VISION_REQUIRED"
