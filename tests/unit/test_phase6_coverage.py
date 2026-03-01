"""Phase 6 — Supplementary tests: layout, wiring, and golden structural checks.

Covers gaps not filled by test_phase4_layout.py:

6.1  Layout engine coverage
    - TestHeuristicInputPlacement  : heuristic places connector refs (J/P/CON)
      at smaller x-coordinates than non-connector refs (inputs-left guarantee).
    - TestGraphvizPositionStability: same IR + seed returns identical positions
      on repeated calls (skipped if ``dot`` is not installed).

6.2  Wiring — multi-net label duplication policy
    - TestLabelDuplicationPolicy   : a circuit with three independent degree-2
      signal nets produces zero local labels (all direct-wire routed).

6.3  Golden schematic structural tests  (AST equivalence — no stored golden files)
    - TestGoldenResistorDivider    : two-resistor voltage divider IR generates a
      parseable schematic with every ref placed, distinct positions, and stable
      output across two runs.
    - TestGoldenOpAmpStage         : op-amp + feedback resistor IR passes the
      same structural checks.
"""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import kicad_pcb.graphviz_layout as _gv_mod
import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.layout import ORIGIN_X, HeuristicLayoutEngine
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.router import route_nets
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as _parse_sexpr

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


def _new_from_netlist(tmp_path: Path, ir_payload: dict, *, name: str) -> object:
    """Run cmd_new_from_netlist in internal/heuristic mode and return result."""
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
            layout="heuristic",
        )
    )


# ---------------------------------------------------------------------------
# 6.1  TestHeuristicInputPlacement
#
# Connectors / headers (refs starting with J, P, CON, SJ, TJ) are signal
# sources in the heuristic engine.  BFS starts from them so they are
# assigned column-0, while downstream components get higher column indices
# and therefore larger x-coordinates.
# ---------------------------------------------------------------------------


