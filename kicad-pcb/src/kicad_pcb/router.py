"""Orthogonal net routing for kicad_pcb managed schematics.

Routes 2-pin nets directly with L-shaped wire segments when both pins
are known and within :data:`MAX_DIRECT_DIST_MM` (Manhattan distance,
stub-end to stub-end).  For nets with 3–:data:`_HUB_MAX_DEGREE` pins the
default strategy is spine routing (bus-style wire with T-junction taps),
which produces cleaner schematics than the per-pin stub+label fallback.
All other nets fall back to the classic wire-stub + net-label-per-pin
approach.

Public API
----------
:func:`route_nets`             — compute routing decisions for all nets in a CircuitIR.
:func:`write_routing`          — emit computed decisions into a SchematicDoc.
:class:`LabelPolicy`           — policy dataclass controlling label deduplication.
:data:`DEFAULT_LABEL_POLICY`   — default policy (max 2 local labels, 4 global per net).
:class:`PowerSymbolPlacement`  — placed power symbol (GND/VCC etc.), emitted for power nets.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR, PinRefIR
    from .sch_doc import SchematicDoc

from .component_types import component_type as _component_type
from .component_types import is_power_net as _base_is_power_net_name
from .component_types import power_rail_polarity
from .errors import ErrorCode, UserError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
WIRE_EXTEND_MM: float = 5.08  # pin stub length — 200 mil (one KiCad grid unit)
# Raised from 120.0 → 200.0 to accommodate the wider layout scale
# (ranksep=2.5 × SCALE_MM_PER_GV=24.0 puts simple 2-component circuits up to
# ~150 mm apart when routed without tier information).
MAX_DIRECT_DIST_MM: float = 200.0  # Manhattan distance cap for direct routing
# When tiers are provided, a wire longer than this triggers net-label routing
# even between adjacent-tier components.  Separate from MAX_DIRECT_DIST_MM so
# the Manhattan fallback (no tiers) is not affected.  (Rule §4)
# Raised from 30.0 → 70.0 to accommodate the wider layout scale
# (ranksep=2.5 × SCALE_MM_PER_GV=24.0 = 60 mm between adjacent tiers).
MAX_DIRECT_WIRE_MM: float = 70.0
# Approximate half-edge of a KiCad symbol bounding box (200 mil = 5.08 mm).
# Used by detect_body_crossings to detect component-body wire crossings.
SYMBOL_HALF_SIZE_MM: float = 5.08

# Nets with degree > this threshold fall back to global-label style (avoids
# spaghetti wiring for busses and power rails).
_HUB_MAX_DEGREE: int = 6

# Maximum Euclidean distance (mm) for grouping power pins into a shared
# power symbol cluster.  Pins within this radius share one power symbol,
# reducing visual ground/power clutter (Phase 5.1).
_POWER_CLUSTER_RADIUS_MM: float = 40.0
_POWER_LABEL_CLEARANCE_MM: float = 6.35


@dataclass(frozen=True)
class LabelPolicy:
    """Policy controlling label deduplication in :func:`route_nets`.

    Capping labels per net reduces schematic clutter and keeps the LAY001
    lint rule (warn when a label appears > 3 times) from triggering on
    high-fanout signal nets.

    Attributes
    ----------
    max_labels_per_net:
        Maximum number of local :class:`NetLabel` nodes emitted for any
        single non-power net falling through to the label-route fallback.
        Pins beyond this limit still receive a stub wire and bind marker;
        only the visible label is suppressed.  Default: ``2`` (one label
        per connection endpoint, the minimum KiCad requires for
        schematic connectivity).
    max_global_labels_per_net:
        Maximum number of :class:`GlobalLabelPlacement` nodes emitted for
        a single *non-power* high-degree net (see :data:`_HUB_MAX_DEGREE`).
        Power nets use :class:`PowerSymbolPlacement` instead and are not
        subject to this cap.  Default: ``4``.
    """

    max_labels_per_net: int = 2
    max_global_labels_per_net: int = 4


DEFAULT_LABEL_POLICY: LabelPolicy = LabelPolicy()


@dataclass(frozen=True)
class RoutingHeuristicPolicy:
    """Policy seam for analog-specific routing heuristics.

    The generic router still owns direct, hub, spine, lane, and label routing.
    This policy only governs the analog-audio special cases layered on top of
    those generic strategies so later profile work can swap or disable them
    without rewriting :func:`route_nets`.
    """

    enable_compact_output_tails: bool = True
    enable_compact_local_ground_clusters: bool = True

    def should_skip_shared_lane_plan(
        self,
        endpoints: list[tuple[float, float]],
        inferred_plan: SharedLanePlan | None,
    ) -> bool:
        """Return True when the analog compact-tail rule should override a lane."""
        if not self.enable_compact_output_tails or inferred_plan is None:
            return False
        return _should_skip_inferred_lane_plan(endpoints, inferred_plan)

    def route_compact_signal_tail(
        self,
        endpoints: list[tuple[float, float]],
        *,
        inferred_plan: SharedLanePlan | None,
        positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    ) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
        """Return the analog compact-tail route when that policy applies."""
        if not self.enable_compact_output_tails or inferred_plan is None:
            return None
        if not self.should_skip_shared_lane_plan(endpoints, inferred_plan):
            return None
        return _compact_vertical_tail_route(
            endpoints,
            coordinate=inferred_plan.coordinate,
            positions=positions,
        )

    def route_compact_power_cluster(
        self,
        *,
        net_name: str,
        cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
        positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    ) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
        """Return the analog local-ground cluster route when that policy applies."""
        if not self.enable_compact_local_ground_clusters or net_name.upper() != "GND":
            return None
        return _compact_local_ground_cluster_route(cluster, positions=positions)


DEFAULT_ROUTING_HEURISTIC_POLICY: RoutingHeuristicPolicy = RoutingHeuristicPolicy()


def _is_power_net_name(net_name: str) -> bool:
    """Return True for routed power rails, including VPLUS/VMINUS aliases."""
    return _base_is_power_net_name(net_name) or power_rail_polarity(net_name) is not None


def _tier_distance(ref_a: str, ref_b: str, tiers: dict[str, int]) -> int:
    """Return the absolute tier-index difference between two component refs."""
    return abs(tiers.get(ref_a, 0) - tiers.get(ref_b, 0))


def _wire_crosses_box(  # noqa: PLR0913
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    bx: float,
    by: float,
    half: float,
) -> bool:
    """Return True when segment (x1,y1)→(x2,y2) intersects the axis-aligned
    bounding box centred at *(bx, by)* with half-edge *half*.

    Only orthogonal (horizontal and vertical) segments are considered;
    diagonal segments always return False.
    """
    lx, rx = bx - half, bx + half
    ty, bot = by - half, by + half  # top (smaller y in KiCad) / bottom
    if math.isclose(y1, y2, abs_tol=0.01):  # horizontal segment
        seg_lx, seg_rx = min(x1, x2), max(x1, x2)
        return ty <= y1 <= bot and seg_lx < rx and seg_rx > lx
    if math.isclose(x1, x2, abs_tol=0.01):  # vertical segment
        seg_ty, seg_bot = min(y1, y2), max(y1, y2)
        return lx <= x1 <= rx and seg_ty < bot and seg_bot > ty
    return False


def _point_in_or_on_box(x: float, y: float, bx: float, by: float, half: float) -> bool:
    """Return True when point *(x, y)* lies inside or on the box boundary."""
    return (bx - half) <= x <= (bx + half) and (by - half) <= y <= (by + half)


def _detour_segment(
    seg: WireSegment,
    bx: float,
    by: float,
    half: float,
) -> list[WireSegment]:
    """Replace *seg* with a detour path that bypasses the AABB at *(bx, by)*.

    For a horizontal wire the detour routes **above** the box
    (``y_new = by - half - half``); for a vertical wire the detour routes
    **to the left** (``x_new = bx - half - half``).

    The returned list contains up to five orthogonal segments forming a
    rectangular jog around the obstacle.
    """
    if math.isclose(seg.y1, seg.y2, abs_tol=0.01):  # horizontal
        detour_y = by - half - half  # one symbol-height above box top
        enter_x = max(bx - half, min(seg.x1, seg.x2))
        exit_x = min(bx + half, max(seg.x1, seg.x2))
        return [
            WireSegment(seg.x1, seg.y1, enter_x, seg.y1),
            WireSegment(enter_x, seg.y1, enter_x, detour_y),
            WireSegment(enter_x, detour_y, exit_x, detour_y),
            WireSegment(exit_x, detour_y, exit_x, seg.y2),
            WireSegment(exit_x, seg.y2, seg.x2, seg.y2),
        ]
    if math.isclose(seg.x1, seg.x2, abs_tol=0.01):  # vertical
        detour_x = bx - half - half  # one symbol-width to the left of box
        enter_y = max(by - half, min(seg.y1, seg.y2))
        exit_y = min(by + half, max(seg.y1, seg.y2))
        return [
            WireSegment(seg.x1, seg.y1, seg.x1, enter_y),
            WireSegment(seg.x1, enter_y, detour_x, enter_y),
            WireSegment(detour_x, enter_y, detour_x, exit_y),
            WireSegment(detour_x, exit_y, seg.x1, exit_y),
            WireSegment(seg.x1, exit_y, seg.x2, seg.y2),
        ]
    return [seg]  # diagonal — no detour (uncommon in schematic routing)


def detect_body_crossings(
    wires: list[WireSegment],
    positions: Mapping[str, tuple[float, float, float | None]],
) -> list[WireSegment]:
    """Reroute wire segments that pass through a component bounding box.

    Each component in *positions* is approximated as a
    ``SYMBOL_HALF_SIZE_MM``-square bounding box centred on its KiCad
    coordinates.  Any wire segment that intersects such a box is replaced with
    a rectangular detour path (see :func:`_detour_segment`); all other
    segments pass through unchanged.

    Parameters
    ----------
    wires:
        Current list of :class:`WireSegment` objects from :func:`route_nets`.
    positions:
        ``{ref: (x, y, rotation)}`` position map from the layout engine.

    Returns a new wire list with all body crossings rerouted.
    """
    bboxes = [(pos[0], pos[1]) for pos in positions.values()]
    result: list[WireSegment] = []
    for seg in wires:
        replaced = False
        for bx, by in bboxes:
            if _point_in_or_on_box(
                seg.x1, seg.y1, bx, by, SYMBOL_HALF_SIZE_MM
            ) or _point_in_or_on_box(seg.x2, seg.y2, bx, by, SYMBOL_HALF_SIZE_MM):
                continue
            if _wire_crosses_box(seg.x1, seg.y1, seg.x2, seg.y2, bx, by, SYMBOL_HALF_SIZE_MM):
                result.extend(_detour_segment(seg, bx, by, SYMBOL_HALF_SIZE_MM))
                replaced = True
                break  # one detour per segment; further crossings resolved on next call
        if not replaced:
            result.append(seg)
    return result


def _cluster_power_pins(
    pins: list[tuple[PinRefIR, tuple[float, float, float]]],
    radius: float = _POWER_CLUSTER_RADIUS_MM,
) -> list[list[tuple[PinRefIR, tuple[float, float, float]]]]:
    """Group power pins by proximity into clusters sharing a symbol.

    Uses simple greedy clustering: each unclustered pin either joins the
    nearest existing cluster (if within *radius*) or starts a new cluster.

    Parameters
    ----------
    pins:
        List of ``(pin_ref, (x, y, angle))`` tuples for pins on a power net.
    radius:
        Maximum Euclidean distance (mm) for pins to share a cluster.

    Returns
    -------
    list[list[tuple]]
        List of clusters, where each cluster is a list of pin tuples.

    Notes
    -----
    Phase 5.1: reduces visual ground/power clutter by placing one power
    symbol per cluster instead of one per pin.
    """
    if not pins:
        return []

    clusters: list[list[tuple[PinRefIR, tuple[float, float, float]]]] = []

    for pin, (px, py, pangle) in pins:
        # Find the nearest cluster centroid within radius
        best_cluster_idx: int | None = None
        best_dist = float("inf")

        for idx, cluster in enumerate(clusters):
            # Compute cluster centroid
            cx = sum(cpx for _, (cpx, _, _) in cluster) / len(cluster)
            cy = sum(cpy for _, (_, cpy, _) in cluster) / len(cluster)
            dist = math.hypot(px - cx, py - cy)

            if dist < radius and dist < best_dist:
                best_dist = dist
                best_cluster_idx = idx

        # Add to nearest cluster or create new one
        if best_cluster_idx is not None:
            clusters[best_cluster_idx].append((pin, (px, py, pangle)))
        else:
            clusters.append([(pin, (px, py, pangle))])

    return clusters


# ---------------------------------------------------------------------------
# Data classes — immutable routing decisions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class WireSegment:
    """A single wire segment from (x1,y1) to (x2,y2) in mm."""

    x1: float
    y1: float
    x2: float
    y2: float


@dataclass(frozen=True)
class NetLabel:
    """A net-name label placed at (x,y) with the given *angle* (degrees)."""

    name: str
    x: float
    y: float
    angle: int


@dataclass(frozen=True)
class BindMarker:
    """Hidden off-canvas text binding a (ref, pin) pair to a net name."""

    ref: str
    pin: str
    net_name: str


@dataclass(frozen=True)
class PinAnchor:
    """Unit-local anchor ownership plus schematic-space endpoint geometry."""

    ref: str
    pin: str
    x: float
    y: float
    angle: float
    unit: int | None = None


@dataclass(frozen=True)
class GlobalLabelPlacement:
    """A global-label node placed at *(x, y)*.  Used for high-degree
    non-power nets instead of per-pin local labels."""

    name: str
    x: float
    y: float
    angle: int


@dataclass(frozen=True)
class PowerSymbolPlacement:
    """A KiCad power symbol placed at *(x, y)* with a stub wire.

    Used in place of :class:`GlobalLabelPlacement` for power nets (GND,
    VCC, etc.) when a matching ``power:<net_name>`` library symbol is
    available.  The symbol's single connection pin is at *(x, y)* — a
    stub wire should end here.  When the library symbol is unavailable,
    :func:`write_routing` falls back to a ``global_label`` node.
    """

    net_name: str  # e.g. "GND"  — also the KiCad Value of the placed symbol
    x: float
    y: float
    angle: int = 0


@dataclass(frozen=True)
class JunctionPoint:
    """A schematic junction marker at *(x, y)*."""

    x: float
    y: float


@dataclass(frozen=True)
class SharedLanePlan:
    """Planner output for a local shared lane.

    The optional orthogonal bounds let the planner keep a lane on the same
    X/Y coordinate while shortening the visible trunk to the portion that
    actually reads as the downstream continuation.
    """

    axis: str
    coordinate: float
    min_orthogonal: float | None = None
    max_orthogonal: float | None = None


@dataclass(frozen=True)
class LadderLanePlannerContext:
    """Grouped-ladder planning inputs shared across one local neighborhood."""

    candidate_degree: dict[str, int]
    shared_lane_by_net: dict[str, tuple[str, float]]
    endpoints_by_net: dict[str, list[tuple[float, float]]]
    connector_entry_x_by_net: dict[str, float]
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY


@dataclass
class NetRouting:
    """Aggregated routing decisions for a whole schematic."""

    wires: list[WireSegment] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    global_labels: list[GlobalLabelPlacement] = field(default_factory=list)
    power_symbols: list[PowerSymbolPlacement] = field(default_factory=list)
    junctions: list[JunctionPoint] = field(default_factory=list)
    bind_markers: list[BindMarker] = field(default_factory=list)
    route_decisions: list[RouteDecision] = field(default_factory=list)


@dataclass(frozen=True)
class RouteDecision:
    """Debug summary of the final routing strategy selected for one net."""

    net_name: str
    classification: Literal["power", "signal"]
    strategy: str
    pin_count: int
    known_pin_count: int
    unknown_pin_count: int
    use_bus: bool
    heuristic_override: str | None = None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _stub_end(wx: float, wy: float, wa: float) -> tuple[float, float]:
    """Return the far end of the 5.08 mm stub extended outward from a pin.

    *wa* is the KiCad pin angle (direction **from endpoint toward the symbol
    body**; e.g. 0 = body is to the right of the endpoint, stub goes left).
    The stub extends in the *opposite* direction.
    """
    rad = math.radians(wa)
    return wx - math.cos(rad) * WIRE_EXTEND_MM, wy - math.sin(rad) * WIRE_EXTEND_MM


def _manhattan(x1: float, y1: float, x2: float, y2: float) -> float:
    return abs(x2 - x1) + abs(y2 - y1)


def _resolve_pin_anchors(
    pin_endpoints: Mapping[tuple[str, str], tuple[float, float, float]],
    pin_anchors: Mapping[tuple[str, str], PinAnchor] | None = None,
) -> dict[tuple[str, str], PinAnchor]:
    """Return the explicit pin-anchor map used by routing helpers."""

    resolved = dict(pin_anchors or {})
    for (ref, pin), (x, y, angle) in pin_endpoints.items():
        resolved.setdefault(
            (ref, pin),
            PinAnchor(ref=ref, pin=pin, x=x, y=y, angle=angle),
        )
    return resolved


def _coerce_pin_anchor_map(
    pin_map: Mapping[tuple[str, str], PinAnchor | tuple[float, float, float]] | None,
) -> dict[tuple[str, str], PinAnchor]:
    """Normalize legacy endpoint tuples into PinAnchor values."""

    if pin_map is None:
        return {}

    normalized: dict[tuple[str, str], PinAnchor] = {}
    for (ref, pin), anchor in pin_map.items():
        if isinstance(anchor, PinAnchor):
            normalized[(ref, pin)] = anchor
            continue
        x, y, angle = anchor
        normalized[(ref, pin)] = PinAnchor(ref=ref, pin=pin, x=x, y=y, angle=angle)
    return normalized


def _is_connector_passive_edge(ref_a: str, ref_b: str) -> bool:
    """Return True for simple connector-to-passive edge links.

    These 2-pin nets are typically the small input/output links at the circuit
    boundary. Keep them directly wired even if later tier inference stretches
    the passive further into the signal path.
    """
    pair = {_component_type(ref_a), _component_type(ref_b)}
    return pair == {"connector", "passive"}


def _l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    """Return up to two orthogonal segments that connect (ex1,ey1) to (ex2,ey2).

    Uses horizontal-first L-routing: go horizontally to (ex2, ey1), then
    vertically to (ex2, ey2).  Degenerate segments (zero length) are omitted.
    """
    segs: list[WireSegment] = []
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        # Horizontal leg
        segs.append(WireSegment(ex1, ey1, ex2, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        # Vertical leg (starts at corner (ex2, ey1))
        segs.append(WireSegment(ex2, ey1, ex2, ey2))
    return segs


def _snap_grid(v: float, grid: float = 1.27) -> float:
    """Snap *v* to the nearest *grid* mm increment (KiCad 50-mil grid)."""
    return round(round(v / grid) * grid, 4)


def _offset_point_along_angle(
    x: float,
    y: float,
    angle: int,
    distance: float,
) -> tuple[float, float]:
    """Return *(x, y)* shifted *distance* mm along cardinal *angle*."""
    normalized = angle % 360
    if normalized == 0:
        return _snap_grid(x + distance), y
    if normalized == 90:
        return x, _snap_grid(y + distance)
    if normalized == 180:
        return _snap_grid(x - distance), y
    if normalized == 270:
        return x, _snap_grid(y - distance)
    rad = math.radians(normalized)
    return _snap_grid(x + math.cos(rad) * distance), _snap_grid(y + math.sin(rad) * distance)


def _power_label_angle_for_pin(pin_angle: float) -> int:
    """Return the outward-facing label angle for a power pin stub."""
    return int((pin_angle + 180) % 360)


def _power_symbol_angle(net_name: str, default_angle: int) -> int:
    """Return the preferred placed-symbol angle for a power net.

    Keep ground symbols visually consistent by always pointing them downward.
    This avoids sideways/upward ground glyphs that can read like lateral rail
    continuations in crowded local clusters.
    """
    if net_name.upper() == "GND":
        return 0
    return default_angle


def _power_cluster_angle(points: list[tuple[float, float]]) -> int:
    """Choose an outward direction for a shared power label cluster.

    The chosen angle points toward the nearest edge of the cluster bounding box,
    which keeps the visible label on the open side of the local geometry.
    """
    if not points:
        return 0

    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    distances = {
        180: cx - min(xs),
        0: max(xs) - cx,
        270: cy - min(ys),
        90: max(ys) - cy,
    }
    priority = {180: 0, 0: 1, 270: 2, 90: 3}
    return min(distances, key=lambda angle: (distances[angle], priority[angle]))


def _fallback_power_label_position(
    x: float,
    y: float,
    angle: int,
) -> tuple[float, float, int]:
    """Return an off-axis fallback position for power nets missing a library symbol.

    Global labels render as text boxes anchored on the connection point, so
    leaving them colinear with the incoming rail tends to stamp the visible text
    directly on top of that rail. Move them one clearance step orthogonally into
    whitespace instead.
    """
    normalized = angle % 360
    fallback_angle = 270 if normalized in {0, 180} else 180
    fx, fy = _offset_point_along_angle(x, y, fallback_angle, _POWER_LABEL_CLEARANCE_MM)
    return fx, fy, fallback_angle


def _hub_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a multi-pin net through a central hub point.

    Computes the centroid of *endpoints*, snaps it to the KiCad grid, then
    L-routes each endpoint to the hub.  A :class:`JunctionPoint` is added at
    the hub when ≥ 3 spokes converge there.

    Returns *(segments, junctions)*.
    """
    hub_x = _snap_grid(sum(e[0] for e in endpoints) / len(endpoints))
    hub_y = _snap_grid(sum(e[1] for e in endpoints) / len(endpoints))

    segs: list[WireSegment] = []
    for ex, ey in endpoints:
        segs.extend(_l_route(ex, ey, hub_x, hub_y))

    junctions: list[JunctionPoint] = []
    if len(endpoints) >= 3:
        junctions.append(JunctionPoint(hub_x, hub_y))

    return segs, junctions


