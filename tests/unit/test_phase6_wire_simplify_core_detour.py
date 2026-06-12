"""Phase 6 wire simplify tests — detour routing, label attachment, dedup."""

from __future__ import annotations

from kicad_pcb.circuit_ir import PinRefIR
from kicad_pcb.router import (
    DEBUG_LABEL_POLICY,
    GlobalLabelPlacement,
    NetLabel,
    NetRouting,
    WireSegment,
    _append_pin_endpoint_labels,
    _append_promoted_visible_label,
    _best_direct_route_with_protected_points,
    _label_attachment_plan,
    _ProtectedPointContext,
    _simplify_wires,
    _VisibleLabelPromotion,
    detect_body_crossings,
)


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
