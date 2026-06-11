"""Occupied-point tracking, direct-route selection, and label placement."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._router_geometry_basic import (
    _horizontal_first_l_route,
    _offset_point_along_angle,
    _route_protected_point_score,
    _snap_grid,
    _stub_end,
    _three_segment_route_via_x,
    _three_segment_route_via_y,
    _vertical_first_l_route,
    _wire_path_length,
)
from ._router_types import (
    WIRE_EXTEND_MM,
    BindMarker,
    GlobalLabelPlacement,
    NetLabel,
    NetRouting,
    WireSegment,
    _ProtectedPointContext,
)

# NOTE: _simplify_wires is defined in _router_write.py. It is imported lazily
# inside _best_direct_route_with_protected_points to avoid circular imports.

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR


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
