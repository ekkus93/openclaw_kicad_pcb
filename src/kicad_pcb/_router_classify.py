"""Net classification helpers for the router."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Literal

from ._router_classify_geometry import (  # noqa: F401
    _boxes_touch_or_overlap,
    _compact_cluster_detour_x,
    _detour_segment,
    _is_compact_horizontal_stage_tail,
    _is_compact_rightward_tail,
    _is_local_ladder_net,
    _is_power_net_name,
    _point_in_or_on_box,
    _preferred_shared_lane,
    _tier_distance,
    _wire_crosses_box,
    detect_body_crossings,
)
from ._router_classify_labels import (  # noqa: F401
    _CONNECTOR_LABEL_ROLE_PRIORITY,
    _FEEDBACK_LABEL_ROLE_PRIORITY,
    _SIGNAL_LABEL_ROLE_PRIORITY,
    _append_promoted_visible_label,
    _label_role_priority,
    _name_suggests_important_signal,
    _net_is_important_for_display,
    _net_member_roles,
    _prioritize_label_candidates,
    _roles_mark_important_display_seam,
    _should_promote_visible_label,
    _VisibleLabelPromotion,
)
from ._router_types import (
    _POWER_CLUSTER_RADIUS_MM,
    RoutingClassification,
)
from .block_detection import BlockLayout, BlockRole, is_input_like_role, is_output_like_role
from .component_types import (
    component_type as _component_type,
)
from .component_types import (
    is_ground_like_name,
)

if TYPE_CHECKING:
    from .circuit_ir import PinRefIR


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
) -> RoutingClassification:
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
