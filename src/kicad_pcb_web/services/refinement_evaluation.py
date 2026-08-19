"""Phase N experimental evaluation runner for schematic visual refinement."""

from __future__ import annotations

import hashlib
import shutil
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.corpus.reports import write_json_report
from kicad_pcb.errors import UserError
from kicad_pcb.evaluation.refinement_baseline import fixture_definition
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    require_schematic_electrical_invariance,
)
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.rendering import SchematicRenderArtifact, render_schematic_for_refinement

from .llm import LlmClient
from .schematic_refinement import (
    RefinementAnalysisResult,
    RefinementApplyResult,
    RefinementIterationLimits,
    RefinementLoopLimits,
    RefinementLoopResult,
    RefinementPlanResult,
    RefinementProvenance,
    RefinementRuntime,
    analyze_schematic_refinement,
    apply_once_schematic_refinement,
    plan_schematic_refinement,
    refine_schematic,
)

REFINEMENT_EVALUATION_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class RefinementEvaluationFixture:
    fixture_id: str
    source_fixture_id: str
    categories: tuple[str, ...]
    known_visual_defects: tuple[str, ...]


@dataclass(frozen=True)
class RefinementEvaluationRequest:
    fixture: RefinementEvaluationFixture
    authoritative_ir: CircuitIR
    baseline_schematic: Path
    adapter: KicadCliAdapter
    llm_client: LlmClient
    provenance: RefinementProvenance
    iteration_limits: RefinementIterationLimits
    loop_limits: RefinementLoopLimits


@dataclass(frozen=True)
class RefinementEvaluationResult:
    fixture_id: str
    output_dir: Path
    baseline_hash: str
    apply_once_status: str
    apply_once_code: str
    refine_status: str
    refine_stop_reason: str
    final_accepted_hash: str


@dataclass(frozen=True)
class _EvaluationPaths:
    output_tmp: Path
    work_tmp: Path
    baseline_hash: str


def run_refinement_evaluation(
    request: RefinementEvaluationRequest,
    *,
    output_root: Path,
    work_root: Path,
) -> RefinementEvaluationResult:
    """Run all Phase N3 modes from identical baseline bytes and publish one evidence bundle."""

    _validate_request(request)
    output_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / request.fixture.fixture_id
    if final_dir.exists():
        raise UserError(
            "Refinement evaluation result already exists.",
            code="REFINEMENT_EVALUATION_EXISTS",
            details={"fixture_id": request.fixture.fixture_id},
        )

    output_tmp = Path(tempfile.mkdtemp(prefix=".refinement-evaluation-", dir=output_root))
    work_tmp = Path(tempfile.mkdtemp(prefix=f".{request.fixture.fixture_id}-", dir=work_root))
    try:
        result = _run_evaluation(request, output_tmp=output_tmp, work_tmp=work_tmp)
        if final_dir.exists():
            raise UserError(
                "Refinement evaluation result already exists.",
                code="REFINEMENT_EVALUATION_EXISTS",
                details={"fixture_id": request.fixture.fixture_id},
            )
        output_tmp.replace(final_dir)
        return RefinementEvaluationResult(
            fixture_id=result.fixture_id,
            output_dir=final_dir,
            baseline_hash=result.baseline_hash,
            apply_once_status=result.apply_once_status,
            apply_once_code=result.apply_once_code,
            refine_status=result.refine_status,
            refine_stop_reason=result.refine_stop_reason,
            final_accepted_hash=result.final_accepted_hash,
        )
    except Exception:
        shutil.rmtree(output_tmp, ignore_errors=True)
        raise
    finally:
        shutil.rmtree(work_tmp, ignore_errors=True)


