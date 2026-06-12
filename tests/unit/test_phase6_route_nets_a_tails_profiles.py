"""Phase 6 route nets: compact output tails, ground lane, and middle lane routing tests."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
from kicad_pcb.router import (
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
