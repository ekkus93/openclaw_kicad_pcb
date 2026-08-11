"""Transactional schematic visual-refinement service modes."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
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
from kicad_pcb.refinement.layout_fingerprint import compute_schematic_layout_fingerprint
from kicad_pcb.refinement.metrics import RefinementMetricReport, compute_refinement_metrics
from kicad_pcb.refinement.operations import (
    LayoutOperationBatchResult,
    LayoutOperationPolicy,
    execute_layout_operations,
)
from kicad_pcb.refinement.planner import ValidatedRepairPlan
from kicad_pcb.refinement.quality import (
    CandidateQualityDecision,
    evaluate_best_known_replacement,
    evaluate_candidate_quality,
)
from kicad_pcb.refinement.rendering import SchematicRenderArtifact, render_schematic_for_refinement
from kicad_pcb.refinement.transaction import SchematicCandidateTransaction
from kicad_pcb.refinement.validation import (
    CandidateStructuralValidationReport,
    validate_candidate_structure,
)
from kicad_pcb.refinement.vision_context import VisionObjectMap, build_vision_object_map

from .llm import LlmClient
from .refinement_llm import (
    RefinementDecisionHistoryEntry,
    RefinementModelCallBudget,
    RepairPlannerOptions,
    refinement_model_call_upper_bound,
    run_repair_planner,
    run_visual_critic,
)

_NO_MEANINGFUL_IMPROVEMENT_CODES = frozenset(
    {
        "REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT",
        "REFINEMENT_BEST_KNOWN_NO_IMPROVEMENT",
    }
)


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
    candidate_layout_fingerprint: str | None = None


@dataclass(frozen=True)
class RefinementRuntime:
    authoritative_ir: CircuitIR
    adapter: KicadCliAdapter
    llm_client: LlmClient
    work_dir: Path
    evidence_root: Path
    operation_policy: LayoutOperationPolicy = LayoutOperationPolicy()
    prior_decisions: tuple[RefinementDecisionHistoryEntry, ...] = ()


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
    candidate_layout_fingerprint: str
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
        prior_decisions=runtime.prior_decisions,
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
    if analysis.critic.issues:
        plan = run_repair_planner(
            llm_client=runtime.llm_client,
            context=analysis.context,
            critic=analysis.critic,
            options=RepairPlannerOptions(
                iteration_id=iteration_id,
                max_repairs=limits.max_planner_repairs,
                max_operations=limits.max_operations,
            ),
            prior_decisions=runtime.prior_decisions,
        )
    else:
        plan = ValidatedRepairPlan(
            iteration_id=iteration_id,
            source_schematic_hash=analysis.accepted_hash,
            operation_payloads=(),
            addressed_issue_ids=(),
        )
    _assert_hash_unchanged(accepted_path, analysis.accepted_hash, mode="plan")
    return RefinementPlanResult(analysis=analysis, plan=plan)


def apply_planned_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    planned: RefinementPlanResult,
    iteration_id: str,
    enforce_best_known_retention: bool = False,
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
        code = (
            "REFINEMENT_NO_ACTIONABLE_CRITIC_ISSUES"
            if not analysis.critic.issues
            else "REFINEMENT_NO_OPERATIONS"
        )
        return RefinementApplyResult(
            status="no_op",
            code=code,
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
        candidate_layout_fingerprint = compute_schematic_layout_fingerprint(
            transaction.candidate_path
        ).digest

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
                    candidate_layout_fingerprint=candidate_layout_fingerprint,
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
                    candidate_layout_fingerprint=candidate_layout_fingerprint,
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
                    candidate_layout_fingerprint=candidate_layout_fingerprint,
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
        if quality.accepted and enforce_best_known_retention:
            best_known = evaluate_best_known_replacement(analysis.metrics, candidate_metrics)
            if not best_known.replaces_best:
                quality = CandidateQualityDecision(
                    accepted=False,
                    code=best_known.code,
                    reason=best_known.reason,
                    improved_metrics=best_known.improved_metrics,
                    comparison=best_known.comparison,
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
                    candidate_layout_fingerprint=candidate_layout_fingerprint,
                    quality=quality,
                    accepted_path=accepted_path,
                    runtime=runtime,
                    iteration_id=iteration_id,
                ),
                quality.code,
            )

        after_render = render_schematic_for_refinement(
            transaction.candidate_path,
            transaction.candidate_path.parent / "post-edit-render",
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
            candidate_layout_fingerprint=candidate_layout_fingerprint,
        )


def apply_once_schematic_refinement(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    iteration_id: str,
    limits: RefinementIterationLimits,
    enforce_best_known_retention: bool = False,
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
        enforce_best_known_retention=enforce_best_known_retention,
    )


@dataclass(frozen=True)
class RefinementLoopLimits:
    max_rounds: int = 3
    max_operations_per_round: int = 4
    max_total_accepted_operations: int = 8
    max_candidate_rejections: int = 2
    max_critic_repairs: int = 0
    max_planner_repairs: int = 0

    def __post_init__(self) -> None:
        _require_loop_int("max_rounds", self.max_rounds, minimum=1, maximum=20)
        _require_loop_int(
            "max_operations_per_round",
            self.max_operations_per_round,
            minimum=1,
            maximum=32,
        )
        _require_loop_int(
            "max_total_accepted_operations",
            self.max_total_accepted_operations,
            minimum=1,
            maximum=128,
        )
        _require_loop_int(
            "max_candidate_rejections",
            self.max_candidate_rejections,
            minimum=1,
            maximum=20,
        )
        _require_loop_int("max_critic_repairs", self.max_critic_repairs, minimum=0, maximum=8)
        _require_loop_int("max_planner_repairs", self.max_planner_repairs, minimum=0, maximum=8)


@dataclass(frozen=True)
class RefinementLoopResult:
    status: str
    stop_reason: str
    starting_hash: str
    final_accepted_hash: str
    best_accepted_hash: str
    latest_attempted_hash: str | None
    starting_layout_fingerprint: str
    final_layout_fingerprint: str
    rounds_attempted: int
    accepted_rounds: int
    rejected_rounds: int
    accepted_operations: int
    model_calls_made: int
    model_call_limit: int
    iterations: tuple[RefinementApplyResult, ...]


@dataclass
class _RefinementLoopState:
    starting_hash: str
    best_accepted_hash: str
    starting_layout_fingerprint: str
    best_layout_fingerprint: str
    iterations: list[RefinementApplyResult]
    model_call_budget: RefinementModelCallBudget
    decision_history: list[RefinementDecisionHistoryEntry]
    latest_attempted_hash: str | None = None
    accepted_rounds: int = 0
    rejected_rounds: int = 0
    accepted_operations: int = 0


def refine_schematic(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    session_id: str,
    limits: RefinementLoopLimits | None = None,
) -> RefinementLoopResult:
    """Run bounded refinement rounds while preserving the best accepted artifact."""

    if limits is None:
        limits = RefinementLoopLimits()
    _validate_refinement_session_id(session_id)
    starting_hash = _sha(accepted_path)
    starting_layout_fingerprint = compute_schematic_layout_fingerprint(accepted_path).digest
    seen_layout_fingerprints = {starting_layout_fingerprint}
    model_call_budget = RefinementModelCallBudget(
        client=runtime.llm_client,
        max_calls=refinement_model_call_upper_bound(
            max_rounds=limits.max_rounds,
            max_critic_repairs=limits.max_critic_repairs,
            max_planner_repairs=limits.max_planner_repairs,
        ),
    )
    bounded_runtime = replace(runtime, llm_client=model_call_budget)
    state = _RefinementLoopState(
        starting_hash=starting_hash,
        best_accepted_hash=starting_hash,
        starting_layout_fingerprint=starting_layout_fingerprint,
        best_layout_fingerprint=starting_layout_fingerprint,
        iterations=[],
        model_call_budget=model_call_budget,
        decision_history=[],
    )

    for round_number in range(1, limits.max_rounds + 1):
        remaining_operations = limits.max_total_accepted_operations - state.accepted_operations
        if remaining_operations <= 0:
            return _loop_result("REFINEMENT_STOP_OPERATION_BUDGET", accepted_path, state)

        round_start_hash = _sha(accepted_path)
        if round_start_hash != state.best_accepted_hash:
            raise UserError(
                "Canonical schematic no longer matches the best-known accepted state.",
                code="REFINEMENT_BEST_KNOWN_STATE_MISMATCH",
            )
        iteration_id = f"{session_id}-round-{round_number:03d}"
        round_runtime = replace(
            bounded_runtime,
            prior_decisions=tuple(state.decision_history),
        )
        result = apply_once_schematic_refinement(
            accepted_path=accepted_path,
            runtime=round_runtime,
            iteration_id=iteration_id,
            limits=RefinementIterationLimits(
                max_critic_repairs=limits.max_critic_repairs,
                max_planner_repairs=limits.max_planner_repairs,
                max_operations=min(limits.max_operations_per_round, remaining_operations),
            ),
            enforce_best_known_retention=True,
        )
        state.iterations.append(result)
        if result.candidate_hash is not None:
            state.latest_attempted_hash = result.candidate_hash
        if result.accepted_hash_before != round_start_hash:
            raise UserError(
                "Refinement round result is not bound to the round-start schematic.",
                code="REFINEMENT_STALE",
            )
        if result.status not in {"accepted", "rejected", "no_op"}:
            raise UserError(
                "Refinement round returned an unknown status.",
                code="REFINEMENT_INVALID_RESULT",
                details={"status": result.status},
            )
        state.decision_history.append(_decision_history_entry(iteration_id, result))

        repeated_layout = False
        if result.candidate_layout_fingerprint is not None:
            repeated_layout = result.candidate_layout_fingerprint in seen_layout_fingerprints
            if not repeated_layout:
                seen_layout_fingerprints.add(result.candidate_layout_fingerprint)

        actual_hash = _sha(accepted_path)
        if result.status == "no_op":
            _assert_nonaccepted_round_unchanged(result, round_start_hash, actual_hash)
            stop_reason = (
                "REFINEMENT_STOP_NO_ACTIONABLE_ISSUES"
                if result.code == "REFINEMENT_NO_ACTIONABLE_CRITIC_ISSUES"
                else "REFINEMENT_STOP_NO_OPERATIONS"
            )
            return _loop_result(stop_reason, accepted_path, state)

        if result.status == "rejected":
            _assert_nonaccepted_round_unchanged(result, round_start_hash, actual_hash)
            state.rejected_rounds += 1
            if repeated_layout:
                return _loop_result("REFINEMENT_STOP_OSCILLATION", accepted_path, state)
            if result.code in _NO_MEANINGFUL_IMPROVEMENT_CODES:
                return _loop_result(
                    "REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT",
                    accepted_path,
                    state,
                )
            if state.rejected_rounds >= limits.max_candidate_rejections:
                return _loop_result("REFINEMENT_STOP_REJECTION_LIMIT", accepted_path, state)
            continue

        if actual_hash != result.accepted_hash_after:
            raise UserError(
                "Accepted refinement result does not match persisted schematic bytes.",
                code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
            )
        if result.operations is None or not result.operations.results:
            raise UserError(
                "Accepted refinement round contains no applied operations.",
                code="REFINEMENT_INVALID_RESULT",
            )
        if result.candidate_layout_fingerprint is None:
            raise UserError(
                "Accepted refinement round is missing its layout fingerprint.",
                code="REFINEMENT_INVALID_RESULT",
            )

        applied_count = len(result.operations.results)
        if applied_count > min(limits.max_operations_per_round, remaining_operations):
            raise UserError(
                "Accepted refinement round exceeded its operation budget.",
                code="REFINEMENT_OPERATION_BUDGET_EXCEEDED",
            )
        state.accepted_operations += applied_count
        state.accepted_rounds += 1
        state.best_accepted_hash = actual_hash
        state.best_layout_fingerprint = result.candidate_layout_fingerprint

        if repeated_layout:
            return _loop_result("REFINEMENT_STOP_OSCILLATION", accepted_path, state)
        if state.accepted_operations >= limits.max_total_accepted_operations:
            return _loop_result("REFINEMENT_STOP_OPERATION_BUDGET", accepted_path, state)

    return _loop_result("REFINEMENT_STOP_MAX_ROUNDS", accepted_path, state)


def _loop_result(
    stop_reason: str,
    accepted_path: Path,
    state: _RefinementLoopState,
) -> RefinementLoopResult:
    final_hash = _sha(accepted_path)
    if final_hash != state.best_accepted_hash:
        raise UserError(
            "Final canonical schematic does not match the best-known accepted state.",
            code="REFINEMENT_BEST_KNOWN_STATE_MISMATCH",
        )
    final_layout_fingerprint = compute_schematic_layout_fingerprint(accepted_path).digest
    if final_layout_fingerprint != state.best_layout_fingerprint:
        raise UserError(
            "Final canonical layout does not match the best-known accepted layout.",
            code="REFINEMENT_BEST_KNOWN_STATE_MISMATCH",
        )
    return RefinementLoopResult(
        status="stopped",
        stop_reason=stop_reason,
        starting_hash=state.starting_hash,
        final_accepted_hash=final_hash,
        best_accepted_hash=state.best_accepted_hash,
        latest_attempted_hash=state.latest_attempted_hash,
        starting_layout_fingerprint=state.starting_layout_fingerprint,
        final_layout_fingerprint=final_layout_fingerprint,
        rounds_attempted=len(state.iterations),
        accepted_rounds=state.accepted_rounds,
        rejected_rounds=state.rejected_rounds,
        accepted_operations=state.accepted_operations,
        model_calls_made=state.model_call_budget.calls_made,
        model_call_limit=state.model_call_budget.max_calls,
        iterations=tuple(state.iterations),
    )


def _decision_history_entry(
    iteration_id: str,
    result: RefinementApplyResult,
) -> RefinementDecisionHistoryEntry:
    operation_types = (
        tuple(operation.operation_type for operation in result.operations.results)
        if result.operations is not None
        else ()
    )
    return RefinementDecisionHistoryEntry(
        iteration_id=iteration_id,
        status=result.status,
        code=result.code,
        operation_types=operation_types,
        candidate_layout_fingerprint=result.candidate_layout_fingerprint,
        accepted_hash_after=result.accepted_hash_after,
    )


def _assert_nonaccepted_round_unchanged(
    result: RefinementApplyResult,
    round_start_hash: str,
    actual_hash: str,
) -> None:
    if result.accepted_hash_after != round_start_hash or actual_hash != round_start_hash:
        raise UserError(
            "Rejected/no-op refinement round changed accepted schematic bytes.",
            code="REFINEMENT_READ_ONLY_VIOLATION",
        )


def _require_loop_int(name: str, value: int, *, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")


def _validate_refinement_session_id(session_id: str) -> None:
    allowed = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
    if not session_id or len(session_id) > 96 or any(ch not in allowed for ch in session_id):
        raise UserError(
            "Invalid schematic refinement session id.",
            code="REFINEMENT_INVALID_SESSION_ID",
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
        candidate_layout_fingerprint=context.candidate_layout_fingerprint,
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
