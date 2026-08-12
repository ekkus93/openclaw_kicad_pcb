"""Regression coverage for KiCad identity labels on direct signal routes."""

from __future__ import annotations

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import DEBUG_LABEL_POLICY, MINIMAL_LABEL_POLICY, route_nets

pytestmark = pytest.mark.unit


def _direct_vmid_fixture() -> tuple[
    CircuitIR,
    dict[tuple[str, str], tuple[float, float, float]],
]:
    ir = CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
            ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
        ],
        nets=[
            NetIR(
                name="VMID",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="R2", pin="1"),
                ],
            )
        ],
    )
    pin_endpoints = {
        ("R1", "2"): (0.0, 0.0, 180.0),
        ("R2", "1"): (20.0, 0.0, 0.0),
    }
    return ir, pin_endpoints


def test_minimal_direct_route_keeps_one_kicad_net_identity_label() -> None:
    """A physical direct VMID wire must retain its authoritative name in KiCad."""
    ir, pin_endpoints = _direct_vmid_fixture()

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        policy=MINIMAL_LABEL_POLICY,
    )

    assert [decision.strategy for decision in routing.route_decisions] == ["direct"]
    assert len(routing.wires) >= 1
    assert [label.name for label in routing.labels] == ["VMID"]
    assert routing.global_labels == []


def test_debug_direct_route_does_not_duplicate_identity_label() -> None:
    """The existing debug presentation label also satisfies net identity."""
    ir, pin_endpoints = _direct_vmid_fixture()

    routing = route_nets(
        ir=ir,
        pin_endpoints=pin_endpoints,
        policy=DEBUG_LABEL_POLICY,
    )

    assert [decision.strategy for decision in routing.route_decisions] == ["direct"]
    assert [label.name for label in routing.labels] == ["VMID"]
    assert routing.global_labels == []
