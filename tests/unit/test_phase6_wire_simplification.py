"""Phase 6.1 — Wire simplification by merging consecutive colinear segments.

Tests the _simplify_wires() function which reduces visual clutter by
merging wire segments that lie on the same horizontal or vertical line.
"""

from __future__ import annotations

import math

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.router import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    SYMBOL_HALF_SIZE_MM,
    JunctionPoint,
    RoutingHeuristicPolicy,
    SharedLanePlan,
    WireSegment,
    _chain_route,
    _l_route_with_protected_points,
    _plan_local_ladder_routes,
    _point_in_or_on_box,
    _prefer_chain_route,
    _shared_lane_route,
    _simplify_wires,
    _wire_crosses_box,
    detect_body_crossings,
    route_nets,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


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


def test_prefer_chain_route_only_when_it_beats_spine_geometry() -> None:
    """Triangular 3-pin nets should keep spine routing when it is shorter/cleaner."""
    assert _prefer_chain_route([(10.0, 10.0), (20.0, 10.0), (30.0, 10.0)])
    assert not _prefer_chain_route([(10.0, 50.0), (30.0, 30.0), (50.0, 50.0)])


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
    assert len(routing.wires) == 3
    assert all(seg.y1 == seg.y2 == 10.0 for seg in routing.wires)
    covered_spans = sorted((min(seg.x1, seg.x2), max(seg.x1, seg.x2)) for seg in routing.wires)
    assert covered_spans == [(10.0, 20.0), (20.0, 25.08), (20.0, 35.08)]


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

    assert WireSegment(39.37, 123.19, 49.53, 123.19) in routing.wires

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


def test_plan_local_ladder_routes_skips_compact_rightward_output_tail() -> None:
    """A compact rightward 3-pin tail should fall back to chain routing, not a lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="J2", pin="1"),
                    PinRefIR(ref="R7", pin="1"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (55.08, 20.0, 0.0),
            ("C7", "1"): (55.08, 35.0, 0.0),
            ("C7", "2"): (55.08, 10.0, 0.0),
            ("J2", "1"): (55.08, 20.0, 0.0),
            ("R7", "1"): (75.08, 20.0, 0.0),
        },
    )

    assert "HP_L_OUT" not in plans


def test_plan_local_ladder_routes_skips_asymmetric_compact_output_tail() -> None:
    """A near-lane asymmetric output tail should still avoid inferred ladder routing."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (213.36, 173.99, 90.0),
            ("C7", "1"): (213.36, 135.89, 270.0),
            ("C7", "2"): (213.36, 128.27, 90.0),
            ("R7", "1"): (238.76, 166.37, 270.0),
            ("J2", "T"): (217.17, 165.10, 0.0),
        },
    )

    assert "HP_L_OUT" not in plans


