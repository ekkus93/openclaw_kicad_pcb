"""Local ladder lane planning: candidate collection, adjacency, and lane assignment."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._router_types import PinAnchor
    from .circuit_ir import CircuitIR

from ._router_classify import (
    _boxes_touch_or_overlap,
    _is_local_ladder_net,
    _is_power_net_name,
    _preferred_shared_lane,
)
from ._router_geometry import _coerce_pin_anchor_map, _resolve_pin_anchors, _stub_end
from ._router_types import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    WIRE_EXTEND_MM,
    LadderLanePlannerContext,
    RoutingHeuristicPolicy,
    SharedLanePlan,
)

# ---------------------------------------------------------------------------
# Single-net lane planning
# ---------------------------------------------------------------------------


def _plan_single_grouped_ladder_lane(
    *,
    axis: str,
    base_coordinate: float,
    endpoints: list[tuple[float, float]],
    refs: tuple[str, ...] = (),
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> SharedLanePlan | None:
    """Return the single-net lane plan, or ``None`` when chain routing should win."""
    candidate_plan = SharedLanePlan(axis, base_coordinate)
    if heuristic_policy.should_skip_shared_lane_plan(
        endpoints, candidate_plan
    ) or heuristic_policy.should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=candidate_plan,
        refs=refs,
    ):
        return None

    if axis == "horizontal":
        shared_points = [
            point for point in endpoints if math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        other_points = [
            point
            for point in endpoints
            if not math.isclose(point[1], base_coordinate, abs_tol=0.01)
        ]
        if len(shared_points) == 2 and len(other_points) == 1:
            other_x = other_points[0][0]
            anchor_x = max(
                (point[0] for point in shared_points),
                key=lambda x: abs(x - other_x),
            )
            return SharedLanePlan(
                axis,
                base_coordinate,
                min(anchor_x, other_x),
                max(anchor_x, other_x),
            )

    return SharedLanePlan(axis, base_coordinate)


# ---------------------------------------------------------------------------
# Connector-entry grouped lanes
# ---------------------------------------------------------------------------


def _assign_connector_entry_grouped_lanes(
    grouped_names: list[str],
    *,
    axis: str,
    base_coordinate: float,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
    connector_entry_x_by_net: dict[str, float],
) -> dict[str, SharedLanePlan] | None:
    """Return the special connector-entry lane assignment for one grouped component."""
    connector_entry_names = [
        net_name for net_name in grouped_names if net_name in connector_entry_x_by_net
    ]
    if axis != "vertical" or len(connector_entry_names) != 1:
        return None

    entry_net = connector_entry_names[0]
    entry_coordinate = round(connector_entry_x_by_net[entry_net] + WIRE_EXTEND_MM, 2)
    if entry_coordinate >= round(base_coordinate, 2):
        return None

    planned_routes: dict[str, SharedLanePlan] = {entry_net: SharedLanePlan(axis, entry_coordinate)}
    remaining_names = [name for name in grouped_names if name != entry_net]
    for index, net_name in enumerate(remaining_names):
        offset = (index + 1.25) * WIRE_EXTEND_MM
        coordinate = round(base_coordinate + offset, 2)
        if len(remaining_names) == 1:
            shared_points = [
                point
                for point in endpoints_by_net[net_name]
                if math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            other_points = [
                point
                for point in endpoints_by_net[net_name]
                if not math.isclose(point[0], base_coordinate, abs_tol=0.01)
            ]
            if len(shared_points) == 2 and len(other_points) == 1:
                other_y = other_points[0][1]
                anchor_y = min(
                    (point[1] for point in shared_points),
                    key=lambda y: abs(y - other_y),
                )
                planned_routes[net_name] = SharedLanePlan(
                    axis,
                    coordinate,
                    min(anchor_y, other_y),
                    max(anchor_y, other_y),
                )
                continue
        planned_routes[net_name] = SharedLanePlan(axis, coordinate)
    return planned_routes


# ---------------------------------------------------------------------------
# Inferred bounded lane plan
# ---------------------------------------------------------------------------


def _infer_bounded_local_lane_plan(
    endpoints: list[tuple[float, float]],
) -> SharedLanePlan | None:
    """Infer a bounded ladder lane for compact 3-pin nets without an exact shared axis."""
    if len(endpoints) != 3:
        return None

    xs = [point[0] for point in endpoints]
    ys = [point[1] for point in endpoints]
    x_span = max(xs) - min(xs)
    y_span = max(ys) - min(ys)

    indexed_points = list(enumerate(endpoints))
    horizontal_pairs = sorted(
        (
            abs(first[1][1] - second[1][1]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )
    vertical_pairs = sorted(
        (
            abs(first[1][0] - second[1][0]),
            first[0],
            second[0],
        )
        for first in indexed_points
        for second in indexed_points
        if first[0] < second[0]
    )

    horizontal_gap, horizontal_i, horizontal_j = horizontal_pairs[0]
    vertical_gap, vertical_i, vertical_j = vertical_pairs[0]
    if min(horizontal_gap, vertical_gap) > WIRE_EXTEND_MM:
        return None

    if x_span >= y_span:
        pair_i, pair_j = horizontal_i, horizontal_j
        axis = "horizontal"
        third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
        pair_points = [endpoints[pair_i], endpoints[pair_j]]
        third_point = endpoints[third_index]
        coordinate = min(
            (pair_points[0][1], pair_points[1][1]),
            key=lambda value: abs(value - third_point[1]),
        )
        return SharedLanePlan(
            axis,
            round(coordinate, 2),
            round(min(pair_points[0][0], pair_points[1][0]), 2),
            round(max(pair_points[0][0], pair_points[1][0]), 2),
        )

    pair_i, pair_j = vertical_i, vertical_j
    axis = "vertical"
    third_index = next(index for index in range(3) if index not in {pair_i, pair_j})
    pair_points = [endpoints[pair_i], endpoints[pair_j]]
    third_point = endpoints[third_index]
    coordinate = min(
        (pair_points[0][0], pair_points[1][0]),
        key=lambda value: abs(value - third_point[0]),
    )
    return SharedLanePlan(
        axis,
        round(coordinate, 2),
        round(min(pair_points[0][1], pair_points[1][1]), 2),
        round(max(pair_points[0][1], pair_points[1][1]), 2),
    )


# ---------------------------------------------------------------------------
# Candidate collection and adjacency
# ---------------------------------------------------------------------------


def _collect_local_ladder_candidates(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor],
) -> tuple[
    dict[str, tuple[float, float, float, float]],
    dict[str, int],
    dict[str, tuple[str, float]],
    dict[str, list[tuple[float, float]]],
    dict[str, tuple[str, ...]],
    dict[str, float],
]:
    """Collect compact local nets that may participate in ladder routing."""
    from .component_types import component_type as _component_type  # noqa: PLC0415

    candidate_boxes: dict[str, tuple[float, float, float, float]] = {}
    candidate_degree: dict[str, int] = {}
    shared_lane_by_net: dict[str, tuple[str, float]] = {}
    endpoints_by_net: dict[str, list[tuple[float, float]]] = {}
    refs_by_net: dict[str, tuple[str, ...]] = {}
    connector_entry_x_by_net: dict[str, float] = {}

    for net in ir.nets:
        if _is_power_net_name(net.name):
            continue

        endpoints: list[tuple[float, float]] = []
        for pin in net.pins:
            anchor = pin_anchors.get((pin.ref, pin.pin))
            if anchor is None:
                endpoints = []
                break
            endpoints.append(_stub_end(anchor.x, anchor.y, anchor.angle))

        if len(endpoints) < 2 or len(endpoints) > 3:
            continue
        if not _is_local_ladder_net(endpoints):
            continue

        endpoints_by_net[net.name] = endpoints
        refs_by_net[net.name] = tuple(pin.ref for pin in net.pins)
        xs = [point[0] for point in endpoints]
        ys = [point[1] for point in endpoints]
        candidate_boxes[net.name] = (min(xs), max(xs), min(ys), max(ys))
        candidate_degree[net.name] = len(endpoints)

        if any(_component_type(pin.ref) == "connector" for pin in net.pins):
            connector_entry_x_by_net[net.name] = min(xs)

        lane = _preferred_shared_lane(endpoints)
        if lane is not None:
            shared_lane_by_net[net.name] = lane

    return (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        refs_by_net,
        connector_entry_x_by_net,
    )


def _build_ladder_adjacency(
    candidate_boxes: dict[str, tuple[float, float, float, float]],
) -> dict[str, set[str]]:
    """Connect compact local nets whose bounding boxes nearly touch."""
    adjacency: dict[str, set[str]] = {name: set() for name in candidate_boxes}
    names = sorted(candidate_boxes)
    for index, net_name in enumerate(names):
        for other_name in names[index + 1 :]:
            if not _boxes_touch_or_overlap(candidate_boxes[net_name], candidate_boxes[other_name]):
                continue
            adjacency[net_name].add(other_name)
            adjacency[other_name].add(net_name)
    return adjacency


def _connected_ladder_component(
    start_name: str,
    adjacency: dict[str, set[str]],
    visited: set[str],
) -> list[str]:
    """Return one connected component from the ladder-neighborhood graph."""
    stack = [start_name]
    component: list[str] = []
    while stack:
        current = stack.pop()
        if current in visited:
            continue
        visited.add(current)
        component.append(current)
        stack.extend(sorted(adjacency[current] - visited))
    return component


def _lane_center(
    net_name: str,
    axis: str,
    endpoints_by_net: dict[str, list[tuple[float, float]]],
) -> float:
    """Return the orthogonal-axis center used to order parallel local lanes."""
    coordinates = endpoints_by_net[net_name]
    if axis == "vertical":
        return sum(point[1] for point in coordinates) / len(coordinates)
    return sum(point[0] for point in coordinates) / len(coordinates)


# ---------------------------------------------------------------------------
# Lane assignment
# ---------------------------------------------------------------------------


def _assign_grouped_ladder_lanes(
    component: list[str],
    *,
    context: LadderLanePlannerContext,
) -> dict[str, SharedLanePlan]:
    """Assign distinct parallel lanes to all 3-pin nets in one neighborhood."""
    grouped: dict[tuple[str, float], list[str]] = {}
    for net_name in component:
        if context.candidate_degree.get(net_name) != 3:
            continue
        lane = context.shared_lane_by_net.get(net_name)
        if lane is None:
            continue
        axis, base_coordinate = lane
        grouped.setdefault((axis, round(base_coordinate, 2)), []).append(net_name)

    planned_routes: dict[str, SharedLanePlan] = {}
    skipped_single_lane_nets: set[str] = set()
    for (axis, base_coordinate), grouped_names in grouped.items():
        grouped_names.sort(
            key=lambda net_name: _lane_center(net_name, axis, context.endpoints_by_net)
        )
        if len(grouped_names) == 1:
            endpoints = context.endpoints_by_net[grouped_names[0]]
            single_lane_plan = _plan_single_grouped_ladder_lane(
                axis=axis,
                base_coordinate=base_coordinate,
                endpoints=endpoints,
                refs=context.refs_by_net.get(grouped_names[0], ()),
                heuristic_policy=context.heuristic_policy,
            )
            if single_lane_plan is None:
                skipped_single_lane_nets.add(grouped_names[0])
                continue
            planned_routes[grouped_names[0]] = single_lane_plan
            continue

        connector_group_routes = _assign_connector_entry_grouped_lanes(
            grouped_names,
            axis=axis,
            base_coordinate=base_coordinate,
            endpoints_by_net=context.endpoints_by_net,
            connector_entry_x_by_net=context.connector_entry_x_by_net,
        )
        if connector_group_routes is not None:
            planned_routes.update(connector_group_routes)
            continue

        for index, net_name in enumerate(grouped_names):
            offset = (index - (len(grouped_names) - 1) / 2) * WIRE_EXTEND_MM
            planned_routes[net_name] = SharedLanePlan(axis, round(base_coordinate + offset, 2))

    for net_name in component:
        if net_name in planned_routes or context.candidate_degree.get(net_name) != 3:
            continue
        if net_name in skipped_single_lane_nets:
            continue
        inferred_plan = _infer_bounded_local_lane_plan(context.endpoints_by_net[net_name])
        if inferred_plan is None:
            continue
        if context.heuristic_policy.should_skip_shared_lane_plan(
            context.endpoints_by_net[net_name],
            inferred_plan,
        ) or context.heuristic_policy.should_prefer_small_analog_chain(
            context.endpoints_by_net[net_name],
            inferred_plan=inferred_plan,
            refs=context.refs_by_net.get(net_name, ()),
        ):
            continue
        planned_routes[net_name] = inferred_plan

    return planned_routes


def _plan_local_ladder_routes(
    ir: CircuitIR,
    pin_anchors: Mapping[tuple[str, str], PinAnchor | tuple[float, float, float]] | None = None,
    *,
    pin_endpoints: Mapping[tuple[str, str], tuple[float, float, float]] | None = None,
    heuristic_policy: RoutingHeuristicPolicy = DEFAULT_ROUTING_HEURISTIC_POLICY,
) -> dict[str, SharedLanePlan]:
    """Detect nearby small-signal net neighborhoods that should share ladder-style routing."""
    resolved_anchors = _resolve_pin_anchors(
        pin_endpoints or {},
        _coerce_pin_anchor_map(pin_anchors),
    )
    (
        candidate_boxes,
        candidate_degree,
        shared_lane_by_net,
        endpoints_by_net,
        refs_by_net,
        connector_entry_x_by_net,
    ) = _collect_local_ladder_candidates(ir, resolved_anchors)
    planner_context = LadderLanePlannerContext(
        candidate_degree=candidate_degree,
        shared_lane_by_net=shared_lane_by_net,
        endpoints_by_net=endpoints_by_net,
        refs_by_net=refs_by_net,
        connector_entry_x_by_net=connector_entry_x_by_net,
        heuristic_policy=heuristic_policy,
    )
    adjacency = _build_ladder_adjacency(candidate_boxes)

    visited: set[str] = set()
    planned_routes: dict[str, SharedLanePlan] = {}

    for net_name in sorted(candidate_boxes):
        if net_name in visited:
            continue

        component = _connected_ladder_component(net_name, adjacency, visited)
        if len(component) < 2:
            continue

        planned_routes.update(
            _assign_grouped_ladder_lanes(
                component,
                context=planner_context,
            )
        )

    return planned_routes
