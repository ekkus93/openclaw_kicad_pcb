"""Phase 6: wire simplification, chain routing, label attachment, ladder routing."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    DEBUG_LABEL_POLICY,
    SYMBOL_HALF_SIZE_MM,
    GlobalLabelPlacement,
    JunctionPoint,
    NetLabel,
    NetRouting,
    SharedLanePlan,
    WireSegment,
    _append_pin_endpoint_labels,
    _append_promoted_visible_label,
    _best_direct_route_with_protected_points,
    _chain_route,
    _infer_bounded_local_lane_plan,
    _infer_safe_t_junctions,
    _l_route_with_protected_points,
    _label_attachment_plan,
    _plan_local_ladder_routes,
    _prefer_chain_route,
    _prefer_small_analog_chain_route,
    _ProtectedPointContext,
    _route_candidate_key,
    _shared_lane_route,
    _simplify_wires,
    _split_wires_at_points,
    _VisibleLabelPromotion,
    _wire_crosses_box,
    detect_body_crossings,
    route_nets,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


def _connected_power_symbol_sets(routing: NetRouting) -> list[set[str]]:
    parent: dict[tuple[float, float], tuple[float, float]] = {}

    def find(point: tuple[float, float]) -> tuple[float, float]:
        parent.setdefault(point, point)
        if parent[point] != point:
            parent[point] = find(parent[point])
        return parent[point]

    def union(left: tuple[float, float], right: tuple[float, float]) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    labels_by_root: dict[tuple[float, float], set[str]] = defaultdict(set)
    for wire in routing.wires:
        start = (round(wire.x1, 2), round(wire.y1, 2))
        end = (round(wire.x2, 2), round(wire.y2, 2))
        union(start, end)
    for power_symbol in routing.power_symbols:
        point = (round(power_symbol.x, 2), round(power_symbol.y, 2))
        labels_by_root[find(point)].add(f"PWR:{power_symbol.net_name}")
    return list(labels_by_root.values())


def test_simplify_merges_colinear_horizontal_segments() -> None:
    """Consecutive horizontal segments on the same line are merged."""
    # Two horizontal segments that share an endpoint
    wires = [
        WireSegment(0.0, 10.0, 5.0, 10.0),  # (0, 10) → (5, 10)
        WireSegment(5.0, 10.0, 10.0, 10.0),  # (5, 10) → (10, 10)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1, "Two colinear segments should merge into one"
    merged = result[0]
    assert merged.y1 == 10.0 and merged.y2 == 10.0, "Merged segment should be horizontal"
    assert {merged.x1, merged.x2} == {0.0, 10.0}, "Merged segment should span full length"


def test_simplify_merges_colinear_vertical_segments() -> None:
    """Consecutive vertical segments on the same line are merged."""
    wires = [
        WireSegment(20.0, 0.0, 20.0, 5.0),  # (20, 0) → (20, 5)
        WireSegment(20.0, 5.0, 20.0, 10.0),  # (20, 5) → (20, 10)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1, "Two colinear vertical segments should merge"
    merged = result[0]
    assert merged.x1 == 20.0 and merged.x2 == 20.0, "Merged segment should be vertical"
    assert {merged.y1, merged.y2} == {0.0, 10.0}, "Merged segment should span full length"


def test_simplify_preserves_non_colinear_segments() -> None:
    """L-shaped wires (horizontal + vertical) are not merged."""
    wires = [
        WireSegment(0.0, 0.0, 10.0, 0.0),  # horizontal
        WireSegment(10.0, 0.0, 10.0, 10.0),  # vertical (shares endpoint but different direction)
    ]

    result = _simplify_wires(wires)

    assert len(result) == 2, "Non-colinear segments should not merge"


def test_simplify_preserves_junction_points() -> None:
    """Segments meeting at a junction (degree ≥ 3) are not merged.

    A T-junction has one spine segment and two branch segments meeting
    at the junction point.  None of these should be merged because doing
    so would eliminate the junction.
    """
    # T-junction: horizontal spine with vertical branch
    #
    #       |
    #   ----+----
    #
    wires = [
        WireSegment(0.0, 10.0, 10.0, 10.0),  # left spine
        WireSegment(10.0, 10.0, 20.0, 10.0),  # right spine
        WireSegment(10.0, 0.0, 10.0, 10.0),  # vertical branch
    ]

    result = _simplify_wires(wires)

    # All three segments must be preserved (junction point has degree 3)
    assert len(result) == 3, "Junction segments should not be merged"
    # Verify junction point (10, 10) is still an endpoint of all affected segments
    endpoints = {(seg.x1, seg.y1) for seg in result} | {(seg.x2, seg.y2) for seg in result}
    assert (10.0, 10.0) in endpoints, "Junction point must remain as a wire endpoint"


def test_simplify_preserves_explicit_protected_midsegment_junction() -> None:
    wires = [
        WireSegment(10.0, 0.0, 10.0, 10.0),
        WireSegment(10.0, 10.0, 10.0, 20.0),
        WireSegment(5.0, 10.0, 15.0, 10.0),
    ]

    result = _simplify_wires(wires, protected_points={(10.0, 10.0)})

    assert len(result) == 3
    assert (
        sum(
            1
            for seg in result
            if math.isclose(seg.x1, 10.0, abs_tol=0.01) and math.isclose(seg.x2, 10.0, abs_tol=0.01)
        )
        == 2
    )


def test_split_wires_at_points_breaks_segments_at_explicit_junctions() -> None:
    result = _split_wires_at_points(
        [
            WireSegment(30.48, 99.06, 48.26, 99.06),
            WireSegment(39.37, 96.52, 39.37, 102.87),
        ],
        {(39.37, 99.06)},
    )

    assert len(result) == 4
    assert WireSegment(30.48, 99.06, 39.37, 99.06) in result
    assert WireSegment(39.37, 99.06, 48.26, 99.06) in result
    assert WireSegment(39.37, 96.52, 39.37, 99.06) in result
    assert WireSegment(39.37, 99.06, 39.37, 102.87) in result


def test_infer_safe_t_junctions_adds_orthogonal_tee_outside_component_bodies() -> None:
    """A wire endpoint teeing into a trunk away from symbols gets a junction."""
    wires = [
        WireSegment(175.26, 118.11, 175.26, 152.40),
        WireSegment(168.91, 130.18, 175.26, 130.18),
    ]

    result = _infer_safe_t_junctions(
        wires,
        protected_points=set(),
        positions={
            "C64": (175.26, 109.22, 0.0),
            "U2": (175.26, 116.84, 0.0),
        },
    )

    assert result == [JunctionPoint(175.26, 130.18)]


def test_infer_safe_t_junctions_skips_points_inside_component_bodies() -> None:
    """Endpoints near a symbol body should not become inferred junctions."""
    wires = [
        WireSegment(57.15, 168.91, 57.15, 114.30),
        WireSegment(52.07, 168.91, 57.15, 168.91),
    ]

    result = _infer_safe_t_junctions(
        wires,
        protected_points=set(),
        positions={"C72": (55.88, 168.91, 90.0)},
    )

    assert result == []


def test_simplify_preserves_protected_points() -> None:
    """Pin endpoints (protected points) are preserved as wire start/end points."""
    # Pin at (5, 10) with stub to (5, 5), then routing to (10, 5)
    wires = [
        WireSegment(5.0, 10.0, 5.0, 5.0),  # stub from pin (vertical)
        WireSegment(5.0, 5.0, 10.0, 5.0),  # routing (horizontal)
    ]
    protected = {(5.0, 10.0)}  # pin position

    result = _simplify_wires(wires, protected_points=protected)

    # Should not merge because shared endpoint (5, 5) is where stub meets routing.
    # But actually these are non-colinear so they wouldn't merge anyway.
    # Better test: same direction but one endpoint is protected.
    wires = [
        WireSegment(5.0, 10.0, 5.0, 15.0),  # stub from pin (vertical)
        WireSegment(5.0, 15.0, 5.0, 20.0),  # routing extension (vertical, same line)
    ]
    protected = {(5.0, 10.0)}  # pin position

    result = _simplify_wires(wires, protected_points=protected)

    # These are colinear and share endpoint (5, 15), which is NOT protected.
    # They should merge.
    assert len(result) == 1, "Colinear segments should merge when shared point not protected"
    merged = result[0]
    assert (5.0, 10.0) in {(merged.x1, merged.y1), (merged.x2, merged.y2)}, (
        "Protected pin endpoint must remain as wire start/end"
    )


def test_simplify_preserves_label_attachment_boundaries() -> None:
    """Protected label attachment points keep adjacent vertical runs electrically separate."""

    wires = [
        WireSegment(0.0, 0.0, 0.0, 10.0),
        WireSegment(0.0, 10.0, 0.0, 20.0),
        WireSegment(0.0, 20.0, 0.0, 30.0),
    ]

    result = _simplify_wires(wires, protected_points={(0.0, 10.0), (0.0, 20.0)})

    assert len(result) == 3


def test_l_route_prefers_vertical_first_when_horizontal_elbow_crosses_other_stub_points() -> None:
    route = _l_route_with_protected_points(
        10.0,
        10.0,
        30.0,
        30.0,
        protected_points={(30.0, 15.0), (30.0, 20.0), (30.0, 25.0)},
    )

    assert route == [
        WireSegment(10.0, 10.0, 10.0, 30.0),
        WireSegment(10.0, 30.0, 30.0, 30.0),
    ]


def test_best_direct_route_uses_detour_when_both_elbows_hit_protected_stub_points() -> None:
    route = _best_direct_route_with_protected_points(
        20.0,
        10.0,
        40.0,
        30.0,
        protected_points={(20.0, 25.0), (25.0, 30.0), (40.0, 15.0)},
    )

    assert route == [
        WireSegment(20.0, 10.0, 45.08, 10.0),
        WireSegment(45.08, 10.0, 45.08, 30.0),
        WireSegment(45.08, 30.0, 40.0, 30.0),
    ]


def test_best_direct_route_can_use_second_detour_radius_when_nearer_routes_are_blocked() -> None:
    route = _best_direct_route_with_protected_points(
        2.54,
        143.51,
        31.75,
        114.3,
        protected_points={
            (7.62, 143.51),
            (2.54, 120.65),
            (36.83, 130.81),
            (2.54, 109.22),
            (2.54, 146.05),
            (-2.54, 130.81),
        },
    )

    assert route == [
        WireSegment(2.54, 143.51, -7.62, 143.51),
        WireSegment(-7.62, 143.51, -7.62, 114.3),
        WireSegment(-7.62, 114.3, 31.75, 114.3),
    ]


def test_best_direct_route_can_use_third_detour_radius_when_blocked_nearer_routes_remain() -> None:
    route = _best_direct_route_with_protected_points(
        2.54,
        140.97,
        177.8,
        146.05,
        protected_points={
            (76.2, 140.97),
            (2.54, 143.51),
            (2.54, 135.89),
            (177.8, 135.89),
            (76.2, 135.89),
            (2.54, 130.81),
            (177.8, 130.81),
            (76.2, 130.81),
            (76.2, 143.51),
            (76.2, 148.59),
            (76.2, 151.13),
            (76.2, 156.21),
            (-2.54, 143.51),
            (-7.62, 143.51),
            (182.88, 143.51),
            (187.96, 143.51),
            (2.54, 151.13),
            (177.8, 151.13),
            (2.54, 156.21),
            (177.8, 156.21),
        },
    )

    assert len(route) == 3
    assert sum(abs(seg.x2 - seg.x1) + abs(seg.y2 - seg.y1) for seg in route) > 200.66


def test_label_attachment_plan_uses_offset_breakout_when_stub_is_occupied() -> None:
    """Label fallback should choose a nearby breakout instead of the occupied stub."""
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(60.96, 133.35),
        pin_angle=90.0,
        occupied_points={(60.96, 128.27)},
    )

    assert (anchor_x, anchor_y) != (60.96, 128.27)
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_label_attachment_plan_avoids_future_stub_points() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(7.62, 138.43),
        pin_angle=90.0,
        occupied_points=set(),
        protected=_ProtectedPointContext({(7.62, 143.51)}),
    )

    assert (anchor_x, anchor_y) != (7.62, 143.51)
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_label_attachment_plan_avoids_stub_when_shared_with_another_pin() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(7.62, 138.43),
        pin_angle=270.0,
        occupied_points=set(),
        protected=_ProtectedPointContext(
            {(7.62, 143.51)},
            {(7.62, 143.51)},
        ),
    )

    assert (anchor_x, anchor_y) != (7.62, 143.51)
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_label_attachment_plan_avoids_future_pin_endpoints() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(7.62, 138.43),
        pin_angle=0.0,
        occupied_points=set(),
        protected=_ProtectedPointContext({(7.62, 143.51)}),
    )

    assert (anchor_x, anchor_y) != (7.62, 143.51)
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_label_attachment_plan_avoids_occupied_pin_start_from_prior_label() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(38.10, 124.46),
        pin_angle=270.0,
        occupied_points={(38.10, 124.46)},
    )

    assert (anchor_x, anchor_y) != (38.10, 124.46)
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_promoted_visible_label_avoids_occupied_stub_point() -> None:
    routing = NetRouting(labels=[NetLabel("OLD_NET", 38.10, 124.46, 90)])

    _append_promoted_visible_label(
        routing,
        promotion=_VisibleLabelPromotion(
            net_name="NEW_NET",
            refs=("R1", "R2"),
            block_layout=None,
            classification="signal_chain",
            label_candidates=[(PinRefIR(ref="R1", pin="1"), (38.10, 129.54, 90.0))],
        ),
        policy=DEBUG_LABEL_POLICY,
    )

    new_labels = [label for label in routing.labels if label.name == "NEW_NET"]
    assert len(new_labels) == 1
    assert (round(new_labels[0].x, 2), round(new_labels[0].y, 2)) != (38.10, 124.46)
    assert routing.wires


def test_pin_endpoint_label_breakout_avoids_occupied_prior_label_anchor() -> None:
    routing = NetRouting(global_labels=[GlobalLabelPlacement("/CUR1_OUT", 60.96, 132.08, 90)])

    _append_pin_endpoint_labels(
        routing,
        net_name="/SC1_V+",
        known=[
            (PinRefIR(ref="R11", pin="1"), (60.96, 127.00, 90.0)),
            (PinRefIR(ref="SC1", pin="1"), (60.96, 132.08, 90.0)),
            (PinRefIR(ref="U11", pin="3"), (83.82, 127.00, 0.0)),
        ],
        protected=_ProtectedPointContext(
            {(60.96, 127.00), (60.96, 132.08), (83.82, 127.00)},
        ),
    )

    sc1_labels = [label for label in routing.global_labels if label.name == "/SC1_V+"]
    assert len(sc1_labels) == 3
    assert (60.96, 132.08) not in {(round(label.x, 2), round(label.y, 2)) for label in sc1_labels}
    assert routing.wires


def test_pin_endpoint_label_breakout_prefers_perpendicular_anchor() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(59.69, 130.81),
        pin_angle=0.0,
        occupied_points=set(),
        prefer_perpendicular=True,
    )

    assert round(anchor_y, 2) in {125.73, 135.89}
    assert round(anchor_x, 2) in {54.61, 59.69}
    assert round(anchor_y, 2) != 130.81
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_label_attachment_plan_expands_search_before_reusing_bad_anchor() -> None:
    route, anchor_x, anchor_y = _label_attachment_plan(
        pin_point=(109.22, 144.78),
        pin_angle=180.0,
        occupied_points={(114.30, 144.78)},
        protected=_ProtectedPointContext(
            {
                (109.22, 144.78),
                (114.30, 139.70),
                (114.30, 149.86),
                (109.22, 139.70),
                (109.22, 149.86),
            }
        ),
        prefer_perpendicular=True,
    )

    assert (round(anchor_x, 2), round(anchor_y, 2)) not in {
        (109.22, 144.78),
        (114.30, 144.78),
    }
    assert route
    assert route[-1].x2 == anchor_x and route[-1].y2 == anchor_y


def test_simplify_iterates_until_no_more_merges() -> None:
    """Multiple consecutive colinear segments are fully merged."""
    # Three horizontal segments forming a chain
    wires = [
        WireSegment(0.0, 10.0, 5.0, 10.0),
        WireSegment(5.0, 10.0, 10.0, 10.0),
        WireSegment(10.0, 10.0, 15.0, 10.0),
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1, "All three colinear segments should merge into one"
    merged = result[0]
    assert merged.y1 == 10.0 and merged.y2 == 10.0
    assert {merged.x1, merged.x2} == {0.0, 15.0}


def test_simplify_handles_empty_list() -> None:
    """Empty wire list returns empty result."""
    assert _simplify_wires([]) == []


def test_detect_body_crossings_vertical_detour_uses_non_overlapping_reentry() -> None:
    positions = {"C1": (162.56, 91.44, 0.0)}
    seg = WireSegment(162.56, 106.68, 162.56, 82.55)

    result = detect_body_crossings([seg], positions)
    rounded = [
        WireSegment(
            round(item.x1, 2),
            round(item.y1, 2),
            round(item.x2, 2),
            round(item.y2, 2),
        )
        for item in result
    ]

    assert rounded == [
        WireSegment(162.56, 106.68, 162.56, 96.52),
        WireSegment(162.56, 96.52, 152.4, 96.52),
        WireSegment(152.4, 96.52, 152.4, 86.36),
        WireSegment(152.4, 86.36, 162.56, 86.36),
        WireSegment(162.56, 86.36, 162.56, 82.55),
    ]


def test_simplify_handles_single_segment() -> None:
    """Single segment is returned unchanged."""
    wires = [WireSegment(0.0, 0.0, 10.0, 10.0)]
    result = _simplify_wires(wires)
    assert len(result) == 1
    assert result[0] == wires[0]


def test_simplify_drops_zero_length_segments() -> None:
    """Degenerate zero-length segments are removed before further simplification."""
    wires = [
        WireSegment(10.0, 10.0, 10.0, 10.0),
        WireSegment(0.0, 5.0, 10.0, 5.0),
    ]

    result = _simplify_wires(wires)

    assert result == [WireSegment(0.0, 5.0, 10.0, 5.0)]


def test_simplify_deduplicates_identical_segments_regardless_of_direction() -> None:
    """Exact duplicate segments are collapsed even when reversed."""
    wires = [
        WireSegment(0.0, 5.0, 10.0, 5.0),
        WireSegment(10.0, 5.0, 0.0, 5.0),
    ]

    result = _simplify_wires(wires)

    assert len(result) == 1
    assert {result[0].x1, result[0].x2} == {0.0, 10.0}
    assert result[0].y1 == result[0].y2 == 5.0


def test_chain_route_orders_local_three_pin_net_along_dominant_axis() -> None:
    """Compact local 3-pin nets should route as a simple chain, not a bus."""
    segs, junctions = _chain_route([(30.0, 10.0), (10.0, 10.0), (20.0, 10.0)])

    assert junctions == []
    assert segs == [
        WireSegment(10.0, 10.0, 20.0, 10.0),
        WireSegment(20.0, 10.0, 30.0, 10.0),
    ]


def test_chain_route_avoids_foreign_stub_points_when_detour_is_available() -> None:
    """Chain routing should detour around unrelated stub endpoints when possible."""
    endpoints = [(2.54, 93.98), (45.72, 107.95), (111.76, 129.54)]
    protected_points = {
        (2.54, 96.52),
        (2.54, 99.06),
        (2.54, 104.14),
        (45.72, 97.79),
        (45.72, 105.41),
        (111.76, 113.03),
        (111.76, 121.92),
    }

    segs, junctions = _chain_route(endpoints, protected_points=protected_points)

    assert junctions == []
    assert (
        _route_candidate_key(
            segs,
            protected_points=protected_points,
            endpoints=endpoints,
        )[0]
        == 0
    )


def test_prefer_chain_route_only_when_it_beats_spine_geometry() -> None:
    """Triangular 3-pin nets should keep spine routing when it is shorter/cleaner."""
    assert _prefer_chain_route([(10.0, 10.0), (20.0, 10.0), (30.0, 10.0)])
    assert not _prefer_chain_route([(10.0, 50.0), (30.0, 30.0), (50.0, 50.0)])


def test_small_analog_chain_route_keeps_stage_tail_chain_geometry() -> None:
    """Compact stage tails should still prefer the analog chain heuristic."""
    endpoints = [(69.85, 160.02), (86.36, 160.02), (60.96, 158.75)]

    assert _prefer_small_analog_chain_route(
        endpoints,
        inferred_plan=SharedLanePlan("horizontal", 160.02),
        refs=("RV1", "U1A", "R4"),
    )


def test_small_analog_chain_route_prefers_shared_lane_for_vertical_buffer_handoff() -> None:
    """A compact buffer handoff should keep its shared vertical lane instead of zig-zagging."""
    endpoints = [(106.68, 168.91), (106.68, 158.75), (116.84, 160.02)]

    assert not _prefer_small_analog_chain_route(
        endpoints,
        inferred_plan=SharedLanePlan("vertical", 106.68),
        refs=("C6", "R5", "U1B"),
    )


def test_small_analog_chain_route_prefers_spine_for_output_connector_tail() -> None:
    """A long diagonal connector tail should avoid forcing an all-bends chain."""
    endpoints = [(152.4, 176.53), (167.64, 158.75), (187.96, 167.64)]

    assert not _prefer_small_analog_chain_route(
        endpoints,
        inferred_plan=_infer_bounded_local_lane_plan(endpoints),
        refs=("C7", "R7", "J2"),
    )


def test_shared_lane_route_uses_existing_vertical_lane() -> None:
    """Repeated X coordinates should produce a clean vertical trunk instead of a box."""
    segs, junctions = _shared_lane_route([(44.45, 123.19), (54.61, 101.60), (54.61, 118.11)])

    assert WireSegment(54.61, 101.60, 54.61, 123.19) in segs
    assert WireSegment(44.45, 123.19, 54.61, 123.19) in segs
    assert JunctionPoint(54.61, 123.19) in junctions


def test_plan_local_ladder_routes_prefers_left_entry_lane_for_connector_input_net() -> None:
    """Connector-entry input nets should use an asymmetric left-entry ladder lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ComponentIR(ref="R9", symbol="Device:R", value="1k"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="C5", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                ],
            ),
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
            NetIR(
                name="ISOLATED",
                pins=[
                    PinRefIR(ref="J2", pin="1"),
                    PinRefIR(ref="R9", pin="1"),
                ],
            ),
        ],
    )

    ladder_routes = _plan_local_ladder_routes(
        ir,
        {
            ("J1", "1"): (39.37, 123.19, 180.0),
            ("C5", "1"): (54.61, 106.68, 270.0),
            ("R1", "1"): (54.61, 118.11, 270.0),
            ("C5", "2"): (54.61, 114.30, 90.0),
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
            ("J2", "1"): (200.0, 100.0, 180.0),
            ("R9", "1"): (230.0, 100.0, 180.0),
        },
    )

    assert ladder_routes["LEFT_IN"] == SharedLanePlan("vertical", 49.53)
    assert ladder_routes["IN_L_AC"] == SharedLanePlan("vertical", 60.96, 120.65, 134.62)


