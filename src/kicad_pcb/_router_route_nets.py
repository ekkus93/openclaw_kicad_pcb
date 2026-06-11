"""The main route_nets function — routing decisions for all nets in a Circuit IR."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._router_classify import (
    _classify_routing_net,
    _is_power_net_name,
    _prioritize_label_candidates,
    detect_body_crossings,
)
from ._router_geometry import (
    _foreign_attachment_points_for_known,
    _label_attachment_plan,
    _occupied_label_points,
    _occupied_wire_points,
    _resolve_pin_anchors,
    _stub_end,
)
from ._router_route_power import _route_power_net
from ._router_route_signal import _route_direct_net, _route_hub_net
from ._router_strategies import _plan_local_ladder_routes
from ._router_types import (
    _HUB_MAX_DEGREE,
    DEFAULT_LABEL_POLICY,
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    WIRE_EXTEND_MM,
    BindMarker,
    GlobalLabelPlacement,
    LabelPolicy,
    NetLabel,
    NetRouting,
    RouteDecision,
    RoutingHeuristicPolicy,
    WireSegment,
    _ProtectedPointContext,
)
from ._router_write import _simplify_wires
from .block_detection import BlockLayout
from .errors import ErrorCode, UserError

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from ._router_types import PinAnchor  # noqa: F401  # used in function signature annotation


def route_nets(  # noqa: PLR0912, PLR0913, PLR0915
    *,
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
    pin_anchors: Mapping[tuple[str, str], PinAnchor] | None = None,
    block_layout: BlockLayout | None = None,
    use_bus: bool = True,
    tiers: dict[str, int] | None = None,
    positions: Mapping[str, tuple[float, float, float | None]] | None = None,
    policy: LabelPolicy = DEFAULT_LABEL_POLICY,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
    strict: bool = False,
) -> NetRouting:
    """Compute routing decisions for all nets in *ir*.

    Strategy per net:

    * **Power net** (`GND`, `VCC`, etc.) — emit a
      :class:`GlobalLabelPlacement` (power shape) per pin; avoids spaghetti
      on high-fanout rails.
    * **Direct route** — exactly 2 known pins.  Routing condition (Rule §4):

      * When *tiers* is provided: direct route is used only when the
        tier distance between the two pins is ≤ 1 **and** the Manhattan
        distance is ≤ :data:`MAX_DIRECT_DIST_MM`.  The tier-distance guard
        prevents cross-tier spaghetti; the Manhattan cap prevents wire runs
        so long they become unreadable.
      * When *tiers* is ``None`` (default): the tier guard is skipped;
        only the Manhattan-distance cap (:data:`MAX_DIRECT_DIST_MM`) applies.

      No net labels are emitted for direct routes.
    * **Hub route** — 3–:data:`_HUB_MAX_DEGREE` known pins, all endpoints
      reachable: route spokes to a centroid hub; add a
      :class:`JunctionPoint` at the hub.  No net labels emitted.
      When *use_bus* is ``True`` the hub strategy is replaced by
      :func:`_spine_route` which draws a straight spine wire with
      T-junction taps for a cleaner bus-style visual.
    * **Global-label route** — high-degree non-power nets (> ``_HUB_MAX_DEGREE``):
      emit one :class:`GlobalLabelPlacement` per pin (same result as power
      nets but using the ``passive`` shape so it's visually distinct).
    * **Label route** — fallback for anything else: one stub wire + one
      local net label per pin.

    Unknown pins (absent from the resolved anchor set) always fall back to an
    off-canvas position with local labels.

    Parameters
    ----------
    ir:
        Circuit IR with nets and components.
    pin_endpoints:
        ``{(ref, pin): (x, y, angle)}`` map produced by the schematic builder.
    pin_anchors:
        Optional ``{(ref, pin): PinAnchor}`` map carrying explicit placed-unit
        anchor ownership plus endpoint geometry. When supplied, router helpers
        use this richer model as their source of truth and only fall back to
        *pin_endpoints* for legacy callers.
    block_layout:
        Optional functional block classification from
        :func:`kicad_pcb.block_detection.classify_circuit`. When supplied,
        net classification prefers structural roles before falling back to
        net-name heuristics.
    use_bus:
        When ``True``, replace centroid-hub routing for multi-pin local nets
        with spine-style routing (:func:`_spine_route`).  Produces a cleaner
        "one long wire with taps" visual instead of star-shaped spokes.
    tiers:
        Optional ``{ref: tier_index}`` map.  When supplied, tier distance
        governs the direct-route decision instead of Manhattan distance.
    positions:
        Optional ``{ref: (x, y, rotation)}`` layout position map.  When
        supplied, wire segments that cross component bounding boxes are
        automatically rerouted via :func:`detect_body_crossings`.
    policy:
        Label deduplication policy; controls how many local and global
        labels are emitted per net.  Defaults to
        :data:`DEFAULT_LABEL_POLICY` (2 local labels per net, 4 global
        labels per high-degree net). Use :data:`LABEL_MODE_POLICIES` for the
        bundled ``minimal``, ``debug``, and ``always-show-important-labels`` modes.
    heuristic_policy:
        Analog-specific routing policy that governs compact output-tail and
        local-ground-cluster special cases while leaving generic routing
        strategies unchanged.
    strict:
        When ``True``, unknown pin endpoints are treated as an error instead
        of falling back to off-canvas stub+label/symbol routing.

    Returns a :class:`NetRouting` with all decisions.
    """
    routing = NetRouting()
    fallback_y = -1500.0
    resolved_anchors = _resolve_pin_anchors(pin_endpoints, pin_anchors)
    protected_pin_points = {
        (
            round(anchor.x, 2),
            round(anchor.y, 2),
        )
        for anchor in resolved_anchors.values()
    }
    protected_stub_point_counts = Counter(
        (
            round(stub_x, 2),
            round(stub_y, 2),
        )
        for anchor in resolved_anchors.values()
        for stub_x, stub_y in [_stub_end(anchor.x, anchor.y, anchor.angle)]
    )
    protected_stub_points = set(protected_stub_point_counts)
    shared_protected_stub_points = {
        point for point, count in protected_stub_point_counts.items() if count > 1
    }
    ladder_routes = _plan_local_ladder_routes(
        ir,
        resolved_anchors,
        heuristic_policy=heuristic_policy,
    )

    for net in sorted(ir.nets, key=lambda n: n.name):
        pins = sorted(net.pins, key=lambda p: (p.ref, p.pin))
        net_refs = tuple(dict.fromkeys(pin.ref for pin in pins))
        known = [
            (
                p,
                (
                    resolved_anchors[(p.ref, p.pin)].x,
                    resolved_anchors[(p.ref, p.pin)].y,
                    resolved_anchors[(p.ref, p.pin)].angle,
                ),
            )
            for p in pins
            if (p.ref, p.pin) in resolved_anchors
        ]
        unknown = [p for p in pins if (p.ref, p.pin) not in resolved_anchors]

        if strict and unknown:
            raise UserError(
                f"Cannot route net '{net.name}' with unknown pin endpoints in strict mode",
                code=ErrorCode.PIN_INVALID,
                details={
                    "net_name": net.name,
                    "missing_pins": [
                        {
                            "ref": pin.ref,
                            "pin": pin.pin,
                        }
                        for pin in unknown
                    ],
                },
            )

        is_power = _is_power_net_name(net.name)
        net_classification = _classify_routing_net(
            net.name,
            net_refs,
            block_layout=block_layout,
        )
        occupied_label_points = _occupied_label_points(routing)
        occupied_route_points = _occupied_wire_points(routing.wires) | occupied_label_points
        foreign_attachment_points = _foreign_attachment_points_for_known(
            known,
            protected_pin_points=protected_pin_points,
            protected_stub_points=protected_stub_points,
            shared_protected_stub_points=shared_protected_stub_points,
        )
        dynamic_protected_points = foreign_attachment_points | occupied_route_points
        use_named_global_labels = net.name.startswith("/")

        # ----------------------------------------------------------------
        # Power nets → cluster-based power symbol placement
        # ----------------------------------------------------------------
        if is_power:
            fallback_y = _route_power_net(
                routing,
                net,
                known,
                unknown,
                fallback_y=fallback_y,
                use_bus=use_bus,
                heuristic_policy=heuristic_policy,
                positions=positions,
                net_classification=net_classification,
                pins_count=len(pins),
                protected_pin_points=protected_pin_points,
                protected_stub_points=protected_stub_points,
                shared_protected_stub_points=shared_protected_stub_points,
                foreign_attachment_points=foreign_attachment_points,
            )
            continue

        # ----------------------------------------------------------------
        # 2-pin direct route
        # ----------------------------------------------------------------
        if (
            len(known) == 2
            and not unknown
            and _route_direct_net(
                routing,
                net,
                known,
                use_bus=use_bus,
                policy=policy,
                block_layout=block_layout,
                tiers=tiers,
                net_classification=net_classification,
                pins_count=len(pins),
                net_refs=net_refs,
                protected_pin_points=protected_pin_points,
                protected_stub_points=protected_stub_points,
                shared_protected_stub_points=shared_protected_stub_points,
                foreign_attachment_points=foreign_attachment_points,
                dynamic_protected_points=dynamic_protected_points,
            )
        ):
            continue

        # ----------------------------------------------------------------
        # Hub route (3 – _HUB_MAX_DEGREE known, no unknown pins)
        # ----------------------------------------------------------------
        if 3 <= len(known) <= _HUB_MAX_DEGREE and not unknown:
            _route_hub_net(
                routing,
                net,
                known,
                use_bus=use_bus,
                policy=policy,
                heuristic_policy=heuristic_policy,
                block_layout=block_layout,
                positions=positions,
                ladder_routes=ladder_routes,
                net_classification=net_classification,
                pins_count=len(pins),
                net_refs=net_refs,
                protected_pin_points=protected_pin_points,
                protected_stub_points=protected_stub_points,
                shared_protected_stub_points=shared_protected_stub_points,
                foreign_attachment_points=foreign_attachment_points,
                dynamic_protected_points=dynamic_protected_points,
            )
            continue

        # ----------------------------------------------------------------
        # High-degree non-power → global label per pin (capped by policy)
        # ----------------------------------------------------------------
        label_candidates = _prioritize_label_candidates(
            known,
            block_layout=block_layout,
            classification=net_classification,
        )

        if len(known) > _HUB_MAX_DEGREE:
            strategy = "global_labels"
            global_label_count = 0
            for pin_ref, (wx, wy, wa) in label_candidates:
                label_route, ex, ey = _label_attachment_plan(
                    pin_point=(wx, wy),
                    pin_angle=wa,
                    occupied_points=occupied_route_points,
                    protected=_ProtectedPointContext(
                        foreign_attachment_points,
                        shared_protected_stub_points,
                    ),
                )
                label_angle = int((wa + 180) % 360)
                routing.wires.extend(label_route)
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                if global_label_count < policy.max_global_labels_per_net:
                    routing.global_labels.append(
                        GlobalLabelPlacement(net.name, ex, ey, label_angle)
                    )
                    global_label_count += 1
                occupied_route_points |= _occupied_wire_points(label_route)
                occupied_route_points.add((round(ex, 2), round(ey, 2)))
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, 0))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Label route (classic fallback: stub + local net label per pin)
        # Labels are capped at policy.max_labels_per_net to reduce clutter.
        # Stub wires and bind markers are always emitted (every pin).
        # ----------------------------------------------------------------
        strategy = "global_labels" if use_named_global_labels else "local_labels"
        label_count = 0
        for pin_ref, (wx, wy, wa) in label_candidates:
            label_route, ex, ey = _label_attachment_plan(
                pin_point=(wx, wy),
                pin_angle=wa,
                occupied_points=occupied_route_points,
                protected=_ProtectedPointContext(
                    foreign_attachment_points,
                    shared_protected_stub_points,
                ),
            )
            label_angle = int((wa + 180) % 360)
            routing.wires.extend(label_route)
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            if use_named_global_labels:
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, label_angle))
            elif label_count < policy.max_labels_per_net:
                routing.labels.append(NetLabel(net.name, ex, ey, label_angle))
                label_count += 1
            occupied_route_points |= _occupied_wire_points(label_route)
            occupied_route_points.add((round(ex, 2), round(ey, 2)))

        for pin_ref in unknown:
            wx, wy = -1200.0, fallback_y
            ex, ey = wx + WIRE_EXTEND_MM, wy
            routing.wires.append(WireSegment(wx, wy, ex, ey))
            routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
            # Off-canvas unknown pins always get a label regardless of policy
            # (they have no physical wire connection; the label IS their connection).
            if use_named_global_labels:
                routing.global_labels.append(GlobalLabelPlacement(net.name, ex, ey, 0))
            else:
                routing.labels.append(NetLabel(net.name, ex, ey, 0))
            fallback_y -= 10.0

        routing.route_decisions.append(
            RouteDecision(
                net_name=net.name,
                classification=net_classification,
                strategy=strategy,
                pin_count=len(pins),
                known_pin_count=len(known),
                unknown_pin_count=len(unknown),
                use_bus=use_bus,
            )
        )

    # ----------------------------------------------------------------
    # Body-crossing guard (Rule §4.3)
    # ----------------------------------------------------------------
    if positions is not None:
        routing.wires = detect_body_crossings(routing.wires, positions)

    # ----------------------------------------------------------------
    # Wire simplification pass (Phase 6.1)
    # ----------------------------------------------------------------
    # Protect pin endpoints and visible/off-canvas label attachment points from
    # being merged away; they are required electrical boundaries.  Round to
    # 2 decimal places (0.01 mm precision) to avoid floating-point comparison
    # issues.
    protected = {(round(x, 2), round(y, 2)) for x, y, _angle in pin_endpoints.values()}
    protected.update(
        (round(stub_x, 2), round(stub_y, 2))
        for x, y, angle in pin_endpoints.values()
        for stub_x, stub_y in [_stub_end(x, y, angle)]
    )
    protected.update((round(label.x, 2), round(label.y, 2)) for label in routing.labels)
    protected.update((round(label.x, 2), round(label.y, 2)) for label in routing.global_labels)
    protected.update((round(symbol.x, 2), round(symbol.y, 2)) for symbol in routing.power_symbols)
    protected.update((round(junction.x, 2), round(junction.y, 2)) for junction in routing.junctions)
    routing.wires = _simplify_wires(routing.wires, protected_points=protected)

    oriented_wires: list[WireSegment] = []
    for seg in routing.wires:
        start = (round(seg.x1, 2), round(seg.y1, 2))
        end = (round(seg.x2, 2), round(seg.y2, 2))
        if end in protected and start not in protected:
            oriented_wires.append(WireSegment(seg.x2, seg.y2, seg.x1, seg.y1))
        else:
            oriented_wires.append(seg)
    routing.wires = oriented_wires

    return routing
