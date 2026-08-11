"""Explicit server-side composition for schematic-refinement runtime dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.refinement.operations import LayoutOperationPolicy

from .llm import LlmClient
from .schematic_refinement import RefinementProvenance, RefinementRuntime


@dataclass(frozen=True)
class RefinementRuntimeInputs:
    """Server-owned runtime dependencies that external requests cannot override."""

    authoritative_ir: CircuitIR
    adapter: KicadCliAdapter
    llm_client: LlmClient
    work_dir: Path
    evidence_root: Path
    operation_policy: LayoutOperationPolicy = LayoutOperationPolicy()


def build_refinement_runtime(
    inputs: RefinementRuntimeInputs,
    provenance: RefinementProvenance,
) -> RefinementRuntime:
    """Build a runtime from explicit dependencies and explicit provider/model provenance."""

    return RefinementRuntime(
        authoritative_ir=inputs.authoritative_ir,
        adapter=inputs.adapter,
        llm_client=inputs.llm_client,
        work_dir=inputs.work_dir,
        evidence_root=inputs.evidence_root,
        operation_policy=inputs.operation_policy,
        provenance=provenance,
    )
