"""Wire geometry primitives, route building, and point/segment helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ._router_types import (
    WIRE_EXTEND_MM,
    PinAnchor,
    WireSegment,
)


def _stub_end(wx: float, wy: float, wa: float) -> tuple[float, float]:
    """Return the far end of the 5.08 mm stub extended outward from a pin."""
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
    """Return True for simple connector-to-passive edge links."""
    from .component_types import component_type as _component_type  # noqa: PLC0415

    pair = {_component_type(ref_a), _component_type(ref_b)}
    return pair == {"connector", "passive"}


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


# ---------------------------------------------------------------------------
# Route building
# ---------------------------------------------------------------------------
def _horizontal_first_l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex2, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex2, ey1, ex2, ey2))
    return segs


def _vertical_first_l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex1, ey2))
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey2, ex2, ey2))
    return segs


def _three_segment_route_via_x(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    via_x: float,
) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ex1, via_x, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, via_x, ey1))
    if not math.isclose(ey1, ey2, abs_tol=0.01):
        segs.append(WireSegment(via_x, ey1, via_x, ey2))
    if not math.isclose(via_x, ex2, abs_tol=0.01):
        segs.append(WireSegment(via_x, ey2, ex2, ey2))
    return segs


def _three_segment_route_via_y(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    via_y: float,
) -> list[WireSegment]:
    segs: list[WireSegment] = []
    if not math.isclose(ey1, via_y, abs_tol=0.01):
        segs.append(WireSegment(ex1, ey1, ex1, via_y))
    if not math.isclose(ex1, ex2, abs_tol=0.01):
        segs.append(WireSegment(ex1, via_y, ex2, via_y))
    if not math.isclose(via_y, ey2, abs_tol=0.01):
        segs.append(WireSegment(ex2, via_y, ex2, ey2))
    return segs


def _wire_path_length(route: list[WireSegment]) -> float:
    return sum(abs(seg.x2 - seg.x1) + abs(seg.y2 - seg.y1) for seg in route)


def _segment_label_angle(segment: WireSegment) -> int:
    if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
        return 0 if segment.x2 >= segment.x1 else 180
    return 90 if segment.y2 >= segment.y1 else 270


# ---------------------------------------------------------------------------
# Point/segment helpers
# ---------------------------------------------------------------------------
def _point_on_segment(point: tuple[float, float], segment: WireSegment) -> bool:
    x, y = point
    if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
        return math.isclose(y, segment.y1, abs_tol=0.01) and min(
            segment.x1, segment.x2
        ) <= x <= max(segment.x1, segment.x2)
    if math.isclose(segment.x1, segment.x2, abs_tol=0.01):
        return math.isclose(x, segment.x1, abs_tol=0.01) and min(
            segment.y1, segment.y2
        ) <= y <= max(segment.y1, segment.y2)
    return False


def _route_protected_point_score(
    route: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]],
    excluded_points: set[tuple[float, float]],
) -> int:
    return sum(
        1
        for point in protected_points
        if point not in excluded_points
        and any(_point_on_segment(point, segment) for segment in route)
    )


def _route_candidate_key(
    route: list[WireSegment],
    *,
    protected_points: set[tuple[float, float]] | None,
    endpoints: list[tuple[float, float]],
) -> tuple[int, float, int]:
    excluded_points = {(round(x, 2), round(y, 2)) for x, y in endpoints}
    return (
        _route_protected_point_score(
            route,
            protected_points=protected_points or set(),
            excluded_points=excluded_points,
        ),
        _wire_path_length(route),
        len(route),
    )


def _l_route(ex1: float, ey1: float, ex2: float, ey2: float) -> list[WireSegment]:
    """Return up to two orthogonal segments connecting (ex1,ey1) to (ex2,ey2)."""
    return _l_route_with_protected_points(ex1, ey1, ex2, ey2, protected_points=None)


def _l_route_with_protected_points(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    *,
    protected_points: set[tuple[float, float]] | None,
) -> list[WireSegment]:
    horizontal_first = _horizontal_first_l_route(ex1, ey1, ex2, ey2)
    if not protected_points:
        return horizontal_first

    vertical_first = _vertical_first_l_route(ex1, ey1, ex2, ey2)
    current_endpoints = {(round(ex1, 2), round(ey1, 2)), (round(ex2, 2), round(ey2, 2))}
    horizontal_score = _route_protected_point_score(
        horizontal_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    vertical_score = _route_protected_point_score(
        vertical_first,
        protected_points=protected_points,
        excluded_points=current_endpoints,
    )
    if vertical_score < horizontal_score:
        return vertical_first
    return horizontal_first