def _run_evaluation(
    request: RefinementEvaluationRequest,
    *,
    output_tmp: Path,
    work_tmp: Path,
) -> RefinementEvaluationResult:
    baseline_hash = _sha(request.baseline_schematic)
    paths = _EvaluationPaths(
        output_tmp=output_tmp,
        work_tmp=work_tmp,
        baseline_hash=baseline_hash,
    )
    baseline_dir = output_tmp / "baseline"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(request.baseline_schematic, baseline_dir / "accepted.kicad_sch")

    analyze_path = _stage_schematic(request.baseline_schematic, work_tmp / "analyze")
    analyze_runtime = _runtime(request, work_tmp / "analyze", output_tmp / "analyze" / "evidence")
    analysis = analyze_schematic_refinement(
        accepted_path=analyze_path,
        runtime=analyze_runtime,
        max_critic_repairs=request.iteration_limits.max_critic_repairs,
    )
    _require_stage_baseline("analyze", baseline_hash, analysis.accepted_hash, analyze_path)
    analyze_render = _publish_render(
        analysis.render,
        output_tmp / "analyze" / "render",
        relative_prefix=Path("analyze/render"),
    )
    write_json_report(
        output_tmp / "analyze" / "result.json",
        _analysis_payload(analysis, render=analyze_render),
    )

    plan_path = _stage_schematic(request.baseline_schematic, work_tmp / "plan")
    plan_runtime = _runtime(request, work_tmp / "plan", output_tmp / "plan" / "evidence")
    plan_iteration_id = f"{request.fixture.fixture_id}-plan-001"
    planned = plan_schematic_refinement(
        accepted_path=plan_path,
        runtime=plan_runtime,
        iteration_id=plan_iteration_id,
        limits=request.iteration_limits,
    )
    _require_stage_baseline("plan", baseline_hash, planned.analysis.accepted_hash, plan_path)
    plan_render = _publish_render(
        planned.analysis.render,
        output_tmp / "plan" / "render",
        relative_prefix=Path("plan/render"),
    )
    write_json_report(
        output_tmp / "plan" / "result.json",
        _plan_payload(planned, render=plan_render),
    )

    apply_path = _stage_schematic(request.baseline_schematic, work_tmp / "apply-once")
    apply_runtime = _runtime(
        request,
        work_tmp / "apply-once",
        output_tmp / "apply_once" / "evidence",
    )
    apply_iteration_id = f"{request.fixture.fixture_id}-apply-001"
    applied = apply_once_schematic_refinement(
        accepted_path=apply_path,
        runtime=apply_runtime,
        iteration_id=apply_iteration_id,
        limits=request.iteration_limits,
    )
    apply_final = _capture_final_state(
        request,
        accepted_path=apply_path,
        stage="apply_once",
        paths=paths,
    )
    _require_apply_binding(applied, baseline_hash, str(apply_final["schematic_hash"]))
    write_json_report(
        output_tmp / "apply_once" / "result.json",
        _apply_payload(applied, evidence_root=output_tmp / "apply_once" / "evidence"),
    )

    refine_path = _stage_schematic(request.baseline_schematic, work_tmp / "refine")
    refine_runtime = _runtime(request, work_tmp / "refine", output_tmp / "refine" / "evidence")
    session_id = f"{request.fixture.fixture_id}-refine"
    refined = refine_schematic(
        accepted_path=refine_path,
        runtime=refine_runtime,
        session_id=session_id,
        limits=request.loop_limits,
    )
    refine_final = _capture_final_state(
        request,
        accepted_path=refine_path,
        stage="refine",
        paths=paths,
    )
    _require_loop_binding(refined, baseline_hash, str(refine_final["schematic_hash"]))
    write_json_report(
        output_tmp / "refine" / "result.json",
        _loop_payload(refined, evidence_root=output_tmp / "refine" / "evidence"),
    )

    operations_payload = {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "apply_once": _iteration_operations_payload(applied),
        "refine": [_iteration_operations_payload(item) for item in refined.iterations],
    }
    write_json_report(output_tmp / "operations.json", operations_payload)

    manifest = {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "phase": "N3",
        "fixture_id": request.fixture.fixture_id,
        "source_fixture_id": request.fixture.source_fixture_id,
        "categories": list(request.fixture.categories),
        "known_visual_defects": list(request.fixture.known_visual_defects),
        "baseline_schematic_sha256": baseline_hash,
        "isolation_policy": "each mode starts from identical baseline schematic bytes",
        "analyze": {
            "result": "analyze/result.json",
            "render": analyze_render,
        },
        "plan": {
            "result": "plan/result.json",
            "render": plan_render,
        },
        "apply_once": {
            "result": "apply_once/result.json",
            "status": applied.status,
            "code": applied.code,
            "final_electrical": "apply_once/final/electrical.json",
            "final_metrics": "apply_once/final/metrics.json",
            "after_render": apply_final["render"],
            "final_schematic": "apply_once/final/accepted.kicad_sch",
        },
        "refine": {
            "result": "refine/result.json",
            "status": refined.status,
            "stop_reason": refined.stop_reason,
            "final_electrical": "refine/final/electrical.json",
            "final_metrics": "refine/final/metrics.json",
            "after_render": refine_final["render"],
            "final_schematic": "refine/final/accepted.kicad_sch",
        },
        "operations": "operations.json",
        "before_after": {
            "before_render": analyze_render,
            "after_render": refine_final["render"],
        },
        "final_electrical_status": refine_final["electrical_status"],
        "final_accepted_hash": refine_final["schematic_hash"],
        "stop_reason": refined.stop_reason,
    }
    write_json_report(output_tmp / "manifest.json", manifest)
    if _sha(request.baseline_schematic) != baseline_hash:
        raise UserError(
            "Refinement evaluation baseline changed during the run.",
            code="REFINEMENT_EVALUATION_STALE",
        )

    return RefinementEvaluationResult(
        fixture_id=request.fixture.fixture_id,
        output_dir=output_tmp,
        baseline_hash=baseline_hash,
        apply_once_status=applied.status,
        apply_once_code=applied.code,
        refine_status=refined.status,
        refine_stop_reason=refined.stop_reason,
        final_accepted_hash=str(refine_final["schematic_hash"]),
    )


