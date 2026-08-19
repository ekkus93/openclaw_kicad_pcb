"""Shared data contracts for Phase N3 corpus evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.evaluation.refinement_baseline import RefinementBaselineFixtureDefinition

from .llm import LlmClient
from .schematic_refinement import (
    RefinementIterationLimits,
    RefinementLoopLimits,
    RefinementProvenance,
)

REFINEMENT_CORPUS_EVALUATION_SCHEMA_VERSION = "1.0"
SUMMARY_FILENAME = "summary.json"


@dataclass(frozen=True)
class RefinementCorpusPreparedFixture:
    """Fully validated fixture inputs ready for one model-directed evaluation."""

    fixture: RefinementBaselineFixtureDefinition
    authoritative_ir: CircuitIR
    baseline_schematic: Path


@dataclass(frozen=True)
class RefinementCorpusFixtureResult:
    """Path-independent result fields retained in the corpus summary."""

    fixture_id: str
    baseline_hash: str
    apply_once_status: str
    apply_once_code: str
    refine_status: str
    refine_stop_reason: str
    final_accepted_hash: str


@dataclass(frozen=True)
class RefinementCorpusEvaluationRequest:
    """Trusted dependencies and bounded settings for one corpus evaluation."""

    repo_root: Path
    manifest_path: Path
    adapter: KicadCliAdapter
    llm_client: LlmClient
    provenance: RefinementProvenance
    iteration_limits: RefinementIterationLimits
    loop_limits: RefinementLoopLimits
    fixture_ids: tuple[str, ...] = ()
    expectations_path: Path | None = None


@dataclass(frozen=True)
class RefinementCorpusPreparation:
    """Fully preflighted corpus selection and immutable baseline bindings."""

    fixtures: tuple[RefinementCorpusPreparedFixture, ...]
    manifest_hash: str
    expectations_hash: str


@dataclass(frozen=True)
class RefinementCorpusEvaluationResult:
    """Completed corpus run and its aggregate evidence index."""

    output_root: Path
    summary_path: Path
    fixture_ids: tuple[str, ...]
    results: tuple[RefinementCorpusFixtureResult, ...]
