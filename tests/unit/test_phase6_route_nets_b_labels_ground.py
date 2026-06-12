from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    SYMBOL_HALF_SIZE_MM,
    WireSegment,
    _point_in_or_on_box,
    detect_body_crossings,
    route_nets,
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