def _runtime(
    request: RefinementEvaluationRequest,
    work_dir: Path,
    evidence_root: Path,
) -> RefinementRuntime:
    return RefinementRuntime(
        authoritative_ir=request.authoritative_ir,
        adapter=request.adapter,
        llm_client=request.llm_client,
        work_dir=work_dir,
        evidence_root=evidence_root,
        provenance=request.provenance,
    )


def _stage_schematic(source: Path, stage_dir: Path) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=True)
    target = stage_dir / "accepted.kicad_sch"
    shutil.copyfile(source, target)
    return target


def _capture_final_state(
    request: RefinementEvaluationRequest,
    *,
    accepted_path: Path,
    stage: str,
    paths: _EvaluationPaths,
) -> dict[str, object]:
    baseline = build_schematic_electrical_baseline(
        request.authoritative_ir,
        request.baseline_schematic,
    )
    if baseline.accepted_schematic_hash != paths.baseline_hash:
        raise UserError(
            "Refinement evaluation baseline changed during the run.",
            code="REFINEMENT_EVALUATION_STALE",
        )
    electrical = require_schematic_electrical_invariance(
        authoritative_ir=request.authoritative_ir,
        baseline=baseline,
        candidate_schematic=accepted_path,
        adapter=request.adapter,
        work_dir=paths.work_tmp / stage / "final-electrical",
    )
    metrics = compute_refinement_metrics(accepted_path)
    render = render_schematic_for_refinement(
        accepted_path,
        paths.work_tmp / stage / "final-render",
        adapter=request.adapter,
    )
    final_dir = paths.output_tmp / stage / "final"
    final_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(accepted_path, final_dir / "accepted.kicad_sch")
    published_render = _publish_render(
        render,
        final_dir / "render",
        relative_prefix=Path(stage) / "final" / "render",
    )
    write_json_report(final_dir / "electrical.json", asdict(electrical))
    write_json_report(final_dir / "metrics.json", metrics.to_dict())
    return {
        "schematic_hash": metrics.schematic_hash,
        "electrical_status": electrical.status,
        "render": published_render,
    }


def _analysis_payload(
    result: RefinementAnalysisResult,
    *,
    render: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "accepted_hash": result.accepted_hash,
        "authoritative_hash": result.baseline.authoritative_hash,
        "metrics": result.metrics.to_dict(),
        "critic": result.critic.model_dump(mode="json"),
        "render": render,
    }


def _plan_payload(
    result: RefinementPlanResult,
    *,
    render: dict[str, object],
) -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "analysis": _analysis_payload(result.analysis, render=render),
        "plan": result.plan.model_dump(mode="json"),
    }


def _apply_payload(result: RefinementApplyResult, *, evidence_root: Path) -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "status": result.status,
        "code": result.code,
        "accepted_hash_before": result.accepted_hash_before,
        "accepted_hash_after": result.accepted_hash_after,
        "candidate_hash": result.candidate_hash,
        "candidate_layout_fingerprint": result.candidate_layout_fingerprint,
        "evidence_dir": _relative_evidence(result.evidence_dir, evidence_root),
        "operations": result.operations,
        "electrical": result.electrical,
        "structural": result.structural,
        "quality": result.quality,
    }


