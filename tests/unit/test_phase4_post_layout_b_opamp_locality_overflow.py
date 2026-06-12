"""Phase 4: opamp locality — overflow spread and negative decoupling tests."""

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


class TestApplyPostLayoutSnaps_HaloOpamp_Locality_Overflow:
    """Overflow spread and negative decoupling placement tests."""

    def test_opamp_locality_spreads_overflow_feedback_and_decoupling_lanes(self) -> None:
        """Phase 6.2: large op-amp support stacks should not all share U1's x-column."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB1", symbol="Device:R", value="47k"),
                ComponentIR(ref="RFB2", symbol="Device:R", value="22k"),
                ComponentIR(ref="CFB1", symbol="Device:C", value="22p"),
                ComponentIR(ref="CDEC1", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC2", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC3", symbol="Device:C", value="10u"),
            ],
            nets=[
                NetIR(
                    name="FB_A",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB1", pin="1"),
                        PinRefIR(ref="CFB1", pin="1"),
                    ],
                ),
                NetIR(
                    name="FB_B",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RFB1", pin="2"),
                        PinRefIR(ref="RFB2", pin="1"),
                        PinRefIR(ref="CFB1", pin="2"),
                    ],
                ),
                NetIR(
                    name="FB_C",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="RFB2", pin="2")],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="CDEC1", pin="1"),
                        PinRefIR(ref="CDEC2", pin="1"),
                        PinRefIR(ref="CDEC3", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="CDEC1", pin="2"),
                        PinRefIR(ref="CDEC2", pin="2"),
                        PinRefIR(ref="CDEC3", pin="2"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB1", BlockRole.FEEDBACK)
        block_layout.add_assignment("RFB2", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB1", BlockRole.FEEDBACK)
        block_layout.add_assignment("CDEC1", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC2", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC3", BlockRole.DECOUPLING)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (110.0, 100.0, None),
            "RFB1": (150.0, 80.0, None),
            "RFB2": (150.0, 90.0, None),
            "CFB1": (150.0, 110.0, None),
            "CDEC1": (70.0, 140.0, None),
            "CDEC2": (70.0, 150.0, None),
            "CDEC3": (70.0, 160.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB1", "RFB2", "CFB1"},
            annotations={
                "RFB1": ComponentAnnotation(feedback=True),
                "RFB2": ComponentAnnotation(feedback=True),
                "CFB1": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC1": "U1", "CDEC2": "U1", "CDEC3": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        feedback_xs = {round(result[ref][0], 2) for ref in ("RFB1", "RFB2", "CFB1")}
        decoupling_xs = {round(result[ref][0], 2) for ref in ("CDEC1", "CDEC2", "CDEC3")}

        assert len(feedback_xs) >= 2, (
            f"Overflow feedback parts should use multiple x lanes: {result}"
        )
        assert len(decoupling_xs) >= 2, (
            f"Overflow decoupling parts should use multiple x lanes: {result}"
        )
        assert all(result[ref][1] > uy for ref in ("RFB1", "RFB2", "CFB1")), (
            "Feedback overflow should remain below the op-amp body"
        )
        assert all(result[ref][1] < uy for ref in ("CDEC1", "CDEC2", "CDEC3")), (
            "Decoupling overflow should remain above the op-amp body"
        )
        assert round(ux, 2) in feedback_xs, "Primary feedback lane should remain aligned to U1"
        assert round(ux, 2) in decoupling_xs, "Primary decoupling lane should remain aligned to U1"

    def test_opamp_locality_spreads_negative_decoupling_overflow_below_body(self) -> None:
        """Negative-rail overflow should use compact x lanes while staying below the op-amp."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC1", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC2", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC3", symbol="Device:C", value="10u"),
                ComponentIR(ref="CDEC4", symbol="Device:C", value="22u"),
            ],
            nets=[
                NetIR(
                    name="VMINUS15",
                    pins=[
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="CDEC1", pin="1"),
                        PinRefIR(ref="CDEC2", pin="1"),
                        PinRefIR(ref="CDEC3", pin="1"),
                        PinRefIR(ref="CDEC4", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="CDEC1", pin="2"),
                        PinRefIR(ref="CDEC2", pin="2"),
                        PinRefIR(ref="CDEC3", pin="2"),
                        PinRefIR(ref="CDEC4", pin="2"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CDEC1", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC2", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC3", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC4", BlockRole.DECOUPLING)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (110.0, 100.0, None),
            "CDEC1": (70.0, 80.0, None),
            "CDEC2": (72.0, 82.0, None),
            "CDEC3": (74.0, 84.0, None),
            "CDEC4": (76.0, 86.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={
                "CDEC1": "U1",
                "CDEC2": "U1",
                "CDEC3": "U1",
                "CDEC4": "U1",
            },
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        decoupling_xs = {round(result[ref][0], 2) for ref in ("CDEC1", "CDEC2", "CDEC3", "CDEC4")}
        expected_xs = {
            round(ux, 2),
            round(ux - GRID_COL_MM, 2),
            round(ux + GRID_COL_MM, 2),
        }

        assert decoupling_xs == expected_xs, (
            f"Negative overflow bank should use compact symmetric x lanes: {result}"
        )
        assert all(result[ref][1] > uy for ref in ("CDEC1", "CDEC2", "CDEC3", "CDEC4")), (
            "Negative decoupling overflow should remain below the op-amp body"
        )
