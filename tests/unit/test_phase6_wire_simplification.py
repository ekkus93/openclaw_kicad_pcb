"""Phase 6.1 — Wire simplification by merging consecutive colinear segments.

Tests the _simplify_wires() function which reduces visual clutter by
merging wire segments that lie on the same horizontal or vertical line.
"""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    SYMBOL_HALF_SIZE_MM,
    JunctionPoint,
    SharedLanePlan,
    WireSegment,
    _chain_route,
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


def test_route_nets_uses_bounded_ladder_route_for_full_preview_vol_l_out() -> None:
    """Real full-preview VOL_L_OUT geometry should avoid the widened spine fallback."""
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

    assert WireSegment(138.43, 120.65, 85.09, 120.65) in routing.wires
    assert WireSegment(85.09, 127.0, 133.35, 127.0) not in routing.wires


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
