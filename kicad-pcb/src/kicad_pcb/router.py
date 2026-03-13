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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR, PinRefIR
    from .sch_doc import SchematicDoc

from .component_types import component_type as _component_type
from .component_types import is_power_net as _base_is_power_net_name
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


def _is_power_net_name(net_name: str) -> bool:
    """Return True for routed power rails, including VPLUS/VMINUS aliases."""
    upper_name = net_name.upper()
    return _base_is_power_net_name(net_name) or upper_name.startswith(
        ("VPLUS", "VMINUS", "VPOS", "VNEG")
    )


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


@dataclass
class NetRouting:
    """Aggregated routing decisions for a whole schematic."""

    wires: list[WireSegment] = field(default_factory=list)
    labels: list[NetLabel] = field(default_factory=list)
    global_labels: list[GlobalLabelPlacement] = field(default_factory=list)
    power_symbols: list[PowerSymbolPlacement] = field(default_factory=list)
    junctions: list[JunctionPoint] = field(default_factory=list)
    bind_markers: list[BindMarker] = field(default_factory=list)


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


def _collect_local_ladder_candidates(
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
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
            endpoint = pin_endpoints.get((pin.ref, pin.pin))
            if endpoint is None:
                endpoints = []
                break
            endpoints.append(_stub_end(*endpoint))

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
    candidate_degree: dict[str, int],
    shared_lane_by_net: dict[str, tuple[str, float]],
    endpoints_by_net: dict[str, list[tuple[float, float]]],
    connector_entry_x_by_net: dict[str, float],
) -> dict[str, SharedLanePlan]:
    """Assign distinct parallel lanes to all 3-pin nets in one neighborhood."""
    grouped: dict[tuple[str, float], list[str]] = {}
    for net_name in component:
        if candidate_degree.get(net_name) != 3:
            continue
        lane = shared_lane_by_net.get(net_name)
        if lane is None:
            continue
        axis, base_coordinate = lane
        grouped.setdefault((axis, round(base_coordinate, 2)), []).append(net_name)

    planned_routes: dict[str, SharedLanePlan] = {}
    for (axis, base_coordinate), grouped_names in grouped.items():
        grouped_names.sort(key=lambda net_name: _lane_center(net_name, axis, endpoints_by_net))
        if len(grouped_names) == 1:
            planned_routes[grouped_names[0]] = SharedLanePlan(axis, base_coordinate)
            continue

        connector_entry_names = [
            net_name for net_name in grouped_names if net_name in connector_entry_x_by_net
        ]
        if axis == "vertical" and len(connector_entry_names) == 1:
            entry_net = connector_entry_names[0]
            entry_coordinate = round(connector_entry_x_by_net[entry_net] + WIRE_EXTEND_MM, 2)
            if entry_coordinate < round(base_coordinate, 2):
                planned_routes[entry_net] = SharedLanePlan(axis, entry_coordinate)
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
                continue

        for index, net_name in enumerate(grouped_names):
            offset = (index - (len(grouped_names) - 1) / 2) * WIRE_EXTEND_MM
            planned_routes[net_name] = SharedLanePlan(axis, round(base_coordinate + offset, 2))

    return planned_routes


