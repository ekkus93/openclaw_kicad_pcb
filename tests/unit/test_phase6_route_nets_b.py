"""Phase 6: decoupling lane routing, direct power symbols, label routing, and misc."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    DEBUG_LABEL_POLICY,
    SYMBOL_HALF_SIZE_MM,
    NetRouting,
    RoutingHeuristicPolicy,
    WireSegment,
    _point_in_or_on_box,
    detect_body_crossings,
    route_nets,
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
        and math.isclose(min(seg.x1, seg.x2), 45.72, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 106.68, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 81.28, abs_tol=0.01)
        and math.isclose(seg.x2, 81.28, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 58.42, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 71.12, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.y1, 58.42, abs_tol=0.01)
        and math.isclose(seg.y2, 58.42, abs_tol=0.01)
        and math.isclose(min(seg.x1, seg.x2), 81.28, abs_tol=0.01)
        and math.isclose(max(seg.x1, seg.x2), 96.52, abs_tol=0.01)
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
        and math.isclose(max(seg.x1, seg.x2), 105.41, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 72.39, abs_tol=0.01)
        and math.isclose(seg.x2, 72.39, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 66.04, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 115.57, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 77.47, abs_tol=0.01)
        and math.isclose(seg.x2, 77.47, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 66.04, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 105.41, abs_tol=0.01)
        for seg in routing.wires
    )


def test_route_nets_uses_direct_symbols_for_aligned_two_pin_ground_cluster() -> None:
    """Aligned two-pin fallback GND clusters should use direct per-pin GND symbols."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C64", symbol="Device:C", value="10u"),
            ComponentIR(ref="U2", symbol="Interface_UART:MAX232", value="MAX232"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[PinRefIR(ref="C64", pin="2"), PinRefIR(ref="U2", pin="15")],
            )
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C64", "2"): (175.26, 113.03, 270.0),
            ("U2", "15"): (175.26, 147.32, 270.0),
        },
        positions={
            "C64": (175.26, 109.22, 0.0),
            "U2": (175.26, 116.84, 0.0),
        },
    )

    assert len(routing.power_symbols) == 2
    assert all(power_symbol.net_name == "GND" for power_symbol in routing.power_symbols)
    assert any(
        math.isclose(seg.x1, 175.26, abs_tol=0.01)
        and math.isclose(seg.x2, 175.26, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 113.03, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 118.11, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 175.26, abs_tol=0.01)
        and math.isclose(seg.x2, 175.26, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 147.32, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 152.40, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 175.26, abs_tol=0.01)
        and math.isclose(seg.x2, 175.26, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 118.11, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 124.46, abs_tol=0.01)
        for seg in routing.wires
    )
    assert any(
        math.isclose(seg.x1, 175.26, abs_tol=0.01)
        and math.isclose(seg.x2, 175.26, abs_tol=0.01)
        and math.isclose(min(seg.y1, seg.y2), 152.40, abs_tol=0.01)
        and math.isclose(max(seg.y1, seg.y2), 158.75, abs_tol=0.01)
        for seg in routing.wires
    )
    assert not any(
        math.isclose(seg.x1, 180.34, abs_tol=0.01) and math.isclose(seg.x2, 180.34, abs_tol=0.01)
        for seg in routing.wires
    )
    assert not any(math.isclose(jpt.x, 180.34, abs_tol=0.01) for jpt in routing.junctions)