def test_simplify_floating_point_tolerance() -> None:
    """Endpoint coordinates are compared with 0.01 mm tolerance (2 decimal places).

    Segments must still be exactly colinear (same x or same y) to merge;
    only the endpoint matching uses tolerance for floating-point stability.
    """
    # Segments with endpoints that round to the same value
    wires = [
        WireSegment(0.0, 10.0, 5.004, 10.0),  # rounds to (0.0, 10.0) → (5.0, 10.0)
        WireSegment(5.006, 10.0, 10.0, 10.0),  # rounds to (5.01, 10.0) → (10.0, 10.0)
    ]

    result = _simplify_wires(wires)

    # Segments don't share an endpoint after rounding (5.0 vs 5.01), so no merge
    assert len(result) == 2, "Segments without shared rounded endpoints don't merge"

    # But if they DO share a rounded endpoint:
    wires = [
        WireSegment(0.0, 10.0, 5.003, 10.0),  # rounds to (5.0, 10.0)
        WireSegment(5.006, 10.0, 10.0, 10.0),  # rounds to (5.01, 10.0)
    ]
    result = _simplify_wires(wires)
    assert len(result) == 2  # Still no shared endpoint


def test_simplify_reduces_short_segments_vs_unsimplified_baseline() -> None:
    """Simplification should reduce unnecessary 5.08mm jog-heavy patterns."""
    baseline = [
        WireSegment(0.0, 0.0, 5.08, 0.0),
        WireSegment(5.08, 0.0, 10.16, 0.0),
        WireSegment(10.16, 0.0, 15.24, 0.0),
        WireSegment(15.24, 0.0, 20.32, 0.0),
        WireSegment(20.32, 0.0, 20.32, 20.0),
    ]

    simplified = _simplify_wires(baseline)

    assert _count_short_segments(simplified) < _count_short_segments(baseline)
    assert len(simplified) < len(baseline)


