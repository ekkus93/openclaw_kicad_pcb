"""Hub, spine, shared-lane, ladder, chain, and compact routing strategies."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._router_classify import (
    _boxes_touch_or_overlap,
    _compact_cluster_detour_x,
    _is_compact_rightward_tail,
    _is_local_ladder_net,
    _is_power_net_name,
    _preferred_shared_lane,
    _wire_crosses_box,
)
from ._router_geometry import (
    _best_direct_route_with_protected_points,
    _coerce_pin_anchor_map,
    _l_route,
    _manhattan,
    _resolve_pin_anchors,
    _route_candidate_key,
    _snap_grid,
    _stub_end,
)
from ._router_types import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    SYMBOL_HALF_SIZE_MM,
    WIRE_EXTEND_MM,
    JunctionPoint,
    LadderLanePlannerContext,
    RoutingHeuristicPolicy,
    SharedLanePlan,
    WireSegment,
)
from ._router_write import _simplify_wires
from .block_detection import BlockLayout, BlockRole

if TYPE_CHECKING:
    from ._router_types import PinAnchor
    from .circuit_ir import CircuitIR, PinRefIR


# ---------------------------------------------------------------------------
# Hub and spine routes
# ---------------------------------------------------------------------------
def _hub_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a multi-pin net through a central hub point."""
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
    """Route a multi-pin net as a spine with T-junction taps."""
    xs = [e[0] for e in endpoints]
    ys = [e[1] for e in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    segs: list[WireSegment] = []
    junctions: list[JunctionPoint] = []

    if x_span >= y_span:
        # Horizontal spine
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
        # Vertical spine
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
    """Route a local net along a repeated endpoint lane."""
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


# ---------------------------------------------------------------------------
# Compact tail / analog routing
# ---------------------------------------------------------------------------
def _plan_single_grouped_ladder_lane(
    *,
    axis: str,
    base_coordinate: float,
    endpoints: list[tuple[float, float]],
    refs: tuple[str, ...] = (),
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> SharedLanePlan | None:
    """Return the single-net lane plan, or ``None`` when chain routing should win."""
    candidate_plan = SharedLanePlan(axis, base_coordinate)
    if heuristic_policy.should_skip_shared_lane_plan(
        endpoints, candidate_plan
    ) or heuristic_policy.should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=candidate_plan,
        refs=refs,
    ):
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


def _compact_horizontal_stage_tail_route(
    endpoints: list[tuple[float, float]],
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a short left support into a stage node plus downstream continuation."""
    if len(endpoints) != 3:
        return _chain_route(endpoints)

    left, middle, right = sorted(endpoints, key=lambda point: (point[0], point[1]))
    left_x, left_y = left
    middle_x, middle_y = middle
    right_x, right_y = right

    segs: list[WireSegment] = []
    if not math.isclose(left_x, middle_x, abs_tol=0.01):
        segs.append(WireSegment(left_x, left_y, middle_x, left_y))
    if not math.isclose(left_y, middle_y, abs_tol=0.01):
        segs.append(WireSegment(middle_x, left_y, middle_x, middle_y))
    if not math.isclose(middle_x, right_x, abs_tol=0.01):
        segs.append(WireSegment(middle_x, middle_y, right_x, middle_y))
    if not math.isclose(middle_y, right_y, abs_tol=0.01):
        segs.append(WireSegment(right_x, middle_y, right_x, right_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    return _simplify_wires(segs, protected_points=protected), []


def _compact_vertical_tail_members(
    endpoints: list[tuple[float, float]],
    *,
    coordinate: float,
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float], bool] | None:
    """Return ``(upstream, pivot, downstream, exact_lane_match)`` for a compact tail."""
    lane_tolerance = (WIRE_EXTEND_MM / 4) + 0.05
    near_lane_points = [
        point for point in endpoints if math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    other_points = [
        point
        for point in endpoints
        if not math.isclose(point[0], coordinate, abs_tol=lane_tolerance)
    ]
    if len(near_lane_points) == 2 and len(other_points) == 1:
        downstream = other_points[0]
        pivot = min(near_lane_points, key=lambda point: abs(point[1] - downstream[1]))
        upstream = next(point for point in near_lane_points if point != pivot)
        return upstream, pivot, downstream, True
    if len(endpoints) != 3:
        return None
    downstream = max(endpoints, key=lambda point: (point[0], -point[1]))
    remaining = [point for point in endpoints if point != downstream]
    if len(remaining) != 2:
        return None
    pivot = min(remaining, key=lambda point: abs(point[1] - downstream[1]))
    upstream = next(point for point in remaining if point != pivot)
    return upstream, pivot, downstream, False


def _compact_vertical_tail_tail_y(
    *,
    pivot_y: float,
    downstream_y: float,
    prefer_below: bool,
) -> float:
    if abs(downstream_y - pivot_y) > (1.5 * WIRE_EXTEND_MM):
        return pivot_y
    edge_y = max(pivot_y, downstream_y) if prefer_below else min(pivot_y, downstream_y)
    offset = (2.5 * WIRE_EXTEND_MM) + (1.27 if prefer_below else 0.0)
    return _snap_grid(edge_y + offset if prefer_below else edge_y - offset)


def _compact_vertical_tail_clearance_x(
    *,
    downstream_x: float,
    downstream_y: float,
    tail_y: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
) -> float:
    clearance_x = downstream_x
    if positions is None or math.isclose(tail_y, downstream_y, abs_tol=0.01):
        return clearance_x
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
    return clearance_x


def _compact_vertical_tail_route(
    endpoints: list[tuple[float, float]],
    *,
    coordinate: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    prefer_below: bool = False,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact asymmetric output tail with one long downstream run."""
    members = _compact_vertical_tail_members(endpoints, coordinate=coordinate)
    if members is None:
        return _chain_route(endpoints)

    upstream, pivot, downstream, exact_lane_match = members
    trunk_x = coordinate
    upstream_x, upstream_y = upstream
    pivot_x, pivot_y = pivot
    downstream_x, downstream_y = downstream
    tail_y = _compact_vertical_tail_tail_y(
        pivot_y=pivot_y,
        downstream_y=downstream_y,
        prefer_below=prefer_below,
    )
    clearance_x = _compact_vertical_tail_clearance_x(
        downstream_x=downstream_x,
        downstream_y=downstream_y,
        tail_y=tail_y,
        positions=positions,
    )

    segs: list[WireSegment] = []
    if not math.isclose(upstream_x, trunk_x, abs_tol=0.01):
        segs.append(WireSegment(upstream_x, upstream_y, trunk_x, upstream_y))
    if not math.isclose(upstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(trunk_x, upstream_y, trunk_x, tail_y))
    if exact_lane_match:
        if not math.isclose(pivot_y, tail_y, abs_tol=0.01):
            segs.append(WireSegment(pivot_x, pivot_y, pivot_x, tail_y))
        segs.append(WireSegment(pivot_x, tail_y, clearance_x, tail_y))
    else:
        if not math.isclose(pivot_x, trunk_x, abs_tol=0.01):
            segs.append(WireSegment(pivot_x, pivot_y, trunk_x, pivot_y))
        if not math.isclose(pivot_y, tail_y, abs_tol=0.01):
            segs.append(WireSegment(trunk_x, pivot_y, trunk_x, tail_y))
        segs.append(WireSegment(trunk_x, tail_y, clearance_x, tail_y))
    if not math.isclose(downstream_y, tail_y, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, tail_y, clearance_x, downstream_y))
    if not math.isclose(clearance_x, downstream_x, abs_tol=0.01):
        segs.append(WireSegment(clearance_x, downstream_y, downstream_x, downstream_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    junctions: list[JunctionPoint] = []
    if not math.isclose(trunk_x, clearance_x, abs_tol=0.01):
        junctions.append(JunctionPoint(trunk_x, tail_y))
    if exact_lane_match and math.isclose(pivot_x, trunk_x, abs_tol=0.01):
        junctions = []
    return _simplify_wires(segs, protected_points=protected), junctions


def _best_compact_vertical_tail_route(
    endpoints: list[tuple[float, float]],
    *,
    preferred_coordinate: float,
    protected_points: set[tuple[float, float]] | None = None,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Choose the cleanest compact vertical tail route near the preferred lane."""
    preferred_route = _compact_vertical_tail_route(
        endpoints,
        coordinate=preferred_coordinate,
        positions=positions,
    )
    if not protected_points:
        return preferred_route

    candidate_coordinates = [preferred_coordinate]
    seen_coordinates = {round(preferred_coordinate, 2)}
    for offset in range(1, 5):
        for direction in (-1, 1):
            candidate = _snap_grid(preferred_coordinate + (direction * offset * 1.27))
            rounded = round(candidate, 2)
            if rounded in seen_coordinates:
                continue
            seen_coordinates.add(rounded)
            candidate_coordinates.append(candidate)

    best_choice: (
        tuple[
            tuple[int, float, int],
            float,
            tuple[list[WireSegment], list[JunctionPoint]],
        ]
        | None
    ) = None
    for candidate_coordinate in candidate_coordinates:
        for prefer_below in (False, True):
            candidate_route = _compact_vertical_tail_route(
                endpoints,
                coordinate=candidate_coordinate,
                positions=positions,
                prefer_below=prefer_below,
            )
            route, junctions = candidate_route
            route_key = _route_candidate_key(
                route,
                protected_points=protected_points,
                endpoints=endpoints,
            )
            choice = (route_key, abs(candidate_coordinate - preferred_coordinate), candidate_route)
            if best_choice is None or choice < best_choice:
                best_choice = choice

    assert best_choice is not None
    return best_choice[2]


def _compact_local_ground_cluster_route(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact local 3-pin ground cluster on one calm horizontal lane."""
    if len(cluster) != 3:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 80.0 or y_span > 30.0:
        return None
    if x_span + 2.54 < y_span:
        return None

    lane_y = min(ys)
    lane_x0 = min(xs)
    lane_x1 = max(xs)
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}
    if positions is not None:
        cluster_positions = [
            pos for pin_ref, _anchor in cluster if (pos := positions.get(pin_ref.ref)) is not None
        ]
        lane_candidates = sorted(set(ys))
        best_lane: tuple[float, float, dict[tuple[float, float], float]] | None = None
        for candidate_y in lane_candidates:
            candidate_targets = {(x, y): x for x, y in stub_ends}
            for x, y in stub_ends:
                if math.isclose(y, candidate_y, abs_tol=0.01):
                    continue
                clearance_x = x
                for bx, by, _rotation in cluster_positions:
                    if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                        clearance_x = min(clearance_x, bx - (2 * SYMBOL_HALF_SIZE_MM))
                candidate_targets[(x, y)] = _snap_grid(clearance_x)

            candidate_x0 = min(lane_x0, *candidate_targets.values())
            blocked = any(
                _wire_crosses_box(
                    candidate_x0,
                    candidate_y,
                    lane_x1,
                    candidate_y,
                    bx,
                    by,
                    SYMBOL_HALF_SIZE_MM,
                )
                for bx, by, _rotation in cluster_positions
            )
            if blocked:
                continue

            score = sum(abs(y - candidate_y) for _x, y in stub_ends) + sum(
                abs(x - candidate_targets[(x, y)]) for x, y in stub_ends
            )
            if (
                best_lane is None
                or score < best_lane[0]
                or (math.isclose(score, best_lane[0], abs_tol=0.01) and candidate_y < best_lane[1])
            ):
                best_lane = (score, candidate_y, candidate_targets)

        if best_lane is None:
            return None

        _score, lane_y, vertical_target_x = best_lane
        lane_x0 = min(lane_x0, *vertical_target_x.values())

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
    protected.add((round(lane_x1, 2), round(lane_y, 2)))
    return _simplify_wires(segs, protected_points=protected), junctions, (symbol_x, lane_y)


def _decoupling_ground_members(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
) -> (
    tuple[
        list[tuple[PinRefIR, tuple[float, float, float]]],
        list[tuple[PinRefIR, tuple[float, float, float]]],
    ]
    | None
):
    """Return decoupling-cap and support members for a local decoupling GND cluster."""
    from .component_types import component_type as _component_type  # noqa: PLC0415

    if not 2 <= len(cluster) <= 5:
        return None

    capacitor_members = [
        (pin_ref, anchor) for pin_ref, anchor in cluster if pin_ref.ref.upper().startswith("C")
    ]
    support_members = [
        (pin_ref, anchor) for pin_ref, anchor in cluster if not pin_ref.ref.upper().startswith("C")
    ]
    if len(capacitor_members) < 2 or len(support_members) > 2:
        return None
    for support_pin_ref, _anchor in support_members:
        support_kind = _component_type(support_pin_ref.ref)
        if support_kind not in {"ic", "passive"}:
            return None

    return capacitor_members, support_members


def _choose_compact_ground_lane(
    *,
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    stub_ends: list[tuple[float, float]],
    lane_bounds: tuple[float, float],
    avg_y: float,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
) -> tuple[float, dict[tuple[float, float], float], float]:
    """Return the best local ground lane candidate for a compact cluster."""
    ys = [point[1] for point in stub_ends]
    lane_y = min(sorted(set(ys)), key=lambda y: abs(y - avg_y))
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}
    lane_x0, lane_x1 = lane_bounds
    if positions is None:
        return lane_y, vertical_target_x, lane_x0

    positioned_cluster = [
        (pin_ref.ref, pos, stub_ends[index])
        for index, (pin_ref, _anchor) in enumerate(cluster)
        if (pos := positions.get(pin_ref.ref)) is not None
    ]
    lane_candidates = sorted(set(ys))
    best_lane: tuple[float, float, dict[tuple[float, float], float], float] | None = None
    for candidate_y in lane_candidates:
        candidate_targets = {(x, y): x for x, y in stub_ends}
        for x, y in stub_ends:
            if math.isclose(y, candidate_y, abs_tol=0.01):
                continue
            clearance_x = x
            for ref, (bx, by, _rotation), member_stub in positioned_cluster:
                is_own_member = math.isclose(member_stub[0], x, abs_tol=0.01) and math.isclose(
                    member_stub[1], y, abs_tol=0.01
                )
                if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                    clearance_x = min(
                        clearance_x,
                        _compact_cluster_detour_x(
                            ref,
                            bx,
                            crossing_own_member=is_own_member,
                        ),
                    )
            candidate_targets[(x, y)] = _snap_grid(clearance_x)

        candidate_x0 = min(lane_x0, *candidate_targets.values())
        blocked = any(
            _wire_crosses_box(
                candidate_x0,
                candidate_y,
                lane_x1,
                candidate_y,
                bx,
                by,
                SYMBOL_HALF_SIZE_MM,
            )
            for _ref, (bx, by, _rotation), _member_stub in positioned_cluster
        )
        if blocked:
            continue

        vertical_cost = sum(abs(y - candidate_y) for _x, y in stub_ends)
        horizontal_cost = sum(abs(x - candidate_targets[(x, y)]) for x, y in stub_ends)
        centering_cost = abs(candidate_y - avg_y)
        score = vertical_cost + horizontal_cost + centering_cost
        if (
            best_lane is None
            or score < best_lane[0]
            or (
                math.isclose(score, best_lane[0], abs_tol=0.01)
                and centering_cost < abs(best_lane[1] - avg_y)
            )
        ):
            best_lane = (score, candidate_y, candidate_targets, candidate_x0)

    if best_lane is None:
        return lane_y, vertical_target_x, lane_x0

    _score, lane_y, vertical_target_x, lane_x0 = best_lane
    return lane_y, vertical_target_x, lane_x0


def _compact_local_decoupling_ground_cluster_route(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact local GND lane for a small decoupling support cluster."""
    members = _decoupling_ground_members(cluster)
    if members is None:
        return None
    capacitor_members, _support_members = members

    cap_stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in capacitor_members]
    cap_xs = [point[0] for point in cap_stub_ends]
    cap_ys = [point[1] for point in cap_stub_ends]
    cap_x_span = max(cap_xs) - min(cap_xs)
    cap_y_span = max(cap_ys) - min(cap_ys)
    if cap_x_span > 50.0 or cap_y_span > 60.0:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 80.0 or y_span > 60.0:
        return None

    lane_x0 = min(xs)
    lane_x1 = max(xs)
    avg_y = sum(cap_ys) / len(cap_ys)
    lane_y, vertical_target_x, lane_x0 = _choose_compact_ground_lane(
        cluster=cluster,
        stub_ends=stub_ends,
        lane_bounds=(lane_x0, lane_x1),
        avg_y=avg_y,
        positions=positions,
    )

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


def _compact_local_decoupling_power_cluster_route(  # noqa: PLR0911, PLR0915
    net_name: str,
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint], tuple[float, float]] | None:
    """Route a compact decoupling rail on one calm horizontal lane."""
    from .component_types import component_type as _component_type  # noqa: PLC0415
    from .component_types import power_rail_polarity  # noqa: PLC0415

    if len(cluster) < 2 or len(cluster) > 4:
        return None
    rail_polarity = power_rail_polarity(net_name)
    if rail_polarity is None:
        return None

    component_kinds = [_component_type(pin_ref.ref) for pin_ref, _anchor in cluster]
    has_capacitor = any(pin_ref.ref.upper().startswith("C") for pin_ref, _anchor in cluster)
    if "ic" not in component_kinds or not has_capacitor:
        return None

    stub_ends = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    xs = [point[0] for point in stub_ends]
    ys = [point[1] for point in stub_ends]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)
    if x_span > 90.0 or y_span > 70.0:
        return None
    if len(cluster) > 2 and x_span + 15.0 < y_span:
        return None

    local_points = [
        stub_ends[index]
        for index, (pin_ref, _anchor) in enumerate(cluster)
        if _component_type(pin_ref.ref) != "connector"
    ]
    if len(local_points) < 2:
        return None

    if (
        rail_polarity == "positive"
        and len(cluster) == 2
        and not math.isclose(stub_ends[0][0], stub_ends[1][0], abs_tol=0.01)
    ):
        lane_x0 = min(xs)
        lane_x1 = max(xs)
        lane_y = min(point[1] for point in local_points)
        segs: list[WireSegment] = []
        if not math.isclose(lane_x0, lane_x1, abs_tol=0.01):
            segs.append(WireSegment(lane_x0, lane_y, lane_x1, lane_y))
        initial_junctions = [JunctionPoint(x, lane_y) for x, _y in stub_ends]
        for x, y in stub_ends:
            if not math.isclose(y, lane_y, abs_tol=0.01):
                segs.append(WireSegment(x, y, x, lane_y))
        symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
        segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
        protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
        return (
            _simplify_wires(segs, protected_points=protected),
            initial_junctions,
            (symbol_x, lane_y),
        )

    lane_x1 = max(xs)
    prefer_upper_lane = rail_polarity == "positive"
    lane_y = (
        min(point[1] for point in local_points)
        if prefer_upper_lane
        else max(point[1] for point in local_points)
    )
    vertical_target_x: dict[tuple[float, float], float] = {(x, y): x for x, y in stub_ends}

    if positions is not None:
        positioned_cluster = [
            (pin_ref.ref, pos, stub_ends[index])
            for index, (pin_ref, _anchor) in enumerate(cluster)
            if (pos := positions.get(pin_ref.ref)) is not None
        ]
        lane_candidates = sorted(
            {point[1] for point in local_points},
            reverse=not prefer_upper_lane,
        )
        best_lane: tuple[float, float, dict[tuple[float, float], float], float] | None = None
        for candidate_y in lane_candidates:
            candidate_targets = {(x, y): x for x, y in stub_ends}
            for x, y in stub_ends:
                if math.isclose(y, candidate_y, abs_tol=0.01):
                    continue
                clearance_x = x
                for _ref, (bx, by, _rotation), _member_stub in positioned_cluster:
                    is_own_member = math.isclose(_member_stub[0], x, abs_tol=0.01) and math.isclose(
                        _member_stub[1], y, abs_tol=0.01
                    )
                    if is_own_member and _ref.upper().startswith("C"):
                        box_top = by - SYMBOL_HALF_SIZE_MM
                        box_bottom = by + SYMBOL_HALF_SIZE_MM
                        if (
                            bx - SYMBOL_HALF_SIZE_MM <= x <= bx + SYMBOL_HALF_SIZE_MM
                            and min(y, candidate_y) < box_bottom
                            and max(y, candidate_y) > box_top
                        ):
                            clearance_x = min(
                                clearance_x,
                                _compact_cluster_detour_x(
                                    _ref,
                                    bx,
                                    crossing_own_member=True,
                                ),
                            )
                    if (
                        math.isclose(_member_stub[0], x, abs_tol=0.01)
                        and math.isclose(_member_stub[1], y, abs_tol=0.01)
                        and not (_ref.upper().startswith("C") or _component_type(_ref) == "ic")
                    ):
                        continue
                    if _wire_crosses_box(x, y, x, candidate_y, bx, by, SYMBOL_HALF_SIZE_MM):
                        clearance_x = min(
                            clearance_x,
                            _compact_cluster_detour_x(
                                _ref,
                                bx,
                                crossing_own_member=is_own_member,
                            ),
                        )
                candidate_targets[(x, y)] = _snap_grid(clearance_x)

            candidate_x0 = min(*xs, *candidate_targets.values())
            blocked_positions = [
                pos
                for _ref, pos, (_stub_x, stub_y) in positioned_cluster
                if not math.isclose(stub_y, candidate_y, abs_tol=0.01)
            ]
            blocked = any(
                _wire_crosses_box(
                    candidate_x0,
                    candidate_y,
                    lane_x1,
                    candidate_y,
                    bx,
                    by,
                    SYMBOL_HALF_SIZE_MM,
                )
                for bx, by, _rotation in blocked_positions
            )
            if blocked:
                continue

            vertical_cost = sum(abs(y - candidate_y) for _x, y in local_points)
            horizontal_cost = sum(abs(x - candidate_targets[(x, y)]) for x, y in stub_ends)
            score = vertical_cost + horizontal_cost
            if (
                best_lane is None
                or score < best_lane[0]
                or (
                    math.isclose(score, best_lane[0], abs_tol=0.01)
                    and (
                        candidate_y < best_lane[1]
                        if prefer_upper_lane
                        else candidate_y > best_lane[1]
                    )
                )
            ):
                best_lane = (score, candidate_y, candidate_targets, candidate_x0)

        if best_lane is not None:
            _score, lane_y, vertical_target_x, lane_x0 = best_lane
        else:
            lane_x0 = min(xs)
    else:
        lane_x0 = min(xs)

    segs = [WireSegment(lane_x0, lane_y, lane_x1, lane_y)]
    result_junctions: list[JunctionPoint] = []
    for x, y in stub_ends:
        target_x = vertical_target_x[(x, y)]
        if not math.isclose(x, target_x, abs_tol=0.01):
            segs.append(WireSegment(x, y, target_x, y))
        if not math.isclose(y, lane_y, abs_tol=0.01):
            segs.append(WireSegment(target_x, y, target_x, lane_y))
        result_junctions.append(JunctionPoint(target_x, lane_y))

    symbol_x = _snap_grid(lane_x1 + (2 * SYMBOL_HALF_SIZE_MM))
    segs.append(WireSegment(lane_x1, lane_y, symbol_x, lane_y))
    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), result_junctions, (symbol_x, lane_y)


# ---------------------------------------------------------------------------
# Connector entry, inferred bounds, ladder planning
# ---------------------------------------------------------------------------
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
    """Infer a bounded ladder lane for compact 3-pin nets without an exact shared axis."""
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
    dict[str, tuple[str, ...]],
    dict[str, float],
]:
    """Collect compact local nets that may participate in ladder routing."""
    from .component_types import component_type as _component_type  # noqa: PLC0415

    candidate_boxes: dict[str, tuple[float, float, float, float]] = {}
    candidate_degree: dict[str, int] = {}
    shared_lane_by_net: dict[str, tuple[str, float]] = {}
    endpoints_by_net: dict[str, list[tuple[float, float]]] = {}
    refs_by_net: dict[str, tuple[str, ...]] = {}
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
        refs_by_net[net.name] = tuple(pin.ref for pin in net.pins)
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
        refs_by_net,
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
                refs=context.refs_by_net.get(grouped_names[0], ()),
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
        ) or context.heuristic_policy.should_prefer_small_analog_chain(
            context.endpoints_by_net[net_name],
            inferred_plan=inferred_plan,
            refs=context.refs_by_net.get(net_name, ()),
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
    """Detect nearby small-signal net neighborhoods that should share ladder-style routing."""
    resolved_anchors = _resolve_pin_anchors(
        pin_endpoints or {},
        _coerce_pin_anchor_map(pin_anchors),
    )
    (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        refs_by_net,
        connector_entry_x_by_net,
    ) = _collect_local_ladder_candidates(ir, resolved_anchors)
    planner_context = LadderLanePlannerContext(
        candidate_degree=candidate_degree,
        shared_lane_by_net=shared_lane_by_net,
        endpoints_by_net=endpoints_by_net,
        refs_by_net=refs_by_net,
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


# ---------------------------------------------------------------------------
# Chain routing
# ---------------------------------------------------------------------------
def _chain_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    """Route a compact 3-pin net as a simple ordered chain."""
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
        segs.extend(
            _best_direct_route_with_protected_points(
                x1,
                y1,
                x2,
                y2,
                protected_points=protected_points,
            )
        )

    protected = {(round(x, 2), round(y, 2)) for x, y in ordered}
    return _simplify_wires(segs, protected_points=protected), []


def _compact_aligned_chain_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]]:
    if len(endpoints) < 2:
        return [], []
    ordered = sorted(endpoints, key=lambda point: (point[0], point[1]))
    if all(math.isclose(point[1], ordered[0][1], abs_tol=0.01) for point in ordered):
        lane_y = round(ordered[0][1] - WIRE_EXTEND_MM, 2)
        segs = []
        for x, y in ordered:
            if not math.isclose(y, lane_y, abs_tol=0.01):
                segs.append(WireSegment(x, y, x, lane_y))
        for (x1, _y1), (x2, _y2) in zip(ordered, ordered[1:], strict=False):
            segs.append(WireSegment(x1, lane_y, x2, lane_y))
        return segs, []
    ordered = sorted(endpoints, key=lambda point: (point[1], point[0]))
    if all(math.isclose(point[0], ordered[0][0], abs_tol=0.01) for point in ordered):
        lane_x = round(ordered[0][0] - WIRE_EXTEND_MM, 2)
        segs = []
        for x, y in ordered:
            if not math.isclose(x, lane_x, abs_tol=0.01):
                segs.append(WireSegment(x, y, lane_x, y))
        for (_x1, y1), (_x2, y2) in zip(ordered, ordered[1:], strict=False):
            segs.append(WireSegment(lane_x, y1, lane_x, y2))
        return segs, []
    return _chain_route(endpoints, protected_points=protected_points)


def _protected_shared_lane_route(
    endpoints: list[tuple[float, float]],
    *,
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
    """Return a lower-collision shared lane route for crowded multi-pin nets when available."""
    if len(endpoints) < 3 or not protected_points:
        return None

    candidate_routes: list[tuple[list[WireSegment], list[JunctionPoint]]] = []
    for coordinate in sorted({round(point[0], 2) for point in endpoints}):
        candidate_routes.append(
            _shared_lane_route(endpoints, axis="vertical", coordinate=coordinate)
        )
    for coordinate in sorted({round(point[1], 2) for point in endpoints}):
        candidate_routes.append(
            _shared_lane_route(endpoints, axis="horizontal", coordinate=coordinate)
        )

    best_route, best_junctions = min(
        candidate_routes,
        key=lambda candidate: _route_candidate_key(
            candidate[0],
            protected_points=protected_points,
            endpoints=endpoints,
        ),
    )
    return best_route, best_junctions


def _buffer_follower_feedback_route(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    stub_ends: list[tuple[float, float]],
    *,
    block_layout: BlockLayout | None,
) -> tuple[list[WireSegment], list[JunctionPoint]] | None:
    """Route a 3-pin buffer follower net as a short local loop plus branch."""
    if block_layout is None or len(known) != 3 or len(stub_ends) != 3:
        return None

    indices_by_ref: dict[str, list[int]] = {}
    for index, (pin_ref, _endpoint) in enumerate(known):
        indices_by_ref.setdefault(pin_ref.ref, []).append(index)

    repeated_refs = [(ref, indices) for ref, indices in indices_by_ref.items() if len(indices) == 2]
    if len(repeated_refs) != 1 or len(indices_by_ref) != 2:
        return None

    buffer_ref, shared_indices = repeated_refs[0]
    if block_layout.get_role(buffer_ref) != BlockRole.BUFFER_STAGE:
        return None

    downstream_index = next(index for index in range(len(known)) if index not in shared_indices)
    downstream_ref = known[downstream_index][0].ref
    if block_layout.get_role(downstream_ref) not in {
        BlockRole.OUTPUT_CONDITIONING,
        BlockRole.OUTPUT,
    }:
        return None

    first_index, second_index = shared_indices
    first_point = stub_ends[first_index]
    second_point = stub_ends[second_index]
    downstream_point = stub_ends[downstream_index]

    if abs(first_point[0] - second_point[0]) <= WIRE_EXTEND_MM:
        return None

    branch_index = (
        first_index
        if _manhattan(*first_point, *downstream_point)
        <= _manhattan(*second_point, *downstream_point)
        else second_index
    )
    feedback_index = second_index if branch_index == first_index else first_index

    branch_x, branch_y = stub_ends[branch_index]
    feedback_x, feedback_y = stub_ends[feedback_index]
    downstream_x, downstream_y = downstream_point

    loop_y = _snap_grid(min(branch_y, feedback_y) - WIRE_EXTEND_MM)
    if loop_y >= min(branch_y, feedback_y) - 0.05:
        loop_y = _snap_grid(loop_y - WIRE_EXTEND_MM)

    segs: list[WireSegment] = []
    if not math.isclose(feedback_y, loop_y, abs_tol=0.01):
        segs.append(WireSegment(feedback_x, feedback_y, feedback_x, loop_y))
    segs.append(WireSegment(feedback_x, loop_y, branch_x, loop_y))
    if not math.isclose(branch_y, loop_y, abs_tol=0.01):
        segs.append(WireSegment(branch_x, loop_y, branch_x, branch_y))
    segs.extend(_l_route(branch_x, branch_y, downstream_x, downstream_y))

    protected = {(round(x, 2), round(y, 2)) for x, y in stub_ends}
    return _simplify_wires(segs, protected_points=protected), []


def _route_length(segments: list[WireSegment]) -> float:
    """Return total Manhattan wire length for *segments*."""
    return sum(_manhattan(seg.x1, seg.y1, seg.x2, seg.y2) for seg in segments)


def _route_visual_cost(
    segments: list[WireSegment],
    junctions: list[JunctionPoint],
) -> float:
    """Return a small readability-oriented cost for local routing alternatives."""
    local_junction_penalty = WIRE_EXTEND_MM / 2
    local_bend_penalty = (3 * WIRE_EXTEND_MM) / 4
    short_segment_threshold = WIRE_EXTEND_MM + 0.05
    short_segment_count = sum(
        1
        for seg in segments
        if _manhattan(seg.x1, seg.y1, seg.x2, seg.y2) <= short_segment_threshold
    )
    bend_count = max(len(segments) - 1, 0)
    return (
        _route_length(segments)
        + (len(junctions) * local_junction_penalty)
        + (bend_count * local_bend_penalty)
        + (short_segment_count * (WIRE_EXTEND_MM / 4))
    )


def _is_small_analog_chain_candidate(
    endpoints: list[tuple[float, float]],
    *,
    refs: tuple[str, ...] = (),
) -> bool:
    """Return True when compact 3-pin analog heuristics should evaluate *endpoints*."""
    if len(endpoints) != 3 or not _is_local_ladder_net(endpoints):
        return False
    return not (refs and all(ref.upper().startswith("R") for ref in refs))


def _prefer_chain_route(endpoints: list[tuple[float, float]]) -> bool:
    """Return True when a local 3-pin net reads better as a chain than a spine."""
    if len(endpoints) != 3:
        return False
    if all(math.isclose(point[1], endpoints[0][1], abs_tol=0.01) for point in endpoints):
        return True
    if all(math.isclose(point[0], endpoints[0][0], abs_tol=0.01) for point in endpoints):
        return True

    chain_segs, _ = _chain_route(endpoints)
    spine_segs, _ = _spine_route(endpoints)
    chain_length = _route_length(chain_segs)
    spine_length = _route_length(spine_segs)

    if chain_length < spine_length - 0.01:
        return True

    return math.isclose(chain_length, spine_length, abs_tol=0.01) and len(chain_segs) <= len(
        spine_segs
    )


def _prefer_small_analog_chain_route(
    endpoints: list[tuple[float, float]],
    *,
    inferred_plan: SharedLanePlan | None = None,
    refs: tuple[str, ...] = (),
) -> bool:
    """Return True when a compact local analog net reads better as a chain."""
    if not _is_small_analog_chain_candidate(endpoints, refs=refs):
        return False

    chain_segs, chain_junctions = _chain_route(endpoints)
    if inferred_plan is None:
        candidate_segs, candidate_junctions = _spine_route(endpoints)
    else:
        candidate_segs, candidate_junctions = _shared_lane_route(
            endpoints,
            axis=inferred_plan.axis,
            coordinate=inferred_plan.coordinate,
            min_bound=inferred_plan.min_orthogonal,
            max_bound=inferred_plan.max_orthogonal,
        )

    return (
        _route_visual_cost(chain_segs, chain_junctions)
        <= _route_visual_cost(
            candidate_segs,
            candidate_junctions,
        )
        + 0.01
    )