def test_route_nets_uses_direct_symbols_when_ground_cluster_hits_foreign_endpoint() -> None:
    """Three-pin GND clusters should fall back when a shared lane crosses a foreign endpoint."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="C1", symbol="Device:C", value="10u"),
            ComponentIR(ref="C2", symbol="Device:C", value="10u"),
            ComponentIR(ref="C3", symbol="Device:C", value="10u"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp", value="AMP"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="C2", pin="2"),
                    PinRefIR(ref="C3", pin="2"),
                ],
            ),
            NetIR(
                name="SIG",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="U1", pin="1")],
            ),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("C1", "2"): (38.10, 114.30, 270.0),
            ("C2", "2"): (38.10, 134.62, 270.0),
            ("C3", "2"): (38.10, 149.86, 270.0),
            ("R1", "1"): (38.10, 129.54, 90.0),
            ("U1", "1"): (76.20, 129.54, 180.0),
        },
        positions={
            "C1": (38.10, 111.76, 0.0),
            "C2": (38.10, 132.08, 0.0),
            "C3": (38.10, 147.32, 0.0),
            "R1": (38.10, 124.46, 0.0),
            "U1": (83.82, 129.54, 0.0),
        },
        heuristic_policy=RoutingHeuristicPolicy(enable_compact_local_ground_clusters=False),
    )

    assert routing.power_symbols
    assert all(power_symbol.net_name == "GND" for power_symbol in routing.power_symbols)
    assert not any(
        math.isclose(seg.x1, 38.10, abs_tol=0.01)
        and math.isclose(seg.x2, 38.10, abs_tol=0.01)
        and min(seg.y1, seg.y2) < 129.54 < max(seg.y1, seg.y2)
        for seg in routing.wires
    )


def test_route_nets_direct_power_symbols_avoid_foreign_shared_stub_collisions() -> None:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U14", symbol="74xGxx:74AHC1G04", value="74AHC1G04"),
            ComponentIR(ref="C42", symbol="Device:C", value="100n"),
            ComponentIR(ref="R58", symbol="Device:R", value="47k"),
            ComponentIR(ref="U22", symbol="Power_Management:AP22913W6-7", value="AP22913W6-7"),
        ],
        nets=[
            NetIR(name="+3.3V", pins=[PinRefIR(ref="U14", pin="5"), PinRefIR(ref="U22", pin="4")]),
            NetIR(name="GND", pins=[PinRefIR(ref="C42", pin="2")]),
            NetIR(name="/+3.3V@SD", pins=[PinRefIR(ref="R58", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("U14", "5"): (91.44, 134.62, 90.0),
            ("C42", "2"): (91.44, 124.46, 90.0),
            ("R58", "1"): (109.22, 148.59, 90.0),
            ("U22", "4"): (114.30, 137.16, 0.0),
        },
        positions={
            "U14": (96.52, 144.78, 0.0),
            "C42": (91.44, 121.92, 0.0),
            "R58": (109.22, 144.78, 0.0),
            "U22": (121.92, 144.78, 0.0),
        },
    )

    mixed_power_components = _connected_power_symbol_sets(routing)
    assert not any({"PWR:+3.3V", "PWR:GND"} <= component for component in mixed_power_components)
    assert not any(
        {"PWR:+3.3V", "PWR:/+3.3V@SD"} <= component for component in mixed_power_components
    )


def test_route_nets_prefers_protected_chain_over_colliding_spine_for_three_pin_net() -> None:
    """Three-pin local routes should avoid a spine that would run through foreign stubs."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R0", symbol="Device:R", value="IN"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:R", value="ICPIN"),
            ComponentIR(ref="X1", symbol="Device:R", value="guard"),
            ComponentIR(ref="X2", symbol="Device:R", value="guard"),
            ComponentIR(ref="X3", symbol="Device:R", value="guard"),
        ],
        nets=[
            NetIR(
                name="/NET",
                pins=[
                    PinRefIR(ref="R0", pin="1"),
                    PinRefIR(ref="R1", pin="1"),
                    PinRefIR(ref="U1", pin="1"),
                ],
            ),
            NetIR(name="/GUARD_A", pins=[PinRefIR(ref="X1", pin="1")]),
            NetIR(name="/GUARD_B", pins=[PinRefIR(ref="X2", pin="1")]),
            NetIR(name="/GUARD_C", pins=[PinRefIR(ref="X3", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("R0", "1"): (7.62, 91.44, 0.0),
            ("R1", "1"): (45.72, 133.35, 270.0),
            ("U1", "1"): (106.68, 121.92, 180.0),
            ("X1", "1"): (76.20, 116.84, 0.0),
            ("X2", "1"): (7.62, 96.52, 0.0),
            ("X3", "1"): (45.72, 120.65, 90.0),
        },
        positions={
            "R0": (7.62, 91.44, 0.0),
            "R1": (45.72, 133.35, 0.0),
            "U1": (106.68, 121.92, 0.0),
            "X1": (76.20, 116.84, 0.0),
            "X2": (7.62, 96.52, 0.0),
            "X3": (45.72, 120.65, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "/NET")
    assert decision.strategy == "chain"
    assert decision.heuristic_override == "protected_stub_avoidance"
    assert not any(
        math.isclose(seg.y1, 116.84, abs_tol=0.01)
        and math.isclose(seg.y2, 116.84, abs_tol=0.01)
        and min(seg.x1, seg.x2) < 71.12 < max(seg.x1, seg.x2)
        for seg in routing.wires
    )


def test_route_nets_uses_local_labels_when_local_four_pin_net_hits_foreign_attachment() -> None:
    """Four-pin local routes should fall back to local labels when foreign attachments collide."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="A1", symbol="Device:R", value="A"),
            ComponentIR(ref="A2", symbol="Device:R", value="B"),
            ComponentIR(ref="A3", symbol="Device:R", value="C"),
            ComponentIR(ref="A4", symbol="Device:R", value="D"),
            ComponentIR(ref="G1", symbol="Device:R", value="guard"),
            ComponentIR(ref="G2", symbol="Device:R", value="guard"),
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
            NetIR(name="/GUARD_A", pins=[PinRefIR(ref="G1", pin="1")]),
            NetIR(name="/GUARD_B", pins=[PinRefIR(ref="G2", pin="1")]),
        ],
    )

    routing = route_nets(
        ir=ir,
        pin_endpoints={
            ("A1", "1"): (177.80, 83.82, 0.0),
            ("A2", "1"): (2.54, 146.05, 0.0),
            ("A3", "1"): (45.72, 124.46, 0.0),
            ("A4", "1"): (104.14, 121.92, 0.0),
            ("G1", "1"): (71.12, 119.38, 0.0),
            ("G2", "1"): (134.62, 119.38, 0.0),
        },
        positions={
            "A1": (177.80, 83.82, 0.0),
            "A2": (2.54, 146.05, 0.0),
            "A3": (45.72, 124.46, 0.0),
            "A4": (104.14, 121.92, 0.0),
            "G1": (71.12, 119.38, 0.0),
            "G2": (134.62, 119.38, 0.0),
        },
    )

    decision = next(decision for decision in routing.route_decisions if decision.net_name == "NET4")
    assert decision.strategy == "local_labels"
    assert decision.heuristic_override == "foreign_attachment_label_breakout"
    assert len([label for label in routing.labels if label.name == "NET4"]) == 4


def test_route_nets_uses_global_labels_when_multi_pin_stub_hits_foreign_attachment() -> None:
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
                name="/NET4",
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

    decision = next(
        decision for decision in routing.route_decisions if decision.net_name == "/NET4"
    )
    assert decision.strategy == "global_labels"
    assert decision.heuristic_override == "foreign_attachment_label_breakout"
    assert len([label for label in routing.global_labels if label.name == "/NET4"]) == 4


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
