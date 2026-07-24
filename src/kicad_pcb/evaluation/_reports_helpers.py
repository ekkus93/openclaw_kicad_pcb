"""Scoring, rendering, and artifact-writing helpers for evaluation reports."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from kicad_pcb.corpus.kicadxml import schematic_symbols_by_ref
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata
from kicad_pcb.corpus.reports import write_json_report, write_markdown_report
from kicad_pcb.sch_doc import SchematicDoc

from ._reports_types import (
    ActionableFailure,
    EvaluationReport,
    FixtureEvaluationSummary,
)
from .electrical import ElectricalEquivalenceReport
from .scoring import IntrinsicQualityReport
from .similarity import LayoutSimilarityReport


def _schematic_symbol_fallback(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    return schematic_symbols_by_ref(SchematicDoc.load(path))


def _build_actionable_failures(
    *,
    electrical: ElectricalEquivalenceReport,
    intrinsic: IntrinsicQualityReport,
    similarity: LayoutSimilarityReport,
) -> list[ActionableFailure]:
    failures: list[ActionableFailure] = []
    if electrical.status == "failed":
        failures.append(
            ActionableFailure(
                rule="electrical_equivalence",
                severity="high",
                message="Generated schematic connectivity differs from the source fixture.",
                suggested_files=("src/kicad_pcb/router.py", "src/kicad_pcb/commands/_sch_apply.py"),
            )
        )

    failures.extend(_reason_failures(intrinsic.reasons))
    failures.extend(_reason_failures(similarity.reasons))
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    return sorted(failures, key=lambda failure: (severity_rank[failure.severity], failure.rule))


def _reason_failures(reasons: tuple[str, ...]) -> list[ActionableFailure]:
    failures: list[ActionableFailure] = []
    for reason in reasons:
        rule = reason.split(":", 1)[0]
        severity = "medium"
        suggested_files = _suggested_files(rule)
        if rule in {"validity", "electrical_equivalence"}:
            severity = "high"
        elif rule in {
            "spread",
            "geometry_spread",
            "relative_positions",
            "zone_positions",
            "orientation_match",
        }:
            severity = "medium"
        else:
            severity = "low"
        failures.append(
            ActionableFailure(
                rule=rule,
                severity=severity,
                message=reason.split(":", 1)[1].strip() if ":" in reason else reason,
                suggested_files=suggested_files,
            )
        )
    return failures


def _suggested_files(rule: str) -> tuple[str, ...]:
    mapping = {
        "role_counts": ("src/kicad_pcb/block_detection.py",),
        "relative_positions": (
            "src/kicad_pcb/graphviz_layout/dot_builder.py",
            "src/kicad_pcb/graphviz_layout/snap.py",
        ),
        "label_strategy": ("src/kicad_pcb/router.py",),
        "wire_stub_ratio": ("src/kicad_pcb/router.py",),
        "routing_simplicity": ("src/kicad_pcb/router.py",),
        "geometry_spread": (
            "src/kicad_pcb/graphviz_layout/dot_builder.py",
            "src/kicad_pcb/graphviz_layout/snap.py",
        ),
        "zone_positions": (
            "src/kicad_pcb/commands/_sch_apply_write.py",
            "src/kicad_pcb/graphviz_layout/snap.py",
        ),
        "orientation_match": ("src/kicad_pcb/commands/_sch_apply_write.py",),
        "overlap": (
            "src/kicad_pcb/schematic_metrics.py",
            "src/kicad_pcb/corpus/layout_features.py",
            "src/kicad_pcb/evaluation/",
        ),
        "electrical_equivalence": (
            "src/kicad_pcb/commands/_sch_apply.py",
            "src/kicad_pcb/router.py",
        ),
    }
    return mapping.get(rule, ("src/kicad_pcb/evaluation/",))


def _total_score(
    *,
    electrical: ElectricalEquivalenceReport,
    intrinsic: IntrinsicQualityReport,
    similarity: LayoutSimilarityReport,
) -> float:
    if electrical.status == "failed":
        return 0.0
    weighted = (intrinsic.score * 0.55) + (similarity.score * 0.45)
    return round(weighted, 2)


def _result_status(
    *,
    electrical: ElectricalEquivalenceReport,
    actionable_failures: list[ActionableFailure],
) -> str:
    if electrical.status == "failed":
        return "fail"
    if electrical.status == "not_run":
        return "partial"
    if actionable_failures:
        return "pass_with_warnings"
    return "pass"


def _report_payload(report: EvaluationReport) -> dict[str, object]:
    return {
        "schema_version": report.schema_version,
        "fixture_id": report.fixture_id,
        "source_file_name": report.source_file_name,
        "result": report.result,
        "total_score": report.total_score,
        "electrical_equivalence": asdict(report.electrical_equivalence),
        "scores": {
            "intrinsic_quality": report.intrinsic_quality.score,
            "source_similarity": report.source_similarity.score,
            "validity": report.intrinsic_quality.sub_scores.get("validity", 0.0),
            "intrinsic_sub_scores": report.intrinsic_quality.sub_scores,
            "similarity_sub_scores": report.source_similarity.sub_scores,
            "similarity_sub_score_details": {
                name: asdict(detail)
                for name, detail in report.source_similarity.sub_score_details.items()
            },
        },
        "generated_artifacts": report.generated_artifacts,
        "actionable_failures": [asdict(item) for item in report.actionable_failures],
    }


def _render_actionable_failures(report: EvaluationReport) -> str:
    lines = [
        f"# Actionable failures for {report.fixture_id}",
        "",
        f"- Source file: **{report.source_file_name}**",
        f"- Result: **{report.result}**",
        f"- Total score: **{report.total_score:.2f}**",
        f"- Electrical status: **{report.electrical_equivalence.status}**",
        "",
        "## Sub-scores",
        "",
    ]
    for name, value in report.intrinsic_quality.sub_scores.items():
        lines.append(f"- intrinsic `{name}` = {value:.2f}")
    for name, value in report.source_similarity.sub_scores.items():
        detail = report.source_similarity.sub_score_details.get(name)
        if detail is not None and not detail.applicable:
            lines.append(f"- similarity `{name}` = N/A ({detail.reason})")
        else:
            lines.append(f"- similarity `{name}` = {value:.2f}")
    lines.extend(
        [
            "",
            "## Guidance",
            "",
            "Do not special-case this fixture. Fix generic generator/evaluator rules instead.",
            "",
            "## Failures",
            "",
        ]
    )
    if not report.actionable_failures:
        lines.append("- No actionable failures recorded.")
        return "\n".join(lines)
    for failure in report.actionable_failures:
        lines.append(
            f"- [{failure.severity}] `{failure.rule}` — {failure.message} "
            f"(edit: {', '.join(failure.suggested_files)})"
        )
    return "\n".join(lines)


def _render_summary_markdown(
    summaries: list[FixtureEvaluationSummary],
    *,
    skipped_count: int,
    failed_count: int,
) -> str:
    lines = [
        "# Model corpus evaluation summary",
        "",
        f"- evaluated: {len(summaries)}",
        f"- skipped: {skipped_count}",
        f"- failed_or_partial: {failed_count}",
        "",
        "## Fixtures",
        "",
    ]
    for summary in summaries:
        lines.append(
            f"- `{summary.fixture_id}` -> **{summary.result}** score={summary.total_score:.2f}"
        )
    return "\n".join(lines)


def _load_fixture_metadata(corpus_dir: Path) -> dict[str, CorpusFixtureMetadata]:
    return {
        metadata_path.parent.name: CorpusFixtureMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        for metadata_path in sorted(corpus_dir.glob("*/metadata.json"))
    }


def write_evaluation_artifacts(
    *,
    out_dir: Path,
    report: EvaluationReport,
) -> None:
    """Write evaluation JSON and Markdown artifacts."""
    write_json_report(out_dir / "evaluation_report.json", _report_payload(report))
    write_markdown_report(out_dir / "actionable_failures.md", _render_actionable_failures(report))
