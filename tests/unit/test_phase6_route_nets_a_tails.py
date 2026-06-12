"""Phase 6 route nets: compact output tails, ground lane, and middle lane routing tests."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.router import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    RoutingHeuristicPolicy,
    WireSegment,
    route_nets,
)


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
    assert WireSegment(50.0, 14.92, 70.0, 14.92) in routing.wires
    assert WireSegment(70.0, 20.0, 70.0, 14.92) in routing.wires


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

    assert routing.route_decisions[1].strategy == "compact_signal_tail"
    assert routing.route_decisions[1].heuristic_override == "compact_output_tail"
    assert routing.junctions == []
    assert WireSegment(213.36, 123.19, 213.36, 165.10) not in routing.wires
    assert any(
        math.isclose(seg.y1, 185.42, abs_tol=0.01)
        and math.isclose(seg.y2, 185.42, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 212.09, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 238.76, abs_tol=0.01)
        for seg in routing.wires
    )
    assert WireSegment(238.76, 165.10, 238.76, 171.45) not in routing.wires
    assert any(
        math.isclose(seg.x1, seg.x2, abs_tol=0.01)
        and math.isclose(seg.x1, 238.76, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 171.45, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 185.42, abs_tol=0.01)
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


def test_named_routing_profiles_diverge_on_output_tail_with_command_pin_geometry() -> None:
    """Command-derived pin geometry should still keep the analog compact tail route."""
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
                    PinRefIR(ref="J2", pin="1"),
                ],
            ),
        ],
    )
    pin_endpoints = {
        ("C7", "1"): (213.36, 133.35, 270.0),
        ("C7", "2"): (213.36, 138.43, 90.0),
        ("J2", "1"): (217.17, 165.10, 0.0),
        ("J2", "2"): (217.17, 167.64, 0.0),
        ("J2", "3"): (217.17, 170.18, 0.0),
        ("R6", "1"): (213.36, 179.07, 270.0),
        ("R6", "2"): (213.36, 184.15, 90.0),
        ("R7", "1"): (238.76, 166.37, 270.0),
        ("R7", "2"): (238.76, 171.45, 90.0),
    }
    positions = {
        "R6": (213.36, 179.07, 270.0),
        "C7": (213.36, 133.35, 270.0),
        "R7": (238.76, 166.37, 270.0),
        "J2": (217.17, 165.10, 0.0),
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

    assert analog_choice.strategy == "compact_signal_tail"
    assert analog_choice.heuristic_override == "compact_output_tail"
    assert digital_choice.strategy == "shared_lane"
    assert digital_choice.heuristic_override is None


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
        and math.isclose(max(seg.x1, seg.x2), 238.76, abs_tol=0.01)
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
