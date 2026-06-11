"""Phase 4: snap — input and output stage cohesion tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
    compute_orientations,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.tier import (
    assign_tiers,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


def _minimal_ir(
    *,
    refs: list[str] | None = None,
    nets: list[dict] | None = None,
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *refs* defaults to ["R1", "R2"].
    *nets* is a list of dicts with keys ``name`` and ``pins`` (list of
    ``{"ref": ..., "pin": ...}`` dicts).
    """
    if refs is None:
        refs = ["R1", "R2"]
    if nets is None:
        nets = [
            {"name": "NET1", "pins": [{"ref": refs[0], "pin": "1"}, {"ref": refs[1], "pin": "1"}]}
        ]

    components = [ComponentIR(ref=r, symbol="Device:R", value="1k") for r in refs]
    ir_nets = [NetIR(name=n["name"], pins=[PinRefIR(**p) for p in n["pins"]]) for n in nets]
    return CircuitIR(version="1", components=components, nets=ir_nets)


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body*."""
    return parse(
        "(kicad_sch (version 20230121) (generator test)\n"
        "  (lib_symbols)\n"
        f"  {body}\n"
        '  (sheet_instances (path "/" (page "1")))\n'
        ")"
    )


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return an S-expression wire snippet."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


def _symbol_at(x: float, y: float) -> str:
    """Return a minimal symbol snippet at (x, y)."""
    return (
        f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "00000000-0000-0000-0000-000000000001"))'
    )


def _label(name: str, x: float = 10.0, y: float = 10.0) -> str:
    return f'(label "{name}" (at {x} {y} 0))'


def _codes(issues: list) -> list[str]:
    return [i.code for i in issues]


def _sev(issues: list, code: str) -> LintSeverity | None:
    for i in issues:
        if i.code == code:
            return i.severity
    return None


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestApplyPostLayoutSnaps_Cohesion:
    def test_input_stage_cohesion_left_to_right_transition(self) -> None:
        """Phase 7.1: input stage should read connector -> preconditioning -> op-amp."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
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
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 70.0, None),
            "CIN": (175.0, 110.0, None),
            "RIN": (180.0, 125.0, None),
            "U1": (190.0, 100.0, None),
            "ROUT": (120.0, 140.0, None),
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

        jx, jy, _ = result["JIN"]
        cin_x, cin_y, _ = result["CIN"]
        rin_x, rin_y, _ = result["RIN"]
        ux, uy, _ = result["U1"]

        assert jx < cin_x < ux, f"Input flow should be left-to-right: {result}"
        assert jx < rin_x < ux, f"Input flow should be left-to-right: {result}"
        assert abs(cin_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Input preconditioning should stay vertically close to op-amp input side"
        )
        assert abs(rin_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Input preconditioning should stay vertically close to op-amp input side"
        )
        assert ux - jx <= 3.5 * GRID_COL_MM, (
            "Input-to-op-amp transition should remain short and readable"
        )

    def test_input_stage_cohesion_avoids_unrelated_role_mixing(self) -> None:
        """Phase 7.1: unrelated support parts should not occupy the input lane."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]),
                NetIR(name="IN2", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (165.0, 100.0, None),
            "RIN": (175.0, 105.0, None),
            "U1": (190.0, 100.0, None),
            "CDEC": (150.0, 102.0, None),
            "ROUT": (130.0, 103.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        jx, _jy, _ = result["JIN"]
        rin_x, _rin_y, _ = result["RIN"]
        ux, uy, _ = result["U1"]
        cdec_x, cdec_y, _ = result["CDEC"]
        rout_x, _rout_y, _ = result["ROUT"]

        assert jx < rin_x < ux, f"Input lane should remain ordered: {result}"
        assert cdec_x >= rin_x, "Decoupling support should not intrude into the input lane"
        assert cdec_y < uy, "Decoupling support should stay on power-side (above op-amp)"
        assert rout_x >= rin_x, "Output support should not intrude into the input lane"

    def test_input_stage_cohesion_spreads_longer_preconditioning_chain_into_inner_lane(
        self,
    ) -> None:
        """Longer input chains should keep op-amp-side conditioning inward."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RBIAS", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ],
            nets=[
                NetIR(
                    name="IN_A",
                    pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")],
                ),
                NetIR(
                    name="IN_B",
                    pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")],
                ),
                NetIR(
                    name="IN_C",
                    pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="RBIAS", pin="1")],
                ),
                NetIR(
                    name="IN_D",
                    pins=[PinRefIR(ref="RBIAS", pin="2"), PinRefIR(ref="U1", pin="3")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RBIAS", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 100.0, None),
            "CIN": (178.0, 84.0, None),
            "RIN": (182.0, 96.0, None),
            "RBIAS": (186.0, 108.0, None),
            "U1": (190.0, 100.0, None),
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

        jx, _jy, _ = result["JIN"]
        cin_x, _cin_y, _ = result["CIN"]
        rin_x, _rin_y, _ = result["RIN"]
        rbias_x, _rbias_y, _ = result["RBIAS"]
        ux, _uy, _ = result["U1"]

        assert jx < cin_x < ux, (
            f"Connector-side conditioning should stay between JIN and U1: {result}"
        )
        assert cin_x < rin_x, (
            f"Longer input chain should use an inner preconditioning lane: {result}"
        )
        assert cin_x < rbias_x, (
            f"Op-amp-side conditioning should remain inside connector-side conditioning: {result}"
        )
        assert max(rin_x, rbias_x) < ux, f"Preconditioning should stay left of the op-amp: {result}"

    def test_output_stage_cohesion_left_to_right_transition(self) -> None:
        """Phase 7.2: output stage should read op-amp -> feedback -> connector."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="10k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="10n"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="FB",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="CFB", pin="2"),
                        PinRefIR(ref="COUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="JOUT_NET",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "RFB": (170.0, 95.0, None),
            "CFB": (175.0, 105.0, None),
            "COUT": (210.0, 85.0, None),
            "JOUT": (215.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rfb_x, rfb_y, _ = result["RFB"]
        _cfb_x, cfb_y, _ = result["CFB"]
        cout_x, cout_y, _ = result["COUT"]
        jout_x, jout_y, _ = result["JOUT"]

        assert ux < cout_x < jout_x, f"Output flow should be left-to-right: {result}"
        assert rfb_x <= cout_x, "Feedback support should not overtake output stage terminal lane"
        assert abs(rfb_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM + 0.01, (
            "Output feedback should stay vertically close to op-amp output side"
        )
        assert abs(cfb_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Output feedback should stay vertically close to op-amp output side"
        )
        assert jout_x - ux <= 3.5 * GRID_COL_MM, (
            "Op-amp-to-output transition should remain short and readable"
        )

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

    def test_output_stage_cohesion_avoids_unrelated_role_mixing(self) -> None:
        """Phase 7.2: unrelated support parts should not occupy the output lane."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="10k"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="FB",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="2")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")],
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (160.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (200.0, 105.0, None),
            "CDEC": (205.0, 85.0, None),
            "JOUT": (212.0, 103.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rfb_x, rfb_y, _ = result["RFB"]
        cdec_x, cdec_y, _ = result["CDEC"]
        jout_x, _jout_y, _ = result["JOUT"]
        jin_x, _jin_y, _ = result["JIN"]

        assert ux < jout_x, f"Output lane should remain ordered: {result}"
        assert rfb_x <= jout_x, "Feedback support should stay before output connector lane"
        assert jin_x <= rfb_x, "Input support should not intrude into the output lane"
        assert cdec_x <= rfb_x, "Decoupling support should not intrude into the output lane"
        assert cdec_y < uy, "Decoupling support should stay on power-side (above op-amp)"

    def test_output_stage_cohesion_without_ic_aligns_connectors_to_support(self) -> None:
        """Phase 7.2: output-only stages still align connectors with support parts."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JOUTL", symbol="Connector_Generic:Conn_01x01", value="OutL"),
                ComponentIR(ref="JOUTR", symbol="Connector_Generic:Conn_01x01", value="OutR"),
                ComponentIR(ref="ROUTL", symbol="Device:R", value="100"),
                ComponentIR(ref="ROUTR", symbol="Device:R", value="100"),
            ],
            nets=[
                NetIR(
                    name="OUT_L",
                    pins=[PinRefIR(ref="ROUTL", pin="1"), PinRefIR(ref="JOUTL", pin="1")],
                ),
                NetIR(
                    name="OUT_R",
                    pins=[PinRefIR(ref="ROUTR", pin="1"), PinRefIR(ref="JOUTR", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JOUTL", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUTR", BlockRole.OUTPUT)
        block_layout.add_assignment("ROUTL", BlockRole.OUTPUT)
        block_layout.add_assignment("ROUTR", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JOUTL": (220.0, 100.0, None),
            "JOUTR": (220.0, 110.0, None),
            "ROUTL": (205.0, 128.0, None),
            "ROUTR": (205.0, 140.0, None),
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

        assert result["JOUTL"][1] >= result["ROUTL"][1] - 0.01
        assert result["JOUTR"][1] >= result["ROUTR"][1] - 0.01
        assert result["JOUTL"][0] >= positions["JOUTL"][0] - 1.0
        assert result["JOUTR"][0] >= positions["JOUTR"][0] - 1.0

    def test_output_stage_cohesion_spreads_longer_output_chain_into_inner_lane(self) -> None:
        """Longer output chains should keep op-amp-side support inside connector-side support."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RISO", symbol="Device:R", value="47"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100u"),
                ComponentIR(ref="RLOAD", symbol="Device:R", value="100k"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="RISO", pin="1")],
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_C",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="RLOAD", pin="1")],
                ),
                NetIR(
                    name="OUT_D",
                    pins=[PinRefIR(ref="RLOAD", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RISO", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("RLOAD", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "RISO": (195.0, 88.0, None),
            "COUT": (205.0, 96.0, None),
            "RLOAD": (208.0, 104.0, None),
            "JOUT": (216.0, 100.0, None),
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
        riso_x, _riso_y, _ = result["RISO"]
        cout_x, _cout_y, _ = result["COUT"]
        rload_x, _rload_y, _ = result["RLOAD"]
        jout_x, _jout_y, _ = result["JOUT"]

        assert ux < riso_x < jout_x, f"Op-amp-side output support should stay right of U1: {result}"
        assert riso_x < rload_x, f"Longer output chain should use an inner support lane: {result}"
        assert cout_x < rload_x, (
            f"Connector-side output support should remain in the outer support lane: {result}"
        )
        assert rload_x < jout_x, f"Connector should remain the outermost output lane: {result}"

    def test_output_stage_cohesion_gives_connector_extra_clearance(self) -> None:
        """Output connectors should sit one grid step beyond the nominal connector lane."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RISO", symbol="Device:R", value="47"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="RISO", pin="1")],
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="RISO", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RISO", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "RISO": (205.0, 100.0, None),
            "JOUT": (215.0, 100.0, None),
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

        expected_connector_x = round(190.0 + 3.0 * GRID_COL_MM + 1.27, 2)
        assert result["JOUT"][0] >= expected_connector_x - 0.01
        assert result["RISO"][0] < result["JOUT"][0]

    def test_input_connector_stays_on_incoming_signal_handoff_row(self) -> None:
        """Phase 2.4.3: the input connector should attach to the first signal handoff row."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="1u"),
                ComponentIR(ref="RIN", symbol="Device:R", value="100k"),
                ComponentIR(ref="RSH", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")],
                ),
                NetIR(
                    name="IN_L_AC",
                    pins=[
                        PinRefIR(ref="CIN", pin="2"),
                        PinRefIR(ref="RIN", pin="1"),
                        PinRefIR(ref="U1", pin="3"),
                        PinRefIR(ref="RSH", pin="1"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="RSH", pin="2")]),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RSH", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.48, 106.68, None),
            "CIN": (45.72, 91.44, None),
            "RIN": (60.96, 99.06, None),
            "RSH": (60.96, 106.68, None),
            "U1": (91.44, 99.06, None),
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

        assert result["JIN"][1] == pytest.approx(result["CIN"][1]), (
            f"The input connector should align to the first incoming signal handoff row: {result}"
        )
        assert result["JIN"][1] != pytest.approx(result["RSH"][1]), (
            f"The input connector should not collapse onto the grounded shunt row: {result}"
        )
        assert result["JIN"][0] <= result["CIN"][0], (
            "The input connector should stay on the left side of the incoming handoff path: "
            f"{result}"
        )

        snapped_positions = {ref: (coords[0], coords[1]) for ref, coords in result.items()}
        orientations = compute_orientations(
            ir,
            snapped_positions,
            tiers=assign_tiers(ir),
        )
        assert orientations["JIN"] == 0, (
            "The input connector should keep its inward-facing 0° orientation on the "
            f"incoming handoff row: {orientations}"
        )
