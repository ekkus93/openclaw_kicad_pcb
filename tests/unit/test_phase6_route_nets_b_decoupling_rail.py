"""Phase 6: decoupling lane routing, direct power symbols, label routing, and misc."""

from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    NetRouting,
    RoutingHeuristicPolicy,
    WireSegment,
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
