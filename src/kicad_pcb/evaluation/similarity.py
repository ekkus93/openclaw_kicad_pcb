"""Source-vs-generated layout similarity scoring."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.corpus.layout_features import LayoutFeatures


@dataclass(frozen=True)
class LayoutSimilarityReport:
    score: float
    sub_scores: dict[str, float]
    reasons: tuple[str, ...] = field(default_factory=tuple)


def compare_layout_similarity(
    source: LayoutFeatures,
    generated: LayoutFeatures,
) -> LayoutSimilarityReport:
    """Compare source and generated layout features without exact coordinates."""

    reasons: list[str] = []

    role_counts = _score_role_counts(source, generated)
    if role_counts < 20.0:
        reasons.append("role_counts: generated symbol-role counts diverge from the source fixture.")

    relative_positions = _score_relative_positions(source, generated)
    if relative_positions < 20.0:
        reasons.append(
            "relative_positions: major-symbol ordering diverges from the source fixture."
        )

    label_strategy = _score_label_strategy(source, generated)
    if label_strategy < 15.0:
        reasons.append(
            "label_strategy: generated label/global-label strategy diverges from the source."
        )

    geometry_spread = _score_geometry_spread(source, generated)
    if geometry_spread < 15.0:
        reasons.append("geometry_spread: generated x-column spread is significantly worse.")

    wire_strategy = _score_wire_strategy(source, generated)
    if wire_strategy < 15.0:
        reasons.append("wire_stub_ratio: generated routing is noticeably stubbier than the source.")

    sub_scores = {
        "role_counts": round(role_counts, 2),
        "relative_positions": round(relative_positions, 2),
        "label_strategy": round(label_strategy, 2),
        "geometry_spread": round(geometry_spread, 2),
        "wire_stub_ratio": round(wire_strategy, 2),
    }
    return LayoutSimilarityReport(
        score=round(sum(sub_scores.values()), 2),
        sub_scores=sub_scores,
        reasons=tuple(reasons),
    )


def _score_role_counts(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    roles = set(source.role_counts) | set(generated.role_counts)
    if not roles:
        return 25.0
    total_delta = sum(
        abs(source.role_counts.get(role, 0) - generated.role_counts.get(role, 0))
        for role in roles
    )
    return max(25.0 - total_delta * 3.0, 0.0)


def _score_relative_positions(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_relations = {(item.a, item.b, item.relation) for item in source.relative_positions}
    generated_relations = {(item.a, item.b, item.relation) for item in generated.relative_positions}
    if not source_relations:
        return 25.0
    overlap = len(source_relations & generated_relations) / len(source_relations)
    return round(overlap * 25.0, 2)


def _score_label_strategy(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    keys = {"local_label_count", "global_label_count", "power_symbol_count"}
    delta = sum(
        abs(source.net_label_strategy.get(key, 0) - generated.net_label_strategy.get(key, 0))
        for key in keys
    )
    return max(20.0 - delta * 2.5, 0.0)


def _score_geometry_spread(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_columns = int(source.geometry.get("distinct_x_columns", 0))
    generated_columns = int(generated.geometry.get("distinct_x_columns", 0))
    if generated_columns >= source_columns:
        return 15.0
    return max(15.0 - (source_columns - generated_columns) * 3.0, 0.0)


def _score_wire_strategy(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_stub_ratio = float(source.geometry.get("wire_stub_ratio", 1.0))
    generated_stub_ratio = float(generated.geometry.get("wire_stub_ratio", 1.0))
    if generated_stub_ratio <= source_stub_ratio:
        return 15.0
    return max(15.0 - (generated_stub_ratio - source_stub_ratio) * 60.0, 0.0)