def _spine_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a multi-pin net as a spine with T-junction taps.

    Determines the dominant axis (horizontal vs vertical) from the bounding
    box of *endpoints*, draws a single spine wire along that axis, then
    connects each endpoint to the nearest point on the spine with a
    perpendicular segment and a :class:`JunctionPoint` at the T-intersection.

    This produces a cleaner "bus-style" visual than the centroid-hub approach
    when all stubs lie roughly along a line.

    Returns *(segments, junctions)*.
    """
    xs = [e[0] for e in endpoints]
    ys = [e[1] for e in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    segs: list[WireSegment] = []
    junctions: list[JunctionPoint] = []

    if x_span >= y_span:
        # Horizontal spine — spine runs left-to-right at the mean Y.
        spine_y = _snap_grid(sum(ys) / len(ys))
        spine_x0 = _snap_grid(min(xs))
        spine_x1 = _snap_grid(max(xs))
        segs.append(WireSegment(spine_x0, spine_y, spine_x1, spine_y))
        for ex, ey in endpoints:
            sx = _snap_grid(ex)
            if not math.isclose(ey, spine_y, abs_tol=0.01):
                segs.append(WireSegment(sx, ey, sx, spine_y))
            junctions.append(JunctionPoint(sx, spine_y))
    else:
        # Vertical spine — spine runs top-to-bottom at the mean X.
        spine_x = _snap_grid(sum(xs) / len(xs))
        spine_y0 = _snap_grid(min(ys))
        spine_y1 = _snap_grid(max(ys))
        segs.append(WireSegment(spine_x, spine_y0, spine_x, spine_y1))
        for ex, ey in endpoints:
            sy = _snap_grid(ey)
            if not math.isclose(ex, spine_x, abs_tol=0.01):
                segs.append(WireSegment(ex, sy, spine_x, sy))
            junctions.append(JunctionPoint(spine_x, sy))

    return segs, junctions


def _shared_lane_route(
    endpoints: list[tuple[float, float]],
    *,
    axis: str | None = None,
    coordinate: float | None = None,
    min_bound: float | None = None,
    max_bound: float | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a local net along a repeated endpoint lane.

    When two or more endpoints already share the same X or Y coordinate, keep
    the trunk on that existing lane instead of inventing a new centroid spine.
    This avoids the nested-rectangle look common in compact passive ladders.
    """
    if len(endpoints) < 2:
        return [], []

    rounded_xs = [round(point[0], 2) for point in endpoints]
    rounded_ys = [round(point[1], 2) for point in endpoints]

    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for value in rounded_xs:
        x_counts[value] = x_counts.get(value, 0) + 1
    for value in rounded_ys:
        y_counts[value] = y_counts.get(value, 0) + 1

    shared_x, shared_x_count = max(x_counts.items(), key=lambda item: item[1])
    shared_y, shared_y_count = max(y_counts.items(), key=lambda item: item[1])

    if axis is None or coordinate is None:
        if shared_x_count >= shared_y_count and shared_x_count >= 2:
            axis = "vertical"
            coordinate = shared_x
        elif shared_y_count >= 2:
            axis = "horizontal"
            coordinate = shared_y
        else:
            return _spine_route(endpoints)

    segs: list[WireSegment] = []
    junctions: list[JunctionPoint] = []

    if axis == "vertical":
        trunk_x = coordinate
        min_y = min(point[1] for point in endpoints) if min_bound is None else min_bound
        max_y = max(point[1] for point in endpoints) if max_bound is None else max_bound
        segs.append(WireSegment(trunk_x, min_y, trunk_x, max_y))
        for ex, ey in endpoints:
            target_y = min(max(ey, min_y), max_y)
            if not math.isclose(ey, target_y, abs_tol=0.01):
                segs.append(WireSegment(ex, ey, ex, target_y))
            if not math.isclose(ex, trunk_x, abs_tol=0.01):
                segs.append(WireSegment(ex, target_y, trunk_x, target_y))
            junctions.append(JunctionPoint(trunk_x, target_y))
        return _simplify_wires(segs), junctions

    if axis == "horizontal":
        trunk_y = coordinate
        min_x = min(point[0] for point in endpoints) if min_bound is None else min_bound
        max_x = max(point[0] for point in endpoints) if max_bound is None else max_bound
        segs.append(WireSegment(min_x, trunk_y, max_x, trunk_y))
        for ex, ey in endpoints:
            target_x = min(max(ex, min_x), max_x)
            if not math.isclose(ex, target_x, abs_tol=0.01):
                segs.append(WireSegment(ex, ey, target_x, ey))
            if not math.isclose(ey, trunk_y, abs_tol=0.01):
                segs.append(WireSegment(target_x, ey, target_x, trunk_y))
            junctions.append(JunctionPoint(target_x, trunk_y))
        return _simplify_wires(segs), junctions

    return _spine_route(endpoints)