def test_simplify_keeps_required_5mm_jogs_at_junctions() -> None:
    """5.08mm segments are preserved when they form a required T-junction."""
    wires = [
        WireSegment(0.0, 5.08, 5.08, 5.08),
        WireSegment(5.08, 5.08, 10.16, 5.08),
        WireSegment(5.08, 0.0, 5.08, 5.08),
    ]

    simplified = _simplify_wires(wires)

    assert len(simplified) == 3, "T-junction branches must not be merged away"
    assert _count_short_segments(simplified) == _count_short_segments(wires)


def test_route_nets_simplified_wires_remain_collision_safe() -> None:
    """Route simplification should not reintroduce component-body crossings."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
            ComponentIR(ref="R2", symbol="Device:R", value="1k"),
        ],
        nets=[
            NetIR(
                name="SIG",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
            )
        ],
    )

    pin_endpoints = {
        ("R1", "1"): (5.0, 0.0, 180.0),
        ("R2", "1"): (35.0, 0.0, 0.0),
    }
    obstacle_x, obstacle_y = 20.0, 0.0

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions={"U_OBS": (obstacle_x, obstacle_y, 0.0)},
    )

    assert routing.wires, "Expected routed wire segments"
    # Boundary-touching detour segments are acceptable; they should not
    # enter the obstacle interior.
    interior_half = SYMBOL_HALF_SIZE_MM - 0.01
    for seg in routing.wires:
        assert not _wire_crosses_box(
            seg.x1,
            seg.y1,
            seg.x2,
            seg.y2,
            obstacle_x,
            obstacle_y,
            interior_half,
        )


def test_route_nets_cleanup_removes_zero_length_detour_segments() -> None:
    """Boundary-touching detours should not leak zero-length wires into output."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
            ComponentIR(ref="R2", symbol="Device:R", value="1k"),
        ],
        nets=[
            NetIR(
                name="SIG",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
            )
        ],
    )

    pin_endpoints = {
        ("R1", "1"): (100.08, 0.0, 0.0),
        ("R2", "1"): (140.0, 0.0, 180.0),
    }

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions={"U_OBS": (100.0, 0.0, 0.0)},
    )

    assert routing.wires

    def _is_zero_length(seg: WireSegment) -> bool:
        return math.isclose(seg.x1, seg.x2, abs_tol=0.01) and math.isclose(
            seg.y1, seg.y2, abs_tol=0.01
        )

    assert all(not _is_zero_length(seg) for seg in routing.wires)


