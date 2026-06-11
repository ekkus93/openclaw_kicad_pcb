"""Wire geometry, route-building, label/junction, and power-symbol helpers."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._router_types import (
    _POWER_LABEL_CLEARANCE_MM,
    WIRE_EXTEND_MM,
    BindMarker,
    GlobalLabelPlacement,
    NetLabel,
    NetRouting,
    PinAnchor,
    PowerSymbolPlacement,
    WireSegment,
    _ProtectedPointContext,
)

# NOTE: _simplify_wires is defined in _router_write.py. It is imported lazily
# inside _best_direct_route_with_protected_points to avoid circular imports.


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


def _best_direct_route_with_protected_points(
    ex1: float,
    ey1: float,
    ex2: float,
    ey2: float,
    *,
    protected_points: set[tuple[float, float]] | None,
) -> list[WireSegment]:
    from ._router_write import _simplify_wires  # noqa: PLC0415

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
    best_l_score = min(horizontal_score, vertical_score)
    best_l_route = vertical_first if vertical_score < horizontal_score else horizontal_first

    if best_l_score == 0:
        return best_l_route

    candidates: list[list[WireSegment]] = []
    for detour_multiplier in (1, 2, 3):
        detour = round(WIRE_EXTEND_MM * detour_multiplier, 2)
        candidates.extend(
            [
                _three_segment_route_via_x(ex1, ey1, ex2, ey2, max(ex1, ex2) + detour),
                _three_segment_route_via_y(ex1, ey1, ex2, ey2, min(ey1, ey2) - detour),
                _three_segment_route_via_y(ex1, ey1, ex2, ey2, max(ey1, ey2) + detour),
                _three_segment_route_via_x(ex1, ey1, ex2, ey2, min(ex1, ex2) - detour),
            ]
        )

    def _candidate_key(route: list[WireSegment]) -> tuple[int, float, int]:
        return (
            _route_protected_point_score(
                route,
                protected_points=protected_points,
                excluded_points=current_endpoints,
            ),
            _wire_path_length(route),
            len(route),
        )

    best_detour = min(candidates, key=_candidate_key)
    best_l_key = (best_l_score, _wire_path_length(best_l_route), len(best_l_route))
    if _candidate_key(best_detour) < best_l_key:
        return _simplify_wires(best_detour, protected_points=current_endpoints)
    return best_l_route


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


def _occupied_wire_points(
    wires: list[WireSegment],
) -> set[tuple[float, float]]:
    """Return snapped grid points occupied by already-routed orthogonal wires."""
    occupied: set[tuple[float, float]] = set()
    for segment in wires:
        if math.isclose(segment.x1, segment.x2, abs_tol=0.01):
            x = round(segment.x1, 2)
            y0 = min(segment.y1, segment.y2)
            y1 = max(segment.y1, segment.y2)
            steps = int(round((y1 - y0) / WIRE_EXTEND_MM * 4)) + 1
            for index in range(steps + 1):
                y = round(_snap_grid(y0 + (index * 1.27)), 2)
                if y0 - 0.01 <= y <= y1 + 0.01:
                    occupied.add((x, y))
            continue
        if math.isclose(segment.y1, segment.y2, abs_tol=0.01):
            y = round(segment.y1, 2)
            x0 = min(segment.x1, segment.x2)
            x1 = max(segment.x1, segment.x2)
            steps = int(round((x1 - x0) / WIRE_EXTEND_MM * 4)) + 1
            for index in range(steps + 1):
                x = round(_snap_grid(x0 + (index * 1.27)), 2)
                if x0 - 0.01 <= x <= x1 + 0.01:
                    occupied.add((x, y))
    return occupied


def _occupied_label_points(routing: NetRouting) -> set[tuple[float, float]]:
    occupied: set[tuple[float, float]] = set()
    occupied.update((round(label.x, 2), round(label.y, 2)) for label in routing.labels)
    occupied.update((round(label.x, 2), round(label.y, 2)) for label in routing.global_labels)
    occupied.update((round(symbol.x, 2), round(symbol.y, 2)) for symbol in routing.power_symbols)
    return occupied


def _safe_stub_label_anchor(
    *,
    pin_point: tuple[float, float],
    pin_angle: float,
    occupied_label_points: set[tuple[float, float]],
    protected_points: set[tuple[float, float]] | None = None,
    shared_protected_points: set[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    stub_x, stub_y = _stub_end(pin_point[0], pin_point[1], pin_angle)
    stub = (round(stub_x, 2), round(stub_y, 2))
    if stub in occupied_label_points:
        return None
    if (
        protected_points
        and stub in protected_points
        and (not shared_protected_points or stub not in shared_protected_points)
    ):
        return None
    return stub_x, stub_y


def _safe_pin_label_anchor(
    *,
    pin_point: tuple[float, float],
    occupied_label_points: set[tuple[float, float]],
    protected_points: set[tuple[float, float]] | None = None,
) -> tuple[float, float] | None:
    pin = (round(pin_point[0], 2), round(pin_point[1], 2))
    if pin in occupied_label_points:
        return None
    if protected_points and pin in protected_points:
        return None
    return pin_point


def _label_attachment_plan(
    *,
    pin_point: tuple[float, float],
    pin_angle: float,
    occupied_points: set[tuple[float, float]],
    protected: _ProtectedPointContext | None = None,
    prefer_perpendicular: bool = False,
) -> tuple[list[WireSegment], float, float]:
    """Return a short breakout route to a safe label attachment point."""
    pin_x, pin_y = pin_point
    stub_x, stub_y = _stub_end(pin_x, pin_y, pin_angle)
    stub = (round(stub_x, 2), round(stub_y, 2))
    start = (round(pin_x, 2), round(pin_y, 2))
    angle = int(round(pin_angle)) % 360
    blocked_points = set(occupied_points)
    start_was_occupied = start in occupied_points
    stub_was_occupied = stub in occupied_points
    if protected:
        blocked_points.update(protected.points)
    if not start_was_occupied:
        blocked_points.discard(start)
    if (
        not protected or not protected.shared_points or stub not in protected.shared_points
    ) and not stub_was_occupied:
        blocked_points.discard(stub)

    if prefer_perpendicular:
        candidates = [
            _offset_point_along_angle(stub_x, stub_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            (stub_x, stub_y),
            _offset_point_along_angle(stub_x, stub_y, angle, WIRE_EXTEND_MM),
            (pin_x, pin_y),
        ]
    else:
        candidates = [
            (stub_x, stub_y),
            _offset_point_along_angle(stub_x, stub_y, angle, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(stub_x, stub_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 90) % 360, WIRE_EXTEND_MM),
            _offset_point_along_angle(pin_x, pin_y, (angle + 270) % 360, WIRE_EXTEND_MM),
            (pin_x, pin_y),
        ]

    seen_candidates = {(round(x, 2), round(y, 2)) for x, y in candidates}
    for origin_x, origin_y in ((stub_x, stub_y), (pin_x, pin_y)):
        for step_count in range(2, 5):
            for x_steps in range(-step_count, step_count + 1):
                for y_steps in range(-step_count, step_count + 1):
                    if max(abs(x_steps), abs(y_steps)) != step_count:
                        continue
                    candidate = (
                        round(origin_x + (x_steps * WIRE_EXTEND_MM), 2),
                        round(origin_y + (y_steps * WIRE_EXTEND_MM), 2),
                    )
                    if candidate in seen_candidates:
                        continue
                    seen_candidates.add(candidate)
                    candidates.append(candidate)

    best_route: list[WireSegment] | None = None
    best_anchor = (stub_x, stub_y)
    best_key: tuple[int, int, int, float, int, int] | None = None
    for index, (candidate_x, candidate_y) in enumerate(candidates):
        candidate = (round(candidate_x, 2), round(candidate_y, 2))
        if candidate == start:
            route: list[WireSegment] = []
        else:
            route = _best_direct_route_with_protected_points(
                pin_x,
                pin_y,
                candidate_x,
                candidate_y,
                protected_points=blocked_points,
            )
        route_score = _route_protected_point_score(
            route,
            protected_points=blocked_points,
            excluded_points={start, candidate},
        )
        occupied_penalty = 1 if candidate in blocked_points else 0
        pin_penalty = 1 if candidate == start else 0
        key = (
            occupied_penalty,
            route_score,
            pin_penalty,
            _wire_path_length(route),
            len(route),
            index,
        )
        if best_key is None or key < best_key:
            best_key = key
            best_route = route
            best_anchor = (candidate_x, candidate_y)

    return best_route or [], best_anchor[0], best_anchor[1]


def _append_pin_endpoint_labels(
    routing: NetRouting,
    *,
    net_name: str,
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    protected: _ProtectedPointContext | None = None,
    prefer_stub_anchor: bool = False,
) -> None:
    for pin_ref, (wx, wy, wa) in known:
        label_anchor = None
        if prefer_stub_anchor:
            label_anchor = _safe_stub_label_anchor(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_label_points=_occupied_label_points(routing),
                protected_points=protected.points if protected else None,
                shared_protected_points=protected.shared_points if protected else None,
            )
            if label_anchor is None:
                label_anchor = _safe_pin_label_anchor(
                    pin_point=(wx, wy),
                    occupied_label_points=_occupied_label_points(routing),
                    protected_points=protected.points if protected else None,
                )
        if label_anchor is None:
            occupied_points = _occupied_wire_points(routing.wires) | _occupied_label_points(routing)
            label_route, ex, ey = _label_attachment_plan(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_points=occupied_points,
                protected=protected,
                prefer_perpendicular=True,
            )
        else:
            label_route = []
            ex, ey = label_anchor
        routing.wires.extend(label_route)
        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net_name))
        label_angle = int((wa + 180) % 360)
        if net_name.startswith("/"):
            routing.global_labels.append(GlobalLabelPlacement(net_name, ex, ey, label_angle))
        else:
            routing.labels.append(NetLabel(net_name, ex, ey, label_angle))


# ---------------------------------------------------------------------------
# Power symbol helpers
# ---------------------------------------------------------------------------
def _power_label_angle_for_pin(pin_angle: float) -> int:
    """Return the outward-facing label angle for a power pin stub."""
    return int((pin_angle + 180) % 360)


def _power_symbol_angle(net_name: str, default_angle: int) -> int:
    """Return the preferred placed-symbol angle for a power net."""
    if net_name.upper() == "GND":
        return 0
    return default_angle


def _power_cluster_angle(points: list[tuple[float, float]]) -> int:
    """Choose an outward direction for a shared power label cluster."""
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
    """Return an off-axis fallback position for power nets missing a library symbol."""
    normalized = angle % 360
    fallback_angle = 270 if normalized in {0, 180} else 180
    fx, fy = _offset_point_along_angle(x, y, fallback_angle, _POWER_LABEL_CLEARANCE_MM)
    return fx, fy, fallback_angle


def _append_direct_power_symbol(
    routing: NetRouting,
    *,
    net_name: str,
    pin_ref: PinRefIR,
    endpoint: tuple[float, float, float],
    protected: _ProtectedPointContext,
) -> None:
    wx, wy, wa = endpoint
    occupied_label_points = _occupied_label_points(routing)
    anchor = _safe_stub_label_anchor(
        pin_point=(wx, wy),
        pin_angle=wa,
        occupied_label_points=occupied_label_points,
        protected_points=protected.points,
        shared_protected_points=protected.shared_points,
    )
    if anchor is None:
        occupied_points = _occupied_wire_points(routing.wires) | occupied_label_points
        route, anchor_x, anchor_y = _label_attachment_plan(
            pin_point=(wx, wy),
            pin_angle=wa,
            occupied_points=occupied_points,
            protected=_ProtectedPointContext(
                protected.points | occupied_points,
                protected.shared_points,
            ),
            prefer_perpendicular=True,
        )
        if route:
            connection_angle = _segment_label_angle(route[-1])
        else:
            connection_angle = _power_label_angle_for_pin(wa)
    else:
        anchor_x, anchor_y = anchor
        route = []
        if not (
            math.isclose(wx, anchor_x, abs_tol=0.01) and math.isclose(wy, anchor_y, abs_tol=0.01)
        ):
            route.append(WireSegment(wx, wy, anchor_x, anchor_y))
        connection_angle = _power_label_angle_for_pin(wa)
    px, py = _offset_point_along_angle(
        anchor_x,
        anchor_y,
        connection_angle,
        _POWER_LABEL_CLEARANCE_MM,
    )
    if not (math.isclose(anchor_x, px, abs_tol=0.01) and math.isclose(anchor_y, py, abs_tol=0.01)):
        route.append(WireSegment(anchor_x, anchor_y, px, py))
    symbol_angle = _power_symbol_angle(net_name, connection_angle)
    routing.wires.extend(route)
    routing.power_symbols.append(PowerSymbolPlacement(net_name, px, py, symbol_angle))
    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net_name))


def _foreign_attachment_points_for_known(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]] | None = None,
) -> set[tuple[float, float]]:
    current_pin_points = {(round(wx, 2), round(wy, 2)) for _pin_ref, (wx, wy, _wa) in known}
    current_stub_points = {
        (round(ex, 2), round(ey, 2))
        for _pin_ref, (wx, wy, wa) in known
        for ex, ey in [_stub_end(wx, wy, wa)]
    }
    foreign_stub_points = {
        point
        for point in protected_stub_points
        if point not in current_stub_points or point in (shared_protected_stub_points or set())
    }
    return (protected_pin_points - current_pin_points) | foreign_stub_points


def _known_pin_stub_hits_foreign_attachment(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]] | None = None,
) -> bool:
    foreign_attachment_points = _foreign_attachment_points_for_known(
        known,
        protected_pin_points=protected_pin_points,
        protected_stub_points=protected_stub_points,
        shared_protected_stub_points=shared_protected_stub_points,
    )
    return any(
        (round(ex, 2), round(ey, 2)) in foreign_attachment_points
        for _pin_ref, (wx, wy, wa) in known
        for ex, ey in [_stub_end(wx, wy, wa)]
    )


def _aligned_power_cluster_route_points(
    cluster: list[tuple[PinRefIR, tuple[float, float, float]]],
) -> tuple[list[WireSegment], list[tuple[float, float]], str | None, float | None]:
    """Return stub wires, stub-end points, and any fully aligned stub axis."""
    stub_points = [_stub_end(x, y, angle) for _pin_ref, (x, y, angle) in cluster]
    shared_x = round(stub_points[0][0], 2) if stub_points else None
    shared_y = round(stub_points[0][1], 2) if stub_points else None
    aligned_x = (
        shared_x
        if shared_x is not None
        and all(math.isclose(point[0], shared_x, abs_tol=0.01) for point in stub_points)
        else None
    )
    aligned_y = (
        shared_y
        if shared_y is not None
        and all(math.isclose(point[1], shared_y, abs_tol=0.01) for point in stub_points)
        else None
    )

    stub_wires: list[WireSegment] = []
    route_points: list[tuple[float, float]] = []
    for _pin_ref, (wx, wy, wa) in cluster:
        ex, ey = _stub_end(wx, wy, wa)
        stub_wires.append(WireSegment(wx, wy, ex, ey))
        route_points.append((ex, ey))
    if aligned_x is not None:
        return stub_wires, route_points, "vertical", aligned_x
    if aligned_y is not None:
        return stub_wires, route_points, "horizontal", aligned_y
    return stub_wires, route_points, None, None


if TYPE_CHECKING:
    from .circuit_ir import PinRefIR
