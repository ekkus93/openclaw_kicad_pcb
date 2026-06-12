from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR

pytestmark = pytest.mark.unit


class TestApplyPostLayoutSnaps_Cohesion_Output:
    def test_output_stage_cohesion_keeps_buffer_stage_support_compact(self) -> None:
        """Phase 4.2.3: buffer-loop support should stay near the op-amp output side."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RBUF", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="RBUF", pin="1")],
                ),
                NetIR(
                    name="OUT_BUF",
                    pins=[PinRefIR(ref="RBUF", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_TERM",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RBUF", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "RBUF": (160.0, 70.0, None),
            "COUT": (228.0, 132.0, None),
            "JOUT": (250.0, 122.0, None),
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

        ux, uy, _ = result["U1"]
        rbuf_x, rbuf_y, _ = result["RBUF"]
        cout_x, cout_y, _ = result["COUT"]
        jout_x, jout_y, _ = result["JOUT"]

        assert ux < rbuf_x <= cout_x < jout_x, (
            f"Buffer-loop support should stay in the short output-side transition: {result}"
        )
        assert abs(rbuf_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM + 0.01, (
            "Buffer-loop support should remain vertically close to the op-amp"
        )
        assert max(cout_y, jout_y) - min(rbuf_y, cout_y, jout_y) <= 2.5 * _gv_mod.GRID_ROW_MM, (
            "Buffer-loop support should remain in a compact local y-band"
        )

    def test_post_layout_snaps_separate_interstage_and_buffer_subbands(self) -> None:
        """Phase 8.5: transition roles should read as ordered sub-bands after cohesion."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CINT", symbol="Device:C", value="47n"),
                ComponentIR(ref="RBUF", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="OUT_STAGE",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="CINT", pin="1")],
                ),
                NetIR(
                    name="HANDOFF",
                    pins=[PinRefIR(ref="CINT", pin="2"), PinRefIR(ref="RBUF", pin="1")],
                ),
                NetIR(
                    name="BUF_OUT",
                    pins=[PinRefIR(ref="RBUF", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_TERM",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CINT", BlockRole.INTERSTAGE)
        block_layout.add_assignment("RBUF", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "CINT": (245.0, 84.0, None),
            "RBUF": (168.0, 70.0, None),
            "COUT": (228.0, 132.0, None),
            "JOUT": (224.0, 122.0, None),
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

        ux, _uy, _ = result["U1"]
        cint_x, _cint_y, _ = result["CINT"]
        rbuf_x, _rbuf_y, _ = result["RBUF"]
        cout_x, _cout_y, _ = result["COUT"]
        jout_x, _jout_y, _ = result["JOUT"]

        assert ux < cint_x < rbuf_x < cout_x < jout_x, (
            f"Transition sub-bands should read core->interstage->buffer->output: {result}"
        )
