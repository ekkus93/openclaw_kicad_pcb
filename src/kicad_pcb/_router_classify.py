"""Net classification helpers for the router."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ._router_types import (
    _POWER_CLUSTER_RADIUS_MM,
    SYMBOL_HALF_SIZE_MM,
    WIRE_EXTEND_MM,
    GlobalLabelPlacement,
    LabelPolicy,
    NetLabel,
    NetRouting,
    WireSegment,
    _ProtectedPointContext,
)
from .block_detection import BlockLayout, BlockRole, is_input_like_role, is_output_like_role
from .component_types import (
    component_type as _component_type,
)
from .component_types import (
    is_ground_like_name,
    power_rail_polarity,
)
from .component_types import (
    is_power_net as _base_is_power_net_name,
)

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR

# ---------------------------------------------------------------------------
# Role-priority tables (used by label candidate prioritisation)
# ---------------------------------------------------------------------------
_SIGNAL_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.INPUT: 0,
    BlockRole.INTERSTAGE: 0,
    BlockRole.OUTPUT: 0,
    BlockRole.PRECONDITIONING: 1,
    BlockRole.OUTPUT_CONDITIONING: 1,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.OPAMP_CORE: 3,
    BlockRole.FEEDBACK: 4,
    BlockRole.POWER_ENTRY: 5,
    BlockRole.DECOUPLING: 5,
}

_CONNECTOR_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.INPUT: 0,
    BlockRole.OUTPUT: 0,
    BlockRole.PRECONDITIONING: 1,
    BlockRole.OUTPUT_CONDITIONING: 1,
    BlockRole.INTERSTAGE: 2,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.OPAMP_CORE: 3,
    BlockRole.FEEDBACK: 4,
    BlockRole.POWER_ENTRY: 5,
    BlockRole.DECOUPLING: 5,
}

_FEEDBACK_LABEL_ROLE_PRIORITY: dict[BlockRole, int] = {
    BlockRole.FEEDBACK: 0,
    BlockRole.OPAMP_CORE: 1,
    BlockRole.BUFFER_STAGE: 2,
    BlockRole.INTERSTAGE: 3,
    BlockRole.PRECONDITIONING: 4,
    BlockRole.OUTPUT_CONDITIONING: 4,
    BlockRole.INPUT: 5,
    BlockRole.OUTPUT: 5,
    BlockRole.POWER_ENTRY: 6,
    BlockRole.DECOUPLING: 6,
}


# ---------------------------------------------------------------------------
# Small geometry helpers also used by classify
# ---------------------------------------------------------------------------
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


def _cluster_power_pins(
    pins: list[tuple[PinRefIR, tuple[float, float, float]]],
    radius: float = _POWER_CLUSTER_RADIUS_MM,
) -> list[list[tuple[PinRefIR, tuple[float, float, float]]]]:
    """Group power pins by proximity into clusters sharing a symbol."""
    from ._router_geometry import _snap_grid  # noqa: PLC0415

    if not pins:
        return []

    clusters: list[list[tuple[PinRefIR, tuple[float, float, float]]]] = []

    for pin, (px, py, pangle) in pins:
        best_cluster_idx: int | None = None
        best_dist = float("inf")

        for idx, cluster in enumerate(clusters):
            cx = _snap_grid(sum(cpx for _, (cpx, _, _) in cluster) / len(cluster))
            cy = _snap_grid(sum(cpy for _, (_, cpy, _) in cluster) / len(cluster))
            dist = math.hypot(px - cx, py - cy)

            if dist < radius and dist < best_dist:
                best_dist = dist
                best_cluster_idx = idx

        if best_cluster_idx is not None:
            clusters[best_cluster_idx].append((pin, (px, py, pangle)))
        else:
            clusters.append([(pin, (px, py, pangle))])

    return clusters


# ---------------------------------------------------------------------------
# Net classification
# ---------------------------------------------------------------------------
def _net_member_roles(
    refs: tuple[str, ...],
    block_layout: BlockLayout | None,
) -> set[BlockRole]:
    """Return the set of block roles present on *refs* when available."""
    if block_layout is None:
        return set()
    return {
        assignment.role
        for ref in refs
        if (assignment := block_layout.assignments.get(ref)) is not None
    }


def _classify_routing_net_from_roles(
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    has_connector: bool,
) -> (
    Literal[
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ]
    | None
):
    """Return a structural routing class derived from block roles when possible."""
    roles = _net_member_roles(refs, block_layout)
    if not roles:
        return None

    if BlockRole.FEEDBACK in roles:
        return "feedback"

    if (
        has_connector
        and len(refs) <= 3
        and any(is_input_like_role(role) or is_output_like_role(role) for role in roles)
    ):
        return "connector_attachment"

    signal_roles = {
        BlockRole.INPUT,
        BlockRole.PRECONDITIONING,
        BlockRole.OPAMP_CORE,
        BlockRole.INTERSTAGE,
        BlockRole.BUFFER_STAGE,
        BlockRole.OUTPUT,
        BlockRole.OUTPUT_CONDITIONING,
    }
    if roles & signal_roles:
        return "signal_chain"

    return None


def _classify_routing_net(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None = None,
) -> Literal[
    "power",
    "local_decoupling",
    "shunt_ground",
    "connector_only",
    "connector_attachment",
    "signal_chain",
    "feedback",
    "generic_signal",
]:
    """Return a first-class routing taxonomy for one net."""
    component_kinds = tuple(_component_type(ref) for ref in refs)
    has_connector = any(kind == "connector" for kind in component_kinds)
    has_ic = any(kind == "ic" for kind in component_kinds)
    has_capacitor = any(ref.upper().startswith("C") for ref in refs)
    all_connectors = bool(refs) and all(kind == "connector" for kind in component_kinds)
    all_passive_or_connector = bool(refs) and all(
        kind in {"passive", "connector"} for kind in component_kinds
    )
    upper_name = net_name.upper()

    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ] = "generic_signal"

    if _is_power_net_name(net_name):
        if is_ground_like_name(net_name) and len(refs) <= 3 and all_passive_or_connector:
            classification = "shunt_ground"
        elif len(refs) <= 3 and has_capacitor and has_ic:
            classification = "local_decoupling"
        else:
            classification = "power"
    elif all_connectors:
        classification = "connector_only"
    else:
        structural_classification = _classify_routing_net_from_roles(
            refs,
            block_layout=block_layout,
            has_connector=has_connector,
        )
        if structural_classification is not None:
            return structural_classification

        if any(token in upper_name for token in ("INV", "FB", "FEEDBACK")):
            classification = "feedback"
        elif has_connector and len(refs) <= 3:
            classification = "connector_attachment"
        elif (
            has_ic
            or has_connector
            or any(token in upper_name for token in ("IN", "OUT", "BUF", "STAGE", "VOL", "HP"))
        ):
            classification = "signal_chain"

    return classification


def _classification_prefers_compact_tail(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should try the compact tail heuristic."""
    return classification in {"connector_attachment", "signal_chain"}


