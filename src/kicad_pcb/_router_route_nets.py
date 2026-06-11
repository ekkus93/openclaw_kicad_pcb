"""The main route_nets function — routing decisions for all nets in a Circuit IR."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._router_classify import (
    _append_promoted_visible_label,
    _classification_prefers_compact_tail,
    _classification_prefers_local_chain,
    _classification_prefers_short_local_direct_route,
    _classify_routing_net,
    _cluster_power_pins,
    _is_power_net_name,
    _prioritize_label_candidates,
    _tier_distance,
    _VisibleLabelPromotion,
    detect_body_crossings,
)
from ._router_geometry import (
    _aligned_power_cluster_route_points,
    _append_direct_power_symbol,
    _append_pin_endpoint_labels,
    _best_direct_route_with_protected_points,
    _foreign_attachment_points_for_known,
    _is_connector_passive_edge,
    _known_pin_stub_hits_foreign_attachment,
    _label_attachment_plan,
    _manhattan,
    _occupied_label_points,
    _occupied_wire_points,
    _offset_point_along_angle,
    _power_cluster_angle,
    _power_symbol_angle,
    _resolve_pin_anchors,
    _route_candidate_key,
    _segment_label_angle,
    _snap_grid,
    _stub_end,
)
from ._router_strategies import (
    _buffer_follower_feedback_route,
    _compact_aligned_chain_route,
    _hub_route,
    _infer_bounded_local_lane_plan,
    _is_small_analog_chain_candidate,
    _plan_local_ladder_routes,
    _prefer_chain_route,
    _protected_shared_lane_route,
    _shared_lane_route,
    _spine_route,
)
from ._router_types import (
    _HUB_MAX_DEGREE,
    _POWER_CLUSTER_RADIUS_MM,
    _POWER_LABEL_CLEARANCE_MM,
    DEFAULT_LABEL_POLICY,
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    MAX_DIRECT_DIST_MM,
    MAX_DIRECT_WIRE_MM,
    WIRE_EXTEND_MM,
    BindMarker,
    GlobalLabelPlacement,
    JunctionPoint,
    LabelPolicy,
    NetLabel,
    NetRouting,
    PowerSymbolPlacement,
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
        strategy = "local_labels"
        heuristic_override: str | None = None

        # ----------------------------------------------------------------
        # Power nets → cluster-based power symbol placement (Phase 5.1)
        # ----------------------------------------------------------------
        if is_power:
            compact_power_override: str | None = None
            whole_power_cluster = heuristic_policy.route_compact_power_cluster(
                net_name=net.name,
                cluster=known,
                positions=positions,
            )
            occupied_route_points = _occupied_wire_points(routing.wires) | _occupied_label_points(
                routing
            )
            known_stub_ends = [_stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in known]
            if (
                whole_power_cluster is not None
                and _route_candidate_key(
                    whole_power_cluster[0],
                    protected_points=occupied_route_points,
                    endpoints=known_stub_ends,
                )[0]
                > 0
            ):
                whole_power_cluster = None
            if whole_power_cluster is not None:
                compact_power_override = (
                    "compact_local_ground_cluster"
                    if net.name.upper() == "GND"
                    else "compact_local_decoupling_cluster"
                )
                for pin_ref, (wx, wy, wa) in known:
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                cluster_segs, cluster_junctions, (px, py) = whole_power_cluster
                routing.wires.extend(cluster_segs)
                routing.junctions.extend(cluster_junctions)
                routing.power_symbols.append(
                    PowerSymbolPlacement(
                        net.name,
                        px,
                        py,
                        _power_symbol_angle(net.name, 0),
                    )
                )
                for pin_ref in unknown:
                    wx, wy = -1200.0, fallback_y
                    ex, ey = wx + WIRE_EXTEND_MM, wy
                    power_angle = _power_symbol_angle(net.name, 0)
                    px, py = _offset_point_along_angle(
                        ex,
                        ey,
                        power_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )
                    routing.wires.append(WireSegment(wx, wy, ex, ey))
                    routing.wires.append(WireSegment(ex, ey, px, py))
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, power_angle)
                    )
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    fallback_y -= 10.0
                routing.route_decisions.append(
                    RouteDecision(
                        net_name=net.name,
                        classification=net_classification,
                        strategy="power_symbols",
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override=compact_power_override,
                    )
                )
                continue

            # Cluster known pins by proximity to share power symbols
            clusters = _cluster_power_pins(known, radius=_POWER_CLUSTER_RADIUS_MM)

            for cluster in clusters:
                if len(cluster) == 1:
                    # Single pin: traditional stub + symbol
                    pin_ref, (wx, wy, wa) = cluster[0]
                    _append_direct_power_symbol(
                        routing,
                        net_name=net.name,
                        pin_ref=pin_ref,
                        endpoint=(wx, wy, wa),
                        protected=_ProtectedPointContext(
                            foreign_attachment_points,
                            shared_protected_stub_points,
                        ),
                    )
                else:
                    # Multiple pins: compute cluster centroid for shared symbol
                    compact_ground_cluster = heuristic_policy.route_compact_power_cluster(
                        net_name=net.name,
                        cluster=cluster,
                        positions=positions,
                    )
                    cluster_stub_ends = [
                        _stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in cluster
                    ]
                    if (
                        compact_ground_cluster is not None
                        and _route_candidate_key(
                            compact_ground_cluster[0],
                            protected_points=occupied_route_points,
                            endpoints=cluster_stub_ends,
                        )[0]
                        > 0
                    ):
                        compact_ground_cluster = None
                    if compact_ground_cluster is not None:
                        compact_power_override = (
                            "compact_local_ground_cluster"
                            if net.name.upper() == "GND"
                            else "compact_local_decoupling_cluster"
                        )
                        for pin_ref, (wx, wy, wa) in cluster:
                            ex, ey = _stub_end(wx, wy, wa)
                            routing.wires.append(WireSegment(wx, wy, ex, ey))
                            routing.bind_markers.append(
                                BindMarker(pin_ref.ref, pin_ref.pin, net.name)
                            )
                        cluster_segs, cluster_junctions, (px, py) = compact_ground_cluster
                        routing.wires.extend(cluster_segs)
                        routing.junctions.extend(cluster_junctions)
                        routing.power_symbols.append(
                            PowerSymbolPlacement(
                                net.name,
                                px,
                                py,
                                _power_symbol_angle(net.name, 0),
                            )
                        )
                        continue

                    cx = _snap_grid(sum(cpx for _, (cpx, _, _) in cluster) / len(cluster))
                    cy = _snap_grid(sum(cpy for _, (_, cpy, _) in cluster) / len(cluster))

                    # Wire each pin to centroid via hub routing. If every stub end
                    # is already collinear, keep the outward stubs and place the
                    # shared lane on a nearby parallel track so no pin is attached
                    # from the symbol/body side through an overlapping collinear run.
                    (
                        stub_wires,
                        stub_ends,
                        aligned_axis,
                        aligned_coordinate,
                    ) = _aligned_power_cluster_route_points(cluster)
                    cluster_foreign_attachment_points = _foreign_attachment_points_for_known(
                        cluster,
                        protected_pin_points=protected_pin_points,
                        protected_stub_points=protected_stub_points,
                        shared_protected_stub_points=shared_protected_stub_points,
                    )
                    candidate_protected_points = (
                        cluster_foreign_attachment_points | occupied_route_points
                    )
                    if (
                        net.name.upper() == "GND"
                        and len(cluster) == 2
                        and use_bus
                        and aligned_axis == "vertical"
                    ):
                        # KiCad 9 still drops one pin from some aligned two-pin GND
                        # fallback clusters even with an offset shared lane, so keep
                        # vertically aligned cases as direct per-pin GND symbol
                        # attachments instead.
                        for pin_ref, (wx, wy, wa) in cluster:
                            _append_direct_power_symbol(
                                routing,
                                net_name=net.name,
                                pin_ref=pin_ref,
                                endpoint=(wx, wy, wa),
                                protected=_ProtectedPointContext(
                                    cluster_foreign_attachment_points,
                                    shared_protected_stub_points,
                                ),
                            )
                        continue
                    power_angle = _power_cluster_angle(stub_ends)
                    symbol_angle = _power_symbol_angle(net.name, power_angle)
                    px, py = _offset_point_along_angle(
                        cx,
                        cy,
                        power_angle,
                        _POWER_LABEL_CLEARANCE_MM,
                    )

                    # Add the snapped centroid as the hub target so the power-symbol
                    # branch wire and the spine-route junction land on the same point.
                    route_anchor = (cx, cy)
                    route_points = list(stub_ends)
                    if use_bus and aligned_axis is not None and aligned_coordinate is not None:
                        preferred_sign = (
                            1
                            if (
                                (aligned_axis == "vertical" and power_angle == 180)
                                or (aligned_axis == "horizontal" and power_angle == 270)
                            )
                            else -1
                        )
                        preferred_lane_coordinate = _snap_grid(
                            aligned_coordinate + preferred_sign * WIRE_EXTEND_MM
                        )
                        candidate_offsets = (
                            preferred_sign,
                            preferred_sign * 2,
                            -preferred_sign,
                            -preferred_sign * 2,
                        )
                        candidate_coordinates: list[float] = [preferred_lane_coordinate]
                        seen_coordinates = {preferred_lane_coordinate}
                        for offset_sign in candidate_offsets:
                            candidate_coordinate = _snap_grid(
                                aligned_coordinate + offset_sign * WIRE_EXTEND_MM
                            )
                            if candidate_coordinate in seen_coordinates:
                                continue
                            seen_coordinates.add(candidate_coordinate)
                            candidate_coordinates.append(candidate_coordinate)

                        best_lane: (
                            tuple[
                                tuple[int, float, int],
                                float,
                                tuple[float, float],
                                list[WireSegment],
                                list[JunctionPoint],
                            ]
                            | None
                        ) = None
                        for candidate_coordinate in candidate_coordinates:
                            candidate_anchor = (
                                (candidate_coordinate, cy)
                                if aligned_axis == "vertical"
                                else (cx, candidate_coordinate)
                            )
                            candidate_points = [*stub_ends, candidate_anchor]
                            candidate_segs, candidate_junctions = _shared_lane_route(
                                candidate_points,
                                axis=aligned_axis,
                                coordinate=candidate_coordinate,
                            )
                            candidate_key = _route_candidate_key(
                                candidate_segs,
                                protected_points=candidate_protected_points,
                                endpoints=stub_ends,
                            )
                            lane_choice = (
                                candidate_key,
                                abs(candidate_coordinate - preferred_lane_coordinate),
                                candidate_anchor,
                                candidate_segs,
                                candidate_junctions,
                            )
                            if best_lane is None or lane_choice < best_lane:
                                best_lane = lane_choice

                        assert best_lane is not None
                        default_route_points = [*stub_ends, route_anchor]
                        if use_bus:
                            default_hub_segs, default_hub_junctions = _spine_route(
                                default_route_points
                            )
                        else:
                            default_hub_segs, default_hub_junctions = _hub_route(
                                default_route_points
                            )
                        default_key = _route_candidate_key(
                            default_hub_segs,
                            protected_points=candidate_protected_points,
                            endpoints=stub_ends,
                        )
                        (
                            best_aligned_key,
                            _distance_from_preferred,
                            best_anchor,
                            best_hub_segs,
                            best_hub_junctions,
                        ) = best_lane
                        if best_aligned_key < default_key:
                            route_anchor = best_anchor
                            hub_segs = best_hub_segs
                            hub_junctions = best_hub_junctions
                        else:
                            hub_segs = default_hub_segs
                            hub_junctions = default_hub_junctions
                    else:
                        route_points.append(route_anchor)
                        # Route stubs to centroid via spine/hub
                        if use_bus:
                            hub_segs, hub_junctions = _spine_route(route_points)
                        else:
                            hub_segs, hub_junctions = _hub_route(route_points)
                    selected_hub_key = _route_candidate_key(
                        hub_segs,
                        protected_points=candidate_protected_points,
                        endpoints=stub_ends,
                    )
                    if selected_hub_key[0] > 0:
                        for pin_ref, (wx, wy, wa) in cluster:
                            _append_direct_power_symbol(
                                routing,
                                net_name=net.name,
                                pin_ref=pin_ref,
                                endpoint=(wx, wy, wa),
                                protected=_ProtectedPointContext(
                                    cluster_foreign_attachment_points,
                                    shared_protected_stub_points,
                                ),
                            )
                        continue
                    routing.wires.extend(stub_wires)
                    for pin_ref, _anchor in cluster:
                        routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    routing.power_symbols.append(
                        PowerSymbolPlacement(net.name, px, py, symbol_angle)
                    )
                    routing.wires.extend(hub_segs)
                    routing.junctions.extend(hub_junctions)
                    if (
                        aligned_axis == "horizontal"
                        and len(cluster) == 2
                        and power_angle in {90, 270}
                    ):
                        tail_y = _snap_grid(
                            route_anchor[1] - WIRE_EXTEND_MM
                            if power_angle == 90
                            else route_anchor[1] + WIRE_EXTEND_MM
                        )
                        routing.wires.append(WireSegment(px, py, route_anchor[0], tail_y))
                    else:
                        routing.wires.append(WireSegment(route_anchor[0], route_anchor[1], px, py))

            # Off-canvas fallback for power pins with no known endpoint
            for pin_ref in unknown:
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                power_angle = _power_symbol_angle(net.name, 0)
                px, py = _offset_point_along_angle(
                    ex,
                    ey,
                    power_angle,
                    _POWER_LABEL_CLEARANCE_MM,
                )
                routing.wires.append(WireSegment(wx, wy, ex, ey))
                routing.wires.append(WireSegment(ex, ey, px, py))
                routing.power_symbols.append(PowerSymbolPlacement(net.name, px, py, power_angle))
                routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                fallback_y -= 10.0
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy="power_symbols",
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=compact_power_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # 2-pin direct route
        # ----------------------------------------------------------------
        routed_directly = False
        if len(known) == 2 and not unknown:
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
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override=heuristic_override,
                    )
                )
                continue
            manhattan = _manhattan(ex0, ey0, ex1, ey1)
            if tiers is not None:
                # Rule §4: tier distance ≤ 1 guards signal-flow adjacency;
                # manhattan cap (MAX_DIRECT_DIST_MM) guards physical wire length,
                # matching the behaviour of the non-tier path.
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
            if can_direct:
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
                routed_directly = True
                strategy = "direct"

        if routed_directly:
            routing.route_decisions.append(
                RouteDecision(
                    net_name=net.name,
                    classification=net_classification,
                    strategy=strategy,
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=heuristic_override,
                )
            )
            continue

        # ----------------------------------------------------------------
        # Hub route (3 – _HUB_MAX_DEGREE known, no unknown pins)
        # ----------------------------------------------------------------
        if 3 <= len(known) <= _HUB_MAX_DEGREE and not unknown:
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
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override="foreign_attachment_label_breakout",
                    )
                )
                continue
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
                        pin_count=len(pins),
                        known_pin_count=len(known),
                        unknown_pin_count=len(unknown),
                        use_bus=use_bus,
                        heuristic_override="connector_label_breakout",
                    )
                )
                continue
            if use_bus and net.name in ladder_routes:
                lane_plan = ladder_routes[net.name]
                stub_ends = []
                for pin_ref, (wx, wy, wa) in known:
                    ex, ey = _stub_end(wx, wy, wa)
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
                    # For a horizontal shared lane, suppress the sideways stub from
                    # pins that exit horizontally (angle ≈ 0° or 180°).  Routing
                    # from the pin endpoint directly avoids the "right/left-then-up"
                    # L-shaped detour; _shared_lane_route reproduces any needed
                    # horizontal span internally, leaving connectivity unchanged.
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
                    and (
                        max(point[0] for point in stub_ends) - min(point[0] for point in stub_ends)
                    )
                    > 80.0
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
                            pin_count=len(pins),
                            known_pin_count=len(known),
                            unknown_pin_count=len(unknown),
                            use_bus=use_bus,
                            heuristic_override="foreign_attachment_label_breakout",
                        )
                    )
                    continue
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
                            strategy=(
                                "global_labels" if net.name.startswith("/") else "local_labels"
                            ),
                            pin_count=len(pins),
                            known_pin_count=len(known),
                            unknown_pin_count=len(unknown),
                            use_bus=use_bus,
                            heuristic_override="foreign_attachment_label_breakout",
                        )
                    )
                    continue
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
                    pin_count=len(pins),
                    known_pin_count=len(known),
                    unknown_pin_count=len(unknown),
                    use_bus=use_bus,
                    heuristic_override=heuristic_override,
                )
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
        if use_named_global_labels:
            strategy = "global_labels"
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
