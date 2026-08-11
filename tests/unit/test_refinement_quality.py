from dataclasses import replace
from pathlib import Path

from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.quality import evaluate_candidate_quality


def test_model_rubric_cannot_override_no_deterministic_improvement() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current" / "baseline_generated.kicad_sch"
    metrics = compute_refinement_metrics(fixture)
    decision = evaluate_candidate_quality(metrics, metrics, addressed_categories=("wire_crossing",))
    assert not decision.accepted
    assert decision.code == "REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT"


def test_protected_metric_regression_rejects_even_with_other_improvement() -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current" / "baseline_generated.kicad_sch"
    before = compute_refinement_metrics(fixture)
    after = replace(before, component_overlap_count=before.component_overlap_count + 1, non_junction_wire_crossing_count=max(0, before.non_junction_wire_crossing_count - 1), schematic_hash="f" * 64)
    decision = evaluate_candidate_quality(before, after, addressed_categories=("wire_crossing",))
    assert not decision.accepted
    assert decision.code == "REFINEMENT_PROTECTED_METRIC_REGRESSION"