def test_plan_local_ladder_routes_can_disable_compact_output_tail_policy() -> None:
    """Disabling the analog compact-tail rule should restore the inferred lane plan."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )

    plans = _plan_local_ladder_routes(
        ir,
        pin_endpoints={
            ("R6", "2"): (213.36, 173.99, 90.0),
            ("C7", "1"): (213.36, 135.89, 270.0),
            ("C7", "2"): (213.36, 128.27, 90.0),
            ("R7", "1"): (238.76, 166.37, 270.0),
            ("J2", "T"): (217.17, 165.10, 0.0),
        },
        heuristic_policy=RoutingHeuristicPolicy(enable_compact_output_tails=False),
    )

    assert DEFAULT_ROUTING_HEURISTIC_POLICY.enable_compact_output_tails
    assert "HP_L_OUT" in plans
    assert plans["HP_L_OUT"] == SharedLanePlan("vertical", 213.36, 123.19, 165.1)


def test_small_analog_local_routing_prefers_chain_over_compact_lane() -> None:
    """Analog small-circuit mode should drop a compact lane when a chain is cleaner."""
    endpoints = [(54.61, 114.30), (54.61, 125.73), (85.09, 129.54)]
    lane_plan = SharedLanePlan("vertical", 54.61, 114.30, 129.54)
    analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]

    assert DEFAULT_ROUTING_HEURISTIC_POLICY.enable_small_analog_local_routing is False
    assert analog_audio.routing_policy.enable_small_analog_local_routing is True
    assert analog_audio.routing_policy.should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=lane_plan,
    )
    assert not RoutingHeuristicPolicy(
        enable_small_analog_local_routing=False,
    ).should_prefer_small_analog_chain(
        endpoints,
        inferred_plan=lane_plan,
    )


def test_small_analog_local_routing_draws_buffer_follower_as_local_loop_plus_branch() -> None:
    """Analog mode should draw a buffer follower net as a compact local loop."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
        ],
        nets=[
            NetIR(
                name="OUT_L_STAGE2_RAW",
                pins=[
                    PinRefIR(ref="U1B", pin="6"),
                    PinRefIR(ref="U1B", pin="7"),
                    PinRefIR(ref="R6", pin="1"),
                ],
            ),
        ],
    )
    pin_endpoints = {
        ("U1B", "6"): (140.0, 100.0, 0.0),
        ("U1B", "7"): (150.0, 100.0, 180.0),
        ("R6", "1"): (170.0, 100.0, 0.0),
    }
    block_layout = BlockLayout()
    block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
    block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)

    analog_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        block_layout=block_layout,
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["analog_audio"].routing_policy,
    )
    digital_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        block_layout=block_layout,
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["generic_digital"].routing_policy,
    )

    analog_choice = analog_routing.route_decisions[0]
    digital_choice = digital_routing.route_decisions[0]
    analog_segments = {
        (
            round(seg.x1, 2),
            round(seg.y1, 2),
            round(seg.x2, 2),
            round(seg.y2, 2),
        )
        for seg in analog_routing.wires
    }
    digital_segments = {
        (
            round(seg.x1, 2),
            round(seg.y1, 2),
            round(seg.x2, 2),
            round(seg.y2, 2),
        )
        for seg in digital_routing.wires
    }

    assert analog_choice.strategy == "chain"
    assert analog_choice.heuristic_override == "small_analog_local_routing"
    assert digital_choice.heuristic_override is None
    assert (134.92, 95.25, 155.08, 95.25) in analog_segments, analog_routing.wires
    assert (134.92, 100.0, 134.92, 95.25) in analog_segments, analog_routing.wires
    assert (155.08, 95.25, 155.08, 100.0) in analog_segments, analog_routing.wires
    assert (170.0, 100.0, 155.08, 100.0) in analog_segments, analog_routing.wires
    assert (134.92, 95.25, 155.08, 95.25) not in digital_segments, digital_routing.wires


def test_named_routing_profiles_diverge_on_small_analog_input_chain_fixture() -> None:
    """Analog small-circuit mode should prefer a chain where digital keeps a lane."""
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
    pin_endpoints = {
        ("J1", "1"): (39.37, 123.19, 180.0),
        ("C5", "1"): (54.61, 106.68, 270.0),
        ("R1", "1"): (54.61, 118.11, 270.0),
        ("C5", "2"): (54.61, 114.30, 90.0),
        ("R1", "2"): (54.61, 125.73, 90.0),
        ("RV1", "1"): (85.09, 129.54, 270.0),
    }
    analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]
    generic_digital = SCHEMATIC_HEURISTIC_PROFILES["generic_digital"]

    analog_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        heuristic_policy=analog_audio.routing_policy,
    )
    digital_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        heuristic_policy=generic_digital.routing_policy,
    )
    analog_choice = next(
        choice for choice in analog_routing.route_decisions if choice.net_name == "IN_L_AC"
    )
    digital_choice = next(
        choice for choice in digital_routing.route_decisions if choice.net_name == "IN_L_AC"
    )
    left_in_choice = next(
        choice for choice in analog_routing.route_decisions if choice.net_name == "LEFT_IN"
    )

    assert analog_audio.routing_policy.enable_small_analog_local_routing is True
    assert generic_digital.routing_policy.enable_small_analog_local_routing is False
    assert left_in_choice.classification == "connector_attachment"
    assert analog_choice.classification == "signal_chain"
    assert digital_choice.classification == "signal_chain"
    assert analog_choice.strategy == "chain"
    assert analog_choice.heuristic_override == "small_analog_local_routing"
    assert digital_choice.strategy == "shared_lane"
    assert digital_choice.heuristic_override is None


