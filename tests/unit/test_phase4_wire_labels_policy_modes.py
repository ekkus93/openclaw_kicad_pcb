"""Phase 4: label priority, label modes, and power symbol tests."""

from __future__ import annotations

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.router import (
    LABEL_MODE_POLICIES,
    LabelPolicy,
    route_nets,
)

pytestmark = pytest.mark.unit


def _label(name: str, x: float = 10.0, y: float = 10.0) -> str:
    return f'(label "{name}" (at {x} {y} 0))'


def _make_ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Minimal CircuitIR factory.

    *components* is ``[(ref, symbol), ...]``.
    *nets* is ``[(net_name, [(ref, pin), ...]), ...]``.
    """
    ir_components = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


class TestStructuralLabelPriority:
    def test_structural_roles_prioritize_visible_local_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R4", BlockRole.OUTPUT)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
        )

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {30.48, 39.37}

    def test_structural_roles_prioritize_visible_global_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("N_BUS", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints = {(f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)}
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.OUTPUT)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.DECOUPLING)
        block_layout.add_assignment("R8", BlockRole.POWER_ENTRY)
        policy = LabelPolicy(max_global_labels_per_net=2)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=policy,
        )

        visible_x = {round(label.x, 2) for label in routing.global_labels if label.name == "N_BUS"}
        assert visible_x == {39.37, 49.53}

    def test_label_caps_preserve_existing_order_without_structural_context(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {10.16, 20.32}


class TestLabelModes:
    def test_debug_mode_expands_label_fallback_caps(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            policy=LABEL_MODE_POLICIES["debug"],
        )

        assert len(routing.labels) == 5

    def test_important_mode_promotes_one_direct_signal_chain_label(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert len(routing.labels) == 1
        label = routing.labels[0]
        assert (label.name, round(label.x, 2), round(label.y, 2), label.angle) == (
            "LEFT_IN",
            24.92,
            100.0,
            180,
        )

    def test_minimal_mode_keeps_direct_signal_chain_net_label_free(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["minimal"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert routing.labels == []

    def test_important_mode_still_promotes_label_on_multi_pin_stage_seam(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("C5", "Device:C"),
                ("R1", "Device:R"),
            ],
            [("IN_L_AC", [("J1", "1"), ("C5", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (20.0, 100.0, 0.0),
            ("C5", "1"): (60.0, 90.0, 180.0),
            ("R1", "1"): (60.0, 110.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy != "direct"
        assert [label.name for label in routing.labels] == ["IN_L_AC"]

    def test_important_mode_promotes_only_explicit_stage_seam_nets(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="C5", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="22k"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
                ComponentIR(ref="R5", symbol="Device:R", value="22k"),
                ComponentIR(ref="R6", symbol="Device:R", value="100"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="10k"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[
                        PinRefIR(ref="J1", pin="1"),
                        PinRefIR(ref="C5", pin="1"),
                        PinRefIR(ref="R1", pin="1"),
                    ],
                ),
                NetIR(
                    name="IN_L_AC",
                    pins=[
                        PinRefIR(ref="C5", pin="2"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="RV1", pin="1"),
                    ],
                ),
                NetIR(
                    name="VOL_L_OUT",
                    pins=[
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="U1", pin="3"),
                        PinRefIR(ref="R4", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE1",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1", pin="5"),
                    ],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (20.0, 80.0, 0.0),
            ("C5", "1"): (40.0, 72.0, 180.0),
            ("R1", "1"): (40.0, 88.0, 180.0),
            ("C5", "2"): (60.0, 72.0, 0.0),
            ("R1", "2"): (60.0, 88.0, 0.0),
            ("RV1", "1"): (80.0, 80.0, 180.0),
            ("RV1", "2"): (100.0, 80.0, 0.0),
            ("U1", "3"): (120.0, 72.0, 180.0),
            ("R4", "1"): (120.0, 88.0, 180.0),
            ("U1", "1"): (140.0, 80.0, 0.0),
            ("R2", "1"): (160.0, 72.0, 180.0),
            ("C6", "1"): (160.0, 88.0, 180.0),
            ("C6", "2"): (180.0, 72.0, 0.0),
            ("R5", "1"): (180.0, 88.0, 0.0),
            ("U1", "5"): (200.0, 80.0, 180.0),
            ("C7", "2"): (220.0, 72.0, 0.0),
            ("R7", "1"): (220.0, 88.0, 0.0),
            ("J2", "1"): (240.0, 80.0, 180.0),
            ("U1", "7"): (260.0, 72.0, 0.0),
            ("U1", "6"): (260.0, 88.0, 0.0),
            ("R6", "1"): (280.0, 80.0, 180.0),
            ("R6", "2"): (300.0, 72.0, 0.0),
            ("C7", "1"): (300.0, 88.0, 180.0),
            ("U1", "2"): (320.0, 80.0, 0.0),
            ("R2", "2"): (340.0, 72.0, 180.0),
            ("R3", "1"): (340.0, 88.0, 180.0),
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        label_names = {label.name for label in routing.labels}

        assert {
            "LEFT_IN",
            "IN_L_AC",
            "VOL_L_OUT",
            "OUT_L_STAGE1",
            "BUF_L_IN",
            "HP_L_OUT",
        } <= label_names
        assert {"OUT_L_STAGE2_RAW", "AFTER_R6", "U1A_INV"}.isdisjoint(label_names)
