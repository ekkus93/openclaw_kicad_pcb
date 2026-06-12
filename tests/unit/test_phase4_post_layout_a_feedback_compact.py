"""Phase 4 post-layout snap: feedback node, opamp stage handoff, and fallback tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
)

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_Basic_Feedback:
    def test_feedback_node_stays_compact_after_late_stage2_locality_passes(self) -> None:
        """Phase 2.2.4: late U1B locality passes must not stretch the U1A feedback node."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1A", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_STAGE1",
                    pins=[
                        PinRefIR(ref="U1A", pin="1"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R3", pin="2")]),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1B", pin="5"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1B", pin="6"),
                        PinRefIR(ref="U1B", pin="7"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "R2": (150.0, 60.0, None),
            "R3": (152.0, 170.0, None),
            "C6": (134.62, 153.67, None),
            "R5": (134.62, 161.29, None),
            "U1B": (171.45, 146.05, None),
            "R6": (208.27, 168.91, None),
            "C7": (246.38, 205.74, None),
            "R7": (262.89, 220.98, None),
            "J2": (297.18, 205.74, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R2", "R3"},
            annotations={
                "R2": ComponentAnnotation(feedback=True),
                "R3": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1a_x, u1a_y, _ = result["U1A"]
        r2_x, r2_y, _ = result["R2"]
        r3_x, r3_y, _ = result["R3"]

        assert r2_x == pytest.approx(r3_x), (
            f"Feedback bridge and shunt should share one compact node column: {result}"
        )
        assert u1a_x - r2_x <= GRID_COL_MM, (
            f"Feedback node should stay close to U1A instead of stretching leftward: {result}"
        )
        assert r2_y == pytest.approx(u1a_y), (
            f"Feedback bridge should stay on U1A's stage row after late passes: {result}"
        )
        assert r3_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"Gain-to-ground shunt should stay one row below the inverting node: {result}"
        )

    def test_opamp_stage_shapes_non_inverting_input_handoff_as_bridge_plus_shunt_node(
        self,
    ) -> None:
        """Phase 2.2.1: the U1A non-inverting input should read as
        bridge-on-row plus shunt-below.
        """
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="VIN", symbol="Device:R", value="src"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
            ],
            nets=[
                NetIR(
                    name="IN_L_AC",
                    pins=[PinRefIR(ref="VIN", pin="1"), PinRefIR(ref="RV1", pin="1")],
                ),
                NetIR(
                    name="VOL_L_OUT",
                    pins=[
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="R4", pin="1"),
                        PinRefIR(ref="U1A", pin="3"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R4", pin="2"), PinRefIR(ref="R3", pin="2")]),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1A", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("VIN", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)

        positions: dict[str, tuple[float, float, float | None]] = {
            "VIN": (30.48, 91.44, None),
            "RV1": (52.0, 120.0, None),
            "R4": (60.0, 70.0, None),
            "U1A": (97.79, 91.44, None),
            "R2": (160.0, 60.0, None),
            "R3": (162.0, 160.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R2", "R3"},
            annotations={
                "R2": ComponentAnnotation(feedback=True),
                "R3": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        u1a_x, u1a_y, _ = result["U1A"]
        rv1_x, rv1_y, _ = result["RV1"]
        r4_x, r4_y, _ = result["R4"]

        assert rv1_x == pytest.approx(r4_x), (
            f"The non-inverting bridge and shunt should share one input-node column: {result}"
        )
        assert u1a_x - rv1_x == pytest.approx(GRID_COL_MM), (
            f"The non-inverting input node should sit one grid lane left of U1A: {result}"
        )
        assert rv1_y == pytest.approx(u1a_y), (
            f"The bridge into U1A should stay on the stage row: {result}"
        )
        assert r4_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The local shunt should hang one row below the non-inverting input node: {result}"
        )
