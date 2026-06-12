"""Phase 4: snap — input and output stage cohesion tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

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
