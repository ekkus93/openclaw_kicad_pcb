"""Power symbol placement and cluster routing helpers."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ._router_geometry_basic import (
    _offset_point_along_angle,
    _segment_label_angle,
    _stub_end,
)
from ._router_geometry_labels import (
    _label_attachment_plan,
    _occupied_label_points,
    _occupied_wire_points,
    _safe_stub_label_anchor,
)
from ._router_types import (
    _POWER_LABEL_CLEARANCE_MM,
    BindMarker,
    NetRouting,
    PowerSymbolPlacement,
    WireSegment,
    _ProtectedPointContext,
)

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR


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