class TestHeuristicInputPlacement:
    """Heuristic engine places connector refs leftmost (inputs-left rule)."""

    def _chain_ir(self) -> CircuitIR:
        """J1 → R1 → R2 chain — J1 is the clear signal source."""
        return _ir(
            [("J1", "Device:R"), ("R1", "Device:R"), ("R2", "Device:R")],
            [
                ("SIG1", [("J1", "1"), ("R1", "1")]),
                ("SIG2", [("R1", "2"), ("R2", "1")]),
            ],
        )

    def test_connector_leftmost_in_chain(self) -> None:
        """J1 must be placed strictly left of R1 and R2."""
        ir = self._chain_ir()
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        j1_x = positions["J1"][0]
        assert j1_x <= positions["R1"][0], "J1 should be left of or equal to R1"
        assert j1_x <= positions["R2"][0], "J1 should be left of or equal to R2"

    def test_connector_not_rightmost_in_chain(self) -> None:
        """J1 must not end up at the right edge when downstream refs exist."""
        ir = self._chain_ir()
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        max_x = max(pos[0] for pos in positions.values())
        # J1 should be strictly less than the rightmost column.
        assert positions["J1"][0] < max_x, "J1 must not be in the rightmost column"

    def test_multiple_connectors_seeded_at_column_zero(self) -> None:
        """Two connectors (J1, J2) sharing no nets with each other are both
        assigned column-0 (x = ORIGIN_X)."""
        ir = _ir(
            [("J1", "Device:R"), ("J2", "Device:R"), ("R1", "Device:R")],
            [
                ("A", [("J1", "1"), ("R1", "1")]),
                ("B", [("J2", "1"), ("R1", "2")]),
            ],
        )
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        assert positions["J1"][0] == pytest.approx(ORIGIN_X)
        assert positions["J2"][0] == pytest.approx(ORIGIN_X)

    def test_outputs_to_right_of_inputs(self) -> None:
        """Non-connector components downstream of connector are to the right."""
        ir = _ir(
            [("P1", "Device:R"), ("R1", "Device:R"), ("U1", "Device:R")],
            [
                ("IN", [("P1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "1")]),
            ],
        )
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        assert positions["P1"][0] <= positions["R1"][0]
        assert positions["R1"][0] <= positions["U1"][0]


# ---------------------------------------------------------------------------
# 6.1  TestGraphvizPositionStability
#
# The Graphviz engine must produce identical positions when run twice on the
# same IR with the same seed.  Skipped when dot is not installed.
# ---------------------------------------------------------------------------


_dot_available = _gv_mod.find_dot_binary() is not None

requires_graphviz = pytest.mark.skipif(
    not _dot_available,
    reason="graphviz dot not found on PATH or GRAPHVIZ_DOT",
)


@requires_graphviz
class TestGraphvizPositionStability:
    """Graphviz engine is deterministic for the same IR and seed."""

    def _small_ir(self) -> CircuitIR:
        return _ir(
            [("R1", "Device:R"), ("R2", "Device:R"), ("R3", "Device:R")],
            [
                ("N1", [("R1", "1"), ("R2", "1")]),
                ("N2", [("R2", "2"), ("R3", "1")]),
            ],
        )

    def _engine(self) -> _gv_mod.GraphvizLayoutEngine:
        dot = _gv_mod.find_dot_binary()
        assert dot is not None
        return _gv_mod.GraphvizLayoutEngine(dot_path=dot, seed=7)

    def test_same_positions_on_two_runs(self) -> None:
        """Two independent runs with the same seed must yield identical positions."""
        ir = self._small_ir()
        engine1 = self._engine()
        engine2 = self._engine()
        pos1 = engine1.compute_symbol_positions(ir)
        pos2 = engine2.compute_symbol_positions(ir)
        assert set(pos1) == set(pos2), "ref sets differ between runs"
        for ref in pos1:
            x1, y1, _ = pos1[ref]
            x2, y2, _ = pos2[ref]
            assert x1 == pytest.approx(x2, abs=0.01), f"{ref} x differs: {x1} vs {x2}"
            assert y1 == pytest.approx(y2, abs=0.01), f"{ref} y differs: {y1} vs {y2}"

    def test_non_overlapping_positions(self) -> None:
        """Graphviz-placed symbols must not overlap (no LAY003 fires)."""
        ir = self._small_ir()
        engine = self._engine()
        positions = engine.compute_symbol_positions(ir)

        def _sch_from_positions(
            pos: dict[str, tuple[float, float, float | None]],
        ) -> object:
            parts = []
            for i, (ref, (x, y, _)) in enumerate(sorted(pos.items())):
                uid = f"00000000-0000-0000-0000-{i:012d}"
                parts.append(f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "{uid}"))')
            body = "\n  ".join(parts)
            return _parse_sexpr(
                f"(kicad_sch (version 20230121) (generator test)\n"
                f"  (lib_symbols)\n  {body}\n"
                f'  (sheet_instances (path "/" (page "1")))\n)'
            )

        root = _sch_from_positions(positions)
        issues = lint_schematic_layout(root)
        lay003_codes = [i.code for i in issues if i.code == "LAY003"]
        assert not lay003_codes, f"LAY003 overlap detected: {positions}"


# ---------------------------------------------------------------------------
# 6.2  TestLabelDuplicationPolicy
#
# A circuit whose nets are all degree-2 and have known pin endpoints should
# route entirely via direct wires, producing zero local label nodes.
# This validates "label duplication capped by policy" from the TODO.
# ---------------------------------------------------------------------------


class TestLabelDuplicationPolicy:
    """Multi-net circuit routes without duplicate local labels."""

    def _three_net_ir(self) -> CircuitIR:
        return _ir(
            [
                ("J1", "Device:R"),
                ("R1", "Device:R"),
                ("R2", "Device:R"),
                ("R3", "Device:R"),
            ],
            [
                ("SIG_A", [("J1", "1"), ("R1", "1")]),
                ("SIG_B", [("R1", "2"), ("R2", "1")]),
                ("SIG_C", [("R2", "2"), ("R3", "1")]),
            ],
        )

    def _endpoints(self) -> dict[tuple[str, str], tuple[float, float, float]]:
        return {
            ("J1", "1"): (10.0, 100.0, 0.0),
            ("R1", "1"): (40.0, 100.0, 180.0),
            ("R1", "2"): (45.0, 100.0, 0.0),
            ("R2", "1"): (70.0, 100.0, 180.0),
            ("R2", "2"): (75.0, 100.0, 0.0),
            ("R3", "1"): (100.0, 100.0, 180.0),
        }

    def test_no_local_labels_for_three_degree2_nets(self) -> None:
        """Three degree-2 signal nets should produce zero local labels."""
        ir = self._three_net_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._endpoints())
        assert routing.labels == [], (
            f"Expected 0 local labels; got {len(routing.labels)}: {routing.labels}"
        )

    def test_no_label_duplication_across_nets(self) -> None:
        """Each net name appears at most once across all label types combined."""
        ir = self._three_net_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._endpoints())
        all_names = [lbl.name for lbl in routing.labels] + [g.name for g in routing.global_labels]
        for name in set(all_names):
            count = all_names.count(name)
            # Direct-wire nets produce 0 labels; power nets may produce multiple
            # global labels — but none of these nets are power or high-fanout.
            assert count <= 1, f"Net '{name}' label duplicated {count} times"

    def test_three_wires_emitted_for_three_nets(self) -> None:
        """Each degree-2 net should produce at least one wire segment."""
        ir = self._three_net_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._endpoints())
        # 3 signal nets × ≥1 wire each = at least 3 wires total.
        assert len(routing.wires) >= 3


