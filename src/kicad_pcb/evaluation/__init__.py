"""Model-corpus evaluation helpers."""

from .electrical import (
    ElectricalEquivalenceReport,
    ElectricalMismatch,
    compare_circuit_ir_equivalence,
)
from .refinement_baseline import (
    REFINEMENT_BASELINE_SCHEMA_VERSION,
    RefinementBaselineCaptureInputs,
    RefinementBaselineCaptureRequest,
    RefinementBaselineFixtureDefinition,
    capture_refinement_baseline,
    fixture_definition,
    write_refinement_baseline_bundle,
)
from .reports import (
    ActionableFailure,
    CorpusEvaluationSummary,
    EvaluationReport,
    FixtureEvaluationSummary,
    evaluate_model_corpus,
    write_evaluation_artifacts,
)
from .scoring import IntrinsicQualityReport, score_intrinsic_quality
from .similarity import LayoutSimilarityReport, compare_layout_similarity

__all__ = [
    "ActionableFailure",
    "CorpusEvaluationSummary",
    "ElectricalEquivalenceReport",
    "ElectricalMismatch",
    "EvaluationReport",
    "FixtureEvaluationSummary",
    "REFINEMENT_BASELINE_SCHEMA_VERSION",
    "RefinementBaselineCaptureInputs",
    "RefinementBaselineCaptureRequest",
    "RefinementBaselineFixtureDefinition",
    "capture_refinement_baseline",
    "fixture_definition",
    "write_refinement_baseline_bundle",
    "IntrinsicQualityReport",
    "LayoutSimilarityReport",
    "compare_circuit_ir_equivalence",
    "compare_layout_similarity",
    "evaluate_model_corpus",
    "score_intrinsic_quality",
    "write_evaluation_artifacts",
]