def _loop_payload(result: RefinementLoopResult, *, evidence_root: Path) -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_EVALUATION_SCHEMA_VERSION,
        "status": result.status,
        "stop_reason": result.stop_reason,
        "starting_hash": result.starting_hash,
        "final_accepted_hash": result.final_accepted_hash,
        "best_accepted_hash": result.best_accepted_hash,
        "latest_attempted_hash": result.latest_attempted_hash,
        "starting_layout_fingerprint": result.starting_layout_fingerprint,
        "final_layout_fingerprint": result.final_layout_fingerprint,
        "rounds_attempted": result.rounds_attempted,
        "accepted_rounds": result.accepted_rounds,
        "rejected_rounds": result.rejected_rounds,
        "accepted_operations": result.accepted_operations,
        "model_calls_made": result.model_calls_made,
        "model_call_limit": result.model_call_limit,
        "session_evidence_dir": _relative_evidence(result.session_evidence_dir, evidence_root),
        "iterations": [
            _apply_payload(item, evidence_root=evidence_root) for item in result.iterations
        ],
    }


def _iteration_operations_payload(result: RefinementApplyResult) -> dict[str, object]:
    return {
        "status": result.status,
        "code": result.code,
        "candidate_hash": result.candidate_hash,
        "operations": result.operations,
        "quality": result.quality,
    }


def _publish_render(
    render: SchematicRenderArtifact,
    destination: Path,
    *,
    relative_prefix: Path,
) -> dict[str, object]:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(render.svg_path, destination / "schematic.svg")
    shutil.copyfile(render.png_path, destination / "schematic.png")
    review_regions: list[dict[str, object]] = []
    if render.review_regions:
        regions_dir = destination / "review-regions"
        regions_dir.mkdir(parents=True, exist_ok=True)
        for region in render.review_regions:
            shutil.copyfile(region.svg_path, regions_dir / f"{region.region_id}.svg")
            shutil.copyfile(region.png_path, regions_dir / f"{region.region_id}.png")
            review_regions.append(
                {
                    "region_id": region.region_id,
                    "svg": str(relative_prefix / "review-regions" / f"{region.region_id}.svg"),
                    "png": str(relative_prefix / "review-regions" / f"{region.region_id}.png"),
                    "svg_hash": region.svg_hash,
                    "png_hash": region.png_hash,
                    "view_box_mm": list(region.view_box_mm),
                }
            )
    return {
        "schema_version": render.schema_version,
        "schematic_hash": render.schematic_hash,
        "svg_hash": render.svg_hash,
        "png_hash": render.png_hash,
        "kicad_version": render.kicad_version,
        "sheet_id": render.sheet_id,
        "width_px": render.width_px,
        "height_px": render.height_px,
        "view_box_mm": list(render.svg_view_box_mm),
        "svg": str(relative_prefix / "schematic.svg"),
        "png": str(relative_prefix / "schematic.png"),
        "review_regions": review_regions,
    }


def _relative_evidence(path: Path | None, root: Path) -> str | None:
    if path is None:
        return None
    resolved = path.resolve()
    root_resolved = root.resolve()
    try:
        relative = resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise UserError(
            "Refinement evaluation evidence escaped its stage root.",
            code="REFINEMENT_EVALUATION_UNSAFE_DATA",
        ) from exc
    return str(Path("evidence") / relative)


def _require_stage_baseline(stage: str, expected_hash: str, reported_hash: str, path: Path) -> None:
    if reported_hash != expected_hash or _sha(path) != expected_hash:
        raise UserError(
            f"Refinement evaluation {stage} stage did not start from the baseline bytes.",
            code="REFINEMENT_EVALUATION_STALE",
        )


def _require_apply_binding(
    result: RefinementApplyResult,
    baseline_hash: str,
    actual_final_hash: str,
) -> None:
    if (
        result.accepted_hash_before != baseline_hash
        or result.accepted_hash_after != actual_final_hash
    ):
        raise UserError(
            "Refinement evaluation apply-once result is not bound to the isolated artifact.",
            code="REFINEMENT_EVALUATION_STALE",
        )


def _require_loop_binding(
    result: RefinementLoopResult,
    baseline_hash: str,
    actual_final_hash: str,
) -> None:
    if result.starting_hash != baseline_hash or result.final_accepted_hash != actual_final_hash:
        raise UserError(
            "Refinement evaluation refine result is not bound to the isolated artifact.",
            code="REFINEMENT_EVALUATION_STALE",
        )


def _validate_request(request: RefinementEvaluationRequest) -> None:
    fixture_definition(
        fixture_id=request.fixture.fixture_id,
        source_fixture_id=request.fixture.source_fixture_id,
        categories=request.fixture.categories,
        known_visual_defects=request.fixture.known_visual_defects,
    )
    if not request.baseline_schematic.is_file():
        raise UserError(
            "Refinement evaluation baseline schematic is missing.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
