from __future__ import annotations

import math

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    RoutingHeuristicPolicy,
    route_nets,
)


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
