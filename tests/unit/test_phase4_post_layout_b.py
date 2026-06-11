"""Phase 4: post-layout snap — halo members, block zone anchors, opamp locality,
input/output stage cohesion tests.
"""

from __future__ import annotations

import math

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import (
    _OpAmpLocalityContext,
    _snap_opamp_halo,
    _snap_opamp_locality,
)
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
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
    def test_same_column_halo_members_move_to_adjacent_lanes(self) -> None:
        positions = {
            "U1": (100.0, 100.0, None),
            "C6": (100.0, 120.0, None),
            "R2": (100.0, 130.0, None),
        }

        result = _snap_opamp_halo(positions, {"C6": "U1", "R2": "U1"})

        assert result["C6"][0] == pytest.approx(100.0 - GRID_COL_MM)
        assert result["R2"][0] == pytest.approx(100.0 + GRID_COL_MM)
        assert result["C6"][1] == pytest.approx(100.0 - _gv_mod.GRID_ROW_MM)
        assert result["R2"][1] == pytest.approx(100.0 + _gv_mod.GRID_ROW_MM)

    def test_build_dot_source_uses_soft_halo_affinity_without_rank_same(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="N_IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="1")],
                ),
                NetIR(
                    name="N_FB",
                    pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="U1", pin="2")],
                ),
                NetIR(
                    name="N_OUT",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )

        dot_source = _gv_mod.build_dot_source(
            ir,
            tiers={"J1": 0, "R_FB": 1, "U1": 2, "J2": 3},
            connector_roles={"J1": "input", "J2": "output"},
            halo={"R_FB": "U1"},
        )

        assert "R_FB -> U1 [style=invis, weight=6, constraint=false];" in dot_source
        assert "rank=same;\n    U1;\n    R_FB;" not in dot_source

    def test_build_dot_source_emits_soft_block_zone_anchors(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(
                    ref="J2",
                    symbol="Connector_Generic:Conn_01x01",
                    value="OUT",
                ),
                ComponentIR(
                    ref="J3",
                    symbol="Connector_Generic:Conn_01x03",
                    value="+15V / 0V / -15V",
                ),
            ],
            nets=[
                NetIR(
                    name="N_IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="N_STAGE",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="1")],
                ),
                NetIR(
                    name="N_OUT",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="HP_OUT",
                    pins=[PinRefIR(ref="C7", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C7", BlockRole.OUTPUT)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)
        block_layout.add_assignment("J3", BlockRole.POWER_ENTRY)

        dot_source = _gv_mod.build_dot_source(
            ir,
            tiers={"J1": 0, "R1": 1, "U1": 2, "C7": 3, "J2": 4, "J3": 0},
            connector_roles={"J1": "input", "J2": "output", "J3": "power"},
            block_layout=block_layout,
        )

        assert (
            '__blk_input__ [label="", shape=point, width=0, height=0, style=invis];' in dot_source
        )
        assert '__blk_core__ [label="", shape=point, width=0, height=0, style=invis];' in dot_source
        assert (
            '__blk_output__ [label="", shape=point, width=0, height=0, style=invis];' in dot_source
        )
        assert "__blk_input__ -> R1 [style=invis, weight=12];" in dot_source
        assert "R1 -> __blk_core__ [style=invis, weight=8];" in dot_source
        assert "__blk_core__ -> C7 [style=invis, weight=12];" in dot_source
        assert "__blk_input__ -> __blk_core__ [style=invis, weight=30];" in dot_source
        assert "__blk_core__ -> __blk_output__ [style=invis, weight=30];" in dot_source

    def test_build_dot_source_feedback_dummy_nodes_have_empty_point_labels(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="FB",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                )
            ],
        )

        dot_source = _gv_mod.build_dot_source(
            ir,
            tiers={"U1": 1, "R1": 0},
            feedback_refs={"R1"},
        )

        assert (
            '__fbdummy_R1__ [label="", shape=point, style=invis, width=0, height=0];' in dot_source
        )
        assert "subgraph cluster_feedback" not in dot_source
        assert "R1 -> __fbdummy_R1__ [style=invis, weight=10];" in dot_source

    def test_build_dot_source_emits_stage_sequence_edges_within_block_layout(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="IN"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="U1A", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
                ComponentIR(ref="U1B", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R6", symbol="Device:R", value="100R"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="OUT"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="RV1", pin="1")],
                ),
                NetIR(
                    name="VOL_L_OUT",
                    pins=[PinRefIR(ref="RV1", pin="2"), PinRefIR(ref="U1A", pin="1")],
                ),
                NetIR(
                    name="OUT_L_STAGE1",
                    pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="C6", pin="1")],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[PinRefIR(ref="C6", pin="2"), PinRefIR(ref="U1B", pin="1")],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="R6", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
            ],
        )
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1A", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("U1B", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        dot_source = _gv_mod.build_dot_source(
            ir,
            tiers={"J1": 0, "RV1": 1, "U1A": 2, "C6": 3, "U1B": 4, "R6": 5, "J2": 6},
            connector_roles={"J1": "input", "J2": "output"},
            block_layout=block_layout,
        )

        assert "J1 -> RV1 [style=invis, weight=10];" in dot_source
        assert "RV1 -> U1A [style=invis, weight=10];" in dot_source
        assert "U1A -> C6 [style=invis, weight=10];" in dot_source
        assert "C6 -> U1B [style=invis, weight=10];" in dot_source
        assert "U1B -> R6 [style=invis, weight=10];" in dot_source
        assert "R6 -> J2 [style=invis, weight=10];" in dot_source

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
