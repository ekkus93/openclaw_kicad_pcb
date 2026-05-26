"""Model-corpus evaluation helpers."""

from .electrical import (
    ElectricalEquivalenceReport,
    ElectricalMismatch,
    compare_circuit_ir_equivalence,
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
    "IntrinsicQualityReport",
    "LayoutSimilarityReport",
    "compare_circuit_ir_equivalence",
    "compare_layout_similarity",
    "evaluate_model_corpus",
    "score_intrinsic_quality",
    "write_evaluation_artifacts",
]
