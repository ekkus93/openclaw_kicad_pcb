"""Fixture-level evaluation: symbol prep, electrical equivalence, per-fixture runner."""

from __future__ import annotations

import shutil
from pathlib import Path

from pydantic import ValidationError

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.commands._project import create_project_files
from kicad_pcb.commands._sch_apply import _apply_netlist_to_project, _ApplyNetlistRequest
from kicad_pcb.corpus.embedded_symbols import materialize_embedded_symbol_libraries
from kicad_pcb.corpus.kicadxml import (
    kicadxml_to_circuit_ir,
    parse_kicadxml_netlist,
)
from kicad_pcb.corpus.layout_features import (
    LayoutFeatures,
    extract_layout_features,
    write_layout_features,
)
from kicad_pcb.errors import ErrorCode, ToolError
from kicad_pcb.sch_doc import SchematicDoc

from ._reports_helpers import (
    _build_actionable_failures,
    _result_status,
    _schematic_symbol_fallback,
    _total_score,
    write_evaluation_artifacts,
)
from ._reports_types import (
    MINIMUM_REPO_KICAD_VERSION,
    ActionableFailure,
    EvaluationReport,
    FixtureEvaluationContext,
)
from .electrical import (
    ElectricalEquivalenceReport,
    ElectricalMismatch,
    compare_circuit_ir_equivalence,
)
from .scoring import IntrinsicQualityReport, score_intrinsic_quality
from .similarity import LayoutSimilarityReport, compare_layout_similarity


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
            "zone_positions": 0.0,
            "orientation_match": 0.0,
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