def test_route_nets_classifies_feedback_net_explicitly() -> None:
    """Feedback-style nets should surface the first-class feedback routing category."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
        ],
        nets=[
            NetIR(
                name="U1A_INV",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R4", pin="2"),
                    PinRefIR(ref="U1", pin="2"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "2"): (54.61, 125.73, 90.0),
            ("R4", "2"): (85.09, 125.73, 90.0),
            ("U1", "2"): (69.85, 106.68, 270.0),
        },
        heuristic_policy=SCHEMATIC_HEURISTIC_PROFILES["analog_audio"].routing_policy,
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "U1A_INV")

    assert choice.classification == "feedback"


def test_short_signal_chain_net_stays_direct_before_label_fallback() -> None:
    """Short local signal-chain nets should stay wired even when tier drift would label them."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="STAGE_L",
                pins=[
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (70.0, 100.0, 180.0),
        },
        tiers={"R1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "STAGE_L")

    assert choice.classification == "signal_chain"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_short_feedback_net_stays_direct_before_label_fallback() -> None:
    """Short feedback nets should prefer a local wire over label fallback."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="U1A_INV",
                pins=[
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="2"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("U1", "2"): (70.0, 100.0, 180.0),
        },
        tiers={"R1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "U1A_INV")

    assert choice.classification == "feedback"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_short_connector_attachment_net_stays_direct_before_label_fallback() -> None:
    """Short local connector-attachment nets should stay wired instead of labeling."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x02", value="IN"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
        ],
        nets=[
            NetIR(
                name="LEFT_IN",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="U1", pin="3"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (69.0, 100.0, 180.0),
        },
        tiers={"J1": 0, "U1": 2},
    )

    choice = next(choice for choice in routing.route_decisions if choice.net_name == "LEFT_IN")

    assert choice.classification == "connector_attachment"
    assert choice.strategy == "direct"
    assert routing.labels == []


