"""Compact decoupling cluster routing: ground and power rail clusters."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR

from ._router_classify import _compact_cluster_detour_x, _wire_crosses_box
from ._router_geometry import _snap_grid, _stub_end
from ._router_types import (
    SYMBOL_HALF_SIZE_MM,
    JunctionPoint,
    WireSegment,
)
from ._router_write import _simplify_wires

# ---------------------------------------------------------------------------
# Compact local ground cluster
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Compact local decoupling ground cluster
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Compact local decoupling power cluster
# ---------------------------------------------------------------------------


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
