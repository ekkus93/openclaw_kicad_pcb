"""Phase 4: post-layout snap — multi-stage chain, buffer stage placement tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
)

pytestmark = pytest.mark.unit


def _multi_stage_unit_ir() -> CircuitIR:
    """Three-unit op-amp example with two signal stages and one power unit."""
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1P", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_STAGE",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "U1B", "pin": "5"}],
                },
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1B", "pin": "7"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1P", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1P", "pin": "4"}]},
            ],
        }
    )


class TestApplyPostLayoutSnaps_Basic_Chain:
    def test_multi_stage_opamp_chain_stays_on_readable_signal_band(self) -> None:
        """Phase 2.2: split op-amp stages should read as one horizontal analog chain."""
        ir = _multi_stage_unit_ir()
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 121.92, None),
            "U1A": (97.79, 91.44, None),
            "C6": (134.62, 153.67, None),
            "R5": (134.62, 161.29, None),
            "U1B": (171.45, 146.05, None),
            "R6": (208.27, 168.91, None),
            "J2": (245.11, 121.92, None),
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

        main_band_refs = ("U1A", "C6", "R5", "U1B")
        main_band_ys = [result[ref][1] for ref in main_band_refs]
        assert max(main_band_ys) - min(main_band_ys) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            f"Multi-stage op-amp chain should stay on one readable horizontal band: {result}"
        )
        assert result["U1A"][0] < result["C6"][0] <= result["R5"][0] <= result["U1B"][0], (
            f"Interstage handoff should stay between the two op-amp stages: {result}"
        )

    def test_buffer_stage_keeps_direct_output_support_on_short_main_row(self) -> None:
        """Phase 2.2: a unity-gain buffer should keep its direct output loop obvious."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="C6", pin="1")],
                ),
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
                    pins=[PinRefIR(ref="C7", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "C6": (113.03, 129.54, None),
            "R5": (120.65, 137.16, None),
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
            "C7": (208.27, 137.16, None),
            "J2": (245.11, 121.92, None),
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

        buffer_band_refs = ("C6", "R5", "U1B", "R6")
        buffer_band_ys = [result[ref][1] for ref in buffer_band_refs]

        assert max(buffer_band_ys) - min(buffer_band_ys) <= _gv_mod.GRID_ROW_MM + 1e-4, (
            f"Buffer handoff and direct output support should stay on one short row: {result}"
        )
        assert result["C6"][0] <= result["R5"][0] <= result["U1B"][0] < result["R6"][0], (
            f"Buffer row should read left-to-right handoff into the unity-gain stage: {result}"
        )
        assert result["R6"][0] - result["U1B"][0] <= 2.0 * GRID_COL_MM, (
            f"Direct buffer output support should stay close to U1B: {result}"
        )

    def test_buffer_stage_shapes_input_handoff_as_bridge_plus_shunt_node(self) -> None:
        """Phase 2.2.2: the buffer input should read as handoff-on-row plus shunt-below."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1A", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="C6", symbol="Device:C", value="22u"),
                ComponentIR(ref="R5", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1B", symbol="Amplifier:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="47"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="1"), PinRefIR(ref="C6", pin="1")],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1B", pin="5"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R5", pin="2")]),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1B", pin="6"),
                        PinRefIR(ref="U1B", pin="7"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1A": (97.79, 91.44, None),
            "C6": (105.41, 129.54, None),
            "R5": (117.00, 137.16, None),
            "U1B": (171.45, 144.78, None),
            "R6": (208.27, 175.26, None),
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

        u1b_x, u1b_y, _ = result["U1B"]
        c6_x, c6_y, _ = result["C6"]
        r5_x, r5_y, _ = result["R5"]

        assert c6_x == pytest.approx(r5_x), (
            f"Buffer handoff bridge and shunt should share one input-node column: {result}"
        )
        assert u1b_x - c6_x == pytest.approx(GRID_COL_MM / 2.0), (
            f"Buffer input node should sit midway between the handoff and U1B: {result}"
        )
        assert c6_y == pytest.approx(u1b_y), (
            f"Incoming buffer handoff should stay on the U1B stage row: {result}"
        )
        assert r5_y == pytest.approx(u1b_y + _gv_mod.GRID_ROW_MM), (
            f"Local shunt support should hang one row below the U1B input node: {result}"
        )

    def test_buffer_stage_keeps_output_tail_as_compact_right_side_chain(self) -> None:
        """Phase 2.2.3: the U1B output tail should stay compact below the buffer row."""
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

        tail_refs = ("C7", "R7", "J2")
        tail_ys = [result[ref][1] for ref in tail_refs]

        assert max(tail_ys) - min(tail_ys) <= _gv_mod.GRID_ROW_MM, (
            f"Output tail should read as one compact right-side chain: {result}"
        )
        assert min(tail_ys) > result["R6"][1], (
            "Output tail should stay below the fixed buffer row instead of "
            f"collapsing onto it: {result}"
        )
        assert result["R6"][0] < result["C7"][0] <= result["R7"][0] <= result["J2"][0], (
            f"Output tail should stay ordered to the right of the direct buffer support: {result}"
        )
        assert result["J2"][0] - result["R6"][0] <= 3.0 * GRID_COL_MM, (
            f"Output tail should stay local to U1B instead of stretching rightward: {result}"
        )

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
