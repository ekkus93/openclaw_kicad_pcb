from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_Basic_Chain_Connector:
    def test_buffer_stage_keeps_output_connector_on_tail_row_and_outermost_lane(self) -> None:
        """Phase 2.4.3: the output connector should stay attached to the final tail row."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
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
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (246.38, 205.74, None),
            "R7": (262.89, 220.98, None),
            "J2": (297.18, 205.74, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        assert result["J2"][1] == pytest.approx(result["C7"][1]), (
            f"Output connector should stay on the coupling-cap tail row: {result}"
        )
        assert result["J2"][1] == pytest.approx(result["R7"][1]), (
            "Output connector should stay attached to the final "
            f"resistor/capacitor tail row: {result}"
        )
        assert result["R7"][0] < result["J2"][0], (
            f"Output connector should remain the outermost element on the output-tail row: {result}"
        )

    def test_buffer_stage_clears_feedback_corridor_of_intrusive_tail_parts(self) -> None:
        """Phase 2.2.2: downstream output parts should not sit inside the U1B loop corridor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="33"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
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
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (190.50, 137.16, None),
            "R7": (198.12, 129.54, None),
            "J2": (236.22, 137.16, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        assert result["C7"][1] > result["R6"][1], (
            "An intrusive downstream cap should be pushed below the fixed buffer row instead of "
            f"sitting inside the U1B loop corridor: {result}"
        )
        assert result["R6"][0] < result["C7"][0] <= result["R7"][0] <= result["J2"][0], (
            "Once evacuated from the corridor, the downstream tail should resume the normal "
            f"right-side order: {result}"
        )
