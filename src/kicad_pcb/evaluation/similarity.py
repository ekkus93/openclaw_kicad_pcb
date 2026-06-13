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
    """Compare source and generated layout features without exact coordinates.

    Sub-scores (max 100 total):
      role_counts        20 — symbol role mix matches
      relative_positions 10 — ordinal left-of/above ordering matches
      label_strategy     15 — local/global label counts proportionate
      geometry_spread    10 — x-column spread at least as wide as source
      wire_stub_ratio    10 — routing not stubbier than source
      zone_positions     20 — components land in the same 3×3 zone
      orientation_match  15 — component rotations (quantised to 90°) match
    """

    reasons: list[str] = []

    role_counts = _score_role_counts(source, generated)
    if role_counts < 12.0:
        reasons.append("role_counts: generated symbol-role counts diverge from the source fixture.")

    relative_positions = _score_relative_positions(source, generated)
    if relative_positions < 6.0:
        reasons.append(
            "relative_positions: major-symbol ordering diverges from the source fixture."
        )

    label_strategy = _score_label_strategy(source, generated)
    if label_strategy < 9.0:
        reasons.append(
            "label_strategy: generated label/global-label strategy diverges from the source."
        )

    geometry_spread = _score_geometry_spread(source, generated)
    if geometry_spread < 6.0:
        reasons.append("geometry_spread: generated x-column spread is significantly worse.")

    wire_strategy = _score_wire_strategy(source, generated)
    if wire_strategy < 6.0:
        reasons.append("wire_stub_ratio: generated routing is noticeably stubbier than the source.")

    zone_positions = _score_zone_positions(source, generated)
    if zone_positions < 12.0:
        reasons.append(
            "zone_positions: component placements are in significantly different schematic zones."
        )

    orientation_match = _score_orientation_match(source, generated)
    if orientation_match < 9.0:
        reasons.append("orientation_match: component orientations diverge from the source fixture.")

    sub_scores = {
        "role_counts": round(role_counts, 2),
        "relative_positions": round(relative_positions, 2),
        "label_strategy": round(label_strategy, 2),
        "geometry_spread": round(geometry_spread, 2),
        "wire_stub_ratio": round(wire_strategy, 2),
        "zone_positions": round(zone_positions, 2),
        "orientation_match": round(orientation_match, 2),
    }
    return LayoutSimilarityReport(
        score=round(sum(sub_scores.values()), 2),
        sub_scores=sub_scores,
        reasons=tuple(reasons),
    )


# ── Sub-score helpers ─────────────────────────────────────────────────────────


def _score_role_counts(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    roles = set(source.role_counts) | set(generated.role_counts)
    if not roles:
        return 20.0
    total_delta = sum(
        abs(source.role_counts.get(role, 0) - generated.role_counts.get(role, 0)) for role in roles
    )
    return max(20.0 - total_delta * 3.0, 0.0)


def _score_relative_positions(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_relations = {(item.a, item.b, item.relation) for item in source.relative_positions}
    generated_relations = {(item.a, item.b, item.relation) for item in generated.relative_positions}
    if not source_relations:
        return 10.0
    overlap = len(source_relations & generated_relations) / len(source_relations)
    return round(overlap * 10.0, 2)


def _score_label_strategy(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    keys = {"local_label_count", "global_label_count", "power_symbol_count"}
    delta = sum(
        abs(source.net_label_strategy.get(key, 0) - generated.net_label_strategy.get(key, 0))
        for key in keys
    )
    return max(15.0 - delta * 2.5, 0.0)


def _score_geometry_spread(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_columns = int(source.geometry.get("distinct_x_columns", 0))
    generated_columns = int(generated.geometry.get("distinct_x_columns", 0))
    if generated_columns >= source_columns:
        return 10.0
    return max(10.0 - (source_columns - generated_columns) * 3.0, 0.0)


def _score_wire_strategy(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    source_stub_ratio = float(source.geometry.get("wire_stub_ratio", 1.0))
    generated_stub_ratio = float(generated.geometry.get("wire_stub_ratio", 1.0))
    if generated_stub_ratio <= source_stub_ratio:
        return 10.0
    return max(10.0 - (generated_stub_ratio - source_stub_ratio) * 60.0, 0.0)


def _symbol_zone(
    x: float,
    y: float,
    bbox: tuple[float, float, float, float],
) -> int:
    """Return a 3×3 zone index (0–8) for a symbol at (x, y) within *bbox*.

    *bbox* is ``(min_x, max_x, min_y, max_y)``.
    Zones are numbered row-major: top-left=0, top-right=2, bottom-left=6, bottom-right=8.
    When the bounding box is degenerate on an axis, the middle zone is used for that axis.
    """
    min_x, max_x, min_y, max_y = bbox
    col = min(int(3.0 * (x - min_x) / (max_x - min_x)), 2) if max_x > min_x else 1
    row = min(int(3.0 * (y - min_y) / (max_y - min_y)), 2) if max_y > min_y else 1
    return row * 3 + col


def _features_bbox(features: LayoutFeatures) -> tuple[float, float, float, float]:
    g = features.geometry
    return (
        float(g.get("min_x", 0.0)),
        float(g.get("max_x", 0.0)),
        float(g.get("min_y", 0.0)),
        float(g.get("max_y", 0.0)),
    )


def _features_zone(features: LayoutFeatures, ref: str) -> int:
    """Return the 3×3 zone for *ref* within *features*' own bounding box."""
    sym = features.symbols[ref]
    return _symbol_zone(sym.x, sym.y, _features_bbox(features))


def _score_zone_positions(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    """Score how well generated component placements match source 3×3 zone assignments.

    Each schematic is divided into a 3×3 grid using its own normalised bounding box so
    that different absolute coordinate spaces compare fairly.  Power symbols are excluded
    because they are placed automatically by the router independently of layout intent.
    """
    source_syms = {ref for ref, sym in source.symbols.items() if not sym.is_power_symbol}
    gen_syms = {ref for ref, sym in generated.symbols.items() if not sym.is_power_symbol}
    common_refs = source_syms & gen_syms
    if not common_refs:
        return 20.0

    matched = sum(
        1 for ref in common_refs if _features_zone(source, ref) == _features_zone(generated, ref)
    )
    return round(matched / len(common_refs) * 20.0, 2)


def _quantize_rotation(rotation: float) -> int:
    """Round a rotation angle to the nearest 90° step (0, 1, 2, or 3)."""
    return int(round(rotation / 90.0)) % 4


def _score_orientation_match(source: LayoutFeatures, generated: LayoutFeatures) -> float:
    """Score the fraction of shared non-power components with matching 90°-quantised rotations."""
    source_syms = {ref: sym for ref, sym in source.symbols.items() if not sym.is_power_symbol}
    gen_syms = {ref: sym for ref, sym in generated.symbols.items() if not sym.is_power_symbol}
    common_refs = source_syms.keys() & gen_syms.keys()
    if not common_refs:
        return 15.0

    matched = sum(
        1
        for ref in common_refs
        if _quantize_rotation(source_syms[ref].rotation)
        == _quantize_rotation(gen_syms[ref].rotation)
    )
    return round(matched / len(common_refs) * 15.0, 2)
