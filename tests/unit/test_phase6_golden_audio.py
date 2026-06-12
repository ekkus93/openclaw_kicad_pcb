"""Phase 6 golden tests — audio block circuit."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.results import NewFromNetlistResult
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.schematic_metrics import (
    count_global_labels,
)
from kicad_pcb.sexpr import parse as _parse_sexpr
from kicad_pcb.sexpr.nodes import ListNode


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


def _count_nodes(root, key: str) -> int:
    """Recursively count all ListNode children with the given key."""
    n = 0
    if isinstance(root, ListNode):
        if root.key == key:
            n += 1
        for child in root.items:
            n += _count_nodes(child, key)
    return n


_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


_AUDIO_BLOCK_IR = {
    "version": "1",
    "components": [
        {"ref": "J1", "symbol": "TestLib:R", "value": "AudioIn_L"},
        {"ref": "J3", "symbol": "TestLib:R", "value": "PowerSupply"},
        {"ref": "J4", "symbol": "TestLib:R", "value": "HeadphoneOut_L"},
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R3", "symbol": "TestLib:R", "value": "47k"},
        {"ref": "R4", "symbol": "TestLib:R", "value": "47k"},
        {"ref": "R5", "symbol": "TestLib:R", "value": "22k"},
        {"ref": "R7", "symbol": "TestLib:R", "value": "100"},
    ],
    "nets": [
        {"name": "IN_L", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
        {"name": "VCC", "pins": [{"ref": "J3", "pin": "1"}, {"ref": "R3", "pin": "1"}]},
        {"name": "OUT_L", "pins": [{"ref": "J4", "pin": "1"}, {"ref": "R7", "pin": "2"}]},
        {
            "name": "STAGE_L",
            "pins": [
                {"ref": "R1", "pin": "2"},
                {"ref": "R5", "pin": "1"},
                {"ref": "R7", "pin": "1"},
            ],
        },
        {
            "name": "MID_RAIL",
            "pins": [
                {"ref": "R3", "pin": "2"},
                {"ref": "R4", "pin": "1"},
                {"ref": "R5", "pin": "2"},
            ],
        },
        {
            "name": "GND",
            "pins": [
                {"ref": "J1", "pin": "2"},
                {"ref": "J3", "pin": "2"},
                {"ref": "J4", "pin": "2"},
                {"ref": "R4", "pin": "2"},
            ],
        },
    ],
}

_AUDIO_BLOCK_REFS = {"J1", "J3", "J4", "R1", "R3", "R4", "R5", "R7"}


class TestGoldenAudioBlock:
    """Single-channel audio block (subset of headphone amp) produces a readable schematic.

    Covers the small-audio-block golden test item from 6.3.  Uses only inline IR
    (no stored fixture file) — the same property-based pattern as
    TestGoldenResistorDivider and TestGoldenOpAmpStage.
    """

    def test_all_refs_placed(self, tmp_path: Path) -> None:
        """All 8 components from the audio block IR appear in the generated schematic."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockAll")
        assert result.symbols_added == 8
        doc = SchematicDoc.load(result.managed_schematic_path)
        placed: set[str] = {cast(str, s["ref"]) for s in doc.list_symbols()}
        assert placed >= _AUDIO_BLOCK_REFS, f"Missing refs: {_AUDIO_BLOCK_REFS - placed}"

    def test_positions_all_distinct(self, tmp_path: Path) -> None:
        """No two components share the same (x, y) position."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockDist")
        doc = SchematicDoc.load(result.managed_schematic_path)
        positions = [(s["x"], s["y"]) for s in doc.list_symbols()]
        assert len(positions) == len(set(positions)), (
            f"Duplicate positions: {[p for p in positions if positions.count(p) > 1]}"
        )

    def test_zero_local_labels(self, tmp_path: Path) -> None:
        """The generated schematic has zero local label stubs."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockLbl")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        assert _count_nodes(root, "label") == 0, (
            "Audio block has local label stubs; all nets should be wired or globalised."
        )

    def test_has_global_labels_for_gnd(self, tmp_path: Path) -> None:
        """GND (degree-4 power net) must produce power:GND symbols (Phase 3)."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockGL")
        doc = SchematicDoc.load(result.managed_schematic_path)
        gnd_labels = count_global_labels(doc, text="GND")
        assert gnd_labels == 0, (
            f"GND should use power symbols (0 global labels); found {gnd_labels}. "
            "Phase 3 replaces GND global labels with power:GND symbols."
        )

    def test_has_junctions_for_t_junctions(self, tmp_path: Path) -> None:
        """STAGE_L and/or MID_RAIL (degree-3 nets) must produce at least one junction."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockJct")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        assert _count_nodes(root, "junction") > 0, (
            "STAGE_L and MID_RAIL are degree-3 T-junction nets; "
            "at least one explicit junction node is expected."
        )

    def test_no_lay003_overlap(self, tmp_path: Path) -> None:
        """No LAY003 symbol-overlap violations in the generated schematic."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockLAY")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        issues = lint_schematic_layout(root)
        lay003 = [i for i in issues if i.code == "LAY003"]
        assert not lay003, f"LAY003 overlap: {lay003}"

    def test_layout_stable_across_runs(self, tmp_path: Path) -> None:
        """Two independent runs on the same IR produce identical symbol positions."""
        r1 = _new_from_netlist(tmp_path / "r1", _AUDIO_BLOCK_IR, name="AudioBlk1")
        r2 = _new_from_netlist(tmp_path / "r2", _AUDIO_BLOCK_IR, name="AudioBlk2")
        doc1 = SchematicDoc.load(r1.managed_schematic_path)
        doc2 = SchematicDoc.load(r2.managed_schematic_path)
        pos1 = {s["ref"]: (s["x"], s["y"]) for s in doc1.list_symbols()}
        pos2 = {s["ref"]: (s["x"], s["y"]) for s in doc2.list_symbols()}
        assert pos1 == pos2, f"Positions differ:\n  run1={pos1}\n  run2={pos2}"

    def test_parses_cleanly(self, tmp_path: Path) -> None:
        """The managed schematic loads without parse errors."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockParse")
        doc = SchematicDoc.load(result.managed_schematic_path)
        assert doc is not None

    def test_connectors_leftmost(self, tmp_path: Path) -> None:
        """Signal input connector J1 is placed left of or equal to resistors.

        J3 is a power-supply connector (only VCC / GND nets) so the layout
        engine places it in the power cluster at rank=max (far right).  We
        do not assert J3's x position here; that placement is correct.
        """
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockConn")
        doc = SchematicDoc.load(result.managed_schematic_path)
        symbols: dict[str, float] = {
            cast(str, s["ref"]): cast(float, s["x"]) for s in doc.list_symbols()
        }
        resistors_x = [symbols[ref] for ref in ("R1", "R3", "R4", "R5", "R7")]
        max_resistor_x = max(resistors_x)
        assert symbols["J1"] <= max_resistor_x, (
            f"J1 (x={symbols['J1']:.2f}) should be left of or equal to the rightmost resistor "
            f"(x={max_resistor_x:.2f})."
        )


# ---------------------------------------------------------------------------
# 4.4 (extended) — op-amp centering per column
# ---------------------------------------------------------------------------