def test_route_nets_prefers_chain_for_compact_three_pin_signal_net() -> None:
    """Local 3-pin signal nets should avoid the default spine/junction topology."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="C5", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (15.08, 10.0, 0.0),
            ("C5", "1"): (25.08, 10.0, 0.0),
            ("R1", "1"): (35.08, 10.0, 0.0),
        },
    )

    assert routing.junctions == []
    assert routing.route_decisions[0].strategy == "chain"
    actual_segments = {
        (round(seg.x1, 2), round(seg.y1, 2), round(seg.x2, 2), round(seg.y2, 2))
        for seg in routing.wires
    }
    assert {
        (10.0, 10.0, 10.0, 4.92),
        (10.0, 4.92, 20.0, 4.92),
        (20.0, 10.0, 20.0, 4.92),
        (20.0, 4.92, 30.0, 4.92),
        (30.0, 10.0, 30.0, 4.92),
    } <= actual_segments


def test_route_nets_uses_ladder_route_for_adjacent_three_pin_nets() -> None:
    """Adjacent input ladders should keep a distinct left-entry lane for LEFT_IN."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="C5", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                ],
            ),
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (39.37, 123.19, 180.0),
            ("C5", "1"): (54.61, 106.68, 270.0),
            ("R1", "1"): (54.61, 118.11, 270.0),
            ("C5", "2"): (54.61, 114.30, 90.0),
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
        },
    )

    actual_segments = {
        (round(seg.x1, 2), round(seg.y1, 2), round(seg.x2, 2), round(seg.y2, 2))
        for seg in routing.wires
    }
    assert (39.37, 123.19, 44.45, 123.19) in actual_segments
    assert (44.45, 123.19, 49.53, 123.19) in actual_segments

    vertical_lanes = {
        round(seg.x1, 2)
        for seg in routing.wires
        if math.isclose(seg.x1, seg.x2, abs_tol=0.01) and round(seg.x1, 2) in {49.53, 60.96}
    }
    assert vertical_lanes == {49.53, 60.96}


