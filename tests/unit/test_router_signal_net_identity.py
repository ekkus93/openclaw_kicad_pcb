from __future__ import annotations

from kicad_pcb._router_route_signal_direct import _route_direct_net
from kicad_pcb._router_route_signal_hub import _route_hub_net
from kicad_pcb._router_types import (
    DEFAULT_ROUTING_HEURISTIC_POLICY,
    MINIMAL_LABEL_POLICY,
    NetRouting,
)
from kicad_pcb.circuit_ir import NetIR, PinRefIR


def test_minimal_direct_route_preserves_authored_signal_net_name() -> None:
    pins = [PinRefIR(ref="R1", pin="2"), PinRefIR(ref="R2", pin="1")]
    net = NetIR(name="VMID", pins=pins)
    known = [
        (pins[0], (10.0, 10.0, 0.0)),
        (pins[1], (30.0, 10.0, 180.0)),
    ]

    routing = NetRouting()
    handled = _route_direct_net(
        routing,
        net,
        known,
        use_bus=False,
        policy=MINIMAL_LABEL_POLICY,
        block_layout=None,
        tiers=None,
        net_classification="generic_signal",
        pins_count=2,
        net_refs=("R1", "R2"),
        protected_pin_points=set(),
        protected_stub_points=set(),
        shared_protected_stub_points=set(),
        foreign_attachment_points=set(),
        dynamic_protected_points=set(),
    )

    assert handled is True
    assert [label.name for label in routing.labels] == ["VMID"]


def test_minimal_hub_route_preserves_authored_signal_net_name() -> None:
    pins = [
        PinRefIR(ref="R1", pin="2"),
        PinRefIR(ref="R2", pin="1"),
        PinRefIR(ref="R3", pin="1"),
    ]
    net = NetIR(name="SUM_NODE", pins=pins)
    known = [
        (pins[0], (10.0, 10.0, 0.0)),
        (pins[1], (30.0, 10.0, 180.0)),
        (pins[2], (20.0, 25.0, 270.0)),
    ]

    routing = NetRouting()
    handled = _route_hub_net(
        routing,
        net,
        known,
        use_bus=False,
        policy=MINIMAL_LABEL_POLICY,
        heuristic_policy=DEFAULT_ROUTING_HEURISTIC_POLICY,
        block_layout=None,
        positions=None,
        ladder_routes={},
        net_classification="generic_signal",
        pins_count=3,
        net_refs=("R1", "R2", "R3"),
        protected_pin_points=set(),
        protected_stub_points=set(),
        shared_protected_stub_points=set(),
        foreign_attachment_points=set(),
        dynamic_protected_points=set(),
    )

    assert handled is True
    assert [label.name for label in routing.labels] == ["SUM_NODE"]
