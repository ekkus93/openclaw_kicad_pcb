"""Candidate-local metric failure recovery for schematic refinement."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.evidence import IterationEvidenceInputs, write_iteration_evidence_bundle
from kicad_pcb.refinement.layout_fingerprint import compute_schematic_layout_fingerprint
from kicad_pcb.refinement.metrics import RefinementMetricReport
from kicad_pcb.refinement.operations import LayoutOperationBatchResult
from kicad_pcb.refinement.planner import ValidatedRepairPlan
from kicad_pcb.refinement.rendering import SchematicRenderArtifact
from kicad_pcb.refinement.transaction import SchematicCandidateTransaction


@dataclass(frozen=True)
class MetricFailureRecoveryInputs:
    transaction: SchematicCandidateTransaction
    accepted_path: Path
    accepted_hash: str
    authoritative_hash: str
    critic: CriticResponse
    plan: ValidatedRepairPlan
    operations: LayoutOperationBatchResult
    metrics_before: RefinementMetricReport
    before_render: SchematicRenderArtifact
    evidence_root: Path
    iteration_id: str


@dataclass(frozen=True)
class MetricFailureRecoveryResult:
    code: str
    candidate_hash: str
    candidate_layout_fingerprint: str
    evidence_dir: Path


def recover_candidate_metric_failure(
    inputs: MetricFailureRecoveryInputs,
    error: UserError,
) -> MetricFailureRecoveryResult | None:
    """Reject one known-unscorable candidate while preserving unrelated hard failures."""

    if error.code != "REFINEMENT_METRIC_INVALID_GEOMETRY":
        return None

    candidate_hash = inputs.operations.candidate_hash
    actual_candidate_hash = _sha(inputs.transaction.candidate_path)
    if actual_candidate_hash != candidate_hash:
        raise UserError(
            "Candidate metrics recovery observed unexpected schematic bytes.",
            code="REFINEMENT_CANDIDATE_HASH_MISMATCH",
            details={
                "operation_hash": candidate_hash,
                "actual_hash": actual_candidate_hash,
            },
        )
    candidate_layout_fingerprint = compute_schematic_layout_fingerprint(
        inputs.transaction.candidate_path
    ).digest
    metric_failure = {
        "status": "invalid",
        "code": error.code,
        "message": str(error),
        "details": error.details,
    }
    not_run = {"status": "not_run", "reason_code": error.code}
    evidence_dir = write_iteration_evidence_bundle(
        inputs.evidence_root,
        IterationEvidenceInputs(
            iteration_id=inputs.iteration_id,
            accepted_hash_before=inputs.accepted_hash,
            authoritative_hash=inputs.authoritative_hash,
            critic=inputs.critic,
            plan=inputs.plan,
            operation_results=inputs.operations,
            electrical_report=not_run,
            structural_report=not_run,
            metrics_before=inputs.metrics_before,
            metrics_after=metric_failure,
            quality_decision={"accepted": False, "code": error.code},
            before_render=inputs.before_render,
            after_render=None,
            accepted_hash_after=inputs.accepted_hash,
            disposition="rejected",
            reason_code=error.code,
            candidate_hash=candidate_hash,
            candidate_layout_fingerprint=candidate_layout_fingerprint,
        ),
    )
    inputs.transaction.reject()
    if _sha(inputs.accepted_path) != inputs.accepted_hash:
        raise UserError(
            "Rejected metric-invalid candidate changed accepted schematic bytes.",
            code="REFINEMENT_READ_ONLY_VIOLATION",
        )
    return MetricFailureRecoveryResult(
        code=error.code,
        candidate_hash=candidate_hash,
        candidate_layout_fingerprint=candidate_layout_fingerprint,
        evidence_dir=evidence_dir,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
