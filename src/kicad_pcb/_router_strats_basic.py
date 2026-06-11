"""Hub, spine, and shared-lane routing primitives."""

from __future__ import annotations

import math

from ._router_geometry import _l_route, _snap_grid
from ._router_types import JunctionPoint, WireSegment
from ._router_write import _simplify_wires


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
