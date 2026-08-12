"""2-pin direct route for signal nets."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import NetIR, PinRefIR

from ._router_classify import (
    _append_promoted_visible_label,
    _classification_prefers_short_local_direct_route,
    _prioritize_label_candidates,
    _tier_distance,
    _VisibleLabelPromotion,
)
from ._router_geometry import (
    _append_pin_endpoint_labels,
    _best_direct_route_with_protected_points,
    _is_connector_passive_edge,
    _known_pin_stub_hits_foreign_attachment,
    _manhattan,
    _segment_label_angle,
    _stub_end,
)
from ._router_types import (
    MAX_DIRECT_DIST_MM,
    MAX_DIRECT_WIRE_MM,
    BindMarker,
    GlobalLabelPlacement,
    LabelPolicy,
    NetLabel,
    NetRouting,
    RouteDecision,
    RoutingClassification,
    WireSegment,
    _ProtectedPointContext,
)
from .block_detection import BlockLayout


def _append_direct_net_identity_label(
    routing: NetRouting,
    *,
    net_name: str,
    direct_route: list[WireSegment],
    fallback_anchor: tuple[float, float, float],
) -> None:
    """Attach one KiCad net-name object to an already-physical direct route.

    KiCad wires carry connectivity but not the authoritative net name. One
    label on the connected route preserves that identity without using labels
    to bridge the two endpoints: if the physical wire is broken, electrical
    verification still observes different terminal partitions.
    """
    if any(label.name == net_name for label in routing.labels) or any(
        label.name == net_name for label in routing.global_labels
    ):
        return

    if direct_route:
        anchor_segment = direct_route[0]
        label_x = anchor_segment.x2
        label_y = anchor_segment.y2
        label_angle = _segment_label_angle(anchor_segment)
    else:
        label_x, label_y, pin_angle = fallback_anchor
        label_angle = int((pin_angle + 180) % 360)

    if net_name.startswith("/"):
        routing.global_labels.append(GlobalLabelPlacement(net_name, label_x, label_y, label_angle))
    else:
        routing.labels.append(NetLabel(net_name, label_x, label_y, label_angle))


def _route_direct_net(  # noqa: PLR0913
    routing: NetRouting,
    net: NetIR,
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    use_bus: bool,
    policy: LabelPolicy,
    block_layout: BlockLayout | None,
    tiers: dict[str, int] | None,
    net_classification: RoutingClassification,
    pins_count: int,
    net_refs: tuple[str, ...],
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]],
    foreign_attachment_points: set[tuple[float, float]],
    dynamic_protected_points: set[tuple[float, float]],
) -> bool:
    """Route a 2-pin signal net; returns True if handled, False to fall through."""
    strategy = "local_labels"
    heuristic_override: str | None = None
    routed_directly = False

    p0, (wx0, wy0, wa0) = known[0]
    p1, (wx1, wy1, wa1) = known[1]
    ex0, ey0 = _stub_end(wx0, wy0, wa0)
    ex1, ey1 = _stub_end(wx1, wy1, wa1)
    if (
        use_bus
        and net.name.startswith("/")
        and _known_pin_stub_hits_foreign_attachment(
            known,
            protected_pin_points=protected_pin_points,
            protected_stub_points=protected_stub_points,
            shared_protected_stub_points=shared_protected_stub_points,
        )
    ):
        _append_pin_endpoint_labels(
            routing,
            net_name=net.name,
            known=_prioritize_label_candidates(
                known,
                block_layout=block_layout,
                classification=net_classification,
            ),
            protected=_ProtectedPointContext(
                foreign_attachment_points,
                shared_protected_stub_points,
            ),
        )
        routed_directly = True
        strategy = "global_labels" if net.name.startswith("/") else "local_labels"
        heuristic_override = "foreign_attachment_label_breakout"
    elif (
        use_bus
        and net_classification == "connector_attachment"
        and net.name.startswith("/")
        and abs(ex1 - ex0) > 40.0
    ):
        _append_pin_endpoint_labels(
            routing,
            net_name=net.name,
            known=_prioritize_label_candidates(
                known,
                block_layout=block_layout,
                classification=net_classification,
            ),
            prefer_stub_anchor=True,
        )
        routed_directly = True
        strategy = "global_labels"
        heuristic_override = "connector_label_breakout"

    if routed_directly:
        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification=net_classification,
                strategy=strategy,
                pin_count=pins_count,
                known_pin_count=len(known),
                unknown_pin_count=0,
                use_bus=use_bus,
                heuristic_override=heuristic_override,
            )
        )
        return True

    manhattan = _manhattan(ex0, ey0, ex1, ey1)
    if tiers is not None:
        tdist = _tier_distance(p0.ref, p1.ref, tiers)
        short_local_override = (
            _classification_prefers_short_local_direct_route(net_classification)
            and manhattan <= MAX_DIRECT_WIRE_MM
        )
        can_direct = manhattan <= MAX_DIRECT_DIST_MM and (
            tdist <= 1 or _is_connector_passive_edge(p0.ref, p1.ref) or short_local_override
        )
    else:
        can_direct = manhattan <= MAX_DIRECT_DIST_MM

    if not can_direct:
        return False

    direct_route = _best_direct_route_with_protected_points(
        ex0,
        ey0,
        ex1,
        ey1,
        protected_points=dynamic_protected_points,
    )
    routing.wires.append(WireSegment(wx0, wy0, ex0, ey0))
    routing.wires.append(WireSegment(wx1, wy1, ex1, ey1))
    routing.wires.extend(direct_route)
    routing.bind_markers.append(BindMarker(p0.ref, p0.pin, net.name))
    routing.bind_markers.append(BindMarker(p1.ref, p1.pin, net.name))
    if policy.force_all_signal_labels and direct_route:
        anchor_segment = direct_route[0]
        label_x = anchor_segment.x2
        label_y = anchor_segment.y2
        label_angle = _segment_label_angle(anchor_segment)
        if net.name.startswith("/"):
            routing.global_labels.append(
                GlobalLabelPlacement(net.name, label_x, label_y, label_angle)
            )
        else:
            routing.labels.append(
                NetLabel(
                    net.name,
                    label_x,
                    label_y,
                    label_angle,
                )
            )
    else:
        _append_promoted_visible_label(
            routing,
            promotion=_VisibleLabelPromotion(
                net_name=net.name,
                refs=net_refs,
                block_layout=block_layout,
                classification=net_classification,
                label_candidates=known,
            ),
            policy=policy,
            protected_points=foreign_attachment_points,
            shared_protected_points=shared_protected_stub_points,
        )
    _append_direct_net_identity_label(
        routing,
        net_name=net.name,
        direct_route=direct_route,
        fallback_anchor=(ex0, ey0, wa0),
    )
    routing.route_decisions.append(
        RouteDecision(
            net_name=net.name,
            classification=net_classification,
            strategy="direct",
            pin_count=pins_count,
            known_pin_count=len(known),
            unknown_pin_count=0,
            use_bus=use_bus,
            heuristic_override=None,
        )
    )
    return True
