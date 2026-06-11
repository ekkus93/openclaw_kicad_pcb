"""Compact output-tail routing: horizontal stage tail and vertical tail variants."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ._router_classify import _is_compact_rightward_tail, _wire_crosses_box
from ._router_geometry import _route_candidate_key, _snap_grid
from ._router_strats_chain import _chain_route, _prefer_chain_route
from ._router_types import (
    SYMBOL_HALF_SIZE_MM,
    WIRE_EXTEND_MM,
    JunctionPoint,
    SharedLanePlan,
    WireSegment,
)
from ._router_write import _simplify_wires

# ---------------------------------------------------------------------------
# Lane-skip heuristic
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Compact horizontal stage tail
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Compact vertical tail
# ---------------------------------------------------------------------------


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