def _is_local_ladder_net(
    endpoints: list[tuple[float, float]],
) -> bool:
    """Return True when a net is compact enough to participate in a ladder neighborhood."""
    xs = [point[0] for point in endpoints]
    ys = [point[1] for point in endpoints]
    return (max(xs) - min(xs)) <= 80.0 and (max(ys) - min(ys)) <= 50.0


def _boxes_touch_or_overlap(
    box_a: tuple[float, float, float, float],
    box_b: tuple[float, float, float, float],
    *,
    margin: float = 15.0,
) -> bool:
    """Return True when two axis-aligned boxes overlap or nearly touch."""
    ax0, ax1, ay0, ay1 = box_a
    bx0, bx1, by0, by1 = box_b
    return not (
        ax1 + margin < bx0 or bx1 + margin < ax0 or ay1 + margin < by0 or by1 + margin < ay0
    )


def _preferred_shared_lane(
    endpoints: list[tuple[float, float]],
) -> tuple[str, float] | None:
    """Return the dominant repeated lane for a compact local net, if one exists."""
    rounded_xs = [round(point[0], 2) for point in endpoints]
    rounded_ys = [round(point[1], 2) for point in endpoints]

    x_counts: dict[float, int] = {}
    y_counts: dict[float, int] = {}
    for value in rounded_xs:
        x_counts[value] = x_counts.get(value, 0) + 1
    for value in rounded_ys:
        y_counts[value] = y_counts.get(value, 0) + 1

    shared_x, shared_x_count = max(x_counts.items(), key=lambda item: item[1])
    shared_y, shared_y_count = max(y_counts.items(), key=lambda item: item[1])

    if shared_x_count >= shared_y_count and shared_x_count >= 2:
        return "vertical", shared_x
    if shared_y_count >= 2:
        return "horizontal", shared_y
    return None