def _classification_prefers_local_chain(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should prefer a compact local chain."""
    return classification in {"connector_attachment", "signal_chain", "feedback"}


def _classification_prefers_short_local_direct_route(
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a routing class should stay locally wired before using labels."""
    return classification in {"connector_attachment", "signal_chain", "feedback"}


def _label_role_priority(
    role: BlockRole | None,
    *,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> int:
    """Return the sort priority for visible label placement on a routed net."""
    if role is None:
        return 99
    if classification == "feedback":
        return _FEEDBACK_LABEL_ROLE_PRIORITY.get(role, 99)
    if classification == "connector_attachment":
        return _CONNECTOR_LABEL_ROLE_PRIORITY.get(role, 99)
    if classification == "signal_chain":
        return _SIGNAL_LABEL_ROLE_PRIORITY.get(role, 99)
    return 99


def _name_suggests_important_signal(net_name: str) -> bool:
    """Return True when *net_name* reads like a user-meaningful stage handoff."""
    upper_name = net_name.upper()
    if any(token in upper_name for token in ("RAW", "INV", "FB", "FEEDBACK", "AFTER_")):
        return False
    return any(
        token in upper_name
        for token in (
            "LEFT_IN",
            "RIGHT_IN",
            "_IN",
            "IN_",
            "VOL",
            "STAGE",
            "BUF",
            "HP",
            "_OUT",
            "OUT_",
        )
    )


def _roles_mark_important_display_seam(
    roles: set[BlockRole],
    *,
    has_connector: bool,
) -> bool:
    """Return True when *roles* form a stage seam worth keeping visibly labeled."""
    if has_connector and any(
        is_input_like_role(role) or is_output_like_role(role) for role in roles
    ):
        return True
    if BlockRole.INTERSTAGE in roles:
        return True
    if BlockRole.OPAMP_CORE in roles and BlockRole.PRECONDITIONING in roles:
        return True
    return BlockRole.INPUT in roles and BlockRole.PRECONDITIONING in roles


def _net_is_important_for_display(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> bool:
    """Return True when a signal net should stay visibly labeled in important mode."""
    roles = _net_member_roles(refs, block_layout)
    if roles:
        return _roles_mark_important_display_seam(
            roles,
            has_connector=any(_component_type(ref) == "connector" for ref in refs),
        )

    if classification not in {"connector_attachment", "signal_chain"}:
        return False

    return _name_suggests_important_signal(net_name)


def _should_promote_visible_label(
    net_name: str,
    refs: tuple[str, ...],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
    policy: LabelPolicy,
) -> bool:
    """Return True when the selected label mode should add a visible label."""
    if classification in {"power", "local_decoupling", "shunt_ground"}:
        return False
    if policy.force_all_signal_labels:
        return True
    if not policy.force_important_labels:
        return False
    return _net_is_important_for_display(
        net_name,
        refs,
        block_layout=block_layout,
        classification=classification,
    )


def _prioritize_label_candidates(
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    block_layout: BlockLayout | None,
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ],
) -> list[tuple[PinRefIR, tuple[float, float, float]]]:
    """Return *known* reordered so capped labels favor structurally important seams."""
    if block_layout is None or classification not in {
        "connector_attachment",
        "signal_chain",
        "feedback",
    }:
        return known

    scored: list[tuple[int, int, tuple[PinRefIR, tuple[float, float, float]]]] = []
    for index, candidate in enumerate(known):
        pin_ref, _endpoint = candidate
        scored.append(
            (
                _label_role_priority(
                    block_layout.get_role(pin_ref.ref),
                    classification=classification,
                ),
                index,
                candidate,
            )
        )
    return [candidate for _priority, _index, candidate in sorted(scored)]


@dataclass(frozen=True)
class _VisibleLabelPromotion:
    net_name: str
    refs: tuple[str, ...]
    block_layout: BlockLayout | None
    classification: Literal[
        "power",
        "local_decoupling",
        "shunt_ground",
        "connector_only",
        "connector_attachment",
        "signal_chain",
        "feedback",
        "generic_signal",
    ]
    label_candidates: list[tuple[PinRefIR, tuple[float, float, float]]]


def _append_promoted_visible_label(
    routing: NetRouting,
    *,
    promotion: _VisibleLabelPromotion,
    policy: LabelPolicy,
    protected_points: set[tuple[float, float]] | None = None,
    shared_protected_points: set[tuple[float, float]] | None = None,
) -> None:
    """Add one visible label when the selected mode promotes this signal net."""
    from ._router_geometry import (  # noqa: PLC0415
        _label_attachment_plan,
        _occupied_label_points,
        _occupied_wire_points,
        _safe_stub_label_anchor,
    )

    if not promotion.label_candidates:
        return

    promoted_candidate = promotion.label_candidates[0]
    _pin_ref, (wx, wy, wa) = promoted_candidate
    if not _should_promote_visible_label(
        promotion.net_name,
        promotion.refs,
        block_layout=promotion.block_layout,
        classification=promotion.classification,
        policy=policy,
    ):
        return

    label_anchor = _safe_stub_label_anchor(
        pin_point=(wx, wy),
        pin_angle=wa,
        occupied_label_points=_occupied_label_points(routing),
        protected_points=protected_points,
        shared_protected_points=shared_protected_points,
    )
    if label_anchor is None:
        occupied_points = _occupied_wire_points(routing.wires) | _occupied_label_points(routing)
        label_route, ex, ey = _label_attachment_plan(
            pin_point=(wx, wy),
            pin_angle=wa,
            occupied_points=occupied_points,
            protected=(
                _ProtectedPointContext(protected_points, shared_protected_points)
                if protected_points is not None
                else None
            ),
            prefer_perpendicular=True,
        )
    else:
        label_route = []
        ex, ey = label_anchor
    routing.wires.extend(label_route)
    label_angle = int((wa + 180) % 360)
    if promotion.net_name.startswith("/"):
        global_label = GlobalLabelPlacement(promotion.net_name, ex, ey, label_angle)
        if global_label not in routing.global_labels:
            routing.global_labels.append(global_label)
        return

    local_label = NetLabel(promotion.net_name, ex, ey, label_angle)
    if local_label not in routing.labels:
        routing.labels.append(local_label)


# ---------------------------------------------------------------------------
# Local ladder net helpers
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
