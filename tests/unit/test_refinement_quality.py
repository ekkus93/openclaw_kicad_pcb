from dataclasses import replace
from pathlib import Path

from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.quality import (
    evaluate_best_known_replacement,
    evaluate_candidate_quality,
)


def _metrics():
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    return compute_refinement_metrics(fixture)


def test_model_rubric_cannot_override_no_deterministic_improvement() -> None:
    metrics = _metrics()
    decision = evaluate_candidate_quality(
        metrics,
        metrics,
        addressed_categories=("wire_crossing",),
    )
    assert not decision.accepted
    assert decision.code == "REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT"


def test_protected_metric_regression_rejects_even_with_other_improvement() -> None:
    before = _metrics()
    after = replace(
        before,
        component_overlap_count=before.component_overlap_count + 1,
        non_junction_wire_crossing_count=max(0, before.non_junction_wire_crossing_count - 1),
        schematic_hash="f" * 64,
    )
    decision = evaluate_candidate_quality(
        before,
        after,
        addressed_categories=("wire_crossing",),
    )
    assert not decision.accepted
    assert decision.code == "REFINEMENT_PROTECTED_METRIC_REGRESSION"


def test_best_known_replacement_accepts_strict_pareto_improvement() -> None:
    best = _metrics()
    candidate = replace(
        best,
        alignment_residual_mean_mm=max(0.0, best.alignment_residual_mean_mm - 0.1),
        schematic_hash="a" * 64,
    )

    decision = evaluate_best_known_replacement(best, candidate)

    assert decision.replaces_best
    assert decision.code == "REFINEMENT_BEST_KNOWN_IMPROVED"
    assert decision.improved_metrics == ("alignment_residual_mean_mm",)
    assert decision.regressed_metrics == ()


def test_best_known_replacement_rejects_mixed_tradeoff() -> None:
    best = _metrics()
    candidate = replace(
        best,
        alignment_residual_mean_mm=max(0.0, best.alignment_residual_mean_mm - 0.1),
        total_wire_manhattan_length_mm=best.total_wire_manhattan_length_mm + 1.27,
        schematic_hash="b" * 64,
    )

    decision = evaluate_best_known_replacement(best, candidate)

    assert not decision.replaces_best
    assert decision.code == "REFINEMENT_BEST_KNOWN_REGRESSION"
    assert "alignment_residual_mean_mm" in decision.improved_metrics
    assert decision.regressed_metrics == ("total_wire_manhattan_length_mm",)


def test_best_known_replacement_rejects_equal_quality() -> None:
    best = _metrics()
    candidate = replace(best, schematic_hash="c" * 64)

    decision = evaluate_best_known_replacement(best, candidate)

    assert not decision.replaces_best
    assert decision.code == "REFINEMENT_BEST_KNOWN_NO_IMPROVEMENT"
    assert decision.improved_metrics == ()
    assert decision.regressed_metrics == ()
