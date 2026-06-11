"""Dataclasses and constants for corpus evaluation reports."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.corpus.metadata import CorpusFixtureMetadata

from .electrical import ElectricalEquivalenceReport
from .scoring import IntrinsicQualityReport
from .similarity import LayoutSimilarityReport

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
