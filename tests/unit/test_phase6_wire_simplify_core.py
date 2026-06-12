"""Phase 6: wire simplification, chain routing, label attachment, ladder routing."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.circuit_ir import PinRefIR
from kicad_pcb.router import (
    DEBUG_LABEL_POLICY,
    GlobalLabelPlacement,
    JunctionPoint,
    NetLabel,
    NetRouting,
    WireSegment,
    _append_pin_endpoint_labels,
    _append_promoted_visible_label,
    _best_direct_route_with_protected_points,
    _infer_safe_t_junctions,
    _l_route_with_protected_points,
    _label_attachment_plan,
    _ProtectedPointContext,
    _simplify_wires,
    _split_wires_at_points,
    _VisibleLabelPromotion,
    detect_body_crossings,
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