def test_route_nets_routes_full_preview_vol_l_out_as_downstream_continuation() -> None:
    """Full-preview VOL_L_OUT should read as RV1 continuing downstream into U1."""
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

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 106.68, 90.0),
            ("R1", "2"): (54.61, 121.92, 90.0),
            ("RV1", "1"): (85.09, 144.78, 270.0),
            ("RV1", "2"): (88.90, 140.97, 180.0),
            ("R4", "1"): (85.09, 114.30, 270.0),
            ("U1", "3"): (138.43, 120.65, 0.0),
        },
        positions={
            "C5": (54.61, 110.49, 0.0),
            "R1": (54.61, 125.73, 0.0),
            "RV1": (85.09, 140.97, 0.0),
            "R4": (85.09, 110.49, 0.0),
            "U1": (146.05, 118.11, 0.0),
        },
    )
    choice = next(choice for choice in routing.route_decisions if choice.net_name == "VOL_L_OUT")

    assert choice.strategy == "compact_signal_tail"
    assert choice.heuristic_override == "compact_output_tail"
    assert WireSegment(138.43, 120.65, 85.09, 120.65) not in routing.wires
    assert WireSegment(85.09, 119.38, 88.90, 119.38) in routing.wires
    assert any(
        math.isclose(w.x1, 88.90, abs_tol=0.01)
        and math.isclose(w.x2, 88.90, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {119.38, 140.97}
        for w in routing.wires
    )
    assert WireSegment(88.90, 140.97, 138.43, 140.97) in routing.wires
    assert any(
        math.isclose(w.x1, 138.43, abs_tol=0.01)
        and math.isclose(w.x2, 138.43, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {120.65, 140.97}
        for w in routing.wires
    )
    assert not any(
        math.isclose(w.x1, 93.98, abs_tol=0.01) or math.isclose(w.x2, 93.98, abs_tol=0.01)
        for w in routing.wires
    )


def test_route_nets_vol_l_out_no_l_shaped_detour_when_rv1_exits_rightward() -> None:
    """VOL_L_OUT with RV1 pin2 at angle 180° must not produce a right-then-up detour.

    In the original problem geometry (snapshot _230958), RV1 pin2 exits rightward
    while the op-amp input sits to the right. The old behaviour produced a short
    rightward stub followed immediately by a drop into a horizontal bus-like trunk.
    After the fix, RV1 feeds a short support rise from R4 and then continues as one
    dominant downstream run toward U1.
    """
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

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C5", "2"): (54.61, 106.68, 90.0),
            ("R1", "2"): (54.61, 121.92, 90.0),
            ("RV1", "1"): (85.09, 129.54, 270.0),
            ("RV1", "2"): (88.90, 133.35, 180.0),  # exits rightward — the problem pin
            ("R4", "1"): (85.09, 119.38, 270.0),
            ("U1", "3"): (138.43, 120.65, 0.0),
        },
        positions={
            "C5": (54.61, 110.49, 0.0),
            "R1": (54.61, 125.73, 0.0),
            "RV1": (85.09, 133.35, 0.0),
            "R4": (85.09, 123.19, 0.0),
            "U1": (146.05, 118.11, 0.0),
        },
    )
    choice = next(choice for choice in routing.route_decisions if choice.net_name == "VOL_L_OUT")

    assert choice.strategy == "compact_signal_tail"
    assert choice.heuristic_override == "compact_output_tail"
    assert WireSegment(85.09, 124.46, 88.90, 124.46) in routing.wires
    assert any(
        math.isclose(w.x1, 88.90, abs_tol=0.01)
        and math.isclose(w.x2, 88.90, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {124.46, 133.35}
        for w in routing.wires
    )
    assert WireSegment(88.90, 133.35, 138.43, 133.35) in routing.wires
    assert any(
        math.isclose(w.x1, 138.43, abs_tol=0.01)
        and math.isclose(w.x2, 138.43, abs_tol=0.01)
        and {round(w.y1, 2), round(w.y2, 2)} == {120.65, 133.35}
        for w in routing.wires
    )
    # Old bus-like ladder segments must be absent.
    assert WireSegment(85.09, 124.46, 133.35, 124.46) not in routing.wires
    assert not any(
        math.isclose(w.x1, 93.98, abs_tol=0.01) and math.isclose(w.x2, 93.98, abs_tol=0.01)
        for w in routing.wires
    ), "expected no wire at x=93.98 (old L-detour connector position)"


def test_route_nets_uses_chain_for_compact_rightward_output_tail() -> None:
    """Compact rightward tails should avoid forced shared-lane junctions."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="J2", pin="1"),
                    PinRefIR(ref="R7", pin="1"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R6", "2"): (55.08, 20.0, 0.0),
            ("C7", "1"): (55.08, 35.0, 0.0),
            ("C7", "2"): (55.08, 10.0, 0.0),
            ("J2", "1"): (55.08, 20.0, 0.0),
            ("R7", "1"): (75.08, 20.0, 0.0),
        },
    )

    assert routing.junctions == []
    assert WireSegment(50.0, 10.0, 50.0, 20.0) in routing.wires
    assert WireSegment(75.08, 20.0, 50.0, 20.0) in routing.wires


def test_route_nets_uses_chain_for_asymmetric_compact_output_tail() -> None:
    """Real asymmetric output-tail geometry should use the compact tail route."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R6", "2"): (213.36, 173.99, 90.0),
            ("C7", "1"): (213.36, 135.89, 270.0),
            ("C7", "2"): (213.36, 128.27, 90.0),
            ("R7", "1"): (238.76, 166.37, 270.0),
            ("J2", "T"): (217.17, 165.10, 0.0),
        },
        positions={
            "C7": (213.36, 132.08, 0.0),
            "R7": (238.76, 162.56, 0.0),
            "J2": (222.25, 162.56, 0.0),
        },
    )

    assert routing.junctions == [JunctionPoint(213.36, 152.40)]
    assert WireSegment(213.36, 123.19, 213.36, 165.10) not in routing.wires
    assert any(
        math.isclose(seg.y1, 152.40, abs_tol=0.01)
        and math.isclose(seg.y2, 152.40, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 212.09, abs_tol=0.01)
        and max(seg.x1, seg.x2) > 243.84
        for seg in routing.wires
    )
    assert WireSegment(238.76, 165.10, 238.76, 171.45) not in routing.wires
    assert any(
        math.isclose(seg.x1, seg.x2, abs_tol=0.01)
        and seg.x1 > 243.84
        and math.isclose(min(seg.y1, seg.y2), 152.40, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 171.45, abs_tol=0.01)
        for seg in routing.wires
    )


