"""Source-vs-generated layout similarity scoring."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.corpus.layout_features import LayoutFeatures


@dataclass(frozen=True)
class SubScoreDetail:
    """One similarity sub-score with explicit applicability metadata."""

    score: float
    max_score: float
    applicable: bool = True
    reason: str | None = None


@dataclass(frozen=True)
class LayoutSimilarityReport:
    score: float
    sub_scores: dict[str, float]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    sub_score_details: dict[str, SubScoreDetail] = field(default_factory=dict)


def compare_layout_similarity(
    source: LayoutFeatures,
    generated: LayoutFeatures,
) -> LayoutSimilarityReport:
    """Compare source and generated layout features without exact coordinates.

    Applicable sub-scores are normalized back onto a 0-100 scale. A metric that
    has no meaningful source denominator is reported as not applicable instead
    of receiving free credit.
    """

    details = {
        "role_counts": _score_role_counts(source, generated),
        "relative_positions": _score_relative_positions(source, generated),
        "label_strategy": _score_label_strategy(source, generated),
        "geometry_spread": _score_geometry_spread(source, generated),
        "wire_stub_ratio": _score_wire_strategy(source, generated),
        "zone_positions": _score_zone_positions(source, generated),
        "orientation_match": _score_orientation_match(source, generated),
    }

    thresholds = {name: 0.60 for name in details}
    messages = {
        "role_counts": "generated symbol-role counts diverge from the source fixture.",
        "relative_positions": "major-symbol ordering diverges from the source fixture.",
        "label_strategy": "generated label/global-label strategy diverges from the source.",
        "geometry_spread": "generated x-column spread is significantly worse.",
        "wire_stub_ratio": "generated routing is noticeably stubbier than the source.",
        "zone_positions": "component placements are in significantly different schematic zones.",
        "orientation_match": "component orientations diverge from the source fixture.",
    }
    reasons: list[str] = []
    for name, detail in details.items():
        if not detail.applicable:
            continue
        if detail.score < detail.max_score * thresholds[name]:
            reasons.append(f"{name}: {detail.reason or messages[name]}")

    applicable_max = sum(detail.max_score for detail in details.values() if detail.applicable)
    raw_score = sum(detail.score for detail in details.values() if detail.applicable)
    if applicable_max <= 0:
        normalized_score = 0.0
        reasons.append("similarity: no source layout metrics were applicable.")
    else:
        normalized_score = raw_score / applicable_max * 100.0

    sub_scores = {name: round(detail.score, 2) for name, detail in details.items()}
    return LayoutSimilarityReport(
        score=round(normalized_score, 2),
        sub_scores=sub_scores,
        reasons=tuple(reasons),
        sub_score_details=details,
    )


def _not_applicable(max_score: float, reason: str) -> SubScoreDetail:
    return SubScoreDetail(score=0.0, max_score=max_score, applicable=False, reason=reason)


def _score_role_counts(
    source: LayoutFeatures, generated: LayoutFeatures
) -> SubScoreDetail:
    if not source.role_counts:
        return _not_applicable(20.0, "the source fixture has no symbol-role counts.")
    roles = set(source.role_counts) | set(generated.role_counts)
    total_delta = sum(
        abs(source.role_counts.get(role, 0) - generated.role_counts.get(role, 0))
        for role in roles
    )
    return SubScoreDetail(score=max(20.0 - total_delta * 3.0, 0.0), max_score=20.0)


def _score_relative_positions(
    source: LayoutFeatures, generated: LayoutFeatures
) -> SubScoreDetail:
    source_relations = {(item.a, item.b, item.relation) for item in source.relative_positions}
    if not source_relations:
        return _not_applicable(10.0, "the source fixture has no relative-position relations.")
    generated_relations = {(item.a, item.b, item.relation) for item in generated.relative_positions}
    overlap = len(source_relations & generated_relations) / len(source_relations)
    return SubScoreDetail(score=round(overlap * 10.0, 2), max_score=10.0)


def _has_source_symbols(source: LayoutFeatures) -> bool:
    return bool(source.symbols or source.role_counts or int(source.counts.get("symbols", 0)))


def _score_label_strategy(
    source: LayoutFeatures, generated: LayoutFeatures
) -> SubScoreDetail:
    keys = {"local_label_count", "global_label_count", "power_symbol_count"}
    if not _has_source_symbols(source) and not any(
        int(source.net_label_strategy.get(key, 0)) for key in keys
    ):
        return _not_applicable(15.0, "the source fixture has no symbols or label strategy.")
    delta = sum(
        abs(source.net_label_strategy.get(key, 0) - generated.net_label_strategy.get(key, 0))
        for key in keys
    )
    return SubScoreDetail(score=max(15.0 - delta * 2.5, 0.0), max_score=15.0)


def _score_geometry_spread(
    source: LayoutFeatures, generated: LayoutFeatures
) -> SubScoreDetail:
    if not _has_source_symbols(source):
        return _not_applicable(10.0, "the source fixture has no symbols to measure spread.")
    source_columns = int(source.geometry.get("distinct_x_columns", 0))
    generated_columns = int(generated.geometry.get("distinct_x_columns", 0))
    if generated_columns >= source_columns:
        score = 10.0
    else:
        score = max(10.0 - (source_columns - generated_columns) * 3.0, 0.0)
    return SubScoreDetail(score=score, max_score=10.0)


def _score_wire_strategy(
    source: LayoutFeatures, generated: LayoutFeatures
) -> SubScoreDetail:
    if int(source.counts.get("wires", 0)) <= 0:
        return _not_applicable(10.0, "the source fixture has no wires to compare.")
    source_stub_ratio = float(source.geometry.get("wire_stub_ratio", 1.0))
    generated_stub_ratio = float(generated.geometry.get("wire_stub_ratio", 1.0))
    if generated_stub_ratio <= source_stub_ratio:
        score = 10.0
    else:
        score = max(10.0 - (generated_stub_ratio - source_stub_ratio) * 60.0, 0.0)
    return SubScoreDetail(score=score, max_score=10.0)


def _symbol_zone(
    x: float,
    y: float,
    bbox: tuple[float, float, float, float],
) -> int:
    """Return a row-major 3x3 zone index for a point within *bbox*."""

    min_x, max_x, min_y, max_y = bbox
    col = min(max(int(3.0 * (x - min_x) / (max_x - min_x)), 0), 2) if max_x > min_x else 1
    row = min(max(int(3.0 * (y - min_y) / (max_y - min_y)), 0), 2) if max_y > min_y else 1
    return row * 3 + col


def _bbox_for_refs(features: LayoutFeatures, refs: set[str]) -> tuple[float, float, float, float]:
    symbols = [features.symbols[ref] for ref in refs]
    xs = [symbol.x for symbol in symbols]
    ys = [symbol.y for symbol in symbols]
    return (min(xs), max(xs), min(ys), max(ys))


def _score_zone_positions(
    source: LayoutFeatures,
    generated: LayoutFeatures,
) -> SubScoreDetail:
    source_refs = {ref for ref, symbol in source.symbols.items() if not symbol.is_power_symbol}
    if not source_refs:
        return _not_applicable(20.0, "the source fixture has no non-power symbols to compare.")

    generated_refs = {
        ref
        for ref, symbol in generated.symbols.items()
        if ref in source_refs and not symbol.is_power_symbol
    }
    if not generated_refs:
        return SubScoreDetail(
            score=0.0,
            max_score=20.0,
            reason="none of the expected non-power component references exist in generated output.",
        )

    source_bbox = _bbox_for_refs(source, source_refs)
    generated_bbox = _bbox_for_refs(generated, generated_refs)
    matched = 0
    for ref in source_refs:
        generated_symbol = generated.symbols.get(ref)
        if generated_symbol is None or generated_symbol.is_power_symbol:
            continue
        source_symbol = source.symbols[ref]
        if _symbol_zone(source_symbol.x, source_symbol.y, source_bbox) == _symbol_zone(
            generated_symbol.x, generated_symbol.y, generated_bbox
        ):
            matched += 1
    score = round(matched / len(source_refs) * 20.0, 2)
    return SubScoreDetail(score=score, max_score=20.0)


def _quantize_rotation(rotation: float) -> int:
    """Quantize an angle clockwise to the nearest 90-degree step, ties upward."""

    normalized = rotation % 360.0
    return int((normalized + 45.0) // 90.0) % 4


def _score_orientation_match(
    source: LayoutFeatures,
    generated: LayoutFeatures,
) -> SubScoreDetail:
    source_symbols = {
        ref: symbol for ref, symbol in source.symbols.items() if not symbol.is_power_symbol
    }
    if not source_symbols:
        return _not_applicable(15.0, "the source fixture has no non-power symbols to compare.")

    matched = 0
    present = 0
    for ref, source_symbol in source_symbols.items():
        generated_symbol = generated.symbols.get(ref)
        if generated_symbol is None or generated_symbol.is_power_symbol:
            continue
        present += 1
        if _quantize_rotation(source_symbol.rotation) == _quantize_rotation(
            generated_symbol.rotation
        ):
            matched += 1
    score = round(matched / len(source_symbols) * 15.0, 2)
    reason = None
    if present == 0:
        reason = "none of the expected non-power component references exist in generated output."
    return SubScoreDetail(score=score, max_score=15.0, reason=reason)