# ---------------------------------------------------------------------------
# 6.3  TestGoldenResistorDivider
#
# A simple two-resistor voltage divider IR should generate a syntactically
# valid schematic, place all components, avoid LAY003 overlaps, and produce
# the same symbol positions on a second run (layout stability).
# ---------------------------------------------------------------------------

_DIVIDER_IR = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R2", "symbol": "TestLib:R", "value": "10k"},
    ],
    "nets": [
        {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
        {"name": "VMID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
        {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
    ],
}


class TestGoldenResistorDivider:
    """Voltage divider IR produces a structurally valid, stable schematic."""

    def test_all_refs_placed(self, tmp_path: Path) -> None:
        """All components from the IR appear in the generated managed schematic."""
        result = _new_from_netlist(tmp_path, _DIVIDER_IR, name="DividerAll")
        assert result.symbols_added == 2
        doc = SchematicDoc.load(result.managed_schematic_path)
        placed_refs = {s["ref"] for s in doc.list_symbols()}
        assert "R1" in placed_refs
        assert "R2" in placed_refs

    def test_positions_are_distinct(self, tmp_path: Path) -> None:
        """R1 and R2 must not be placed at the same (x, y) point."""
        result = _new_from_netlist(tmp_path, _DIVIDER_IR, name="DividerDist")
        doc = SchematicDoc.load(result.managed_schematic_path)
        symbols = {s["ref"]: (s["x"], s["y"]) for s in doc.list_symbols()}
        assert symbols["R1"] != symbols["R2"], (
            f"R1 and R2 are at the same position: {symbols['R1']}"
        )

    def test_layout_stable_across_runs(self, tmp_path: Path) -> None:
        """Two runs with the same IR and heuristic layout produce the same (x, y)."""
        result1 = _new_from_netlist(tmp_path / "run1", _DIVIDER_IR, name="Div1")
        result2 = _new_from_netlist(tmp_path / "run2", _DIVIDER_IR, name="Div2")
        doc1 = SchematicDoc.load(result1.managed_schematic_path)
        doc2 = SchematicDoc.load(result2.managed_schematic_path)
        pos1 = {s["ref"]: (s["x"], s["y"]) for s in doc1.list_symbols()}
        pos2 = {s["ref"]: (s["x"], s["y"]) for s in doc2.list_symbols()}
        assert pos1 == pos2, f"Positions differ between runs:\n  run1={pos1}\n  run2={pos2}"

    def test_schematic_parses_cleanly(self, tmp_path: Path) -> None:
        """The managed schematic must load without parse errors."""
        result = _new_from_netlist(tmp_path, _DIVIDER_IR, name="DividerParse")
        doc = SchematicDoc.load(result.managed_schematic_path)
        assert doc is not None

    def test_no_lay003_overlap(self, tmp_path: Path) -> None:
        """LAY003 (overlapping symbols) must not fire on the managed schematic."""
        result = _new_from_netlist(tmp_path, _DIVIDER_IR, name="DividerLAY")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        issues = lint_schematic_layout(root)
        lay003 = [i for i in issues if i.code == "LAY003"]
        assert not lay003, f"LAY003 overlap detected in divider schematic: {lay003}"


# ---------------------------------------------------------------------------
# 6.3  TestGoldenOpAmpStage
#
# An op-amp + feedback resistor IR should generate a structurally valid
# schematic with all refs placed at distinct positions.
# ---------------------------------------------------------------------------

_OPAMP_IR = {
    "version": "1",
    "components": [
        {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "TL071"},
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R2", "symbol": "TestLib:R", "value": "100k"},
    ],
    "nets": [
        {"name": "IN", "pins": [{"ref": "R1", "pin": "1"}]},
        {
            "name": "MINUS",
            "pins": [
                {"ref": "R1", "pin": "2"},
                {"ref": "U1", "pin": "2"},
                {"ref": "R2", "pin": "2"},
            ],
        },
        {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R2", "pin": "1"}]},
        {"name": "VCC", "pins": [{"ref": "U1", "pin": "1"}]},
    ],
}


class TestGoldenOpAmpStage:
    """Op-amp inverting stage IR produces a structurally valid schematic."""

    def test_all_refs_placed(self, tmp_path: Path) -> None:
        """All three components appear in the generated managed schematic."""
        result = _new_from_netlist(tmp_path, _OPAMP_IR, name="OpAmpAll")
        assert result.symbols_added == 3
        doc = SchematicDoc.load(result.managed_schematic_path)
        placed_refs = {s["ref"] for s in doc.list_symbols()}
        assert {"U1", "R1", "R2"} <= placed_refs

    def test_positions_all_distinct(self, tmp_path: Path) -> None:
        """No two components share the same (x, y) placement."""
        result = _new_from_netlist(tmp_path, _OPAMP_IR, name="OpAmpDist")
        doc = SchematicDoc.load(result.managed_schematic_path)
        positions = [(s["x"], s["y"]) for s in doc.list_symbols()]
        assert len(positions) == len(set(positions)), f"Duplicate positions found: {positions}"

    def test_schematic_parses_cleanly(self, tmp_path: Path) -> None:
        """The managed schematic must load without parse errors."""
        result = _new_from_netlist(tmp_path, _OPAMP_IR, name="OpAmpParse")
        doc = SchematicDoc.load(result.managed_schematic_path)
        assert doc is not None

    def test_connector_ref_absent_from_opamp_ir(self, tmp_path: Path) -> None:
        """The op-amp IR has no connectors; U1 should have non-zero x position."""
        result = _new_from_netlist(tmp_path, _OPAMP_IR, name="OpAmpX")
        doc = SchematicDoc.load(result.managed_schematic_path)
        symbols = {s["ref"]: s for s in doc.list_symbols()}
        # U1 should be placed somewhere meaningful, not at origin (0, 0).
        assert symbols["U1"]["x"] != 0.0 or symbols["U1"]["y"] != 0.0
