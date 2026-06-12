"""Phase 6: golden integration tests — dynamic headphone amp generation."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.results import NewFromNetlistResult
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    count_distinct_x_columns,
    count_global_labels,
    run_layout_lints,
    wire_stub_ratio,
)
from kicad_pcb.sexpr import parse as _parse_sexpr
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def _write_ir(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _new_from_netlist(tmp_path: Path, ir_payload: dict, *, name: str) -> NewFromNetlistResult:
    """Run cmd_new_from_netlist in internal mode and return result."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path, ir_payload)
    return cmd_new_from_netlist(
        Namespace(
            name=name,
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_FIXTURES_DIR),
            mode="internal",
        )
    )


# ---------------------------------------------------------------------------
# 6.3  TestGoldenHeadphoneAmp  (0.2 — "intended readable layout" golden)
#
# A simplified dual-channel passive headphone amplifier: 13 resistor/connector
# components, 9 nets (5 degree-2 signals, 2 degree-3 T-junctions, 1 degree-4
# spine, 1 degree-6 power-ground).  The full IR lives in
# ``tests/fixtures/regressions/headphone_amp_ir.json``.
#
# The stored golden file is the reference for what the improved layout looks
# like.  The tests below verify that generating from the same IR dynamically
# satisfies the same structural properties:
#
#   • All 13 components placed at distinct, non-overlapping positions.
#   • Zero local label stubs (all nets routed as wires or global labels).
#   • GND represented via global labels (not plain local label stubs).
#   • At least one explicit junction (degree-3 T-junction nets present).
#   • No LAY003 overlap violations.
#   • Better than the baseline on wire/label metrics (regression guard).
#
# The baseline metrics (captured from the original bad generator output stored
# in ``headphone_amp_current_layout.kicad_sch``) are: 16 local labels, 0
# global labels, 34 wires, 0 junctions.
# ---------------------------------------------------------------------------

_HEADPHONE_AMP_IR_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "regressions" / "headphone_amp_ir.json"
)
_HEADPHONE_AMP_GOLDEN_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "regressions"
    / "headphone_amp_golden_layout.kicad_sch"
)
_HEADPHONE_AMP_BASELINE_PATH = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "regressions"
    / "headphone_amp_current_layout.kicad_sch"
)

# Baseline metrics captured from headphone_amp_current_layout.kicad_sch.
# Used as regression lower-bounds in comparisons below.
_BASELINE_LABEL_COUNT = 16
_BASELINE_GLOBAL_LABEL_COUNT = 0
_BASELINE_JUNCTION_COUNT = 0

# Acceptance-criteria thresholds (Phase 6.1):
#   x-columns  : >= 6 ensures signal-flow horizontal spreading
#   stub_ratio : < 0.75 regression guard against pure label-stub mode
#                (0.35 is aspirational; current spine+power layout ~0.58)
_MIN_X_COLUMNS = 6
_WIRE_STUB_RATIO_THRESHOLD = 0.75


def _count_nodes(root, key: str) -> int:
    """Recursively count all ListNode children with the given key."""
    n = 0
    if isinstance(root, ListNode):
        if root.key == key:
            n += 1
        for child in root.items:
            n += _count_nodes(child, key)
    return n


