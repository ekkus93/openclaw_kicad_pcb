"""Phase 6 route nets: label routing, ground lane, debug mode, and misc tests."""

from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    DEBUG_LABEL_POLICY,
    SYMBOL_HALF_SIZE_MM,
    WireSegment,
    _point_in_or_on_box,
    detect_body_crossings,
    route_nets,
)


def test_route_nets_uses_local_labels_when_multi_pin_stub_hits_foreign_attachment() -> None:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="A1", symbol="Device:R", value="A"),
            ComponentIR(ref="A2", symbol="Device:R", value="B"),
            ComponentIR(ref="A3", symbol="Device:R", value="C"),
            ComponentIR(ref="A4", symbol="Device:R", value="D"),
            ComponentIR(ref="X1", symbol="Device:R", value="guard"),
        ],
        nets=[
            NetIR(
                name="NET4",
                pins=[
                    PinRefIR(ref="A1", pin="1"),
                    PinRefIR(ref="A2", pin="1"),
                    PinRefIR(ref="A3", pin="1"),
                    PinRefIR(ref="A4", pin="1"),
                ],
            ),
            NetIR(name="/GUARD", pins=[PinRefIR(ref="X1", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("A1", "1"): (177.80, 83.82, 0.0),
            ("A2", "1"): (2.54, 146.05, 0.0),
            ("A3", "1"): (45.72, 124.46, 0.0),
            ("A4", "1"): (109.22, 121.92, 180.0),
            ("X1", "1"): (114.30, 121.92, 0.0),
        },
        positions={
            "A1": (177.80, 83.82, 0.0),
            "A2": (2.54, 146.05, 0.0),
            "A3": (45.72, 124.46, 0.0),
            "A4": (109.22, 121.92, 0.0),
            "X1": (114.30, 121.92, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "NET4")
    assert decision.strategy == "local_labels"
    assert decision.heuristic_override == "foreign_attachment_label_breakout"
    assert len([label for label in routing.labels if label.name == "NET4"]) == 4


def test_route_nets_uses_local_labels_when_multi_pin_stub_hits_foreign_stub() -> None:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="A1", symbol="Device:R", value="A"),
            ComponentIR(ref="A2", symbol="Device:R", value="B"),
            ComponentIR(ref="A3", symbol="Device:R", value="C"),
            ComponentIR(ref="A4", symbol="Device:R", value="D"),
            ComponentIR(ref="X1", symbol="Device:R", value="guard"),
        ],
        nets=[
            NetIR(
                name="NET4",
                pins=[
                    PinRefIR(ref="A1", pin="1"),
                    PinRefIR(ref="A2", pin="1"),
                    PinRefIR(ref="A3", pin="1"),
                    PinRefIR(ref="A4", pin="1"),
                ],
            ),
            NetIR(name="GUARD", pins=[PinRefIR(ref="X1", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("A1", "1"): (177.80, 83.82, 0.0),
            ("A2", "1"): (2.54, 146.05, 0.0),
            ("A3", "1"): (45.72, 124.46, 0.0),
            ("A4", "1"): (109.22, 121.92, 180.0),
            ("X1", "1"): (114.30, 116.84, 270.0),
        },
        positions={
            "A1": (177.80, 83.82, 0.0),
            "A2": (2.54, 146.05, 0.0),
            "A3": (45.72, 124.46, 0.0),
            "A4": (109.22, 121.92, 0.0),
            "X1": (114.30, 116.84, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "NET4")
    assert decision.strategy == "local_labels"
    assert decision.heuristic_override == "foreign_attachment_label_breakout"
    assert len([label for label in routing.labels if label.name == "NET4"]) == 4


def test_route_nets_uses_global_labels_for_wide_three_pin_connector_attachment_net() -> None:
    """Wide connector-attachment nets should use per-pin global labels instead of long wires."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:R", value="ICPIN"),
        ],
        nets=[
            NetIR(
                name="/BUS",
                pins=[
                    PinRefIR(ref="J1", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="1"),
                ],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (7.62, 91.44, 0.0),
            ("R1", "1"): (45.72, 133.35, 270.0),
            ("U1", "1"): (106.68, 121.92, 180.0),
        },
        positions={
            "J1": (7.62, 91.44, 0.0),
            "R1": (45.72, 133.35, 0.0),
            "U1": (106.68, 121.92, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/BUS")
    assert decision.strategy == "global_labels"
    assert decision.heuristic_override == "connector_label_breakout"
    assert len([label for label in routing.global_labels if label.name == "/BUS"]) == 3
    assert not [label for label in routing.labels if label.name == "/BUS"]
    assert routing.wires == []


def test_route_nets_direct_route_avoids_foreign_pin_endpoints() -> None:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="A1", symbol="Device:R", value="A"),
            ComponentIR(ref="A2", symbol="Device:R", value="B"),
            ComponentIR(ref="X1", symbol="Device:R", value="guard"),
        ],
        nets=[
            NetIR(name="NET", pins=[PinRefIR(ref="A1", pin="1"), PinRefIR(ref="A2", pin="1")]),
            NetIR(name="GUARD", pins=[PinRefIR(ref="X1", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("A1", "1"): (0.0, 0.0, 0.0),
            ("A2", "1"): (20.0, 0.0, 180.0),
            ("X1", "1"): (10.0, 0.0, 0.0),
        },
        positions={
            "A1": (0.0, 0.0, 0.0),
            "A2": (20.0, 0.0, 0.0),
            "X1": (10.0, 0.0, 0.0),
        },
    )

    assert not any(
        math.isclose(seg.y1, 0.0, abs_tol=0.01)
        and math.isclose(seg.y2, 0.0, abs_tol=0.01)
        and min(seg.x1, seg.x2) < 10.0 < max(seg.x1, seg.x2)
        for seg in routing.wires
    )


def test_route_nets_uses_global_labels_for_wide_two_pin_connector_attachment_net() -> None:
    """Wide two-pin connector-attachment nets should avoid long direct wires."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ],
        nets=[NetIR(name="/BUS", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")])],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("J1", "1"): (7.62, 91.44, 0.0),
            ("R1", "1"): (50.80, 133.35, 270.0),
        },
        positions={
            "J1": (7.62, 91.44, 0.0),
            "R1": (50.80, 133.35, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/BUS")
    assert decision.strategy == "global_labels"
    assert decision.heuristic_override == "connector_label_breakout"
    assert len([label for label in routing.global_labels if label.name == "/BUS"]) == 2
    assert routing.wires == []


def test_route_nets_uses_global_labels_when_two_pin_stub_hits_foreign_attachment() -> None:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1", symbol="Device:R", value="A"),
            ComponentIR(ref="U2", symbol="Device:R", value="B"),
            ComponentIR(ref="X1", symbol="Device:R", value="guard"),
        ],
        nets=[
            NetIR(name="/BUS", pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="U2", pin="1")]),
            NetIR(name="/GUARD", pins=[PinRefIR(ref="X1", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("U1", "1"): (109.22, 121.92, 180.0),
            ("U2", "1"): (130.81, 116.84, 0.0),
            ("X1", "1"): (114.30, 121.92, 0.0),
        },
        positions={
            "U1": (109.22, 121.92, 0.0),
            "U2": (130.81, 116.84, 0.0),
            "X1": (114.30, 121.92, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/BUS")
    assert decision.strategy == "global_labels"
    assert decision.heuristic_override == "foreign_attachment_label_breakout"
    assert len([label for label in routing.global_labels if label.name == "/BUS"]) == 2


def test_route_nets_uses_global_labels_for_scoped_single_pin_fallback_net() -> None:
    """Slash-prefixed fallback nets should use global labels instead of local labels."""
    ir = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="Device:R", value="10k")],
        nets=[NetIR(name="/BUS", pins=[PinRefIR(ref="R1", pin="1")])],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={("R1", "1"): (50.80, 133.35, 270.0)},
        positions={"R1": (50.80, 133.35, 0.0)},
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/BUS")
    assert decision.strategy == "global_labels"
    assert [label.name for label in routing.global_labels] == ["/BUS"]
    assert not routing.labels


def test_route_nets_debug_mode_uses_global_labels_for_scoped_two_pin_direct_net() -> None:
    """Debug-mode direct labels should stay global for slash-prefixed nets."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:R", value="ICPIN"),
        ],
        nets=[
            NetIR(
                name="/BUS",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="U1", pin="1")],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R1", "1"): (50.80, 133.35, 270.0),
            ("U1", "1"): (60.96, 133.35, 180.0),
        },
        positions={
            "R1": (50.80, 133.35, 0.0),
            "U1": (60.96, 133.35, 0.0),
        },
        policy=DEBUG_LABEL_POLICY,
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/BUS")
    assert decision.strategy == "direct"
    assert [label.name for label in routing.global_labels] == ["/BUS"]
    assert not routing.labels


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