def test_named_routing_profiles_diverge_on_output_tail_fixture() -> None:
    """Named profiles should pick different routing strategies on the same output-tail fixture."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C7", symbol="Device:C", value="100n"),
            ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
        ],
        nets=[
            NetIR(
                name="AFTER_R6",
                pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
            ),
            NetIR(
                name="HP_L_OUT",
                pins=[
                    PinRefIR(ref="C7", pin="2"),
                    PinRefIR(ref="R7", pin="1"),
                    PinRefIR(ref="J2", pin="T"),
                ],
            ),
        ],
    )
    pin_endpoints = {
        ("R6", "2"): (213.36, 173.99, 90.0),
        ("C7", "1"): (213.36, 135.89, 270.0),
        ("C7", "2"): (213.36, 128.27, 90.0),
        ("R7", "1"): (238.76, 166.37, 270.0),
        ("J2", "T"): (217.17, 165.10, 0.0),
    }
    positions = {
        "C7": (213.36, 132.08, 0.0),
        "R7": (238.76, 162.56, 0.0),
        "J2": (222.25, 162.56, 0.0),
    }
    analog_audio = SCHEMATIC_HEURISTIC_PROFILES["analog_audio"]
    generic_digital = SCHEMATIC_HEURISTIC_PROFILES["generic_digital"]

    analog_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions=positions,
        heuristic_policy=analog_audio.routing_policy,
    )
    digital_routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions=positions,
        heuristic_policy=generic_digital.routing_policy,
    )
    analog_choice = next(
        choice for choice in analog_routing.route_decisions if choice.net_name == "HP_L_OUT"
    )
    digital_choice = next(
        choice for choice in digital_routing.route_decisions if choice.net_name == "HP_L_OUT"
    )

    assert analog_audio.routing_policy.enable_compact_output_tails is True
    assert generic_digital.routing_policy.enable_compact_output_tails is False
    assert analog_choice.strategy == "compact_signal_tail"
    assert analog_choice.heuristic_override == "compact_output_tail"
    assert digital_choice.strategy == "shared_lane"
    assert digital_choice.heuristic_override is None
    assert analog_routing.wires != digital_routing.wires
    assert analog_routing.junctions != digital_routing.junctions


def test_route_nets_uses_compact_local_ground_lane_for_output_cluster() -> None:
    """A compact J2/R5/R7 ground cluster should avoid centroid-knot shorts."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
            ComponentIR(ref="R5", symbol="Device:R", value="10k"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J2", pin="S"),
                    PinRefIR(ref="R5", pin="2"),
                    PinRefIR(ref="R7", pin="2"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J2", "S"): (217.17, 160.02, 0.0),
            ("R5", "2"): (213.36, 143.51, 90.0),
            ("R7", "2"): (238.76, 158.75, 90.0),
        },
        positions={
            "J2": (222.25, 162.56, 0.0),
            "R5": (213.36, 147.32, 0.0),
            "R7": (238.76, 162.56, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    power_symbol = routing.power_symbols[0]
    assert power_symbol.net_name == "GND"
    assert math.isclose(power_symbol.x, 248.92, abs_tol=0.01)
    assert math.isclose(power_symbol.y, 153.67, abs_tol=0.01)
    assert power_symbol.angle == 0
    assert any(
        math.isclose(seg.y1, 153.67, abs_tol=0.01)
        and math.isclose(seg.y2, 153.67, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 203.2, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 248.92, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 203.2, abs_tol=0.01)
        and math.isclose(seg.x2, 203.2, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 138.43, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 153.67, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 212.09, abs_tol=0.01)
        and math.isclose(seg.x2, 212.09, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 153.67, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 160.02, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.y1, 138.43, abs_tol=0.01)
        and math.isclose(seg.y2, 138.43, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 203.2, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 213.36, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_can_disable_compact_local_ground_cluster_policy() -> None:
    """Disabling the analog ground-cluster rule should fall back to centroid routing."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
            ComponentIR(ref="R5", symbol="Device:R", value="10k"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J2", pin="S"),
                    PinRefIR(ref="R5", pin="2"),
                    PinRefIR(ref="R7", pin="2"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J2", "S"): (217.17, 160.02, 0.0),
            ("R5", "2"): (213.36, 143.51, 90.0),
            ("R7", "2"): (238.76, 158.75, 90.0),
        },
        positions={
            "J2": (222.25, 162.56, 0.0),
            "R5": (213.36, 147.32, 0.0),
            "R7": (238.76, 162.56, 0.0),
        },
        heuristic_policy=RoutingHeuristicPolicy(enable_compact_local_ground_clusters=False),
    )

    assert DEFAULT_ROUTING_HEURISTIC_POLICY.enable_compact_local_ground_clusters
    assert len(routing.power_symbols) == 1
    power_symbol = routing.power_symbols[0]
    assert not math.isclose(power_symbol.x, 248.92, abs_tol=0.01)
    assert not math.isclose(power_symbol.y, 138.43, abs_tol=0.01)
    assert not any(
        math.isclose(seg.y1, 138.43, abs_tol=0.01)
        and math.isclose(seg.y2, 138.43, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 203.2, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 238.76, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_uses_compact_local_ground_lane_for_near_square_input_cluster() -> None:
    """A nearly square 3-pin input-side GND cluster should still use the compact lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector:AudioJack3", value="IN"),
            ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="100k"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J1", pin="S"),
                    PinRefIR(ref="RV1", pin="3"),
                    PinRefIR(ref="R4", pin="2"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "S"): (35.56, 201.93, 180.0),
            ("RV1", "3"): (30.48, 195.58, 90.0),
            ("R4", "2"): (30.48, 195.58, 90.0),
        },
        positions={
            "J1": (30.48, 199.39, 0.0),
            "RV1": (30.48, 199.39, 0.0),
            "R4": (30.48, 199.39, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_ground_cluster"
    power_symbol = routing.power_symbols[0]
    assert power_symbol.net_name == "GND"
    assert math.isclose(power_symbol.x, 50.8, abs_tol=0.01)
    assert math.isclose(power_symbol.y, 190.5, abs_tol=0.01)
    assert any(
        math.isclose(seg.y1, 190.5, abs_tol=0.01)
        and math.isclose(seg.y2, 190.5, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 30.48, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 40.64, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 40.64, abs_tol=0.01)
        and math.isclose(seg.x2, 40.64, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 190.5, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 201.93, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.y1, 190.5, abs_tol=0.01)
        and math.isclose(seg.y2, 190.5, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 40.64, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 50.8, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_uses_middle_lane_for_compressed_output_ground_cluster() -> None:
    """Compressed output-side GND clusters should be able to use a higher clear lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J2", symbol="Connector:AudioJack3", value="OUT"),
            ComponentIR(ref="R5", symbol="Device:R", value="10k"),
            ComponentIR(ref="R7", symbol="Device:R", value="100"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J2", pin="S"),
                    PinRefIR(ref="R5", pin="2"),
                    PinRefIR(ref="R7", pin="2"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J2", "S"): (217.17, 119.38, 0.0),
            ("R5", "2"): (213.36, 133.35, 90.0),
            ("R7", "2"): (238.76, 138.43, 90.0),
        },
        positions={
            "J2": (222.25, 121.92, 0.0),
            "R5": (213.36, 137.16, 0.0),
            "R7": (238.76, 142.24, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    power_symbol = routing.power_symbols[0]
    assert power_symbol.net_name == "GND"
    assert math.isclose(power_symbol.x, 248.92, abs_tol=0.01)
    assert math.isclose(power_symbol.y, 128.27, abs_tol=0.01)
    assert any(
        math.isclose(seg.y1, 128.27, abs_tol=0.01)
        and math.isclose(seg.y2, 128.27, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 212.09, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 238.76, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 212.09, abs_tol=0.01)
        and math.isclose(seg.x2, 212.09, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 119.38, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 128.27, abs_tol=0.01)
        for seg in routing.wires
    )

    protected = {
        (212.09, 160.02),
        (213.36, 138.43),
        (238.76, 153.67),
    }
    short_non_stub = [
        seg
        for seg in routing.wires
        if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= 10.0
        and (round(seg.x1, 2), round(seg.y1, 2)) not in protected
        and (round(seg.x2, 2), round(seg.y2, 2)) not in protected
    ]
    assert short_non_stub != []


def test_route_nets_uses_compact_local_decoupling_lane_for_positive_rail_cluster() -> None:
    """A compact VPLUS decoupling cluster should use one local horizontal rail lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x03", value="PWR"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="C3", symbol="Device:C_Polarized", value="10u"),
        ],
        nets=[
            NetIR(
                name="VPLUS15",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="U1P", pin="8"),
                    PinRefIR(ref="C1", pin="1"),
                    PinRefIR(ref="C3", pin="1"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J3", "1"): (40.64, 50.8, 180.0),
            ("U1P", "8"): (91.44, 58.42, 180.0),
            ("C1", "1"): (76.2, 83.82, 90.0),
            ("C3", "1"): (63.5, 76.2, 90.0),
        },
        positions={
            "J3": (45.72, 53.34, 0.0),
            "U1P": (96.52, 58.42, 0.0),
            "C1": (76.2, 87.63, 0.0),
            "C3": (63.5, 80.01, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_decoupling_cluster"
    power_symbol = routing.power_symbols[0]
    assert power_symbol.net_name == "VPLUS15"
    assert math.isclose(power_symbol.x, 106.68, abs_tol=0.01)
    assert math.isclose(power_symbol.y, 71.12, abs_tol=0.01)
    assert any(
        math.isclose(seg.y1, 71.12, abs_tol=0.01)
        and math.isclose(seg.y2, 71.12, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 35.56, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 106.68, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 86.36, abs_tol=0.01)
        and math.isclose(seg.x2, 86.36, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 58.42, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 71.12, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_uses_compact_local_decoupling_lane_for_negative_rail_cluster() -> None:
    """A slightly taller VMINUS decoupling cluster should still use a local rail lane."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x03", value="PWR"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="C2", symbol="Device:C", value="100n"),
            ComponentIR(ref="C4", symbol="Device:C_Polarized", value="10u"),
        ],
        nets=[
            NetIR(
                name="VMINUS15",
                pins=[
                    PinRefIR(ref="J3", pin="3"),
                    PinRefIR(ref="U1P", pin="4"),
                    PinRefIR(ref="C2", pin="1"),
                    PinRefIR(ref="C4", pin="2"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J3", "3"): (52.07, 48.26, 0.0),
            ("U1P", "4"): (105.41, 60.96, 270.0),
            ("C2", "1"): (87.63, 110.49, 270.0),
            ("C4", "2"): (87.63, 110.49, 90.0),
        },
        positions={
            "J3": (57.15, 50.8, 0.0),
            "U1P": (102.87, 58.42, 0.0),
            "C2": (87.63, 106.68, 0.0),
            "C4": (87.63, 114.3, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_decoupling_cluster"
    power_symbol = routing.power_symbols[0]
    assert math.isclose(power_symbol.x, 115.57, abs_tol=0.01)
    assert math.isclose(power_symbol.y, 66.04, abs_tol=0.01)
    assert any(
        math.isclose(seg.y1, 66.04, abs_tol=0.01)
        and math.isclose(seg.y2, 66.04, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 46.99, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 115.57, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 77.47, abs_tol=0.01)
        and math.isclose(seg.x2, 77.47, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 66.04, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 115.57, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_uses_compact_local_ground_lane_for_decoupling_cap_bank() -> None:
    """A tiny cap-only GND bank should keep its GND symbol attached to the bank."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="C3", symbol="Device:C_Polarized", value="10u"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="C3", pin="2"),
                ],
            )
        ],
    )
    pin_endpoints = {
        ("C1", "2"): (76.2, 83.82, 270.0),
        ("C3", "2"): (63.5, 76.2, 270.0),
    }

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions={
            "C1": (76.2, 87.63, 0.0),
            "C3": (63.5, 80.01, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_ground_cluster"
    power_symbol = routing.power_symbols[0]
    assert power_symbol.net_name == "GND"
    nearest_cap_distance = min(
        math.hypot(power_symbol.x - x, power_symbol.y - y)
        for x, y, _angle in pin_endpoints.values()
    )
    assert nearest_cap_distance <= 20.0, (
        f"Local decoupling GND symbol should stay attached to the cap bank: {power_symbol}"
    )
    assert len(routing.junctions) >= 2


def test_route_nets_uses_compact_local_ground_lane_for_mixed_decoupling_support_cluster() -> None:
    """A local decoupling bank may share its ground lane with one nearby support member."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="C3", symbol="Device:C_Polarized", value="10u"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="U1P", pin="2"),
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="C3", pin="2"),
                ],
            )
        ],
    )
    pin_endpoints = {
        ("U1P", "2"): (105.41, 130.81, 270.0),
        ("C1", "2"): (106.68, 125.73, 270.0),
        ("C3", "2"): (106.68, 118.11, 270.0),
    }

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions={
            "U1P": (106.68, 134.62, 0.0),
            "C1": (106.68, 129.54, 0.0),
            "C3": (106.68, 121.92, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_ground_cluster"
    power_symbol = routing.power_symbols[0]
    cap_positions = [pin_endpoints[("C1", "2")], pin_endpoints[("C3", "2")]]
    nearest_cap_distance = min(
        math.hypot(power_symbol.x - x, power_symbol.y - y) for x, y, _angle in cap_positions
    )
    assert nearest_cap_distance <= 20.0, (
        "Mixed local decoupling support cluster should keep the GND symbol near the cap bank: "
        f"{power_symbol}"
    )
    assert len(routing.junctions) >= 3


def test_route_nets_uses_compact_local_ground_lane_for_two_support_decoupling_cluster() -> None:
    """A local decoupling bank may keep one calm GND lane with two nearby support members."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:NE5532", value="NE5532"),
            ComponentIR(ref="R5", symbol="Device:R", value="10k"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="C3", symbol="Device:C_Polarized", value="10u"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="U1P", pin="2"),
                    PinRefIR(ref="R5", pin="2"),
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="C3", pin="2"),
                ],
            )
        ],
    )
    pin_endpoints = {
        ("U1P", "2"): (105.41, 130.81, 270.0),
        ("R5", "2"): (114.30, 132.08, 270.0),
        ("C1", "2"): (106.68, 125.73, 270.0),
        ("C3", "2"): (106.68, 118.11, 270.0),
    }

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        positions={
            "U1P": (106.68, 134.62, 0.0),
            "R5": (114.30, 135.89, 0.0),
            "C1": (106.68, 129.54, 0.0),
            "C3": (106.68, 121.92, 0.0),
        },
    )

    assert len(routing.power_symbols) == 1
    assert routing.route_decisions[0].heuristic_override == "compact_local_ground_cluster"
    power_symbol = routing.power_symbols[0]
    cap_positions = [pin_endpoints[("C1", "2")], pin_endpoints[("C3", "2")]]
    nearest_cap_distance = min(
        math.hypot(power_symbol.x - x, power_symbol.y - y) for x, y, _angle in cap_positions
    )
    assert nearest_cap_distance <= 22.0, (
        "Two-support decoupling cluster should keep the GND symbol near the cap bank: "
        f"{power_symbol}"
    )
    assert len(routing.junctions) >= 4


def test_detect_body_crossings_preserves_pin_stub_touching_own_box() -> None:
    """Pin stubs that start on a symbol boundary must not be detoured."""
    stub = WireSegment(54.61, 106.68, 54.61, 101.60)

    result = detect_body_crossings([stub], {"C5": (54.61, 110.49, 0.0)})

    assert result == [stub]
    assert _point_in_or_on_box(stub.x1, stub.y1, 54.61, 110.49, SYMBOL_HALF_SIZE_MM)


def test_route_nets_treats_vplus_style_rails_as_power() -> None:
    """Custom VPLUS/VMINUS rails should use power-style routing semantics."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J3", symbol="Connector_Generic:Conn_01x02", value="PWR"),
            ComponentIR(ref="U1", symbol="Device:R", value="stub"),
        ],
        nets=[
            NetIR(
                name="VPLUS15",
                pins=[PinRefIR(ref="J3", pin="1"), PinRefIR(ref="U1", pin="1")],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J3", "1"): (0.0, 0.0, 180.0),
            ("U1", "1"): (30.0, 0.0, 0.0),
        },
    )

    assert routing.power_symbols
    assert not routing.labels
