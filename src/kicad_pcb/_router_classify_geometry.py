"""Wire/box geometry helpers and local routing pattern detectors for the router."""

from __future__ import annotations

import math
from collections.abc import Mapping

from ._router_types import (
    SYMBOL_HALF_SIZE_MM,
    WIRE_EXTEND_MM,
    WireSegment,
)
from .component_types import (
    component_type as _component_type,
)
from .component_types import (
    is_power_net as _base_is_power_net_name,
)
from .component_types import (
    power_rail_polarity,
)


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


def _compact_cluster_detour_x(
    ref: str,
    center_x: float,
    *,
    crossing_own_member: bool = False,
) -> float:
    """Return a conservative compact-cluster detour X coordinate."""
    multiplier = 3 if crossing_own_member or _component_type(ref) == "ic" else 2
    return center_x - (multiplier * SYMBOL_HALF_SIZE_MM)


def _detour_segment(
    seg: WireSegment,
    bx: float,
    by: float,
    half: float,
) -> list[WireSegment]:
    """Replace *seg* with a detour path that bypasses the AABB at *(bx, by)*."""
    if math.isclose(seg.y1, seg.y2, abs_tol=0.01):  # horizontal
        detour_y = by - half - half
        left_edge = bx - half
        right_edge = bx + half
        moving_right = seg.x2 >= seg.x1
        enter_x = left_edge if moving_right else right_edge
        exit_x = right_edge if moving_right else left_edge
        return [
            WireSegment(seg.x1, seg.y1, enter_x, seg.y1),
            WireSegment(enter_x, seg.y1, enter_x, detour_y),
            WireSegment(enter_x, detour_y, exit_x, detour_y),
            WireSegment(exit_x, detour_y, exit_x, seg.y2),
            WireSegment(exit_x, seg.y2, seg.x2, seg.y2),
        ]
    if math.isclose(seg.x1, seg.x2, abs_tol=0.01):  # vertical
        detour_x = bx - half - half
        top_edge = by - half
        bottom_edge = by + half
        moving_down = seg.y2 >= seg.y1
        enter_y = top_edge if moving_down else bottom_edge
        exit_y = bottom_edge if moving_down else top_edge
        return [
            WireSegment(seg.x1, seg.y1, seg.x1, enter_y),
            WireSegment(seg.x1, enter_y, detour_x, enter_y),
            WireSegment(detour_x, enter_y, detour_x, exit_y),
            WireSegment(detour_x, exit_y, seg.x1, exit_y),
            WireSegment(seg.x1, exit_y, seg.x2, seg.y2),
        ]
    return [seg]


def detect_body_crossings(
    wires: list[WireSegment],
    positions: Mapping[str, tuple[float, float, float | None]],
) -> list[WireSegment]:
    """Reroute wire segments that pass through a component bounding box."""
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
                break
        if not replaced:
            result.append(seg)
    return result


# ---------------------------------------------------------------------------
# Local routing pattern detectors
# ---------------------------------------------------------------------------


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
    """Return True when a single vertical lane is just a compact rightward tail."""
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


def _is_compact_horizontal_stage_tail(
    endpoints: list[tuple[float, float]],
    *,
    axis: str,
) -> bool:
    """Return True when a local 3-pin net reads as stage then downstream tail."""
    if axis != "horizontal" or len(endpoints) != 3:
        return False

    left, middle, right = sorted(endpoints, key=lambda point: (point[0], point[1]))
    left_x, left_y = left
    middle_x, middle_y = middle
    right_x, right_y = right

    left_gap = middle_x - left_x
    right_gap = right_x - middle_x
    if left_gap <= 0.01 or right_gap <= 0.01:
        return False
    if left_gap > (2 * WIRE_EXTEND_MM) + 0.05:
        return False
    if right_gap <= left_gap + 0.01:
        return False
    if abs(left_y - right_y) > WIRE_EXTEND_MM + 0.05:
        return False

    stage_offset = min(abs(middle_y - left_y), abs(middle_y - right_y))
    return stage_offset > WIRE_EXTEND_MM + 0.05
