"""Corpus evaluation orchestration and report writing."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pydantic import ValidationError

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.commands._project import create_project_files
from kicad_pcb.commands._sch_apply import _apply_netlist_to_project, _ApplyNetlistRequest
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.corpus.embedded_symbols import materialize_embedded_symbol_libraries
from kicad_pcb.corpus.kicadxml import (
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
    schematic_symbols_by_ref,
)
from kicad_pcb.corpus.layout_features import (
    LayoutFeatures,
    extract_layout_features,
    write_layout_features,
)
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata
from kicad_pcb.corpus.reports import write_json_report, write_markdown_report
from kicad_pcb.errors import ErrorCode, ToolError, UserError
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb.sch_doc import SchematicDoc

from .electrical import (
    ElectricalEquivalenceReport,
    ElectricalMismatch,
    compare_circuit_ir_equivalence,
)
from .scoring import IntrinsicQualityReport, score_intrinsic_quality
from .similarity import LayoutSimilarityReport, compare_layout_similarity

MINIMUM_REPO_KICAD_VERSION = KiCadVersion(9, 0, 0)


@dataclass(frozen=True)
class ActionableFailure:
    rule: str
    severity: str
    message: str
    suggested_files: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReport:
    schema_version: str
    fixture_id: str
    source_file_name: str
    result: str
    total_score: float
    electrical_equivalence: ElectricalEquivalenceReport
    intrinsic_quality: IntrinsicQualityReport
    source_similarity: LayoutSimilarityReport
    generated_artifacts: dict[str, str]
    actionable_failures: tuple[ActionableFailure, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FixtureEvaluationSummary:
    fixture_id: str
    result: str
    total_score: float
    report_path: Path
    actionable_failures_path: Path


@dataclass(frozen=True)
class CorpusEvaluationSummary:
    evaluated_count: int
    skipped_count: int
    failed_count: int
    summary_json_path: Path
    summary_md_path: Path
    fixtures: tuple[FixtureEvaluationSummary, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class EvaluationOptions:
    require_kicad: bool = False
    heuristic_profile: str | None = None
    label_mode: str | None = "debug"


@dataclass(frozen=True)
class FixtureEvaluationContext:
    fixture_dir: Path
    metadata: CorpusFixtureMetadata
    out_dir: Path
    adapter: KicadCliAdapter
    options: EvaluationOptions


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
            report = _runtime_failure_report(
                context,
                exc,
            )
        
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


def write_evaluation_artifacts(
    *,
    out_dir: Path,
    report: EvaluationReport,
) -> None:
    """Write evaluation JSON and Markdown artifacts."""

    write_json_report(out_dir / "evaluation_report.json", _report_payload(report))
    write_markdown_report(out_dir / "actionable_failures.md", _render_actionable_failures(report))


def _evaluate_one_fixture(context: FixtureEvaluationContext) -> EvaluationReport:
    fixture_dir = context.fixture_dir
    metadata = context.metadata
    out_dir = context.out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(out_dir / "generated_project", ignore_errors=True)
    project = create_project_files(name="generated_project", out_dir=out_dir, description="")
    custom_symbols_dir = _prepare_fixture_symbol_dir(fixture_dir=fixture_dir, out_dir=out_dir)
    apply_result = _apply_netlist_to_project(
        project,
        _ApplyNetlistRequest(
            netlist_path=fixture_dir / "circuit_ir.json",
            symbols_dir=custom_symbols_dir,
            mode_name="internal",
            force=True,
            dry_run=False,
            heuristic_profile_name=context.options.heuristic_profile,
            label_mode_name=context.options.label_mode,
        ),
    )
    managed_schematic = apply_result.managed_schematic_path
    canonical_generated = out_dir / "generated.kicad_sch"
    shutil.copyfile(managed_schematic, canonical_generated)
    generated_doc = SchematicDoc.load(managed_schematic)
    generated_features = extract_layout_features(
        generated_doc,
        fixture_id=metadata.fixture_id,
        source_file=canonical_generated.name,
    )
    write_layout_features(generated_features, out_dir / "generated_layout_features.json")
    source_features = LayoutFeatures.model_validate_json(
        (fixture_dir / "source_layout_features.json").read_text(encoding="utf-8")
    )

    electrical_report = _run_electrical_equivalence(
        fixture_dir=fixture_dir,
        out_dir=out_dir,
        generated_schematic=canonical_generated,
        adapter=context.adapter,
        require_kicad=context.options.require_kicad,
    )
    intrinsic_report = score_intrinsic_quality(generated_features)
    similarity_report = compare_layout_similarity(source_features, generated_features)
    actionable_failures = _build_actionable_failures(
        electrical=electrical_report,
        intrinsic=intrinsic_report,
        similarity=similarity_report,
    )
    total_score = _total_score(
        electrical=electrical_report,
        intrinsic=intrinsic_report,
        similarity=similarity_report,
    )
    result = _result_status(
        electrical=electrical_report,
        actionable_failures=actionable_failures,
    )
    report = EvaluationReport(
        schema_version="1.0",
        fixture_id=metadata.fixture_id,
        source_file_name=metadata.source_file_name,
        result=result,
        total_score=total_score,
        electrical_equivalence=electrical_report,
        intrinsic_quality=intrinsic_report,
        source_similarity=similarity_report,
        generated_artifacts={
            "project_dir": str(project.path),
            "schematic_path": str(canonical_generated),
            "layout_features": str(out_dir / "generated_layout_features.json"),
        },
        actionable_failures=tuple(actionable_failures),
    )
    write_evaluation_artifacts(out_dir=out_dir, report=report)
    return report


def _runtime_failure_report(
    context: FixtureEvaluationContext,
    exc: Exception,
) -> EvaluationReport:
    out_dir = context.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    runtime_message = str(exc).strip() or exc.__class__.__name__
    generated_artifacts = {"project_dir": str(out_dir / "generated_project")}
    schematic_path = out_dir / "generated.kicad_sch"
    layout_features_path = out_dir / "generated_layout_features.json"
    if schematic_path.exists():
        generated_artifacts["schematic_path"] = str(schematic_path)
    if layout_features_path.exists():
        generated_artifacts["layout_features"] = str(layout_features_path)

    electrical_report = ElectricalEquivalenceReport(
        status="not_run",
        mismatches=(
            ElectricalMismatch(
                field="evaluation_runtime",
                expected="evaluation completed without generator/runtime exceptions",
                actual=runtime_message,
            ),
        ),
    )
    intrinsic_report = IntrinsicQualityReport(
        score=0.0,
        sub_scores={
            "validity": 0.0,
            "overlap": 0.0,
            "page_bounds": 0.0,
            "routing_simplicity": 0.0,
            "label_strategy": 0.0,
            "spread": 0.0,
            "power_symbols": 0.0,
        },
        reasons=("validity: evaluation runtime failed before intrinsic scoring could run.",),
    )
    similarity_report = LayoutSimilarityReport(
        score=0.0,
        sub_scores={
            "role_counts": 0.0,
            "relative_positions": 0.0,
            "label_strategy": 0.0,
            "geometry_spread": 0.0,
            "wire_stub_ratio": 0.0,
        },
        reasons=(
            "relative_positions: evaluation runtime failed before source comparison could run.",
        ),
    )
    actionable_failures = (
        ActionableFailure(
            rule="evaluation_runtime",
            severity="high",
            message=f"evaluation runtime failed before report generation: {runtime_message}",
            suggested_files=(
                "src/kicad_pcb/evaluation/reports.py",
                "src/kicad_pcb/commands/_sch_apply.py",
                "src/kicad_pcb/graphviz_layout/__init__.py",
            ),
        ),
    )
    report = EvaluationReport(
        schema_version="1.0",
        fixture_id=context.metadata.fixture_id,
        source_file_name=context.metadata.source_file_name,
        result=_result_status(
            electrical=electrical_report,
            actionable_failures=list(actionable_failures),
        ),
        total_score=0.0,
        electrical_equivalence=electrical_report,
        intrinsic_quality=intrinsic_report,
        source_similarity=similarity_report,
        generated_artifacts=generated_artifacts,
        actionable_failures=actionable_failures,
    )
    write_evaluation_artifacts(out_dir=out_dir, report=report)
    return report


def _prepare_fixture_symbol_dir(*, fixture_dir: Path, out_dir: Path) -> Path | None:
    symbols_artifact = fixture_dir / "source_embedded_symbols.sexpr"
    generated_symbols_dir = out_dir / "fixture_symbols"
    shutil.rmtree(generated_symbols_dir, ignore_errors=True)
    return materialize_embedded_symbol_libraries(
        symbols_artifact,
        output_dir=generated_symbols_dir,
    )


def _run_electrical_equivalence(
    *,
    fixture_dir: Path,
    out_dir: Path,
    generated_schematic: Path,
    adapter: KicadCliAdapter,
    require_kicad: bool,
) -> ElectricalEquivalenceReport:
    version = adapter.detected_version
    if version is None or version < MINIMUM_REPO_KICAD_VERSION:
        if require_kicad:
            raise ToolError(
                "kicad-cli >= 9.0.0 is required for repo schematic electrical evaluation",
                code=ErrorCode.KICAD_CLI_MISSING,
                details={"detected_version": str(version) if version is not None else None},
            )
        return ElectricalEquivalenceReport(status="not_run", mismatches=())

    netlist_path = out_dir / "generated_netlist.kicadxml"
    export_result, xml_content = adapter.export_netlist(generated_schematic, netlist_path)
    if not export_result.ok or not xml_content:
        if require_kicad:
            raise ToolError(
                "Generated schematic netlist export failed during corpus evaluation",
                code=ErrorCode.TOOL_ERROR,
                details={"stderr": export_result.stderr, "stdout": export_result.stdout},
            )
        return ElectricalEquivalenceReport(status="not_run", mismatches=())

    source_ir = parse_kicadxml_netlist(fixture_dir / "source_netlist.kicadxml")
    generated_ir = parse_kicadxml_netlist(netlist_path)
    source_schematic_path = fixture_dir / "source_normalized.kicad_sch"
    if not source_schematic_path.exists():
        source_schematic_path = fixture_dir / "source.kicad_sch"
    try:
        source_circuit = kicadxml_to_circuit_ir(
            source_ir,
            fallback_symbols_by_ref=_schematic_symbol_fallback(source_schematic_path),
        )
        generated_circuit = kicadxml_to_circuit_ir(
            generated_ir,
            fallback_symbols_by_ref=_schematic_symbol_fallback(generated_schematic),
        )
    except ValidationError as exc:
        return ElectricalEquivalenceReport(
            status="failed",
            mismatches=(
                ElectricalMismatch(
                    field="generated_netlist",
                    expected="a generated KiCad XML netlist with at least one net",
                    actual=str(exc),
                ),
            ),
        )
    return compare_circuit_ir_equivalence(source_circuit, generated_circuit)


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
        elif rule in {"spread", "geometry_spread", "relative_positions"}:
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
            f"- `{summary.fixture_id}` -> **{summary.result}** "
            f"score={summary.total_score:.2f}"
        )
    return "\n".join(lines)


def _load_fixture_metadata(corpus_dir: Path) -> dict[str, CorpusFixtureMetadata]:
    return {
        metadata_path.parent.name: CorpusFixtureMetadata.model_validate_json(
            metadata_path.read_text(encoding="utf-8")
        )
        for metadata_path in sorted(corpus_dir.glob("*/metadata.json"))
    }