def _plan_local_ladder_routes(
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
) -> dict[str, SharedLanePlan]:
    """Detect nearby small-signal net neighborhoods that should share ladder-style routing.

    The detector intentionally works across multiple nets. A 3-pin local net is
    considered a ladder candidate only when it lives in a compact neighborhood
    with at least one adjacent local 2-pin or 3-pin signal net.
    """
    (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        connector_entry_x_by_net,
    ) = _collect_local_ladder_candidates(ir, pin_endpoints)
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
                candidate_degree=candidate_degree,
                shared_lane_by_net=shared_lane_by_net,
                endpoints_by_net=endpoints_by_net,
                connector_entry_x_by_net=connector_entry_x_by_net,
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
    use_bus: bool = True,
    tiers: dict[str, int] | None = None,
    positions: dict[str, tuple[float, float, float | None]] | None = None,
    policy: LabelPolicy = DEFAULT_LABEL_POLICY,
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

    Unknown pins (absent from *pin_endpoints*) always fall back to an
    off-canvas position with local labels.

    Parameters
    ----------
    ir:
        Circuit IR with nets and components.
    pin_endpoints:
        ``{(ref, pin): (x, y, angle)}`` map produced by the schematic builder.
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
    strict:
        When ``True``, unknown pin endpoints are treated as an error instead
        of falling back to off-canvas stub+label/symbol routing.

    Returns a :class:`NetRouting` with all decisions.
    """
    routing = NetRouting()
    fallback_y = -1500.0
    ladder_routes = _plan_local_ladder_routes(ir, pin_endpoints)

    for net in sorted(ir.nets, key=lambda n: n.name):
        pins = sorted(net.pins, key=lambda p: (p.ref, p.pin))
        known = [
            (p, pin_endpoints[(p.ref, p.pin)]) for p in pins if (p.ref, p.pin) in pin_endpoints
        ]
        unknown = [p for p in pins if (p.ref, p.pin) not in pin_endpoints]

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

        # ----------------------------------------------------------------
        # Power nets → cluster-based power symbol placement (Phase 5.1)
        # ----------------------------------------------------------------
        if is_power:
            # Cluster known pins by proximity to share power symbols
            clusters = _cluster_power_pins(known, radius=_POWER_CLUSTER_RADIUS_MM)

            for cluster in clusters:
                if len(cluster) == 1:
                    # Single pin: traditional stub + symbol
                    pin_ref, (wx, wy, wa) = cluster[0]
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.power_symbols.append(PowerSymbolPlacement(net.name, ex, ey))
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                else:
                    # Multiple pins: compute cluster centroid for shared symbol
                    cx = sum(cpx for _, (cpx, _, _) in cluster) / len(cluster)
                    cy = sum(cpy for _, (_, cpy, _) in cluster) / len(cluster)

                    # Place ONE power symbol at centroid
                    routing.power_symbols.append(PowerSymbolPlacement(net.name, cx, cy))

                    # Wire each pin to centroid via hub routing
                    stub_ends: list[tuple[float, float]] = []
                    for pin_ref, (wx, wy, wa) in cluster:
                        ex, ey = _stub_end(wx, wy, wa)
                        routing.wires.append(WireSegment(wx, wy, ex, ey))
                        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                        stub_ends.append((ex, ey))

                    # Add centroid as hub target
                    stub_ends.append((cx, cy))

                    # Route stubs to centroid via spine/hub
                    if use_bus:
                        hub_segs, hub_junctions = _spine_route(stub_ends)
                    else:
                        hub_segs, hub_junctions = _hub_route(stub_ends)
                    routing.wires.extend(hub_segs)
                    routing.junctions.extend(hub_junctions)

            # Off-canvas fallback for power pins with no known endpoint
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.power_symbols.append(PowerSymbolPlacement(net.name, ex, ey))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
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

        if routed_directly:
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
            if use_bus and net.name in ladder_routes:
                lane_plan = ladder_routes[net.name]
                hub_segs, hub_junctions = _shared_lane_route(
                    stub_ends,
                    axis=lane_plan.axis,
                    coordinate=lane_plan.coordinate,
                    min_bound=lane_plan.min_orthogonal,
                    max_bound=lane_plan.max_orthogonal,
                )
            elif use_bus and _prefer_chain_route(stub_ends):
                hub_segs, hub_junctions = _chain_route(stub_ends)
            elif use_bus:
                hub_segs, hub_junctions = _spine_route(stub_ends)
            else:
                hub_segs, hub_junctions = _hub_route(stub_ends)
            routing.wires.extend(hub_segs)
            routing.junctions.extend(hub_junctions)
            continue

        # ----------------------------------------------------------------
        # High-degree non-power → global label per pin (capped by policy)
        # ----------------------------------------------------------------
        if len(known) > _HUB_MAX_DEGREE:
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
            doc.add_global_label(
                ps.net_name, ps.x, ps.y, new_uuid(), angle=ps.angle, shape="passive"
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
