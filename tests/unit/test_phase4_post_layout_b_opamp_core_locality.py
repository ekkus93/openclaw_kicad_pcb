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
    GRID_COL_MM,
    ComponentAnnotation,
)

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_HaloOpamp_Locality:
    def test_opamp_locality_keeps_halo_members_off_ic_column(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="R2", symbol="Device:R", value="47k"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R2", pin="1")],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.OUTPUT)

        positions = {
            "U1": (100.0, 100.0, None),
            "R2": (100.0, 115.0, None),
            "C6": (100.0, 125.0, None),
        }

        result = _snap_opamp_locality(
            positions,
            ir,
            annotations={"R2": ComponentAnnotation(feedback=True)},
            context=_OpAmpLocalityContext(
                decoupling_map={},
                halo={"R2": "U1", "C6": "U1"},
                block_layout=block_layout,
            ),
        )

        ux, _uy, _ = result["U1"]
        assert result["R2"][0] == pytest.approx(ux + GRID_COL_MM)
        assert result["C6"][0] == pytest.approx(ux + GRID_COL_MM)
        assert result["R2"][0] != pytest.approx(ux)
        assert result["C6"][0] != pytest.approx(ux)

    def test_opamp_locality_shapes_non_inverting_feedback_node(self) -> None:
        """Bridge and shunt feedback parts should read like a gain-setting pair."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="RG", symbol="Device:R", value="4.7k"),
            ],
            nets=[
                NetIR(
                    name="INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="RG", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="RFB", pin="2")],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="RG", pin="2")]),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("RG", BlockRole.FEEDBACK)

        positions = {
            "U1": (100.0, 100.0, None),
            "RFB": (132.0, 88.0, None),
            "RG": (140.0, 136.0, None),
        }

        result = _snap_opamp_locality(
            positions,
            ir,
            annotations={
                "RFB": ComponentAnnotation(feedback=True),
                "RG": ComponentAnnotation(feedback=True),
            },
            context=_OpAmpLocalityContext(decoupling_map={}, block_layout=block_layout),
        )

        ux, uy, _ = result["U1"]
        target_x = ux - GRID_COL_MM / 2.0
        assert result["RFB"][0] == pytest.approx(target_x)
        assert result["RG"][0] == pytest.approx(target_x)
        assert result["RFB"][1] == pytest.approx(uy)
        assert result["RG"][1] == pytest.approx(uy + _gv_mod.GRID_ROW_MM)

    def test_opamp_local_rules_input_output_feedback_decoupling(self) -> None:
        """Phase 4.1: op-amp neighborhood should stage local roles clearly."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]
                ),
                NetIR(
                    name="FB_B", pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="RFB", pin="2")]
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="ROUT", pin="1"),
                        PinRefIR(ref="JOUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="GND", pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        # Deliberately scrambled initial placement.
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (100.0, 100.0, None),
            "RIN": (120.0, 70.0, None),
            "ROUT": (80.0, 135.0, None),
            "RFB": (145.0, 70.0, None),
            "CDEC": (70.0, 150.0, None),
            "JIN": (60.0, 70.0, None),
            "JOUT": (150.0, 135.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={
                "U1": "mono",
                "RIN": "mono",
                "ROUT": "mono",
                "RFB": "mono",
                "CDEC": "mono",
                "JIN": "mono",
                "JOUT": "mono",
            },
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rin_x, _rin_y, _ = result["RIN"]
        rout_x, _rout_y, _ = result["ROUT"]
        rfb_x, rfb_y, _ = result["RFB"]
        cdec_x, cdec_y, _ = result["CDEC"]

        assert rin_x < ux, f"Input-side component RIN should be left of U1: {result}"
        assert rout_x > ux, f"Output-side component ROUT should be right of U1: {result}"
        assert math.isclose(rfb_x, ux, abs_tol=0.01), (
            f"Feedback component RFB should stay in U1 column: {result}"
        )
        assert cdec_y < uy, f"Decoupling CDEC should be above U1 (power side): {result}"
        assert math.isclose(cdec_x, ux, abs_tol=0.01), (
            f"Decoupling CDEC should align to U1 x-column: {result}"
        )
        assert not math.isclose(rfb_y, cdec_y, abs_tol=0.01), (
            "Feedback and decoupling parts should not occupy the same y-slot"
        )
