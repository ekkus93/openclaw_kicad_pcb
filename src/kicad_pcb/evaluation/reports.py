"""Corpus evaluation orchestration and report writing."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter  # noqa: F401
from kicad_pcb.corpus.reports import write_json_report, write_markdown_report
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.runner import find_kicad_cli

from ._reports_fixture import _evaluate_one_fixture, _runtime_failure_report  # noqa: F401
from ._reports_helpers import (  # noqa: F401
    _build_actionable_failures,
    _load_fixture_metadata,
    _render_summary_markdown,
    _result_status,
    _schematic_symbol_fallback,
    _total_score,
    write_evaluation_artifacts,
)
from ._reports_types import (  # noqa: F401
    MINIMUM_REPO_KICAD_VERSION,
    ActionableFailure,
    CorpusEvaluationSummary,
    EvaluationOptions,
    EvaluationReport,
    FixtureEvaluationContext,
    FixtureEvaluationSummary,
)
from .electrical import ElectricalEquivalenceReport, ElectricalMismatch  # noqa: F401
from .scoring import IntrinsicQualityReport  # noqa: F401
from .similarity import LayoutSimilarityReport  # noqa: F401


def evaluate_model_corpus(
    *,
    corpus_dir: Path,
    out_dir: Path,
    fixture_id: str | None = None,
    options: EvaluationOptions | None = None,
) -> CorpusEvaluationSummary:
    """Evaluate all eligible fixtures in *corpus_dir* into *out_dir*."""

    options = options or EvaluationOptions()
    metadata_by_fixture = _load_fixture_metadata(corpus_dir)
    if fixture_id is not None:
        if fixture_id not in metadata_by_fixture:
            raise UserError(
                f"Fixture not found: {fixture_id}",
                code=ErrorCode.USER_ERROR,
                details={"fixture_id": fixture_id, "corpus_dir": str(corpus_dir)},
            )
        target_fixtures = [fixture_id]
    else:
        target_fixtures = sorted(metadata_by_fixture)

    summaries: list[FixtureEvaluationSummary] = []
    skipped_count = 0
    failed_count = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())

    for current_fixture_id in target_fixtures:
        fixture_dir = corpus_dir / current_fixture_id
        metadata = metadata_by_fixture[current_fixture_id]
        ir_path = fixture_dir / "circuit_ir.json"
        if not ir_path.exists():
            if fixture_id is not None:
                raise UserError(
                    f"Fixture '{fixture_id}' does not contain circuit_ir.json",
                    code=ErrorCode.USER_ERROR,
                    details={"fixture_id": fixture_id, "fixture_dir": str(fixture_dir)},
                )
            skipped_count += 1
            continue

        context = FixtureEvaluationContext(
            fixture_dir=fixture_dir,
            metadata=metadata,
            out_dir=out_dir / current_fixture_id,
            adapter=adapter,
            options=options,
        )
        try:
            report = _evaluate_one_fixture(context)
        except Exception as exc:
            report = _runtime_failure_report(context, exc)

        if report.result in {"fail", "partial"}:
            failed_count += 1
        summaries.append(
            FixtureEvaluationSummary(
                fixture_id=current_fixture_id,
                result=report.result,
                total_score=report.total_score,
                report_path=out_dir / current_fixture_id / "evaluation_report.json",
                actionable_failures_path=out_dir / current_fixture_id / "actionable_failures.md",
            )
        )

    summary_json_path = out_dir / "summary.json"
    summary_md_path = out_dir / "summary.md"
    write_json_report(
        summary_json_path,
        {
            "evaluated_count": len(summaries),
            "skipped_count": skipped_count,
            "failed_count": failed_count,
            "fixtures": [asdict(summary) for summary in summaries],
        },
    )
    write_markdown_report(
        summary_md_path,
        _render_summary_markdown(summaries, skipped_count=skipped_count, failed_count=failed_count),
    )
    return CorpusEvaluationSummary(
        evaluated_count=len(summaries),
        skipped_count=skipped_count,
        failed_count=failed_count,
        summary_json_path=summary_json_path,
        summary_md_path=summary_md_path,
        fixtures=tuple(summaries),
    )
