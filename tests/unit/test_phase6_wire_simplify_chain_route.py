"""Phase 6 wire simplify chain tests — route_nets and simplification."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    SYMBOL_HALF_SIZE_MM,
    SharedLanePlan,
    WireSegment,
    _plan_local_ladder_routes,
    _wire_crosses_box,
    route_nets,
)


def _count_short_segments(wires: list[WireSegment], threshold_mm: float = 5.1) -> int:
    """Count short wire segments at or below *threshold_mm*."""
    return sum(1 for seg in wires if math.hypot(seg.x2 - seg.x1, seg.y2 - seg.y1) <= threshold_mm)


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
