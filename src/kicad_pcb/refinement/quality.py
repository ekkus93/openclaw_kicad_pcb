"""Deterministic candidate quality acceptance policy."""

from __future__ import annotations

from dataclasses import dataclass

from .metrics import RefinementMetricComparison, RefinementMetricReport, compare_refinement_metrics

PROTECTED_METRICS = (
    "component_overlap_count",
    "component_text_collision_count",
    "wire_text_collision_count",
    "wire_component_body_intersection_count",
    "out_of_page_count",
    "off_grid_geometry_count",
)

_CATEGORY_METRICS: dict[str, tuple[str, ...]] = {
    "component_alignment": ("alignment_residual_mean_mm", "distinct_x_columns"),
    "component_spacing": ("component_overlap_count", "minimum_symbol_spacing_mm", "maximum_local_density"),
    "wire_crossing": ("non_junction_wire_crossing_count",),
    "wire_length_bends": ("total_wire_manhattan_length_mm", "bend_count", "excessive_bend_wire_count", "long_wire_count"),
    "whitespace_crowding": ("maximum_local_density", "occupied_page_fraction"),
    "label_readability": ("component_text_collision_count", "wire_text_collision_count"),
}
_LOWER_IS_BETTER = {
    "component_overlap_count", "component_text_collision_count", "wire_text_collision_count",
    "wire_component_body_intersection_count", "out_of_page_count", "off_grid_geometry_count",
    "non_junction_wire_crossing_count", "total_wire_manhattan_length_mm", "bend_count",
    "excessive_bend_wire_count", "long_wire_count", "maximum_local_density",
    "occupied_page_fraction", "alignment_residual_mean_mm", "distinct_x_columns",
}


@dataclass(frozen=True)
class CandidateQualityDecision:
    accepted: bool
    code: str
    reason: str
    improved_metrics: tuple[str, ...]
    comparison: RefinementMetricComparison


def evaluate_candidate_quality(
    before: RefinementMetricReport,
    after: RefinementMetricReport,
    *,
    addressed_categories: tuple[str, ...] | list[str],
) -> CandidateQualityDecision:
    comparison = compare_refinement_metrics(before, after)
    regressions = [name for name in PROTECTED_METRICS if getattr(after, name) > getattr(before, name)]
    if regressions:
        return CandidateQualityDecision(False, "REFINEMENT_PROTECTED_METRIC_REGRESSION", f"protected metric regression: {', '.join(regressions)}", (), comparison)

    targeted = {metric for category in addressed_categories for metric in _CATEGORY_METRICS.get(category, ())}
    improved: list[str] = []
    for name in sorted(targeted):
        b = getattr(before, name)
        a = getattr(after, name)
        if name in _LOWER_IS_BETTER and a < b:
            improved.append(name)
        elif name not in _LOWER_IS_BETTER and a > b:
            improved.append(name)
    if not improved:
        return CandidateQualityDecision(False, "REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT", "no addressed deterministic metric improved", (), comparison)
    return CandidateQualityDecision(True, "REFINEMENT_ACCEPTED", "deterministic improvement with no protected regression", tuple(improved), comparison)
