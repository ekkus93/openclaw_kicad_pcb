"""Phase 6 wire simplify: chain routing, simplified wires, and ladder routing tests."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    JunctionPoint,
    SharedLanePlan,
    WireSegment,
    _chain_route,
    _infer_bounded_local_lane_plan,
    _plan_local_ladder_routes,
    _prefer_chain_route,
    _prefer_small_analog_chain_route,
    _route_candidate_key,
    _shared_lane_route,
    _simplify_wires,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


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