def _new_from_netlist_file(tmp_path: Path, ir_path: Path, *, name: str) -> NewFromNetlistResult:
    """Run cmd_new_from_netlist using the headphone amp IR file."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    return cmd_new_from_netlist(
        Namespace(
            name=name,
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_FIXTURES_DIR),
            mode="internal",
        )
    )


class TestGoldenHeadphoneAmp_Dynamic:
    """Dynamic generation tests for the headphone amp IR."""

    def test_dynamic_all_refs_placed(self, tmp_path: Path) -> None:
        """Generating from the IR places all 13 components."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpAll")
        assert result.symbols_added == 13
        doc = SchematicDoc.load(result.managed_schematic_path)
        placed: set[str] = {cast(str, s["ref"]) for s in doc.list_symbols()}
        expected = {
            "J1",
            "J2",
            "J3",
            "J4",
            "J5",
            "R1",
            "R2",
            "R3",
            "R4",
            "R5",
            "R6",
            "R7",
            "R8",
        }
        assert expected <= placed, f"Missing refs: {expected - placed}"

    def test_dynamic_positions_all_distinct(self, tmp_path: Path) -> None:
        """No two components share the exact same (x, y) position."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpDist")
        doc = SchematicDoc.load(result.managed_schematic_path)
        positions = [(s["x"], s["y"]) for s in doc.list_symbols()]
        assert len(positions) == len(set(positions)), (
            f"Duplicate positions detected: {[p for p in positions if positions.count(p) > 1]}"
        )

    def test_dynamic_zero_local_labels(self, tmp_path: Path) -> None:
        """The dynamically generated schematic has zero local label stubs."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpLbl")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        label_count = _count_nodes(root, "label")
        assert label_count == 0, (
            f"Generated schematic has {label_count} local labels; expected 0. "
            f"Baseline had {_BASELINE_LABEL_COUNT}."
        )

    def test_dynamic_power_symbols_for_gnd(self, tmp_path: Path) -> None:
        """Generated schematic uses power:GND symbols — zero GND global labels (Phase 3)."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpGL")
        doc = SchematicDoc.load(result.managed_schematic_path)
        gnd_labels = count_global_labels(doc, text="GND")
        assert gnd_labels == 0, (
            f"Generated schematic has {gnd_labels} GND global labels; expected 0. "
            "Phase 3 replaces GND global labels with power:GND symbols."
        )

    def test_dynamic_has_junctions(self, tmp_path: Path) -> None:
        """Generated schematic has at least one junction for T-junction nets."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpJct")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        jct_count = _count_nodes(root, "junction")
        assert jct_count > _BASELINE_JUNCTION_COUNT, (
            f"Generated schematic has {jct_count} junctions; expected >{_BASELINE_JUNCTION_COUNT}."
        )

    def test_dynamic_no_lay003_overlap(self, tmp_path: Path) -> None:
        """No LAY003 symbol-overlap violations in the generated schematic."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpLAY")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        issues = lint_schematic_layout(root)
        lay003 = [i for i in issues if i.code == "LAY003"]
        assert not lay003, f"LAY003 overlap: {lay003}"

    def test_dynamic_layout_stable(self, tmp_path: Path) -> None:
        """Two independent runs on the same IR produce the same symbol positions."""
        r1 = _new_from_netlist_file(tmp_path / "r1", _HEADPHONE_AMP_IR_PATH, name="HpS1")
        r2 = _new_from_netlist_file(tmp_path / "r2", _HEADPHONE_AMP_IR_PATH, name="HpS2")
        doc1 = SchematicDoc.load(r1.managed_schematic_path)
        doc2 = SchematicDoc.load(r2.managed_schematic_path)
        pos1 = {s["ref"]: (s["x"], s["y"]) for s in doc1.list_symbols()}
        pos2 = {s["ref"]: (s["x"], s["y"]) for s in doc2.list_symbols()}
        assert pos1 == pos2, f"Positions differ between runs:\n  run1={pos1}\n  run2={pos2}"

    def test_dynamic_parses_cleanly(self, tmp_path: Path) -> None:
        """The generated managed schematic loads without parse errors."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpParse")
        doc = SchematicDoc.load(result.managed_schematic_path)
        assert doc is not None

    # -- Acceptance criteria (Phase 6.1) -----------------------------------

    def test_dynamic_x_columns_ge_6(self, tmp_path: Path) -> None:
        """Generated schematic has >= 6 distinct x-columns (signal-flow spread)."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpXCols")
        doc = SchematicDoc.load(result.managed_schematic_path)
        x_cols = count_distinct_x_columns(doc)
        assert x_cols >= _MIN_X_COLUMNS, (
            f"Generated schematic has {x_cols} x-columns; expected >= {_MIN_X_COLUMNS}."
        )

    def test_dynamic_gnd_global_labels_zero(self, tmp_path: Path) -> None:
        """Generated schematic has 0 GND global labels after Phase 3 power symbols."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpGndLbl")
        doc = SchematicDoc.load(result.managed_schematic_path)
        gnd = count_global_labels(doc, text="GND")
        assert gnd == 0, (
            f"Generated schematic has {gnd} GND global labels; expected 0 with power symbols."
        )

    def test_dynamic_no_lay004(self, tmp_path: Path) -> None:
        """Generated schematic has no LAY004 symbol-out-of-bounds violations."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpLAY4")
        doc = SchematicDoc.load(result.managed_schematic_path)
        issues = run_layout_lints(doc)
        lay004 = [i for i in issues if i.code == "LAY004"]
        assert not lay004, f"LAY004 out-of-bounds in generated schematic: {lay004}"

    def test_dynamic_stub_ratio_below_threshold(self, tmp_path: Path) -> None:
        """Generated stub ratio < 0.75: spine+power routing beats pure label-stub style."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpStub")
        doc = SchematicDoc.load(result.managed_schematic_path)
        ratio = wire_stub_ratio(doc)
        assert ratio < _WIRE_STUB_RATIO_THRESHOLD, (
            f"Generated stub ratio {ratio:.3f} >= {_WIRE_STUB_RATIO_THRESHOLD}. "
            "Pure label-stub routing approaches 1.0; spine routing should be lower."
        )


# ---------------------------------------------------------------------------
# 6.3  TestGoldenAudioBlock  (small audio block — subset of headphone amp)
#
# One channel of the headphone amp plus its shared bias divider: 8 components,
# 6 nets.  This is a *structural subset* of headphone_amp_ir.json and tests
# the golden behaviour on a smaller, self-contained audio circuit.
#
# Topology
# --------
# Components : J1 (AudioIn_L), J3 (PowerSupply), J4 (HeadphoneOut_L),
#              R1 (10k input series), R3 (47k bias top), R4 (47k bias bottom),
#              R5 (22k signal bias), R7 (100R output)
#
# Net degrees (routing strategy):
#   IN_L    : J1:1, R1:1               → degree-2  → direct wire
#   VCC     : J3:1, R3:1               → degree-2  → direct wire
#   OUT_L   : J4:1, R7:2               → degree-2  → direct wire
#   STAGE_L : R1:2, R5:1, R7:1         → degree-3  → T-junction hub
#   MID_RAIL: R3:2, R4:1, R5:2         → degree-3  → T-junction hub
#   GND     : J1:2, J3:2, J4:2, R4:2   → degree-4  → power net → global labels
#
# Expected structural properties (same as golden headphone amp targets):
#   • All 8 refs placed at distinct, non-overlapping positions.
#   • Zero local label stubs.
#   • At least one global label (GND → degree-4 power net).
#   • At least one explicit junction (STAGE_L or MID_RAIL are degree-3).
#   • No LAY003 overlap violations.
#   • Stable layout (same positions on two independent runs).
# ---------------------------------------------------------------------------
