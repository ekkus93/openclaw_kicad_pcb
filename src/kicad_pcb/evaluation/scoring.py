"""Intrinsic generated-schematic quality scoring."""

from __future__ import annotations

from dataclasses import dataclass, field

from kicad_pcb.corpus.layout_features import LayoutFeatures

VALIDITY_WEIGHT = 20.0
OVERLAP_WEIGHT = 15.0
PAGE_BOUNDS_WEIGHT = 10.0
ROUTING_WEIGHT = 20.0
LABEL_STRATEGY_WEIGHT = 15.0
SPREAD_WEIGHT = 10.0
POWER_WEIGHT = 10.0


@dataclass(frozen=True)
class IntrinsicQualityReport:
    score: float
    sub_scores: dict[str, float]
    reasons: tuple[str, ...] = field(default_factory=tuple)


def score_intrinsic_quality(features: LayoutFeatures) -> IntrinsicQualityReport:
    """Score generated schematic quality independent of the source schematic."""

    reasons: list[str] = []
    counts = features.counts
    geometry = features.geometry

    validity = VALIDITY_WEIGHT if counts["symbols"] > 0 else 0.0
    if validity == 0.0:
        reasons.append("validity: generated schematic contains no symbols.")

    overlap_penalty = min(float(len(features.intrinsic_lints)), 3.0)
    overlap = max(OVERLAP_WEIGHT - overlap_penalty * 5.0, 0.0)
    if overlap < OVERLAP_WEIGHT:
        reasons.append("overlap: layout lints reported potential crowding or composition issues.")

    max_x = float(geometry.get("max_x", 0.0))
    max_y = float(geometry.get("max_y", 0.0))
    page_bounds = PAGE_BOUNDS_WEIGHT if max_x <= 297.0 and max_y <= 210.0 else 0.0
    if page_bounds == 0.0:
        reasons.append("page_bounds: symbols extend beyond a single A4 page envelope.")

    stub_ratio = float(geometry.get("wire_stub_ratio", 1.0))
    routing = max(ROUTING_WEIGHT * (1.0 - min(stub_ratio, 1.0)), 0.0)
    if routing < ROUTING_WEIGHT * 0.5:
        reasons.append("routing_simplicity: wire stub ratio is too high.")

    local_labels = features.net_label_strategy["local_label_count"]
    global_labels = features.net_label_strategy["global_label_count"]
    label_strategy = LABEL_STRATEGY_WEIGHT
    if local_labels > counts["symbols"]:
        label_strategy -= 5.0
        reasons.append("label_strategy: local labels dominate the schematic.")
    if global_labels > counts["symbols"] // 2:
        label_strategy -= 3.0
        reasons.append("label_strategy: too many global labels are exposed.")
    label_strategy = max(label_strategy, 0.0)

    x_columns = int(geometry.get("distinct_x_columns", 0))
    spread = min(float(x_columns) / max(counts["non_power_symbols"], 1) * 25.0, SPREAD_WEIGHT)
    if x_columns < 2:
        reasons.append("spread: schematic is collapsed into too few x-columns.")

    power_symbols = counts["power_symbols"]
    power_score = POWER_WEIGHT if power_symbols > 0 else POWER_WEIGHT / 2.0
    if power_symbols == 0:
        reasons.append("power_symbols: no power symbols were detected.")

    sub_scores = {
        "validity": round(validity, 2),
        "overlap": round(overlap, 2),
        "page_bounds": round(page_bounds, 2),
        "routing_simplicity": round(routing, 2),
        "label_strategy": round(label_strategy, 2),
        "spread": round(spread, 2),
        "power_symbols": round(power_score, 2),
    }
    score = round(sum(sub_scores.values()), 2)
    return IntrinsicQualityReport(score=score, sub_scores=sub_scores, reasons=tuple(reasons))
