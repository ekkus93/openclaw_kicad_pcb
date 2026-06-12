"""Phase 4: snap — halo affinity, block zone anchors, and opamp locality."""

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


class TestApplyPostLayoutSnaps_HaloOpamp:
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