def test_route_nets_secondary_input_lane_avoids_full_c5_r1_rectangle() -> None:
    """IN_L_AC should continue downstream without a full-height parallel box."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="C5", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                ],
            ),
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (39.37, 123.19, 180.0),
            ("C5", "1"): (54.61, 106.68, 270.0),
            ("R1", "1"): (54.61, 118.11, 270.0),
            ("C5", "2"): (54.61, 114.30, 90.0),
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
        },
    )

    assert WireSegment(60.96, 120.65, 60.96, 134.62) in routing.wires
    assert WireSegment(60.96, 109.22, 60.96, 134.62) not in routing.wires
    assert WireSegment(54.61, 109.22, 60.96, 109.22) not in routing.wires


def test_plan_local_ladder_routes_bounds_vol_l_out_horizontal_lane() -> None:
    """VOL_L_OUT should not keep an unbounded full-width horizontal shared lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
            NetIR(
                name="VOL_L_OUT",
                pins=[
                    PinRefIR(ref="RV1", pin="2"),
                    PinRefIR(ref="R4", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 114.30, 90.0),
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
            ("RV1", "2"): (88.90, 128.27, 270.0),
            ("R4", "1"): (85.09, 119.38, 270.0),
            ("U1", "3"): (138.43, 124.46, 0.0),
        },
    )

    assert "VOL_L_OUT" in plans
    assert plans["VOL_L_OUT"].axis == "horizontal"
    assert math.isclose(plans["VOL_L_OUT"].coordinate, 124.46, abs_tol=0.01)
    assert plans["VOL_L_OUT"].min_orthogonal is not None
    assert plans["VOL_L_OUT"].max_orthogonal is not None
    assert plans["VOL_L_OUT"].min_orthogonal >= 85.09
    assert plans["VOL_L_OUT"].max_orthogonal <= 133.35
    assert not (
        math.isclose(plans["VOL_L_OUT"].min_orthogonal, 85.09, abs_tol=0.01)
        and math.isclose(plans["VOL_L_OUT"].max_orthogonal, 133.35, abs_tol=0.01)
    )


def test_plan_local_ladder_routes_infers_full_preview_vol_l_out_lane() -> None:
    """Full-layout VOL_L_OUT geometry should still receive a bounded ladder plan."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C5", symbol="Device:C", value="1u"),
            ComponentIR(ref="R1", symbol="Device:R", value="100k"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="IN_L_AC",
                pins=[
                    PinRefIR(ref="C5", pin="2"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="RV1", pin="1"),
                ],
            ),
            NetIR(
                name="VOL_L_OUT",
                pins=[
                    PinRefIR(ref="RV1", pin="2"),
                    PinRefIR(ref="R4", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 106.68, 90.0),
            ("R1", "2"): (54.61, 121.92, 90.0),
            ("RV1", "1"): (85.09, 144.78, 270.0),
            ("RV1", "2"): (88.90, 140.97, 180.0),
            ("R4", "1"): (85.09, 114.30, 270.0),
            ("U1", "3"): (138.43, 120.65, 0.0),
        },
    )

    assert "VOL_L_OUT" in plans
    assert plans["VOL_L_OUT"] == SharedLanePlan("horizontal", 120.65, 85.09, 133.35)
