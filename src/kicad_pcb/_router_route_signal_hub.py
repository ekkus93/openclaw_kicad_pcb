"""Multi-pin hub route for signal nets."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import NetIR, PinRefIR

from ._router_classify import (
    _append_promoted_visible_label,
    _classification_prefers_compact_tail,
    _classification_prefers_local_chain,
    _prioritize_label_candidates,
    _VisibleLabelPromotion,
)
from ._router_geometry import (
    _append_pin_endpoint_labels,
    _known_pin_stub_hits_foreign_attachment,
    _route_candidate_key,
    _stub_end,
)
from ._router_strategies import (
    _buffer_follower_feedback_route,
    _compact_aligned_chain_route,
    _hub_route,
    _infer_bounded_local_lane_plan,
    _is_small_analog_chain_candidate,
    _prefer_chain_route,
    _protected_shared_lane_route,
    _shared_lane_route,
    _spine_route,
)
from ._router_types import (
    BindMarker,
    LabelPolicy,
    NetRouting,
    RouteDecision,
    RoutingClassification,
    RoutingHeuristicPolicy,
    SharedLanePlan,
    WireSegment,
    _ProtectedPointContext,
)
from .block_detection import BlockLayout


def _route_hub_net(  # noqa: PLR0912, PLR0913, PLR0915
    routing: NetRouting,
    net: NetIR,
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    *,
    use_bus: bool,
    policy: LabelPolicy,
    heuristic_policy: RoutingHeuristicPolicy,
    block_layout: BlockLayout | None,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
    ladder_routes: dict[str, SharedLanePlan],
    net_classification: RoutingClassification,
    pins_count: int,
    net_refs: tuple[str, ...],
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]],
    foreign_attachment_points: set[tuple[float, float]],
    dynamic_protected_points: set[tuple[float, float]],
) -> bool:
    """Route a 3-to-HUB_MAX_DEGREE pin signal net; always returns True."""
    wire_start = len(routing.wires)
    bind_start = len(routing.bind_markers)
    stub_ends = [_stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in known]
    xs = [point[0] for point in stub_ends]
    compact_tail_plan = (
        ladder_routes[net.name]
        if use_bus and net.name in ladder_routes
        else _infer_bounded_local_lane_plan(stub_ends)
    )
    compact_tail_candidate = (
        use_bus
        and _classification_prefers_compact_tail(net_classification)
        and heuristic_policy.route_compact_signal_tail(
            stub_ends,
            inferred_plan=compact_tail_plan,
            protected_points=dynamic_protected_points,
            positions=positions,
        )
        is not None
    )
    if (
        use_bus
        and not compact_tail_candidate
        and net_classification not in {"connector_attachment", "signal_chain"}
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
            prefer_stub_anchor=True,
        )
        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification=net_classification,
                strategy=("global_labels" if net.name.startswith("/") else "local_labels"),
                pin_count=pins_count,
                known_pin_count=len(known),
                unknown_pin_count=0,
                use_bus=use_bus,
                heuristic_override="foreign_attachment_label_breakout",
            )
        )
        return True
    if (
        use_bus
        and len(known) == 3
        and net_classification == "connector_attachment"
        and (max(xs) - min(xs)) > 80.0
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
        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification=net_classification,
                strategy="global_labels",
                pin_count=pins_count,
                known_pin_count=len(known),
                unknown_pin_count=0,
                use_bus=use_bus,
                heuristic_override="connector_label_breakout",
            )
        )
        return True

    strategy = "local_labels"
    heuristic_override: str | None = None

    if use_bus and net.name in ladder_routes:
        lane_plan = ladder_routes[net.name]
        stub_ends = []
        for pin_ref, (wx, wy, wa) in known:
            ex, ey = _stub_end(wx, wy, wa)
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            if lane_plan.axis == "horizontal" and math.isclose(
                math.sin(math.radians(wa)), 0.0, abs_tol=0.01
            ):
                stub_ends.append((wx, wy))
            else:
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                stub_ends.append((ex, ey))
        compact_tail_route = None
        small_analog_candidate = False
        prefer_small_analog_chain = False
        if _classification_prefers_compact_tail(net_classification):
            compact_tail_route = heuristic_policy.route_compact_signal_tail(
                stub_ends,
                inferred_plan=lane_plan,
                protected_points=dynamic_protected_points,
                positions=positions,
            )
        small_analog_candidate = (
            use_bus
            and _classification_prefers_local_chain(net_classification)
            and heuristic_policy.enable_small_analog_local_routing
            and _is_small_analog_chain_candidate(
                stub_ends,
                refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
            )
        )
        prefer_small_analog_chain = (
            small_analog_candidate
            and heuristic_policy.should_prefer_small_analog_chain(
                stub_ends,
                inferred_plan=lane_plan,
                refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
            )
        )
        if compact_tail_route is not None:
            hub_segs, hub_junctions = compact_tail_route
            strategy = "compact_signal_tail"
            heuristic_override = "compact_output_tail"
        elif (
            use_bus
            and _classification_prefers_local_chain(net_classification)
            and heuristic_policy.enable_small_analog_local_routing
            and (
                follower_feedback_route := _buffer_follower_feedback_route(
                    known,
                    stub_ends,
                    block_layout=block_layout,
                )
            )
            is not None
        ):
            hub_segs, hub_junctions = follower_feedback_route
            strategy = "chain"
            heuristic_override = "small_analog_local_routing"
        elif prefer_small_analog_chain:
            hub_segs, hub_junctions = _compact_aligned_chain_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            strategy = "chain"
            heuristic_override = "small_analog_local_routing"
        else:
            hub_segs, hub_junctions = _shared_lane_route(
                stub_ends,
                axis=lane_plan.axis,
                coordinate=lane_plan.coordinate,
                min_bound=lane_plan.min_orthogonal,
                max_bound=lane_plan.max_orthogonal,
            )
            strategy = "shared_lane"
            if small_analog_candidate:
                heuristic_override = "small_analog_local_routing"
    else:
        stub_ends = []
        for pin_ref, (wx, wy, wa) in known:
            ex, ey = _stub_end(wx, wy, wa)
            routing.wires.append(WireSegment(wx, wy, ex, ey))
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            stub_ends.append((ex, ey))
        compact_tail_plan = _infer_bounded_local_lane_plan(stub_ends)
        compact_tail_route = None
        small_analog_candidate = False
        prefer_small_analog_chain = False
        if use_bus and _classification_prefers_compact_tail(net_classification):
            compact_tail_route = heuristic_policy.route_compact_signal_tail(
                stub_ends,
                inferred_plan=compact_tail_plan,
                protected_points=dynamic_protected_points,
                positions=positions,
            )
        small_analog_candidate = (
            use_bus
            and _classification_prefers_local_chain(net_classification)
            and heuristic_policy.enable_small_analog_local_routing
            and _is_small_analog_chain_candidate(
                stub_ends,
                refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
            )
        )
        prefer_small_analog_chain = (
            small_analog_candidate
            and heuristic_policy.should_prefer_small_analog_chain(
                stub_ends,
                inferred_plan=compact_tail_plan,
                refs=tuple(pin_ref.ref for pin_ref, _anchor in known),
            )
        )
        if compact_tail_route is not None:
            hub_segs, hub_junctions = compact_tail_route
            strategy = "compact_signal_tail"
            heuristic_override = "compact_output_tail"
        elif (
            use_bus
            and _classification_prefers_local_chain(net_classification)
            and heuristic_policy.enable_small_analog_local_routing
            and (
                follower_feedback_route := _buffer_follower_feedback_route(
                    known,
                    stub_ends,
                    block_layout=block_layout,
                )
            )
            is not None
        ):
            hub_segs, hub_junctions = follower_feedback_route
            strategy = "chain"
            heuristic_override = "small_analog_local_routing"
        elif prefer_small_analog_chain:
            hub_segs, hub_junctions = _compact_aligned_chain_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            strategy = "chain"
            heuristic_override = "small_analog_local_routing"
        elif (
            use_bus
            and _classification_prefers_local_chain(net_classification)
            and _prefer_chain_route(stub_ends)
        ):
            hub_segs, hub_junctions = _compact_aligned_chain_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            strategy = "chain"
        elif use_bus:
            hub_segs, hub_junctions = _spine_route(stub_ends)
            strategy = "spine"
            if small_analog_candidate:
                heuristic_override = "small_analog_local_routing"
        else:
            hub_segs, hub_junctions = _hub_route(stub_ends)
            strategy = "hub"

    # Protected stub avoidance: try alternative routes when the chosen route
    # has collisions with protected points.
    if use_bus and net.name.startswith("/") and len(stub_ends) == 3:
        best_key = _route_candidate_key(
            hub_segs,
            protected_points=dynamic_protected_points,
            endpoints=stub_ends,
        )
        if best_key[0] > 0:
            protected_shared_lane = _protected_shared_lane_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            if protected_shared_lane is not None:
                protected_lane_segs, protected_lane_junctions = protected_shared_lane
                protected_lane_key = _route_candidate_key(
                    protected_lane_segs,
                    protected_points=dynamic_protected_points,
                    endpoints=stub_ends,
                )
                if protected_lane_key < best_key:
                    hub_segs = protected_lane_segs
                    hub_junctions = protected_lane_junctions
                    strategy = "shared_lane"
                    best_key = protected_lane_key
                    if strategy != "shared_lane":
                        heuristic_override = "protected_stub_avoidance"
            protected_chain_segs, protected_chain_junctions = _compact_aligned_chain_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            protected_chain_key = _route_candidate_key(
                protected_chain_segs,
                protected_points=dynamic_protected_points,
                endpoints=stub_ends,
            )
            if protected_chain_key < best_key:
                hub_segs = protected_chain_segs
                hub_junctions = protected_chain_junctions
                strategy = "chain"
                heuristic_override = "protected_stub_avoidance"
    elif use_bus and len(stub_ends) >= 4:
        current_key = _route_candidate_key(
            hub_segs,
            protected_points=dynamic_protected_points,
            endpoints=stub_ends,
        )
        if current_key[0] > 0:
            protected_shared_lane = _protected_shared_lane_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            if protected_shared_lane is not None:
                protected_lane_segs, protected_lane_junctions = protected_shared_lane
                protected_lane_key = _route_candidate_key(
                    protected_lane_segs,
                    protected_points=dynamic_protected_points,
                    endpoints=stub_ends,
                )
                if protected_lane_key < current_key:
                    hub_segs = protected_lane_segs
                    hub_junctions = protected_lane_junctions
                    strategy = "shared_lane"
                    heuristic_override = "protected_stub_avoidance"
                    current_key = protected_lane_key
        if current_key[0] > 0:
            protected_chain_segs, protected_chain_junctions = _compact_aligned_chain_route(
                stub_ends,
                protected_points=dynamic_protected_points,
            )
            protected_chain_key = _route_candidate_key(
                protected_chain_segs,
                protected_points=dynamic_protected_points,
                endpoints=stub_ends,
            )
            if protected_chain_key < current_key:
                hub_segs = protected_chain_segs
                hub_junctions = protected_chain_junctions
                strategy = "chain"
                heuristic_override = "protected_stub_avoidance"
                current_key = protected_chain_key
        if (
            heuristic_override == "protected_stub_avoidance"
            and not net.name.startswith("/")
            and (max(point[0] for point in stub_ends) - min(point[0] for point in stub_ends)) > 80.0
        ):
            del routing.wires[wire_start:]
            del routing.bind_markers[bind_start:]
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
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy="local_labels",
                    pin_count=pins_count,
                    known_pin_count=len(known),
                    unknown_pin_count=0,
                    use_bus=use_bus,
                    heuristic_override="foreign_attachment_label_breakout",
                )
            )
            return True
        if current_key[0] > 0:
            del routing.wires[wire_start:]
            del routing.bind_markers[bind_start:]
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
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=("global_labels" if net.name.startswith("/") else "local_labels"),
                    pin_count=pins_count,
                    known_pin_count=len(known),
                    unknown_pin_count=0,
                    use_bus=use_bus,
                    heuristic_override="foreign_attachment_label_breakout",
                )
            )
            return True

    routing.wires.extend(hub_segs)
    routing.junctions.extend(hub_junctions)
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
