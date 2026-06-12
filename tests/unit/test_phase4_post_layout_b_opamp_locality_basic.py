"""Phase 4: opamp locality — support roles, interstage, and overflow spread tests."""

from __future__ import annotations

import math

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import (
    _OpAmpLocalityContext,
    _snap_opamp_locality,
)
from kicad_pcb.layout import (
    ComponentAnnotation,
)

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_HaloOpamp_Locality:
    def test_opamp_local_rules_separate_support_roles(self) -> None:
        """Phase 4.2: support roles should form distinct local clusters."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="22p"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
                ComponentIR(ref="CDEC1", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC2", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="FB_B",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_C",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="CDEC1", pin="1"),
                        PinRefIR(ref="CDEC2", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="CDEC1", pin="2"),
                        PinRefIR(ref="CDEC2", pin="2"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("CDEC1", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC2", BlockRole.DECOUPLING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (110.0, 100.0, None),
            "CIN": (145.0, 70.0, None),
            "RIN": (150.0, 80.0, None),
            "RFB": (170.0, 65.0, None),
            "CFB": (170.0, 140.0, None),
            "ROUT": (70.0, 110.0, None),
            "COUT": (70.0, 120.0, None),
            "CDEC1": (80.0, 150.0, None),
            "CDEC2": (80.0, 160.0, None),
            "JIN": (55.0, 75.0, None),
            "JOUT": (190.0, 120.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={
                "RFB": ComponentAnnotation(feedback=True),
                "CFB": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC1": "U1", "CDEC2": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        input_refs = ["CIN", "RIN"]
        output_refs = ["ROUT", "COUT"]
        feedback_refs = ["RFB", "CFB"]
        dec_refs = ["CDEC1", "CDEC2"]

        assert all(result[r][0] < ux for r in input_refs), (
            f"Input support should be left of U1: {result}"
        )
        assert all(result[r][0] > ux for r in output_refs), (
            f"Output support should be right of U1: {result}"
        )
        assert all(math.isclose(result[r][0], ux, abs_tol=0.01) for r in feedback_refs), (
            f"Feedback support should stay in op-amp column: {result}"
        )
        assert all(math.isclose(result[r][0], ux, abs_tol=0.01) for r in dec_refs), (
            f"Decoupling support should align to op-amp column: {result}"
        )

        feedback_ys = [result[r][1] for r in feedback_refs]
        dec_ys = [result[r][1] for r in dec_refs]
        assert max(dec_ys) < min(feedback_ys), (
            "Decoupling cluster should sit above feedback cluster to avoid role mixing"
        )

        input_mean_y = sum(result[r][1] for r in input_refs) / len(input_refs)
        output_mean_y = sum(result[r][1] for r in output_refs) / len(output_refs)
        assert input_mean_y < output_mean_y, (
            f"Input and output support clusters should be vertically staged: {result}"
        )
        assert all(abs(result[r][1] - uy) <= 3.0 * _gv_mod.GRID_ROW_MM for r in feedback_refs), (
            "Feedback cluster should remain local to the op-amp body"
        )

    def test_opamp_locality_keeps_interstage_handoff_near_output_side(self) -> None:
        """Phase 4.2: interstage bridge parts should stay near the op-amp output side."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
                ComponentIR(ref="R5", symbol="Device:R", value="22k"),
                ComponentIR(ref="R6", symbol="Device:R", value="100"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
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
                    name="OUT_L_STAGE2_RAW",
                    pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="R6", pin="1")],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[PinRefIR(ref="C7", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.OUTPUT)
        block_layout.add_assignment("R5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R6", BlockRole.OUTPUT)
        block_layout.add_assignment("C7", BlockRole.OUTPUT)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions = {
            "U1": (100.0, 100.0, None),
            "R2": (100.0, 118.0, None),
            "C6": (98.0, 130.0, None),
            "R5": (86.0, 90.0, None),
            "R6": (101.0, 136.0, None),
            "C7": (102.0, 148.0, None),
            "J2": (103.0, 160.0, None),
        }

        result = _snap_opamp_locality(
            positions,
            ir,
            annotations={"R2": ComponentAnnotation(feedback=True)},
            context=_OpAmpLocalityContext(decoupling_map={}, block_layout=block_layout),
        )

        ux, uy, _ = result["U1"]
        assert result["R5"][0] > ux, (
            f"Interstage resistor R5 should stay on U1's output side: {result}"
        )
        assert abs(result["R5"][1] - uy) <= _gv_mod.GRID_ROW_MM, (
            f"Interstage resistor R5 should stay close to U1's centerline: {result}"
        )
        assert max(result[ref][1] for ref in ["C6", "R6"]) < min(
            result[ref][1] for ref in ["C7", "J2"]
        ), f"Nearest output support should stay above farther output-chain parts: {result}"
