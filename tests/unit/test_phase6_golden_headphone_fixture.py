"""Phase 6: golden integration tests — headphone amp and audio block circuits."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
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

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

# ---------------------------------------------------------------------------
# IR builders
# ---------------------------------------------------------------------------


def _ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Build a CircuitIR from compact component/net specs.

    ``components`` — ``[(ref, symbol), ...]``
    ``nets``       — ``[(name, [(ref, pin), ...]), ...]``
    """
    comps = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    return CircuitIR(version=version, components=comps, nets=ir_nets)


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


class TestGoldenHeadphoneAmp:
    """Headphone amp IR produces a readable schematic — the 'intended golden layout'.

    Verifies the structural improvements over the old label-stub baseline and
    checks that the stored golden fixture has the expected properties.
    """

    # -- Fixture integrity --------------------------------------------------

    def test_golden_fixture_exists_and_loads(self) -> None:
        """The stored golden file exists and parses cleanly."""
        assert _HEADPHONE_AMP_GOLDEN_PATH.exists(), (
            f"Golden fixture missing: {_HEADPHONE_AMP_GOLDEN_PATH}"
        )
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
        assert doc is not None

    def test_golden_fixture_has_all_refs(self) -> None:
        """The stored golden file contains all 13 component refs."""
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
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
        assert expected <= placed, f"Missing refs in golden: {expected - placed}"

    def test_golden_fixture_zero_local_labels(self) -> None:
        """The golden file has zero local label stubs (all nets wired/globalized)."""
        content = _HEADPHONE_AMP_GOLDEN_PATH.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        label_count = _count_nodes(root, "label")
        assert label_count == 0, (
            f"Golden has {label_count} local label stubs; expected 0. "
            "The baseline had 16 — this is a regression."
        )

    def test_golden_fixture_power_symbols_for_gnd(self) -> None:
        """The golden file uses power:GND symbols for GND — no GND global labels (Phase 3)."""
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
        gnd_labels = count_global_labels(doc, text="GND")
        assert gnd_labels == 0, (
            f"Golden has {gnd_labels} GND global labels; expected 0. "
            "Phase 3 replaces GND global labels with power:GND symbols."
        )

    def test_golden_fixture_x_columns_ge_6(self) -> None:
        """The golden file has >= 6 distinct x-columns (signal-flow horizontal spread)."""
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
        x_cols = count_distinct_x_columns(doc)
        assert x_cols >= _MIN_X_COLUMNS, (
            f"Golden has {x_cols} x-columns; expected >= {_MIN_X_COLUMNS}. "
            "Components should be spread horizontally for readability."
        )

    def test_golden_fixture_no_lay004(self) -> None:
        """The golden file has no LAY004 symbol-out-of-bounds violations."""
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
        issues = run_layout_lints(doc)
        lay004 = [i for i in issues if i.code == "LAY004"]
        assert not lay004, f"LAY004 out-of-bounds in golden fixture: {lay004}"

    def test_golden_fixture_stub_ratio_below_threshold(self) -> None:
        """Golden stub ratio < 0.75: spine+power routing beats pure label-stub style."""
        doc = SchematicDoc.load(_HEADPHONE_AMP_GOLDEN_PATH)
        ratio = wire_stub_ratio(doc)
        assert ratio < _WIRE_STUB_RATIO_THRESHOLD, (
            f"Golden stub ratio {ratio:.3f} >= {_WIRE_STUB_RATIO_THRESHOLD}. "
            "Pure label-stub routing approaches 1.0; spine routing should be lower."
        )

    def test_golden_fixture_has_junctions(self) -> None:
        """The golden file has at least one explicit junction for T-junction nets."""
        content = _HEADPHONE_AMP_GOLDEN_PATH.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        jct_count = _count_nodes(root, "junction")
        assert jct_count > _BASELINE_JUNCTION_COUNT, (
            f"Golden has {jct_count} junctions; expected >0. "
            "Degree-3 nets (STAGE_L, STAGE_R) should produce T-junctions."
        )

    def test_golden_fixture_no_lay003_overlap(self) -> None:
        """The stored golden fixture has no LAY003 symbol-overlap violations."""
        content = _HEADPHONE_AMP_GOLDEN_PATH.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        issues = lint_schematic_layout(root)
        lay003 = [i for i in issues if i.code == "LAY003"]
        assert not lay003, f"LAY003 overlap in golden fixture: {lay003}"

    # -- Dynamic generation tests ------------------------------------------