def _is_compact_rightward_tail(
    endpoints: list[tuple[float, float]],
    *,
    axis: str,
    coordinate: float,
) -> bool:
    """Return True when a single vertical lane is just a compact rightward tail.

    These 3-pin tails already read cleanly as a short chain: two nearby entry
    points clustered on the same vertical lane region and one downstream point
    extending to the right. One entry point may land slightly left of the lane
    after symbol/stub geometry, but the shape still reads as a short output
    tail rather than a ladder trunk.
    """
    if axis != "vertical" or len(endpoints) != 3:
        return False

    lane_tolerance = (WIRE_EXTEND_MM / 4) + 0.05
    near_lane_points = [
        point for point in endpoints if math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    other_points = [
        point
        for point in endpoints
        if not math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    if len(near_lane_points) != 2 or len(other_points) != 1:
        return False

    other_x, other_y = other_points[0]
    if other_x <= coordinate + 0.01:
        return False

    nearest_lane_y = min(abs(other_y - point[1]) for point in near_lane_points)
    return nearest_lane_y <= (1.5 * WIRE_EXTEND_MM) and (other_x - coordinate) <= (
        6 * WIRE_EXTEND_MM
    )


def _plan_single_grouped_ladder_lane(
    *,
    axis: str,
    base_coordinate: float,
    endpoints: list[tuple[float, float]],
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> SharedLanePlan | None:
    """Return the single-net lane plan, or ``None`` when chain routing should win."""
    candidate_plan = SharedLanePlan(axis, base_coordinate)
    if heuristic_policy.should_skip_shared_lane_plan(endpoints, candidate_plan):
        return None

    if axis == "horizontal":
        shared_points = [
            point for point in endpoints if math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        other_points = [
            point
            for point in endpoints
            if not math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        if len(shared_points) == 2 and len(other_points) == 1:
            other_x = other_points[0][0]
            anchor_x = max(
                (point[0] for point in shared_points),
                key=lambda x: abs(x - other_x),
            )
            return SharedLanePlan(
                axis,
                base_coordinate,
                min(anchor_x, other_x),
                max(anchor_x, other_x),
            )

    return SharedLanePlan(axis, base_coordinate)


def _should_skip_inferred_lane_plan(
    endpoints: list[tuple[float, float]],
    inferred_plan: SharedLanePlan | None,
) -> bool:
    """Return True when an inferred lane would overfit a compact output tail."""
    if inferred_plan is None:
        return False
    return _prefer_chain_route(endpoints) and _is_compact_rightward_tail(
        endpoints,
        axis=inferred_plan.axis,
        coordinate=inferred_plan.coordinate,
    )


def _compact_vertical_tail_route(
    endpoints: list[tuple[float, float]],
    *,
    coordinate: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact asymmetric output tail with one long downstream run.

    This keeps the upstream support point on the near-lane trunk while letting
    the connector-side endpoint anchor one long horizontal segment toward the
    downstream tail. When body positions are available, the final drop to the
    downstream endpoint detours to the right of any blocking body box first so
    `detect_body_crossings(...)` does not have to fragment the tail afterward.
    """
    lane_tolerance = (WIRE_EXTEND_MM / 4) + 0.05
    near_lane_points = [
        point for point in endpoints if math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    other_points = [
        point
        for point in endpoints
        if not math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    if len(near_lane_points) != 2 or len(other_points) != 1:
        return _chain_route(endpoints)

    downstream_x, downstream_y = other_points[0]
    pivot = min(near_lane_points, key=lambda point: abs(point[1] - downstream_y))
    upstream = next(point for point in near_lane_points if point != pivot)

    trunk_x = coordinate
    pivot_x, pivot_y = pivot
    upstream_x, upstream_y = upstream
    tail_y = pivot_y
    if abs(downstream_y - pivot_y) <= (1.5 * WIRE_EXTEND_MM):
        tail_y = _snap_grid(min(pivot_y, downstream_y) - (2.5 * WIRE_EXTEND_MM))

    clearance_x = downstream_x
    if positions is not None and not math.isclose(tail_y, downstream_y, abs_tol=0.01):
        for position in positions.values():
            bx = position[0]
            by = position[1]
            if _wire_crosses_box(
                downstream_x,
                tail_y,
                downstream_x,
                downstream_y,
                bx,
                by,
                SYMBOL_HALF_SIZE_MM,
            ):
                clearance_x = max(clearance_x, bx + (2 * SYMBOL_HALF_SIZE_MM))

    segs: list[WireSegment] = []
    if not math.isclose(upstream_x, trunk_x, abs_tol=0.01):
        segs.append(WireSegment(upstream_x, upstream_y, trunk_x, upstream_y))
    if not math.isclose(upstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(trunk_x, upstream_y, trunk_x, tail_y))

    if not math.isclose(pivot_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(pivot_x, pivot_y, pivot_x, tail_y))

    segs.append(WireSegment(pivot_x, tail_y, clearance_x, tail_y))
    if not math.isclose(downstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, tail_y, clearance_x, downstream_y))
    if not math.isclose(clearance_x, downstream_x, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, downstream_y, downstream_x, downstream_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    junctions: list[JunctionPoint] = []
    if not math.isclose(pivot_x, trunk_x, abs_tol=0.01):
        junctions.append(JunctionPoint(trunk_x, tail_y))
    return _simplify_wires(segs, protected_points=protected), junctions


def _compact_local_ground_cluster_route(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact local 3-pin ground cluster on one calm horizontal lane.

    This is intentionally narrow: it only handles small horizontal GND groups
    where a lane anchored on the lowest stub end can avoid the member body boxes
    and the old centroid-based cluster route would otherwise create several
    short non-stub cleanup fragments.
    """
    if len(cluster) != 3:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    if (max(xs) - min(xs)) > 80.0 or (max(ys) - min(ys)) > 30.0:
        return None
    if (max(xs) - min(xs)) < (max(ys) - min(ys)):
        return None

    lane_y = min(ys)
    lane_x0 = min(xs)
    lane_x1 = max(xs)
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}
    if positions is not None:
        cluster_positions = [
            pos for pin_ref, _anchor in cluster if (pos := positions.get(pin_ref.ref)) is not None
        ]
        for x, y in stub_ends:
            if math.isclose(y, lane_y, abs_tol=0.01):
                continue
            clearance_x = x
            for bx, by, _rotation in cluster_positions:
                if _wire_crosses_box(x, y, x, lane_y, bx, by, SYMBOL_HALF_SIZE_MM):
                    clearance_x = min(clearance_x, bx - (2 * SYMBOL_HALF_SIZE_MM))
            vertical_target_x[(x, y)] = _snap_grid(clearance_x)

        lane_x0 = min(lane_x0, *vertical_target_x.values())
        for bx, by, _rotation in cluster_positions:
            if _wire_crosses_box(
                lane_x0,
                lane_y,
                lane_x1,
                lane_y,
                bx,
                by,
                SYMBOL_HALF_SIZE_MM,
            ):
                return None

    segs = [WireSegment(lane_x0, lane_y, lane_x1, lane_y)]
    junctions: list[JunctionPoint] = []
    for x, y in stub_ends:
        target_x = vertical_target_x[(x, y)]
        if not math.isclose(x, target_x, abs_tol=0.01):
            segs.append(WireSegment(x, y, target_x, y))
        if not math.isclose(y, lane_y, abs_tol=0.01):
            segs.append(WireSegment(target_x, y, target_x, lane_y))
        junctions.append(JunctionPoint(target_x, lane_y))

    symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
    segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), junctions, (symbol_x, lane_y)


def _assign_connector_entry_grouped_lanes(
    grouped_names: list[str],
    *,
    axis: str,
    base_coordinate: float,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
    connector_entry_x_by_net: dict[str, float],
) -> dict[str, SharedLanePlan] | None:
    """Return the special connector-entry lane assignment for one grouped component."""
    connector_entry_names = [
        net_name for net_name in grouped_names if net_name in connector_entry_x_by_net
    ]
    if axis != "vertical" or len(connector_entry_names) != 1:
        return None

    entry_net = connector_entry_names[0]
    entry_coordinate = round(connector_entry_x_by_net[entry_net] + WIRE_EXTEND_MM, 2)
    if entry_coordinate >= round(base_coordinate, 2):
        return None

    planned_routes: dict[str, SharedLanePlan] = {entry_net: SharedLanePlan(axis, entry_coordinate)}
    remaining_names = [name for name in grouped_names if name != entry_net]
    for index, net_name in enumerate(remaining_names):
        offset = (index + 1.25) * WIRE_EXTEND_MM
        coordinate = round(base_coordinate + offset, 2)
        if len(remaining_names) == 1:
            shared_points = [
                point
                for point in endpoints_by_net[net_name]
                if math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            other_points = [
                point
                for point in endpoints_by_net[net_name]
                if not math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            if len(shared_points) == 2 and len(other_points) == 1:
                other_y = other_points[0][1]
                anchor_y = min(
                    (point[1] for point in shared_points),
                    key=lambda y: abs(y - other_y),
                )
                planned_routes[net_name] = SharedLanePlan(
                    axis,
                    coordinate,
                    min(anchor_y, other_y),
                    max(anchor_y, other_y),
                )
                continue
        planned_routes[net_name] = SharedLanePlan(axis, coordinate)
    return planned_routes


def _infer_bounded_local_lane_plan(
    endpoints: list[tuple[float, float]],
) -> SharedLanePlan | None:
    """Infer a bounded ladder lane for compact 3-pin nets without an exact shared axis.

    This covers full-layout cases where two nearby pins are visually aligned as a
    ladder rung but land on slightly different coordinates after symbol placement.
    """
    if len(endpoints) != 3:
        return None

    xs = [point[0] for point in endpoints]
    ys = [point[1] for point in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    indexed_points = list(enumerate(endpoints))
    horizontal_pairs = sorted(
        (
            abs(first[1][1] - second[1][1]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )
    vertical_pairs = sorted(
        (
            abs(first[1][0] - second[1][0]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )

    horizontal_gap, horizontal_i, horizontal_j = horizontal_pairs[0]
    vertical_gap, vertical_i, vertical_j = vertical_pairs[0]
    if min(horizontal_gap, vertical_gap) > WIRE_EXTEND_MM:
        return None

    if x_span >= y_span:
        pair_i, pair_j = horizontal_i, horizontal_j
        axis = "horizontal"
        third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
        pair_points = [endpoints[pair_i], endpoints[pair_j]]
        third_point = endpoints[third_index]
        coordinate = min(
            (pair_points[0][1], pair_points[1][1]),
            key=lambda value: abs(value - third_point[1]),
        )
        return SharedLanePlan(
            axis,
            round(coordinate, 2),
            round(min(pair_points[0][0], pair_points[1][0]), 2),
            round(max(pair_points[0][0], pair_points[1][0]), 2),
        )

    pair_i, pair_j = vertical_i, vertical_j
    axis = "vertical"
    third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
    pair_points = [endpoints[pair_i], endpoints[pair_j]]
    third_point = endpoints[third_index]
    coordinate = min(
        (pair_points[0][0], pair_points[1][0]),
        key=lambda value: abs(value - third_point[0]),
    )
    return SharedLanePlan(
        axis,
        round(coordinate, 2),
        round(min(pair_points[0][1], pair_points[1][1]), 2),
        round(max(pair_points[0][1], pair_points[1][1]), 2),
    )


def _collect_local_ladder_candidates(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor],
) -> tuple[
    dict[str, tuple[float, float, float, float]],
    dict[str, int],
    dict[str, tuple[str, float]],
    dict[str, list[tuple[float, float]]],
    dict[str, float],
]:
    """Collect compact local nets that may participate in ladder routing."""
    candidate_boxes: dict[str, tuple[float, float, float, float]] = {}
    candidate_degree: dict[str, int] = {}
    shared_lane_by_net: dict[str, tuple[str, float]] = {}
    endpoints_by_net: dict[str, list[tuple[float, float]]] = {}
    connector_entry_x_by_net: dict[str, float] = {}

    for net in ir.nets:
        if _is_power_net_name(net.name):
            continue

        endpoints: list[tuple[float, float]] = []
        for pin in net.pins:
            anchor = pin_anchors.get((pin.ref, pin.pin))
            if anchor is None:
                endpoints = []
                break
            endpoints.append(_stub_end(anchor.x, anchor.y, anchor.angle))

        if len(endpoints) < 2 or len(endpoints) > 3:
            continue
        if not _is_local_ladder_net(endpoints):
            continue

        endpoints_by_net[net.name] = endpoints
        xs = [point[0] for point in endpoints]
        ys = [point[1] for point in endpoints]
        candidate_boxes[net.name] = (min(xs), max(xs), min(ys), max(ys))
        candidate_degree[net.name] = len(endpoints)

        if any(_component_type(pin.ref) == "connector" for pin in net.pins):
            connector_entry_x_by_net[net.name] = min(xs)

        lane = _preferred_shared_lane(endpoints)
        if lane is not None:
            shared_lane_by_net[net.name] = lane

    return (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        connector_entry_x_by_net,
    )


def _build_ladder_adjacency(
    candidate_boxes: dict[str, tuple[float, float, float, float]],
) -> dict[str, set[str]]:
    """Connect compact local nets whose bounding boxes nearly touch."""
    adjacency: dict[str, set[str]] = {name: set() for name in candidate_boxes}
    names = sorted(candidate_boxes)
    for index, net_name in enumerate(names):
        for other_name in names[index + 1 :]:
            if not _boxes_touch_or_overlap(candidate_boxes[net_name], candidate_boxes[other_name]):
                continue
            adjacency[net_name].add(other_name)
            adjacency[other_name].add(net_name)
    return adjacency


def _connected_ladder_component(
    start_name: str,
    adjacency: dict[str, set[str]],
    visited: set[str],
) -> list[str]:
    """Return one connected component from the ladder-neighborhood graph."""
    stack = [start_name]
    component: list[str] = []
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        component.append(current)
        stack.extend(sorted(adjacency[current] - visited))
    return component


def _lane_center(
    net_name: str,
    axis: str,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
) -> float:
    """Return the orthogonal-axis center used to order parallel local lanes."""
    coordinates = endpoints_by_net[net_name]
    if axis == "vertical":
        return sum(point[1] for point in coordinates) / len(coordinates)
    return sum(point[0] for point in coordinates) / len(coordinates)


def _assign_grouped_ladder_lanes(
    component: list[str],
    *,
    context: LadderLanePlannerContext,
) -> dict[str, SharedLanePlan]:
    """Assign distinct parallel lanes to all 3-pin nets in one neighborhood."""
    grouped: dict[tuple[str, float], list[str]] = {}
    for net_name in component:
        if context.candidate_degree.get(net_name) != 3:
            continue
        lane = context.shared_lane_by_net.get(net_name)
        if lane is None:
            continue
        axis, base_coordinate = lane
        grouped.setdefault((axis, round(base_coordinate, 2)), []).append(net_name)

    planned_routes: dict[str, SharedLanePlan] = {}
    skipped_single_lane_nets: set[str] = set()
    for (axis, base_coordinate), grouped_names in grouped.items():
        grouped_names.sort(
            key=lambda net_name: _lane_center(net_name, axis, context.endpoints_by_net)
        )
        if len(grouped_names) == 1:
            endpoints = context.endpoints_by_net[grouped_names[0]]
            single_lane_plan = _plan_single_grouped_ladder_lane(
                axis=axis,
                base_coordinate=base_coordinate,
                endpoints=endpoints,
                heuristic_policy=context.heuristic_policy,
            )
            if single_lane_plan is None:
                skipped_single_lane_nets.add(grouped_names[0])
                continue
            planned_routes[grouped_names[0]] = single_lane_plan
            continue

        connector_group_routes = _assign_connector_entry_grouped_lanes(
            grouped_names,
            axis=axis,
            base_coordinate=base_coordinate,
            endpoints_by_net=context.endpoints_by_net,
            connector_entry_x_by_net=context.connector_entry_x_by_net,
        )
        if connector_group_routes is not None:
            planned_routes.update(connector_group_routes)
            continue

        for index, net_name in enumerate(grouped_names):
            offset = (index - (len(grouped_names) - 1) / 2) * WIRE_EXTEND_MM
            planned_routes[net_name] = SharedLanePlan(axis, round(base_coordinate + offset, 2))

    for net_name in component:
        if net_name in planned_routes or context.candidate_degree.get(net_name) != 3:
            continue
        if net_name in skipped_single_lane_nets:
            continue
        inferred_plan = _infer_bounded_local_lane_plan(context.endpoints_by_net[net_name])
        if inferred_plan is None:
            continue
        if context.heuristic_policy.should_skip_shared_lane_plan(
            context.endpoints_by_net[net_name],
            inferred_plan,
        ):
            continue
        planned_routes[net_name] = inferred_plan

    return planned_routes


def _plan_local_ladder_routes(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor | tuple[float, float, float]] | None = None,
    *,
    pin_endpoints: Mapping[tuple[str, str], tuple[float, float, float]] | None = None,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> dict[str, SharedLanePlan]:
    """Detect nearby small-signal net neighborhoods that should share ladder-style routing.

    The detector intentionally works across multiple nets. A 3-pin local net is
    considered a ladder candidate only when it lives in a compact neighborhood
    with at least one adjacent local 2-pin or 3-pin signal net.
    """
    resolved_anchors = _resolve_pin_anchors(
        pin_endpoints or {},
        _coerce_pin_anchor_map(pin_anchors),
    )
    (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        connector_entry_x_by_net,
    ) = _collect_local_ladder_candidates(ir, resolved_anchors)
    planner_context = LadderLanePlannerContext(
        candidate_degree=candidate_degree,
        shared_lane_by_net=shared_lane_by_net,
        endpoints_by_net=endpoints_by_net,
        connector_entry_x_by_net=connector_entry_x_by_net,
        heuristic_policy=heuristic_policy,
    )
    adjacency = _build_ladder_adjacency(candidate_boxes)

    visited: set[str] = set()
    planned_routes: dict[str, SharedLanePlan] = {}

    for net_name in sorted(candidate_boxes):
        if net_name in visited:
            continue

        component = _connected_ladder_component(net_name, adjacency, visited)
        if len(component) < 2:
            continue

        planned_routes.update(
            _assign_grouped_ladder_lanes(
                component,
                context=planner_context,
            )
        )

    return planned_routes


def _chain_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact 3-pin net as a simple ordered chain.

    Endpoints are ordered along the dominant axis and connected pairwise with
    direct orthogonal segments. This avoids the boxy mini-bus look that the
    generic spine route can produce for short local support nets.
    """
    if len(endpoints) < 2:
        return [], []

    xs = [e[0] for e in endpoints]
    ys = [e[1] for e in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    if x_span >= y_span:
        ordered = sorted(endpoints, key=lambda point: (point[0], point[1]))
    else:
        ordered = sorted(endpoints, key=lambda point: (point[1], point[0]))

    segs: list[WireSegment] = []
    for index in range(len(ordered) - 1):
        x1, y1 = ordered[index]
        x2, y2 = ordered[index + 1]
        segs.extend(_l_route(x1, y1, x2, y2))

    protected = {(round(x, 2), round(y, 2)) for x, y in ordered}
    return _simplify_wires(segs, protected_points=protected), []


def _route_length(segments: list[WireSegment]) -> float:
    """Return total Manhattan wire length for *segments*."""
    return sum(_manhattan(seg.x1, seg.y1, seg.x2, seg.y2) for seg in segments)


def _prefer_chain_route(endpoints: list[tuple[float, float]]) -> bool:
    """Return True when a local 3-pin net reads better as a chain than a spine."""
    if len(endpoints) != 3:
        return False

    chain_segs, _ = _chain_route(endpoints)
    spine_segs, _ = _spine_route(endpoints)
    chain_length = _route_length(chain_segs)
    spine_length = _route_length(spine_segs)

    if chain_length < spine_length - 0.01:
        return True

    return math.isclose(chain_length, spine_length, abs_tol=0.01) and len(chain_segs) <= len(
        spine_segs
    )


def _simplify_wires(  # noqa: PLR0912, PLR0915
    wires: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> list[WireSegment]:
    """Simplify wire routing by merging consecutive colinear segments.

    Reduces visual clutter by combining wire segments that lie on the same
    horizontal or vertical line into longer, direct wire runs.  This
    eliminates unnecessary intermediate junctions and shortens the total
    wire segment count (Phase 6.1).

    Parameters
    ----------
    wires:
        Input wire segments; order-independent.
    protected_points:
        Optional set of (x, y) coordinates that must be preserved as wire
        start/end points.  Typically pin endpoints.  Segments will not be
        merged if doing so would eliminate a protected point.

    Returns
    -------
    list[WireSegment]
        Simplified wire list with consecutive colinear segments merged,
        preserving all protected connection points and junction points.

    Notes
    -----
    The algorithm iteratively merges pairs of segments that:

    * Share at least one endpoint.
    * Lie on the same horizontal line (y₁ = y₂) or vertical line (x₁ = x₂).
    * The shared endpoint is NOT in the protected set.
    * The shared endpoint is NOT a junction (degree ≥ 3).

    Merging continues until no more pairs can be combined.  The resulting
    list typically contains fewer short wire segments and a cleaner visual
    appearance.
    """
    if not wires:
        return []

    def _segment_key(seg: WireSegment) -> tuple[tuple[float, float], tuple[float, float]]:
        p1 = (round(seg.x1, 2), round(seg.y1, 2))
        p2 = (round(seg.x2, 2), round(seg.y2, 2))
        return (p1, p2) if p1 <= p2 else (p2, p1)

    def _is_zero_length(seg: WireSegment) -> bool:
        return math.isclose(seg.x1, seg.x2, abs_tol=0.01) and math.isclose(
            seg.y1, seg.y2, abs_tol=0.01
        )

    normalized: list[WireSegment] = []
    seen_segments: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    for seg in wires:
        if _is_zero_length(seg):
            continue
        key = _segment_key(seg)
        if key in seen_segments:
            continue
        seen_segments.add(key)
        normalized.append(seg)

    if protected_points is None:
        protected_points = set()

    segments = normalized
    changed = True

    while changed:
        changed = False
        new_segments = []
        used = set()

        # Build a degree map (how many segments touch each point)
        degree: dict[tuple[float, float], int] = {}
        for seg in segments:
            p1 = (round(seg.x1, 2), round(seg.y1, 2))
            p2 = (round(seg.x2, 2), round(seg.y2, 2))
            degree[p1] = degree.get(p1, 0) + 1
            degree[p2] = degree.get(p2, 0) + 1

        for i, seg1 in enumerate(segments):
            if i in used:
                continue

            # Try to find a mergeable partner
            merged = False
            for j in range(i + 1, len(segments)):
                if j in used:
                    continue

                seg2 = segments[j]

                # Find shared endpoints (rounded to 0.01 mm for floating-point stability)
                seg1_endpoints = {
                    (round(seg1.x1, 2), round(seg1.y1, 2)),
                    (round(seg1.x2, 2), round(seg1.y2, 2)),
                }
                seg2_endpoints = {
                    (round(seg2.x1, 2), round(seg2.y1, 2)),
                    (round(seg2.x2, 2), round(seg2.y2, 2)),
                }
                shared = seg1_endpoints & seg2_endpoints

                if not shared:
                    continue

                # Don't merge if shared endpoint is protected
                if any(pt in protected_points for pt in shared):
                    continue

                # Don't merge if shared endpoint is a junction (degree ≥ 3)
                if any(degree.get(pt, 0) >= 3 for pt in shared):
                    continue

                # Check if segments are colinear
                # Horizontal segments (both have y1 == y2)
                if seg1.y1 == seg1.y2 and seg2.y1 == seg2.y2 and seg1.y1 == seg2.y1:
                    # Get all x coordinates
                    xs = sorted([seg1.x1, seg1.x2, seg2.x1, seg2.x2])
                    # Merge: use min and max x coordinates
                    new_segments.append(WireSegment(xs[0], seg1.y1, xs[-1], seg1.y1))
                    used.add(i)
                    used.add(j)
                    merged = True
                    changed = True
                    break

                # Vertical segments (both have x1 == x2)
                elif seg1.x1 == seg1.x2 and seg2.x1 == seg2.x2 and seg1.x1 == seg2.x1:
                    # Get all y coordinates
                    ys = sorted([seg1.y1, seg1.y2, seg2.y1, seg2.y2])
                    # Merge: use min and max y coordinates
                    new_segments.append(WireSegment(seg1.x1, ys[0], seg1.x1, ys[-1]))
                    used.add(i)
                    used.add(j)
                    merged = True
                    changed = True
                    break

            if not merged:
                new_segments.append(seg1)
                used.add(i)

        segments = new_segments

    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def route_nets(  # noqa: PLR0912, PLR0913, PLR0915
    *,
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
    pin_anchors: Mapping[tuple[str, str], PinAnchor] | None = None,
    use_bus: bool = True,
    tiers: dict[str, int] | None = None,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    policy: LabelPolicy = DEFAULT_LABEL_POLICY,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
    strict: bool = False,
) -> NetRouting:
    """Compute routing decisions for all nets in *ir*.

    Strategy per net:

    * **Power net** (`GND`, `VCC`, etc.) — emit a
      :class:`GlobalLabelPlacement` (power shape) per pin; avoids spaghetti
      on high-fanout rails.
    * **Direct route** — exactly 2 known pins.  Routing condition (Rule §4):

      * When *tiers* is provided: direct route is used only when the
        tier distance between the two pins is ≤ 1 **and** the Manhattan
        distance is ≤ :data:`MAX_DIRECT_DIST_MM`.  The tier-distance guard
        prevents cross-tier spaghetti; the Manhattan cap prevents wire runs
        so long they become unreadable.
      * When *tiers* is ``None`` (default): the tier guard is skipped;
        only the Manhattan-distance cap (:data:`MAX_DIRECT_DIST_MM`) applies.

      No net labels are emitted for direct routes.
    * **Hub route** — 3–:data:`_HUB_MAX_DEGREE` known pins, all endpoints
      reachable: route spokes to a centroid hub; add a
      :class:`JunctionPoint` at the hub.  No net labels emitted.
      When *use_bus* is ``True`` the hub strategy is replaced by
      :func:`_spine_route` which draws a straight spine wire with
      T-junction taps for a cleaner bus-style visual.
    * **Global-label route** — high-degree non-power nets (> ``_HUB_MAX_DEGREE``):
      emit one :class:`GlobalLabelPlacement` per pin (same result as power
      nets but using the ``passive`` shape so it's visually distinct).
    * **Label route** — fallback for anything else: one stub wire + one
      local net label per pin.

    Unknown pins (absent from the resolved anchor set) always fall back to an
    off-canvas position with local labels.

    Parameters
    ----------
    ir:
        Circuit IR with nets and components.
    pin_endpoints:
        ``{(ref, pin): (x, y, angle)}`` map produced by the schematic builder.
    pin_anchors:
        Optional ``{(ref, pin): PinAnchor}`` map carrying explicit placed-unit
        anchor ownership plus endpoint geometry. When supplied, router helpers
        use this richer model as their source of truth and only fall back to
        *pin_endpoints* for legacy callers.
    use_bus:
        When ``True``, replace centroid-hub routing for multi-pin local nets
        with spine-style routing (:func:`_spine_route`).  Produces a cleaner
        "one long wire with taps" visual instead of star-shaped spokes.
    tiers:
        Optional ``{ref: tier_index}`` map.  When supplied, tier distance
        governs the direct-route decision instead of Manhattan distance.
    positions:
        Optional ``{ref: (x, y, rotation)}`` layout position map.  When
        supplied, wire segments that cross component bounding boxes are
        automatically rerouted via :func:`detect_body_crossings`.

    policy:
        Label deduplication policy; controls how many local and global
        labels are emitted per net.  Defaults to
        :data:`DEFAULT_LABEL_POLICY` (2 local labels per net, 4 global
        labels per high-degree net).
    heuristic_policy:
        Analog-specific routing policy that governs compact output-tail and
        local-ground-cluster special cases while leaving generic routing
        strategies unchanged.
    strict:
        When ``True``, unknown pin endpoints are treated as an error instead
        of falling back to off-canvas stub+label/symbol routing.

    Returns a :class:`NetRouting` with all decisions.
    """
    routing = NetRouting()
    fallback_y = -1500.0
    resolved_anchors = _resolve_pin_anchors(pin_endpoints, pin_anchors)
    ladder_routes = _plan_local_ladder_routes(
        ir,
        resolved_anchors,
        heuristic_policy=heuristic_policy,
    )

    for net in sorted(ir.nets, key=lambda n: n.name):
        pins = sorted(net.pins, key=lambda p: (p.ref, p.pin))
        known = [
            (
                p,
                (
                    resolved_anchors[(p.ref, p.pin)].x,
                    resolved_anchors[(p.ref, p.pin)].y,
                    resolved_anchors[(p.ref, p.pin)].angle,
                ),
            )
            for p in pins
            if (p.ref, p.pin) in resolved_anchors
        ]
        unknown = [p for p in pins if (p.ref, p.pin) not in resolved_anchors]

        if strict and unknown:
            raise UserError(
                f"Cannot route net '{net.name}' with unknown pin endpoints in strict mode",
                code=ErrorCode.PIN_INVALID,
                details={
                    "net_name": net.name,
                    "missing_pins": [
                        {
                            "ref": pin.ref,
                            "pin": pin.pin,
                        }
                        for pin in unknown
                    ],
                },
            )

        is_power = _is_power_net_name(net.name)
        strategy = "local_labels"
        heuristic_override: str | None = None

        # ----------------------------------------------------------------
        # Power nets → cluster-based power symbol placement (Phase 5.1)
        # ----------------------------------------------------------------
        if is_power:
            used_compact_ground_cluster = False
            # Cluster known pins by proximity to share power symbols
            clusters = _cluster_power_pins(known, radius=_POWER_CLUSTER_RADIUS_MM)

            for cluster in clusters:
                if len(cluster) == 1:
                    # Single pin: traditional stub + symbol
                    pin_ref, (wx, wy, wa) = cluster[0]
                    ex, ey = _stub_end(wx, wy, wa)
                    label_angle = _power_label_angle_for_pin(wa)
                    symbol_angle = _power_symbol_angle(net.name, label_angle)
                    px, py = _offset_point_along_angle(
                        ex,
                        ey,
                        label_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.wires.append(WireSegment(ex, ey, px, py))
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, symbol_angle)
                    )
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                else:
                    # Multiple pins: compute cluster centroid for shared symbol
                    compact_ground_cluster = heuristic_policy.route_compact_power_cluster(
                        net_name=net.name,
                        cluster=cluster,
                        positions=positions,
                    )
                    if compact_ground_cluster is not None:
                        used_compact_ground_cluster = True
                        cluster_segs, cluster_junctions, (px, py) = compact_ground_cluster
                        routing.wires.extend(cluster_segs)
                        routing.junctions.extend(cluster_junctions)
                        routing.bind_markers.extend(
                            BindMarker(pin_ref.ref, pin_ref.pin, net.name)
                            for pin_ref, _anchor in cluster
                        )
                        routing.power_symbols.append(
                            PowerSymbolPlacement(
                                net.name,
                                px,
                                py,
                                _power_symbol_angle(net.name, 0),
                            )
                        )
                        continue

                    cx = sum(cpx for _, (cpx, _, _) in cluster) / len(cluster)
                    cy = sum(cpy for _, (_, cpy, _) in cluster) / len(cluster)

                    # Wire each pin to centroid via hub routing
                    stub_ends: list[tuple[float, float]] = []
                    for pin_ref, (wx, wy, wa) in cluster:
                        ex, ey = _stub_end(wx, wy, wa)
                        routing.wires.append(WireSegment(wx, wy, ex, ey))
                        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                        stub_ends.append((ex, ey))

                    power_angle = _power_cluster_angle(stub_ends)
                    symbol_angle = _power_symbol_angle(net.name, power_angle)
                    px, py = _offset_point_along_angle(
                        cx,
                        cy,
                        power_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )

                    # Place ONE power symbol beyond the cluster centroid so the
                    # visible net text does not sit on top of nearby wires.
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, symbol_angle)
                    )

                    # Add centroid as hub target
                    stub_ends.append((cx, cy))

                    # Route stubs to centroid via spine/hub
                    if use_bus:
                        hub_segs, hub_junctions = _spine_route(stub_ends)
                    else:
                        hub_segs, hub_junctions = _hub_route(stub_ends)
                    routing.wires.extend(hub_segs)
                    routing.junctions.extend(hub_junctions)
                    routing.wires.append(WireSegment(cx, cy, px, py))

            # Off-canvas fallback for power pins with no known endpoint
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                power_angle = _power_symbol_angle(net.name, 0)
                px, py = _offset_point_along_angle(
                    ex,
                    ey,
                    power_angle,
                    _POWER_LABEL_CLEARANCE_MM,
                )
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.wires.append(WireSegment(ex, ey, px, py))
                routing.power_symbols.append(PowerSymbolPlacement(net.name, px, py, power_angle))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification="power",
                    strategy="power_symbols",
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=(
                        "compact_local_ground_cluster" if used_compact_ground_cluster else None
                    ),
                )
            )
            continue

        # ----------------------------------------------------------------
        # 2-pin direct route
        # ----------------------------------------------------------------
        routed_directly = False
        if len(known) == 2 and not unknown:
            p0, (wx0, wy0, wa0) = known[0]
            p1, (wx1, wy1, wa1) = known[1]
            ex0, ey0 = _stub_end(wx0, wy0, wa0)
            ex1, ey1 = _stub_end(wx1, wy1, wa1)
            manhattan = _manhattan(ex0, ey0, ex1, ey1)
            if tiers is not None:
                # Rule §4: tier distance ≤ 1 guards signal-flow adjacency;
                # manhattan cap (MAX_DIRECT_DIST_MM) guards physical wire length,
                # matching the behaviour of the non-tier path.
                tdist = _tier_distance(p0.ref, p1.ref, tiers)
                can_direct = manhattan <= MAX_DIRECT_DIST_MM and (
                    tdist <= 1 or _is_connector_passive_edge(p0.ref, p1.ref)
                )
            else:
                can_direct = manhattan <= MAX_DIRECT_DIST_MM
            if can_direct:
                routing.wires.append(WireSegment(wx0, wy0, ex0, ey0))
                routing.wires.append(WireSegment(wx1, wy1, ex1, ey1))
                routing.wires.extend(_l_route(ex0, ey0, ex1, ey1))
                routing.bind_markers.append(BindMarker(p0.ref, p0.pin, net.name))
                routing.bind_markers.append(BindMarker(p1.ref, p1.pin, net.name))
                routed_directly = True
                strategy = "direct"

        if routed_directly:
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification="signal",
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Hub route (3 – _HUB_MAX_DEGREE known, no unknown pins)
        # ----------------------------------------------------------------
        if 3 <= len(known) <= _HUB_MAX_DEGREE and not unknown:
            stub_ends = []
            for pin_ref, (wx, wy, wa) in known:
                ex, ey = _stub_end(wx, wy, wa)
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                stub_ends.append((ex, ey))
            compact_tail_plan = _infer_bounded_local_lane_plan(stub_ends)
            if use_bus and net.name in ladder_routes:
                lane_plan = ladder_routes[net.name]
                hub_segs, hub_junctions = _shared_lane_route(
                    stub_ends,
                    axis=lane_plan.axis,
                    coordinate=lane_plan.coordinate,
                    min_bound=lane_plan.min_orthogonal,
                    max_bound=lane_plan.max_orthogonal,
                )
                strategy = "shared_lane"
            elif (
                use_bus
                and (
                    compact_tail_route := heuristic_policy.route_compact_signal_tail(
                        stub_ends,
                        inferred_plan=compact_tail_plan,
                        positions=positions,
                    )
                )
                is not None
            ):
                hub_segs, hub_junctions = compact_tail_route
                strategy = "compact_signal_tail"
                heuristic_override = "compact_output_tail"
            elif use_bus and _prefer_chain_route(stub_ends):
                hub_segs, hub_junctions = _chain_route(stub_ends)
                strategy = "chain"
            elif use_bus:
                hub_segs, hub_junctions = _spine_route(stub_ends)
                strategy = "spine"
            else:
                hub_segs, hub_junctions = _hub_route(stub_ends)
                strategy = "hub"
            routing.wires.extend(hub_segs)
            routing.junctions.extend(hub_junctions)
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification="signal",
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=heuristic_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # High-degree non-power → global label per pin (capped by policy)
        # ----------------------------------------------------------------
        if len(known) > _HUB_MAX_DEGREE:
            strategy = "global_labels"
            global_label_count = 0
            for pin_ref, (wx, wy, wa) in known:
                ex, ey = _stub_end(wx, wy, wa)
                label_angle = int((wa + 180) % 360)
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                if global_label_count < policy.max_global_labels_per_net:
                    routing.global_labels.append(
                        GlobalLabelPlacement(net.name, ex, ey, label_angle)
                    )
                    global_label_count += 1
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, 0))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification="signal",
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Label route (classic fallback: stub + local net label per pin)
        # Labels are capped at policy.max_labels_per_net to reduce clutter.
        # Stub wires and bind markers are always emitted (every pin).
        # ----------------------------------------------------------------
        label_count = 0
        for pin_ref, (wx, wy, wa) in known:
            ex, ey = _stub_end(wx, wy, wa)
            label_angle = int((wa + 180) % 360)
            routing.wires.append(WireSegment(wx, wy, ex, ey))
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            if label_count < policy.max_labels_per_net:
                routing.labels.append(NetLabel(net.name, ex, ey, label_angle))
                label_count += 1

        for pin_ref in unknown:
            wx, wy = -1200.0, fallback_y
            ex, ey = wx + WIRE_EXTEND_MM, wy
            routing.wires.append(WireSegment(wx, wy, ex, ey))
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            # Off-canvas unknown pins always get a label regardless of policy
            # (they have no physical wire connection; the label IS their connection).
            routing.labels.append(NetLabel(net.name, ex, ey, 0))
            fallback_y -= 10.0

        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification="signal",
                strategy=strategy,
                pin_count=len(pins),
                known_pin_count=len(known),
                unknown_pin_count=len(unknown),
                use_bus=use_bus,
            )
        )

    # ----------------------------------------------------------------
    # Body-crossing guard (Rule §4.3)
    # ----------------------------------------------------------------
    if positions is not None:
        routing.wires = detect_body_crossings(routing.wires, positions)

    # ----------------------------------------------------------------
    # Wire simplification pass (Phase 6.1)
    # ----------------------------------------------------------------
    # Protect pin endpoints from being merged away; they are required
    # connection points for electrical continuity.  Round to 2 decimal
    # places (0.01 mm precision) to avoid floating-point comparison issues.
    protected = {(round(x, 2), round(y, 2)) for x, y, _angle in pin_endpoints.values()}
    routing.wires = _simplify_wires(routing.wires, protected_points=protected)

    oriented_wires: list[WireSegment] = []
    for seg in routing.wires:
        start = (round(seg.x1, 2), round(seg.y1, 2))
        end = (round(seg.x2, 2), round(seg.y2, 2))
        if end in protected and start not in protected:
            oriented_wires.append(WireSegment(seg.x2, seg.y2, seg.x1, seg.y1))
        else:
            oriented_wires.append(seg)
    routing.wires = oriented_wires

    return routing


def write_routing(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    routing: NetRouting,
    new_uuid: Callable[[], str],
    stats: dict[str, int],
    symbols_dir: Path | None = None,
    project_name: str = "project",
    strict: bool = False,
) -> None:
    """Emit *routing* decisions into the schematic document *doc*.

    Writes wire segments (``(wire …)``), local net labels (``(label …)``),
    global labels (``(global_label …)``), power symbols (``(symbol …)``
    with ``lib_id`` from the ``power:`` library), junction markers
    (``(junction …)``), and bind markers (hidden ``(text …)`` nodes).
    Updates *stats* counters:
    ``wires``, ``labels``, ``global_labels``, ``power_symbols``,
    ``junctions``, ``binding_markers``.

    Parameters
    ----------
    symbols_dir:
        Directory containing ``.kicad_sym`` files.  ``None`` uses the system
        default (``/usr/share/kicad/symbols``).  Used only when embedding
        power-library symbols.
    project_name:
        KiCad project name embedded in power-symbol ``(instances …)``
        annotations.  Defaults to ``"project"``.
    strict:
        When ``True``, missing power-library symbols are treated as errors
        instead of falling back to ``global_label`` insertion.
    """
    for seg in routing.wires:
        doc.add_wire(seg.x1, seg.y1, seg.x2, seg.y2, new_uuid())
        stats["wires"] += 1

    for lbl in routing.labels:
        doc.add_label(lbl.name, lbl.x, lbl.y, new_uuid(), angle=lbl.angle)
        stats["labels"] += 1

    for glbl in routing.global_labels:
        doc.add_global_label(
            glbl.name, glbl.x, glbl.y, new_uuid(), angle=glbl.angle, shape="passive"
        )
        stats["global_labels"] = stats.get("global_labels", 0) + 1

    for idx, ps in enumerate(routing.power_symbols):
        sym_uuid = new_uuid()
        pin_uuid = new_uuid()
        ref = f"#PWR{idx + 1:02d}"
        success = doc.add_power_symbol(
            ps.net_name,
            ps.x,
            ps.y,
            sym_uuid,
            pin_uuid,
            ref,
            project_name,
            angle=ps.angle,
            symbols_dir=symbols_dir,
        )
        if not success:
            if strict:
                raise UserError(
                    f"Power symbol not found in library: power:{ps.net_name}",
                    code=ErrorCode.SYMBOL_NOT_FOUND,
                    details={
                        "symbol": f"power:{ps.net_name}",
                        "net_name": ps.net_name,
                    },
                )
            # Fallback: global_label when power symbol is not in the library.
            fallback_x, fallback_y, fallback_angle = _fallback_power_label_position(
                ps.x,
                ps.y,
                ps.angle,
            )
            if not (
                math.isclose(ps.x, fallback_x, abs_tol=0.01)
                and math.isclose(ps.y, fallback_y, abs_tol=0.01)
            ):
                doc.add_wire(ps.x, ps.y, fallback_x, fallback_y, new_uuid())
                stats["wires"] += 1
            doc.add_global_label(
                ps.net_name,
                fallback_x,
                fallback_y,
                new_uuid(),
                angle=fallback_angle,
                shape="passive",
            )
            stats["global_labels"] = stats.get("global_labels", 0) + 1
        else:
            stats["power_symbols"] = stats.get("power_symbols", 0) + 1

    for jpt in routing.junctions:
        doc.add_junction(jpt.x, jpt.y, new_uuid())
        stats["junctions"] = stats.get("junctions", 0) + 1

    for idx, bm in enumerate(routing.bind_markers):
        binding_text = "OpenClaw:bind=" + json.dumps(
            {"ref": bm.ref, "pin": bm.pin, "net_name": bm.net_name},
            separators=(",", ":"),
            sort_keys=True,
        )
        doc.add_text(binding_text, -1200.0, -1500.0 - 10.0 * idx, hidden=True)
        stats["binding_markers"] += 1
