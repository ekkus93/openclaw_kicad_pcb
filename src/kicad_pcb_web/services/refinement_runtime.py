"""Explicit server-side composition for schematic-refinement runtime dependencies."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.refinement.operations import LayoutOperationPolicy

from .llm import LlmClient
from .schematic_refinement import (
    RefinementProvenance,
    RefinementRuntime,
)


def build_refinement_runtime(
    *,
    authoritative_ir: CircuitIR,
    adapter: KicadCliAdapter,
    llm_client: LlmClient,
    work_dir: Path,
    evidence_root: Path,
    provider: str,
    model: str,
    product_version: str | None = None,
    implementation_sha: str | None = None,
    operation_policy: LayoutOperationPolicy = LayoutOperationPolicy(),
) -> RefinementRuntime:
    """Build runtime provenance explicitly; never infer provider/model identity from the client."""

    return RefinementRuntime(
        authoritative_ir=authoritative_ir,
        adapter=adapter,
        llm_client=llm_client,
        work_dir=work_dir,
        evidence_root=evidence_root,
        operation_policy=operation_policy,
        provenance=RefinementProvenance(
            provider=provider,
            model=model,
            product_version=product_version,
            implementation_sha=implementation_sha,
        ),
    )
