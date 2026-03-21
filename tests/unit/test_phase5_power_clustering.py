"""Phase 5.1 tests — power pin clustering and reduce GND clutter."""

from __future__ import annotations

from collections.abc import Mapping

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import PowerSymbolPlacement, WireSegment, _cluster_power_pins, route_nets


def test_cluster_power_pins_single_cluster() -> None:
    """Pins within radius form a single cluster."""
    pins = [
        (PinRefIR(ref="R1", pin="2"), (50.0, 110.0, 270.0)),
        (PinRefIR(ref="R2", pin="2"), (60.0, 110.0, 270.0)),
        (PinRefIR(ref="R3", pin="2"), (70.0, 110.0, 270.0)),
    ]
    clusters = _cluster_power_pins(pins, radius=40.0)
    assert len(clusters) == 1, f"Expected 1 cluster; got {len(clusters)}"
    assert len(clusters[0]) == 3, f"Expected 3 pins in cluster; got {len(clusters[0])}"  # noqa: PLR2004


def test_cluster_power_pins_multiple_clusters() -> None:
    """Pins far apart form separate clusters."""
    pins = [
        (PinRefIR(ref="R1", pin="2"), (50.0, 110.0, 270.0)),
        (PinRefIR(ref="R2", pin="2"), (150.0, 110.0, 270.0)),  # 100mm away
    ]
    clusters = _cluster_power_pins(pins, radius=40.0)
    assert len(clusters) == 2, f"Expected 2 clusters; got {len(clusters)}"  # noqa: PLR2004
    assert len(clusters[0]) == 1
    assert len(clusters[1]) == 1


def test_cluster_power_pins_empty() -> None:
    """Empty pin list returns empty cluster list."""
    clusters = _cluster_power_pins([])
    assert clusters == []


def test_power_net_clustering_integration() -> None:
    """End-to-end: clustered GND pins produce fewer power symbols."""
    # 4 GND pins: 2 pairs within 30mm, pairs 100mm apart
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ComponentIR(ref="R3", symbol="Device:R", value="10k"),
            ComponentIR(ref="R4", symbol="Device:R", value="10k"),
        ],
        nets=[
            NetIR(name="SIG1", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")]),
            NetIR(name="SIG2", pins=[PinRefIR(ref="R3", pin="1"), PinRefIR(ref="R4", pin="1")]),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R2", pin="2"),
                    PinRefIR(ref="R3", pin="2"),
                    PinRefIR(ref="R4", pin="2"),
                ],
            ),
        ],
    )

    endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
        ("R1", "1"): (50.0, 100.0, 0.0),
        ("R1", "2"): (50.0, 110.0, 270.0),
        ("R2", "1"): (70.0, 100.0, 0.0),
        ("R2", "2"): (70.0, 110.0, 270.0),
        ("R3", "1"): (200.0, 100.0, 0.0),
        ("R3", "2"): (200.0, 110.0, 270.0),
        ("R4", "1"): (220.0, 100.0, 0.0),
        ("R4", "2"): (220.0, 110.0, 270.0),
    }

    routing = route_nets(ir=ir, pin_endpoints=endpoints)

    # Without clustering: 4 GND pins → 4 power symbols
    # With clustering (radius=40mm): 2 clusters → 2 power symbols
    assert len(routing.power_symbols) == 2, (  # noqa: PLR2004
        f"Expected 2 clustered power symbols (down from 4); got {len(routing.power_symbols)}"
    )

    # Verify all pins have bind markers (electrical connectivity preserved)
    gnd_bind_markers = [bm for bm in routing.bind_markers if bm.net_name == "GND"]
    assert len(gnd_bind_markers) == 4, (  # noqa: PLR2004
        f"Expected 4 bind markers for GND pins; got {len(gnd_bind_markers)}"
    )

    # Verify power symbols are for GND net
    assert all(ps.net_name == "GND" for ps in routing.power_symbols)
    assert all(ps.angle == 0 for ps in routing.power_symbols)


def test_power_net_single_pin_no_clustering() -> None:
    """Single power pin gets traditional stub + symbol (no clustering overhead)."""
    ir = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="Device:R", value="10k")],
        nets=[NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2")])],
    )
    endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
        ("R1", "2"): (50.0, 110.0, 270.0),
    }
    routing = route_nets(ir=ir, pin_endpoints=endpoints)

    assert len(routing.power_symbols) == 1
    assert routing.power_symbols[0].angle == 0
    assert len(routing.bind_markers) == 1
    # Single pin cluster should not create junctions
    assert len(routing.junctions) == 0


def test_power_net_single_pin_offsets_symbol_beyond_stub() -> None:
    """Single-pin power symbols should move outward so the visible text clears the part."""
    ir = CircuitIR(
        version="1",
        components=[ComponentIR(ref="R1", symbol="Device:R", value="10k")],
        nets=[NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2")])],
    )
    endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
        ("R1", "2"): (50.0, 110.0, 270.0),
    }

    routing = route_nets(ir=ir, pin_endpoints=endpoints)

    assert routing.power_symbols == [
        PowerSymbolPlacement(net_name="GND", x=50.0, y=121.92, angle=0)
    ]
    assert WireSegment(50.0, 110.0, 50.0, 121.92) in routing.wires


def test_compact_ground_cluster_keeps_bind_markers() -> None:
    """Compact local GND clusters must still emit bind markers for every pin."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1", symbol="Device:R", value="10k"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="R2", symbol="Device:R", value="10k"),
        ],
        nets=[
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="U1", pin="4"),
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R2", pin="2"),
                ],
            )
        ],
    )
    endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
        ("U1", "4"): (100.0, 100.0, 270.0),
        ("R1", "2"): (110.0, 100.0, 270.0),
        ("R2", "2"): (120.0, 100.0, 270.0),
    }
    positions: Mapping[str, tuple[float, float, float | None]] = {
        "U1": (100.0, 94.92, 0.0),
        "R1": (110.0, 94.92, 0.0),
        "R2": (120.0, 94.92, 0.0),
    }

    routing = route_nets(ir=ir, pin_endpoints=endpoints, positions=positions)

    assert len(routing.power_symbols) == 1
    assert routing.power_symbols[0].net_name == "GND"

    gnd_bind_markers = {
        (marker.ref, marker.pin, marker.net_name)
        for marker in routing.bind_markers
        if marker.net_name == "GND"
    }
    assert gnd_bind_markers == {
        ("U1", "4", "GND"),
        ("R1", "2", "GND"),
        ("R2", "2", "GND"),
    }


def test_power_net_cluster_offsets_shared_symbol_toward_open_side() -> None:
    """Clustered power labels should be offset away from the local hub instead of sitting on it."""
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="U1", symbol="Device:R", value="10k"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="NEG"),
        ],
        nets=[
            NetIR(
                name="VMINUS15",
                pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J1", pin="1")],
            )
        ],
    )
    endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
        ("U1", "1"): (115.57, 67.31, 180.0),
        ("J1", "1"): (120.65, 67.31, 180.0),
    }

    routing = route_nets(ir=ir, pin_endpoints=endpoints)

    assert len(routing.power_symbols) == 1
    power_symbol = routing.power_symbols[0]
    assert power_symbol.angle == 270
    assert power_symbol == PowerSymbolPlacement(net_name="VMINUS15", x=118.11, y=60.96, angle=270)
    assert WireSegment(118.11, 67.31, 118.11, 60.96) in routing.wires
