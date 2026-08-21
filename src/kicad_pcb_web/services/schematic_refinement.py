"""Transactional schematic visual-refinement service modes."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.electrical_equivalence import build_circuit_ir_fingerprint
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.electrical import (
    SchematicElectricalBaseline,
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.evidence import (
    ITERATION_EVIDENCE_SCHEMA_VERSION,
    RETENTION_POLICY_SCHEMA_VERSION,
    SESSION_EVIDENCE_SCHEMA_VERSION,
    IterationEvidenceInputs,
    SessionEvidenceInputs,
    SessionIterationReferenceInputs,
    build_session_iteration_reference,
    write_iteration_evidence_bundle,
    write_session_evidence_bundle,
)
from kicad_pcb.refinement.layout_fingerprint import (
    LAYOUT_FINGERPRINT_SCHEMA_VERSION,
    compute_schematic_layout_fingerprint,
)
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
from kicad_pcb.refinement.session_reservation import (
    release_refinement_session_reservation,
    reserve_refinement_session_namespace,
)
from kicad_pcb.refinement.transaction import SchematicCandidateTransaction
from kicad_pcb.refinement.validation import (
    CandidateStructuralValidationReport,
    validate_candidate_structure,
)
from kicad_pcb.refinement.vision_context import VisionObjectMap, build_vision_object_map

from ._refinement_metric_recovery import (
    MetricFailureRecoveryInputs,
    recover_candidate_metric_failure,
)
from .llm import LlmClient
from .refinement_llm import (
    RefinementDecisionHistoryEntry,
    RefinementModelCallBudget,
    RepairPlannerOptions,
    refinement_model_call_upper_bound,
    run_repair_planner,
    run_visual_critic,
)
from .refinement_versions import (
    CRITIC_PROMPT_VERSION,
    CRITIC_SCHEMA_VERSION,
    PLANNER_PROMPT_VERSION,
    PLANNER_SCHEMA_VERSION,
    REFINEMENT_SERVICE_VERSION,
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
class RefinementProvenance:
    provider: str
    model: str
    product_version: str | None = None
    implementation_sha: str | None = None

    def __post_init__(self) -> None:
        _require_provenance_text("provider", self.provider, maximum=64)
        _require_provenance_text("model", self.model, maximum=256)
        if self.product_version is not None:
            _require_provenance_text("product_version", self.product_version, maximum=128)
        if self.implementation_sha is not None:
            value = self.implementation_sha
            if len(value) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in value):
                raise ValueError("implementation_sha must be a lowercase Git SHA")


@dataclass(frozen=True)
class RefinementRuntime:
    authoritative_ir: CircuitIR
    adapter: KicadCliAdapter
    llm_client: LlmClient
    work_dir: Path
    evidence_root: Path
    operation_policy: LayoutOperationPolicy = LayoutOperationPolicy()
    prior_decisions: tuple[RefinementDecisionHistoryEntry, ...] = ()
    provenance: RefinementProvenance | None = None


@dataclass(frozen=True)
class RefinementIterationLimits:
    max_critic_repairs: int
    max_planner_repairs: int
    max_operations: int

    def __post_init__(self) -> None:
        _require_loop_int("max_critic_repairs", self.max_critic_repairs, minimum=0, maximum=8)
        _require_loop_int("max_planner_repairs", self.max_planner_repairs, minimum=0, maximum=8)
        _require_loop_int("max_operations", self.max_operations, minimum=1, maximum=32)


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
        image_paths=render.review_image_paths,
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
        evidence_dir = write_iteration_evidence_bundle(
            runtime.evidence_root,
            IterationEvidenceInputs(
                iteration_id=iteration_id,
                accepted_hash_before=analysis.accepted_hash,
                authoritative_hash=analysis.baseline.authoritative_hash,
                critic=analysis.critic,
                plan=plan,
                operation_results={"status": "not_run", "results": []},
                electrical_report={"status": "not_run"},
                structural_report={"status": "not_run"},
                metrics_before=analysis.metrics,
                metrics_after=analysis.metrics,
                quality_decision={"accepted": False, "code": code},
                before_render=analysis.render,
                after_render=None,
                accepted_hash_after=analysis.accepted_hash,
                disposition="no_op",
                reason_code=code,
                candidate_hash=None,
                candidate_layout_fingerprint=None,
            ),
        )
        return RefinementApplyResult(
            status="no_op",
            code=code,
            accepted_hash_before=analysis.accepted_hash,
            accepted_hash_after=analysis.accepted_hash,
            candidate_hash=None,
            evidence_dir=evidence_dir,
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
        try:
            candidate_metrics = compute_refinement_metrics(transaction.candidate_path)
        except UserError as exc:
            recovery = recover_candidate_metric_failure(
                MetricFailureRecoveryInputs(
                    transaction=transaction,
                    accepted_path=accepted_path,
                    accepted_hash=analysis.accepted_hash,
                    authoritative_hash=analysis.baseline.authoritative_hash,
                    critic=analysis.critic,
                    plan=plan,
                    operations=operations,
                    metrics_before=analysis.metrics,
                    before_render=analysis.render,
                    evidence_root=runtime.evidence_root,
                    iteration_id=iteration_id,
                ),
                exc,
            )
            if recovery is None:
                raise
            return RefinementApplyResult(
                status="rejected",
                code=recovery.code,
                accepted_hash_before=analysis.accepted_hash,
                accepted_hash_after=analysis.accepted_hash,
                candidate_hash=recovery.candidate_hash,
                evidence_dir=recovery.evidence_dir,
                operations=operations,
                electrical=None,
                structural=None,
                quality=None,
                candidate_layout_fingerprint=recovery.candidate_layout_fingerprint,
            )
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
                candidate_hash=candidate_hash,
                candidate_layout_fingerprint=candidate_layout_fingerprint,
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
    session_evidence_dir: Path | None = None


@dataclass
class _RefinementLoopState:
    starting_hash: str
    best_accepted_hash: str
    starting_layout_fingerprint: str
    best_layout_fingerprint: str
    iteration_ids: list[str]
    iterations: list[RefinementApplyResult]
    model_call_budget: RefinementModelCallBudget
    decision_history: list[RefinementDecisionHistoryEntry]
    latest_attempted_hash: str | None = None
    accepted_rounds: int = 0
    rejected_rounds: int = 0
    accepted_operations: int = 0


@dataclass(frozen=True)
class _RefinementSessionContext:
    accepted_path: Path
    runtime: RefinementRuntime
    provenance: RefinementProvenance
    session_id: str
    limits: RefinementLoopLimits
    authoritative_hash: str


@dataclass(frozen=True)
class _SessionEvidenceDisposition:
    status: str
    stop_reason: str
    failure_code: str | None


@dataclass(frozen=True)
class _RoundOutcome:
    result: RefinementApplyResult
    round_start_hash: str
    actual_hash: str
    remaining_operations: int
    repeated_layout: bool


def refine_schematic(
    *,
    accepted_path: Path,
    runtime: RefinementRuntime,
    session_id: str,
    limits: RefinementLoopLimits | None = None,
) -> RefinementLoopResult:
    """Run a bounded, idempotent session with durable terminal evidence."""

    if limits is None:
        limits = RefinementLoopLimits()
    _validate_refinement_session_id(session_id)
    provenance = _require_refinement_provenance(runtime.provenance)
    starting_hash = _sha(accepted_path)
    starting_layout_fingerprint = compute_schematic_layout_fingerprint(accepted_path).digest
    model_call_budget = RefinementModelCallBudget(
        client=runtime.llm_client,
        max_calls=refinement_model_call_upper_bound(
            max_rounds=limits.max_rounds,
            max_critic_repairs=limits.max_critic_repairs,
            max_planner_repairs=limits.max_planner_repairs,
        ),
    )
    bounded_runtime = replace(runtime, llm_client=model_call_budget)
    context = _RefinementSessionContext(
        accepted_path=accepted_path,
        runtime=bounded_runtime,
        provenance=provenance,
        session_id=session_id,
        limits=limits,
        authoritative_hash=build_circuit_ir_fingerprint(runtime.authoritative_ir).sha256(),
    )
    state = _RefinementLoopState(
        starting_hash=starting_hash,
        best_accepted_hash=starting_hash,
        starting_layout_fingerprint=starting_layout_fingerprint,
        best_layout_fingerprint=starting_layout_fingerprint,
        iteration_ids=[],
        iterations=[],
        model_call_budget=model_call_budget,
        decision_history=[],
    )
    reservation = reserve_refinement_session_namespace(
        runtime.evidence_root,
        session_id=session_id,
        max_rounds=limits.max_rounds,
    )

    try:
        result = _run_refinement_loop(context, state)
    except Exception as exc:
        disposition = _SessionEvidenceDisposition(
            status="failed",
            stop_reason="REFINEMENT_STOP_HARD_FAILURE",
            failure_code=_exception_code(exc),
        )
        try:
            _publish_session_evidence(context, state, None, disposition)
        except Exception as evidence_exc:
            exc.add_note(
                "Refinement session evidence finalization also failed with code "
                f"{_exception_code(evidence_exc)}; reservation retained to block replay."
            )
            raise exc from evidence_exc
        try:
            release_refinement_session_reservation(reservation)
        except Exception as reservation_exc:
            exc.add_note(
                "Refinement session reservation release failed after durable failure evidence "
                f"with code {_exception_code(reservation_exc)}."
            )
            raise exc from reservation_exc
        raise

    disposition = _SessionEvidenceDisposition(
        status="completed",
        stop_reason=result.stop_reason,
        failure_code=None,
    )
    evidence_dir = _publish_session_evidence(context, state, result, disposition)
    release_refinement_session_reservation(reservation)
    return replace(result, session_evidence_dir=evidence_dir)


def _run_refinement_loop(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
) -> RefinementLoopResult:
    seen_layout_fingerprints = {state.starting_layout_fingerprint}
    for round_number in range(1, context.limits.max_rounds + 1):
        remaining_operations = (
            context.limits.max_total_accepted_operations - state.accepted_operations
        )
        if remaining_operations <= 0:
            return _loop_result(
                "REFINEMENT_STOP_OPERATION_BUDGET",
                context.accepted_path,
                state,
            )
        outcome = _execute_refinement_round(
            context,
            state,
            seen_layout_fingerprints,
            round_number,
            remaining_operations,
        )
        stop_reason = _round_stop_reason(context, state, outcome)
        if stop_reason is not None:
            return _loop_result(stop_reason, context.accepted_path, state)

    return _loop_result("REFINEMENT_STOP_MAX_ROUNDS", context.accepted_path, state)


def _execute_refinement_round(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
    seen_layout_fingerprints: set[str],
    round_number: int,
    remaining_operations: int,
) -> _RoundOutcome:
    round_start_hash = _sha(context.accepted_path)
    if round_start_hash != state.best_accepted_hash:
        raise UserError(
            "Canonical schematic no longer matches the best-known accepted state.",
            code="REFINEMENT_BEST_KNOWN_STATE_MISMATCH",
        )
    iteration_id = f"{context.session_id}-round-{round_number:03d}"
    round_runtime = replace(
        context.runtime,
        prior_decisions=tuple(state.decision_history),
    )
    result = apply_once_schematic_refinement(
        accepted_path=context.accepted_path,
        runtime=round_runtime,
        iteration_id=iteration_id,
        limits=RefinementIterationLimits(
            max_critic_repairs=context.limits.max_critic_repairs,
            max_planner_repairs=context.limits.max_planner_repairs,
            max_operations=min(
                context.limits.max_operations_per_round,
                remaining_operations,
            ),
        ),
        enforce_best_known_retention=True,
    )
    state.iteration_ids.append(iteration_id)
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

    return _RoundOutcome(
        result=result,
        round_start_hash=round_start_hash,
        actual_hash=_sha(context.accepted_path),
        remaining_operations=remaining_operations,
        repeated_layout=repeated_layout,
    )


def _round_stop_reason(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
    outcome: _RoundOutcome,
) -> str | None:
    if outcome.result.status == "no_op":
        _assert_nonaccepted_round_unchanged(
            outcome.result,
            outcome.round_start_hash,
            outcome.actual_hash,
        )
        if outcome.result.code == "REFINEMENT_NO_ACTIONABLE_CRITIC_ISSUES":
            return "REFINEMENT_STOP_NO_ACTIONABLE_ISSUES"
        return "REFINEMENT_STOP_NO_OPERATIONS"
    if outcome.result.status == "rejected":
        return _handle_rejected_round(context, state, outcome)
    return _handle_accepted_round(context, state, outcome)


def _handle_rejected_round(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
    outcome: _RoundOutcome,
) -> str | None:
    _assert_nonaccepted_round_unchanged(
        outcome.result,
        outcome.round_start_hash,
        outcome.actual_hash,
    )
    state.rejected_rounds += 1
    if outcome.repeated_layout:
        return "REFINEMENT_STOP_OSCILLATION"
    if outcome.result.code in _NO_MEANINGFUL_IMPROVEMENT_CODES:
        return "REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT"
    if state.rejected_rounds >= context.limits.max_candidate_rejections:
        return "REFINEMENT_STOP_REJECTION_LIMIT"
    return None


def _handle_accepted_round(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
    outcome: _RoundOutcome,
) -> str | None:
    result = outcome.result
    if outcome.actual_hash != result.accepted_hash_after:
        raise UserError(
            "Accepted refinement result does not match persisted schematic bytes.",
            code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
        )
    applied_results = (
        tuple(item for item in result.operations.results if item.status == "applied")
        if result.operations is not None
        else ()
    )
    if not applied_results:
        raise UserError(
            "Accepted refinement round contains no applied operations.",
            code="REFINEMENT_INVALID_RESULT",
        )
    if result.candidate_layout_fingerprint is None:
        raise UserError(
            "Accepted refinement round is missing its layout fingerprint.",
            code="REFINEMENT_INVALID_RESULT",
        )

    applied_count = len(applied_results)
    allowed_operations = min(
        context.limits.max_operations_per_round,
        outcome.remaining_operations,
    )
    if applied_count > allowed_operations:
        raise UserError(
            "Accepted refinement round exceeded its operation budget.",
            code="REFINEMENT_OPERATION_BUDGET_EXCEEDED",
        )
    state.accepted_operations += applied_count
    state.accepted_rounds += 1
    state.best_accepted_hash = outcome.actual_hash
    state.best_layout_fingerprint = result.candidate_layout_fingerprint

    if outcome.repeated_layout:
        return "REFINEMENT_STOP_OSCILLATION"
    if state.accepted_operations >= context.limits.max_total_accepted_operations:
        return "REFINEMENT_STOP_OPERATION_BUDGET"
    return None


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


def _publish_session_evidence(
    context: _RefinementSessionContext,
    state: _RefinementLoopState,
    result: RefinementLoopResult | None,
    disposition: _SessionEvidenceDisposition,
) -> Path:
    references = tuple(
        build_session_iteration_reference(
            context.runtime.evidence_root,
            SessionIterationReferenceInputs(
                iteration_id=iteration_id,
                status=iteration.status,
                code=iteration.code,
                accepted_hash_before=iteration.accepted_hash_before,
                accepted_hash_after=iteration.accepted_hash_after,
                candidate_hash=iteration.candidate_hash,
                candidate_layout_fingerprint=iteration.candidate_layout_fingerprint,
                evidence_dir=iteration.evidence_dir,
            ),
        )
        for iteration_id, iteration in zip(
            state.iteration_ids,
            state.iterations,
            strict=True,
        )
    )
    final_hash = result.final_accepted_hash if result is not None else _sha(context.accepted_path)
    final_layout = (
        result.final_layout_fingerprint
        if result is not None
        else compute_schematic_layout_fingerprint(context.accepted_path).digest
    )
    return write_session_evidence_bundle(
        context.runtime.evidence_root,
        SessionEvidenceInputs(
            session_id=context.session_id,
            authoritative_hash=context.authoritative_hash,
            starting_accepted_hash=state.starting_hash,
            provider=context.provenance.provider,
            model=context.provenance.model,
            product_version=context.provenance.product_version,
            implementation_sha=context.provenance.implementation_sha,
            prompt_versions={
                "critic": CRITIC_PROMPT_VERSION,
                "planner": PLANNER_PROMPT_VERSION,
            },
            schema_versions={
                "critic": CRITIC_SCHEMA_VERSION,
                "planner": PLANNER_SCHEMA_VERSION,
                "layout_fingerprint": LAYOUT_FINGERPRINT_SCHEMA_VERSION,
                "iteration_evidence": ITERATION_EVIDENCE_SCHEMA_VERSION,
                "session_evidence": SESSION_EVIDENCE_SCHEMA_VERSION,
                "retention_policy": RETENTION_POLICY_SCHEMA_VERSION,
                "refinement_service": REFINEMENT_SERVICE_VERSION,
            },
            configured_bounds=asdict(context.limits),
            iterations=references,
            status=disposition.status,
            stop_reason=disposition.stop_reason,
            failure_code=disposition.failure_code,
            final_accepted_hash=final_hash,
            best_accepted_hash=state.best_accepted_hash,
            starting_layout_fingerprint=state.starting_layout_fingerprint,
            final_layout_fingerprint=final_layout,
            model_calls_made=state.model_call_budget.calls_made,
            model_call_limit=state.model_call_budget.max_calls,
        ),
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


def _require_provenance_text(name: str, value: str, *, maximum: int) -> None:
    if not value or len(value) > maximum or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{name} must contain 1..{maximum} printable characters")


def _require_refinement_provenance(
    provenance: RefinementProvenance | None,
) -> RefinementProvenance:
    if provenance is None:
        raise UserError(
            "Iterative refinement requires explicit provider/model provenance.",
            code="REFINEMENT_PROVENANCE_REQUIRED",
        )
    return provenance


def _exception_code(exc: Exception) -> str:
    value = getattr(exc, "code", None)
    if value is None:
        return type(exc).__name__
    enum_value = getattr(value, "value", None)
    return str(enum_value if enum_value is not None else value)


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
            accepted_hash_after=context.analysis.accepted_hash,
            disposition="rejected",
            reason_code=code,
            candidate_hash=candidate_hash,
            candidate_layout_fingerprint=context.candidate_layout_fingerprint,
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
