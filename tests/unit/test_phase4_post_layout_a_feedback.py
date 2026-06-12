"""Phase 4 post-layout snap: feedback node, opamp stage handoff, and fallback tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, UserError
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

    def test_opamp_stage_keeps_upstream_input_bundle_in_one_left_column(self) -> None:
        """Phase 2.2.1: upstream bridge parts should feed U1A as a clean left bundle."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector:AudioJack3", value="IN"),
                ComponentIR(ref="C5", symbol="Device:C", value="1u"),
                ComponentIR(ref="R1", symbol="Device:R", value="100k"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="2.2k"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[
                        PinRefIR(ref="J1", pin="T"),
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
                        PinRefIR(ref="R4", pin="1"),
                        PinRefIR(ref="U1A", pin="3"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="J1", pin="S"),
                        PinRefIR(ref="RV1", pin="3"),
                        PinRefIR(ref="R4", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                ),
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
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (20.0, 150.0, None),
            "C5": (110.0, 60.0, None),
            "R1": (130.0, 150.0, None),
            "RV1": (140.0, 40.0, None),
            "R4": (60.0, 180.0, None),
            "U1A": (97.79, 121.92, None),
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
        c5_x, c5_y, _ = result["C5"]
        r1_x, r1_y, _ = result["R1"]
        rv1_x, rv1_y, _ = result["RV1"]
        _r4_x, r4_y, _ = result["R4"]
        r2_x, r2_y, _ = result["R2"]
        r3_x, r3_y, _ = result["R3"]

        assert c5_x == pytest.approx(r1_x), (
            f"The upstream bridge bundle should share one left-side column: {result}"
        )
        assert rv1_x - c5_x == pytest.approx(GRID_COL_MM), (
            "The non-inverting input node should sit one lane right of the "
            f"upstream bridge bundle: {result}"
        )
        assert r2_x - rv1_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"The feedback node should stay between the non-inverting node and U1A: {result}"
        )
        assert u1a_x - r2_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"U1A should complete a readable left-to-right gain-stage chain: {result}"
        )
        assert sorted([c5_y, r1_y]) == pytest.approx([u1a_y - _gv_mod.GRID_ROW_MM, u1a_y]), (
            f"The upstream bridge bundle should occupy one compact two-row column: {result}"
        )
        assert rv1_y == pytest.approx(u1a_y), (
            f"The non-inverting bridge should stay on the U1A row: {result}"
        )
        assert r4_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The grounded shunt should hang one row below the input node: {result}"
        )
        assert r2_y == pytest.approx(u1a_y), (
            f"The feedback bridge should stay on the U1A row: {result}"
        )
        assert r3_y == pytest.approx(u1a_y + _gv_mod.GRID_ROW_MM), (
            f"The gain-to-ground shunt should stay one row below the feedback node: {result}"
        )

    def test_feedback_falls_back_to_any_neighbor_non_strict(self) -> None:
        """Non-strict mode preserves fallback from IC/connector anchor to any neighbor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R_FB"},
            annotations=annotations,
            channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
            decoupling_map={},
        )
        anchor_y_after_grid = round(round(80.0 / 1.27) * 1.27, 2)
        expected_y = round(anchor_y_after_grid - _gv_mod.GRID_ROW_MM, 2)
        assert result["R_FB"][1] == pytest.approx(expected_y)

    def test_feedback_fallback_raises_in_strict_mode(self) -> None:
        """Strict mode raises when a feedback component has no IC/connector anchor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        with pytest.raises(UserError, match="no IC/connector anchor") as exc_info:
            _gv_mod.apply_post_layout_snaps(
                positions,
                ir,
                feedback_refs={"R_FB"},
                annotations=annotations,
                channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
                decoupling_map={},
                strict=True,
            )
        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
