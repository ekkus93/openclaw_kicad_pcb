"""Transactional schematic visual-refinement service modes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.electrical import (
    SchematicElectricalBaseline,
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.evidence import (
    IterationEvidenceInputs,
    write_iteration_evidence_bundle,
)
from kicad_pcb.refinement.metrics import RefinementMetricReport, compute_refinement_metrics
from kicad_pcb.refinement.operations import (
    LayoutOperationBatchResult,
    LayoutOperationPolicy,
    execute_layout_operations,
)
from kicad_pcb.refinement.planner import ValidatedRepairPlan
from kicad_pcb.refinement.quality import CandidateQualityDecision, evaluate_candidate_quality
from kicad_pcb.refinement.rendering import SchematicRenderArtifact, render_schematic_for_refinement
from kicad_pcb.refinement.transaction import SchematicCandidateTransaction
from kicad_pcb.refinement.validation import (
    CandidateStructuralValidationReport,
    validate_candidate_structure,
)
from kicad_pcb.refinement.vision_context import VisionObjectMap, build_vision_object_map

from .llm import LlmClient
from .refinement_llm import RepairPlannerOptions, run_repair_planner, run_visual_critic


@dataclass(frozen=True)
class RefinementAnalysisResult:
    accepted_hash: str
    baseline: SchematicElectricalBaseline
    metrics: RefinementMetricReport
    render: SchematicRenderArtifact
    context: VisionObjectMap
    critic: CriticResponse


@dataclass(frozen=True)
class RefinementPlanResult:
    analysis: RefinementAnalysisResult
    plan: ValidatedRepairPlan


@dataclass(frozen=True)
class RefinementApplyResult:
    status: str
    code: str
    accepted_hash_before: str
    accepted_hash_after: str
    candidate_hash: str | None
    evidence_dir: Path | None
    operations: LayoutOperationBatchResult | None
    electrical: SchematicElectricalVerificationReport | None
    structural: CandidateStructuralValidationReport | None
    quality: CandidateQualityDecision | None


@dataclass(frozen=True)
class RefinementRuntime:
    authoritative_ir: CircuitIR
    adapter: KicadCliAdapter
    llm_client: LlmClient
    work_dir: Path
    evidence_root: Path
    operation_policy: LayoutOperationPolicy = LayoutOperationPolicy()


@dataclass(frozen=True)
class RefinementIterationLimits:
    max_critic_repairs: int
    max_planner_repairs: int
    max_operations: int


@dataclass(frozen=True)
class _CandidateRejectionContext:
    transaction: SchematicCandidateTransaction
    analysis: RefinementAnalysisResult
    plan: ValidatedRepairPlan
    operations: LayoutOperationBatchResult
    electrical: SchematicElectricalVerificationReport | None
    structural: CandidateStructuralValidationReport | None
    candidate_metrics: RefinementMetricReport
    quality: CandidateQualityDecision | None
    accepted_path: Path
    runtime: RefinementRuntime
    iteration_id: str


def analyze_schematic_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    max_critic_repairs: int,
) -> RefinementAnalysisResult:
    """Render and critique the accepted schematic without mutating its bytes."""

    accepted_hash = _sha(accepted_path)
    baseline = build_schematic_electrical_baseline(runtime.authoritative_ir, accepted_path)
    metrics = compute_refinement_metrics(accepted_path)
    render = render_schematic_for_refinement(
        accepted_path,
        runtime.work_dir / "accepted-render",
        adapter=runtime.adapter,
    )
    context = build_vision_object_map(
        accepted_path,
        authoritative_ir=runtime.authoritative_ir,
        render=render,
        metrics=metrics,
    )
    critic = run_visual_critic(
        llm_client=runtime.llm_client,
        context=context,
        image_path=render.png_path,
        max_repairs=max_critic_repairs,
    )
    _assert_hash_unchanged(accepted_path, accepted_hash, mode="analyze")
    return RefinementAnalysisResult(
        accepted_hash=accepted_hash,
        baseline=baseline,
        metrics=metrics,
        render=render,
        context=context,
        critic=critic,
    )


def plan_schematic_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    iteration_id: str,
    limits: RefinementIterationLimits,
) -> RefinementPlanResult:
    """Analyze and plan one iteration while proving accepted bytes remain read-only."""

    analysis = analyze_schematic_refinement(
        accepted_path=accepted_path,
        runtime=runtime,
        max_critic_repairs=limits.max_critic_repairs,
    )
    plan = run_repair_planner(
        llm_client=runtime.llm_client,
        context=analysis.context,
        critic=analysis.critic,
        options=RepairPlannerOptions(
            iteration_id=iteration_id,
            max_repairs=limits.max_planner_repairs,
            max_operations=limits.max_operations,
        ),
    )
    _assert_hash_unchanged(accepted_path, analysis.accepted_hash, mode="plan")
    return RefinementPlanResult(analysis=analysis, plan=plan)


def apply_planned_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    planned: RefinementPlanResult,
    iteration_id: str,
) -> RefinementApplyResult:
    """Apply one already-validated plan transactionally and fail closed at every hard gate."""

    analysis = planned.analysis
    plan = planned.plan
    if iteration_id != plan.iteration_id:
        raise UserError(
            "Refinement iteration id does not match validated plan.", code="REFINEMENT_STALE"
        )
    if not plan.operation_payloads:
        _assert_hash_unchanged(accepted_path, analysis.accepted_hash, mode="apply-noop")
        return RefinementApplyResult(
            status="no_op",
            code="REFINEMENT_NO_OPERATIONS",
            accepted_hash_before=analysis.accepted_hash,
            accepted_hash_after=analysis.accepted_hash,
            candidate_hash=None,
            evidence_dir=None,
            operations=None,
            electrical=None,
            structural=None,
            quality=None,
        )

    with SchematicCandidateTransaction(
        accepted_path,
        expected_accepted_hash=analysis.accepted_hash,
    ) as transaction:
        operations = execute_layout_operations(
            transaction.candidate_path,
            plan.operation_payloads,
            expected_source_hash=analysis.accepted_hash,
            authoritative_ir=runtime.authoritative_ir,
            policy=runtime.operation_policy,
        )
        candidate_metrics = compute_refinement_metrics(transaction.candidate_path)
        candidate_hash = candidate_metrics.schematic_hash
        if candidate_hash != operations.candidate_hash:
            raise UserError(
                "Candidate metrics were computed from unexpected schematic bytes.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
            )

        electrical = verify_schematic_electrical_invariance(
            authoritative_ir=runtime.authoritative_ir,
            baseline=analysis.baseline,
            candidate_schematic=transaction.candidate_path,
            adapter=runtime.adapter,
            work_dir=transaction.candidate_path.parent,
        )
        if not electrical.passed:
            return _reject_candidate(
                _CandidateRejectionContext(
                    transaction=transaction,
                    analysis=analysis,
                    plan=plan,
                    operations=operations,
                    electrical=electrical,
                    structural=None,
                    candidate_metrics=candidate_metrics,
                    quality=None,
                    accepted_path=accepted_path,
                    runtime=runtime,
                    iteration_id=iteration_id,
                ),
                "ELECTRICAL_INVARIANCE_FAILED",
            )

        structural = validate_candidate_structure(
            transaction.candidate_path,
            adapter=runtime.adapter,
            work_dir=transaction.candidate_path.parent,
        )
        if not structural.passed:
            return _reject_candidate(
                _CandidateRejectionContext(
                    transaction=transaction,
                    analysis=analysis,
                    plan=plan,
                    operations=operations,
                    electrical=electrical,
                    structural=structural,
                    candidate_metrics=candidate_metrics,
                    quality=None,
                    accepted_path=accepted_path,
                    runtime=runtime,
                    iteration_id=iteration_id,
                ),
                "REFINEMENT_STRUCTURAL_VALIDATION_FAILED",
            )

        hard_geometry_code = _hard_geometry_failure(analysis.metrics, candidate_metrics)
        if hard_geometry_code is not None:
            return _reject_candidate(
                _CandidateRejectionContext(
                    transaction=transaction,
                    analysis=analysis,
                    plan=plan,
                    operations=operations,
                    electrical=electrical,
                    structural=structural,
                    candidate_metrics=candidate_metrics,
                    quality=None,
                    accepted_path=accepted_path,
                    runtime=runtime,
                    iteration_id=iteration_id,
                ),
                hard_geometry_code,
            )

        addressed_categories = tuple(
            sorted(
                {
                    issue.category
                    for issue in analysis.critic.issues
                    if issue.issue_id in plan.addressed_issue_ids
                }
            )
        )
        quality = evaluate_candidate_quality(
            analysis.metrics,
            candidate_metrics,
            addressed_categories=addressed_categories,
        )
        if not quality.accepted:
            return _reject_candidate(
                _CandidateRejectionContext(
                    transaction=transaction,
                    analysis=analysis,
                    plan=plan,
                    operations=operations,
                    electrical=electrical,
                    structural=structural,
                    candidate_metrics=candidate_metrics,
                    quality=quality,
                    accepted_path=accepted_path,
                    runtime=runtime,
                    iteration_id=iteration_id,
                ),
                quality.code,
            )

        after_render = render_schematic_for_refinement(
            transaction.candidate_path,
            runtime.evidence_root / ".candidate-renders" / iteration_id,
            adapter=runtime.adapter,
        )
        if after_render.schematic_hash != candidate_hash:
            raise UserError(
                "Candidate changed before post-edit render capture.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
            )
        evidence_dir = write_iteration_evidence_bundle(
            runtime.evidence_root,
            IterationEvidenceInputs(
                iteration_id=iteration_id,
                accepted_hash_before=analysis.accepted_hash,
                authoritative_hash=analysis.baseline.authoritative_hash,
                critic=analysis.critic,
                plan=plan,
                operation_results=operations,
                electrical_report=electrical,
                structural_report=structural,
                metrics_before=analysis.metrics,
                metrics_after=candidate_metrics,
                quality_decision=quality,
                before_render=analysis.render,
                after_render=after_render,
                accepted_hash_after=candidate_hash,
                disposition="approved_for_promotion",
                reason_code=quality.code,
            ),
        )
        transaction.mark_validated(candidate_hash=candidate_hash)
        promoted_hash = transaction.promote()
        return RefinementApplyResult(
            status="accepted",
            code=quality.code,
            accepted_hash_before=analysis.accepted_hash,
            accepted_hash_after=promoted_hash,
            candidate_hash=candidate_hash,
            evidence_dir=evidence_dir,
            operations=operations,
            electrical=electrical,
            structural=structural,
            quality=quality,
        )


def apply_once_schematic_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    iteration_id: str,
    limits: RefinementIterationLimits,
) -> RefinementApplyResult:
    planned = plan_schematic_refinement(
        accepted_path=accepted_path,
        runtime=runtime,
        iteration_id=iteration_id,
        limits=limits,
    )
    return apply_planned_refinement(
        accepted_path=accepted_path,
        runtime=runtime,
        planned=planned,
        iteration_id=iteration_id,
    )


def _reject_candidate(
    context: _CandidateRejectionContext,
    code: str,
) -> RefinementApplyResult:
    candidate_hash = context.candidate_metrics.schematic_hash
    after_render: SchematicRenderArtifact | None = None
    evidence_dir = write_iteration_evidence_bundle(
        context.runtime.evidence_root,
        IterationEvidenceInputs(
            iteration_id=context.iteration_id,
            accepted_hash_before=context.analysis.accepted_hash,
            authoritative_hash=context.analysis.baseline.authoritative_hash,
            critic=context.analysis.critic,
            plan=context.plan,
            operation_results=context.operations,
            electrical_report=context.electrical or {"status": "not_run"},
            structural_report=context.structural or {"status": "not_run"},
            metrics_before=context.analysis.metrics,
            metrics_after=context.candidate_metrics,
            quality_decision=context.quality or {"accepted": False, "code": code},
            before_render=context.analysis.render,
            after_render=after_render,
            accepted_hash_after=None,
            disposition="rejected",
            reason_code=code,
        ),
    )
    context.transaction.reject()
    _assert_hash_unchanged(
        context.accepted_path,
        context.analysis.accepted_hash,
        mode="reject",
    )
    return RefinementApplyResult(
        status="rejected",
        code=code,
        accepted_hash_before=context.analysis.accepted_hash,
        accepted_hash_after=context.analysis.accepted_hash,
        candidate_hash=candidate_hash,
        evidence_dir=evidence_dir,
        operations=context.operations,
        electrical=context.electrical,
        structural=context.structural,
        quality=context.quality,
    )


def _hard_geometry_failure(
    before: RefinementMetricReport,
    after: RefinementMetricReport,
) -> str | None:
    if after.out_of_page_count > before.out_of_page_count:
        return "REFINEMENT_OUT_OF_PAGE_REGRESSION"
    if after.off_grid_geometry_count > before.off_grid_geometry_count:
        return "REFINEMENT_OFF_GRID_REGRESSION"
    return None


def _assert_hash_unchanged(path: Path, expected: str, *, mode: str) -> None:
    actual = _sha(path)
    if actual != expected:
        raise UserError(
            f"Refinement {mode} mode modified accepted schematic bytes.",
            code="REFINEMENT_READ_ONLY_VIOLATION",
            details={"expected_hash": expected, "actual_hash": actual},
        )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
