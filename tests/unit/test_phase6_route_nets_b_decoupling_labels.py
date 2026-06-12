from __future__ import annotations

import math
from collections import defaultdict

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    NetRouting,
    route_nets,
)


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
