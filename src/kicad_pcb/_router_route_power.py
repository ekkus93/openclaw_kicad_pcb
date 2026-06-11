"""Power net routing: compact clusters, per-cluster hubs, and fallback power symbols."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import NetIR, PinRefIR

from ._router_classify import _cluster_power_pins
from ._router_geometry import (
    _aligned_power_cluster_route_points,
    _append_direct_power_symbol,
    _foreign_attachment_points_for_known,
    _occupied_label_points,
    _occupied_wire_points,
    _offset_point_along_angle,
    _power_cluster_angle,
    _power_symbol_angle,
    _route_candidate_key,
    _snap_grid,
    _stub_end,
)
from ._router_strategies import _hub_route, _shared_lane_route, _spine_route
from ._router_types import (
    _POWER_CLUSTER_RADIUS_MM,
    _POWER_LABEL_CLEARANCE_MM,
    WIRE_EXTEND_MM,
    BindMarker,
    JunctionPoint,
    NetRouting,
    PowerSymbolPlacement,
    RouteDecision,
    RoutingClassification,
    RoutingHeuristicPolicy,
    WireSegment,
    _ProtectedPointContext,
)


def _route_power_net(  # noqa: PLR0913, PLR0915
    routing: NetRouting,
    net: NetIR,
    known: list[tuple[PinRefIR, tuple[float, float, float]]],
    unknown: list[PinRefIR],
    *,
    fallback_y: float,
    use_bus: bool,
    heuristic_policy: RoutingHeuristicPolicy,
    positions: Mapping[str, tuple[float, float, float | None]] | None,
    net_classification: RoutingClassification,
    pins_count: int,
    protected_pin_points: set[tuple[float, float]],
    protected_stub_points: set[tuple[float, float]],
    shared_protected_stub_points: set[tuple[float, float]],
    foreign_attachment_points: set[tuple[float, float]],
) -> float:
    """Route a power net; mutates *routing* and returns the updated fallback_y."""
    compact_power_override: str | None = None
    whole_power_cluster = heuristic_policy.route_compact_power_cluster(
        net_name=net.name,
        cluster=known,
        positions=positions,
    )
    occupied_route_points = _occupied_wire_points(routing.wires) | _occupied_label_points(routing)
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
            px, py = _offset_point_along_angle(ex, ey, power_angle, _POWER_LABEL_CLEARANCE_MM)
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
                pin_count=pins_count,
                known_pin_count=len(known),
                unknown_pin_count=len(unknown),
                use_bus=use_bus,
                heuristic_override=compact_power_override,
            )
        )
        return fallback_y

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
            # Multiple pins: compact cluster or centroid routing
            compact_ground_cluster = heuristic_policy.route_compact_power_cluster(
                net_name=net.name,
                cluster=cluster,
                positions=positions,
            )
            cluster_stub_ends = [_stub_end(wx, wy, wa) for _pin_ref, (wx, wy, wa) in cluster]
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
                    routing.bind_markers.append(BindMarker(pin_ref.ref, pin_ref.pin, net.name))
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
            candidate_protected_points = cluster_foreign_attachment_points | occupied_route_points
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
            px, py = _offset_point_along_angle(cx, cy, power_angle, _POWER_LABEL_CLEARANCE_MM)

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
                    default_hub_segs, default_hub_junctions = _spine_route(default_route_points)
                else:
                    default_hub_segs, default_hub_junctions = _hub_route(default_route_points)
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
            routing.power_symbols.append(PowerSymbolPlacement(net.name, px, py, symbol_angle))
            routing.wires.extend(hub_segs)
            routing.junctions.extend(hub_junctions)
            if aligned_axis == "horizontal" and len(cluster) == 2 and power_angle in {90, 270}:
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
        px, py = _offset_point_along_angle(ex, ey, power_angle, _POWER_LABEL_CLEARANCE_MM)
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
            pin_count=pins_count,
            known_pin_count=len(known),
            unknown_pin_count=len(unknown),
            use_bus=use_bus,
            heuristic_override=compact_power_override,
        )
    )
    return fallback_y
