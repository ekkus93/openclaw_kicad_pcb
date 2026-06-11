"""Label visibility/promotion helpers for the router."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from ._router_types import (
    GlobalLabelPlacement,
    LabelPolicy,
    NetLabel,
    NetRouting,
    _ProtectedPointContext,
)
from .block_detection import BlockLayout, BlockRole, is_input_like_role, is_output_like_role
from .component_types import (
    component_type as _component_type,
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
