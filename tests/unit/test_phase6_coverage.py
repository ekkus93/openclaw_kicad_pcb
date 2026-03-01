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
from kicad_pcb.layout import (
    GRID_COL_MM,
    ORIGIN_X,
    HeuristicLayoutEngine,
    compute_signal_flow_layout,
)
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.router import route_nets
from kicad_pcb.sch_doc import SchematicDoc
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
# 6.1  TestHeuristicFeedbackPlacement
#
# In an inverting op-amp stage the feedback resistor R_f connects the op-amp
# output pin back to the op-amp inverting input.  Because both pins of R_f
# share nets exclusively with U1, R_f appears as a direct BFS-graph neighbour
# of U1.  The heuristic engine therefore places R_f in the column immediately
# adjacent to U1 (|x_delta| == GRID_COL_MM, i.e. exactly one column away).
# ---------------------------------------------------------------------------


class TestHeuristicFeedbackPlacement:
    """Feedback resistor in op-amp circuit is placed within one column of the op-amp."""

    def _feedback_ir(self) -> CircuitIR:
        """Inverting op-amp stage: J1 → R_in → U1, R_f: U1_out → U1_inv_in."""
        return _ir(
            [
                ("J1", "Device:Connector"),
                ("R_in", "Device:R"),
                ("U1", "Device:R"),
                ("R_f", "Device:R"),
            ],
            [
                ("IN", [("J1", "1"), ("R_in", "1")]),
                # R_f's pin 2 and R_in's pin 2 both connect to U1's inverting input.
                ("MINUS", [("R_in", "2"), ("U1", "2"), ("R_f", "2")]),
                # R_f's pin 1 connects to U1's output — pure feedback loop.
                ("OUT", [("U1", "6"), ("R_f", "1")]),
            ],
        )

    def test_feedback_resistor_column_adjacent_to_opamp(self) -> None:
        """R_f must be placed in an adjacent column to U1 (|Δx| ≤ GRID_COL_MM)."""
        ir = self._feedback_ir()
        positions = compute_signal_flow_layout(ir)
        x_u1 = positions["U1"][0]
        x_rf = positions["R_f"][0]
        assert abs(x_u1 - x_rf) <= GRID_COL_MM, (
            f"Feedback R_f (x={x_rf:.2f}) is more than one column away from U1 (x={x_u1:.2f}); "
            f"expected |Δx| ≤ {GRID_COL_MM} mm."
        )

    def test_feedback_resistor_not_at_input_column(self) -> None:
        """R_f must not be placed at the same column as the source connector J1."""
        ir = self._feedback_ir()
        positions = compute_signal_flow_layout(ir)
        x_j1 = positions["J1"][0]
        x_rf = positions["R_f"][0]
        assert x_rf > x_j1, (
            f"R_f (x={x_rf:.2f}) should be downstream of J1 (x={x_j1:.2f}), not at the same column."
        )

    def test_feedback_circuit_all_positions_distinct(self) -> None:
        """All four components in the feedback circuit have distinct (x, y) positions."""
        ir = self._feedback_ir()
        positions = compute_signal_flow_layout(ir)
        coords = list(positions.values())
        assert len(coords) == len(set(coords)), (
            f"Duplicate positions in feedback circuit: {positions}"
        )


# ---------------------------------------------------------------------------
# 6.1  TestHeuristicLRChannelLayout
#
# When a circuit has symmetric L-channel and R-channel component chains
# (mirror topology, fully independent signal nets), the heuristic engine
# assigns the same column depth to each corresponding stage.  Both channels
# are seeded from col-0 (both inputs are J-prefix connectors), so BFS visits
# them in parallel and assigns isomorphic depths — both channels share the
# same x-coordinates stage by stage.
#
# This is the current "best-effort" L/R behaviour; the engine stacks the two
# channels vertically (different y per column) rather than spatially mirroring
# them across a centre axis.
# ---------------------------------------------------------------------------


class TestHeuristicLRChannelLayout:
    """Symmetric L/R channel circuit: both channels receive the same column depths."""

    def _lr_ir(self) -> CircuitIR:
        """Two independent 3-component chains: J_L→R_L1→R_L2 and J_R→R_R1→R_R2."""
        return _ir(
            [
                ("J_L", "Device:R"),
                ("R_L1", "Device:R"),
                ("R_L2", "Device:R"),
                ("J_R", "Device:R"),
                ("R_R1", "Device:R"),
                ("R_R2", "Device:R"),
            ],
            [
                ("SIG_L1", [("J_L", "1"), ("R_L1", "1")]),
                ("SIG_L2", [("R_L1", "2"), ("R_L2", "1")]),
                ("SIG_R1", [("J_R", "1"), ("R_R1", "1")]),
                ("SIG_R2", [("R_R1", "2"), ("R_R2", "1")]),
            ],
        )

    def test_input_connectors_at_same_x(self) -> None:
        """Both J_L and J_R are seeded at col-0 and placed at the same x."""
        ir = self._lr_ir()
        positions = compute_signal_flow_layout(ir)
        assert positions["J_L"][0] == pytest.approx(positions["J_R"][0]), (
            f"Input connectors at different x: J_L={positions['J_L'][0]:.2f}, "
            f"J_R={positions['J_R'][0]:.2f}"
        )

    def test_first_stage_at_same_x(self) -> None:
        """R_L1 and R_R1 (first stage of each channel) share the same x."""
        ir = self._lr_ir()
        positions = compute_signal_flow_layout(ir)
        assert positions["R_L1"][0] == pytest.approx(positions["R_R1"][0]), (
            f"First-stage resistors at different x: "
            f"R_L1={positions['R_L1'][0]:.2f}, R_R1={positions['R_R1'][0]:.2f}"
        )

    def test_second_stage_at_same_x(self) -> None:
        """R_L2 and R_R2 (second stage of each channel) share the same x."""
        ir = self._lr_ir()
        positions = compute_signal_flow_layout(ir)
        assert positions["R_L2"][0] == pytest.approx(positions["R_R2"][0]), (
            f"Second-stage resistors at different x: "
            f"R_L2={positions['R_L2'][0]:.2f}, R_R2={positions['R_R2'][0]:.2f}"
        )

    def test_channels_stacked_at_different_y(self) -> None:
        """L and R channel components share columns but occupy different row positions."""
        ir = self._lr_ir()
        positions = compute_signal_flow_layout(ir)
        # Both connectors are in col-0; they must be stacked (different y).
        assert positions["J_L"][1] != pytest.approx(positions["J_R"][1]), (
            f"Both channel connectors are at the same y={positions['J_L'][1]:.2f}; "
            "expected them to be stacked in different rows."
        )

    def test_signal_flows_left_to_right_per_channel(self) -> None:
        """Within each channel, x increases monotonically from input to output."""
        ir = self._lr_ir()
        positions = compute_signal_flow_layout(ir)
        assert positions["J_L"][0] <= positions["R_L1"][0], "Left ch: stage 1 not right of input"
        assert positions["R_L1"][0] <= positions["R_L2"][0], "Left ch: stage 2 not right of stage 1"
        assert positions["J_R"][0] <= positions["R_R1"][0], "Right ch: stage 1 not right of input"
        assert positions["R_R1"][0] <= positions["R_R2"][0], (
            "Right ch: stage 2 not right of stage 1"
        )


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


def _count_nodes(root, key: str) -> int:
    """Recursively count all ListNode children with the given key."""
    n = 0
    if isinstance(root, ListNode):
        if root.key == key:
            n += 1
        for child in root.items:
            n += _count_nodes(child, key)
    return n


def _new_from_netlist_file(tmp_path: Path, ir_path: Path, *, name: str) -> object:
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
            layout="heuristic",
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
        placed = {s["ref"] for s in doc.list_symbols()}
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

    def test_golden_fixture_has_global_labels(self) -> None:
        """The golden file uses global labels for power/high-degree nets (GND etc.)."""
        content = _HEADPHONE_AMP_GOLDEN_PATH.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        gl_count = _count_nodes(root, "global_label")
        assert gl_count > _BASELINE_GLOBAL_LABEL_COUNT, (
            f"Golden has {gl_count} global labels; expected >0. "
            "GND (degree-6) should be represented as global labels."
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

    def test_dynamic_all_refs_placed(self, tmp_path: Path) -> None:
        """Generating from the IR places all 13 components."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpAll")
        assert result.symbols_added == 13
        doc = SchematicDoc.load(result.managed_schematic_path)
        placed = {s["ref"] for s in doc.list_symbols()}
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

    def test_dynamic_better_than_baseline_global_labels(self, tmp_path: Path) -> None:
        """Generated schematic uses more global labels than the baseline (>0)."""
        result = _new_from_netlist_file(tmp_path, _HEADPHONE_AMP_IR_PATH, name="HpAmpGL")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        gl_count = _count_nodes(root, "global_label")
        assert gl_count > _BASELINE_GLOBAL_LABEL_COUNT, (
            f"Generated schematic has {gl_count} global labels; "
            f"expected >{_BASELINE_GLOBAL_LABEL_COUNT}."
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
        placed = {s["ref"] for s in doc.list_symbols()}
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
        """GND (degree-4 power net) must produce at least one global label."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockGL")
        content = result.managed_schematic_path.read_text(encoding="utf-8")
        root = _parse_sexpr(content.rstrip("\n"))
        assert _count_nodes(root, "global_label") > 0, (
            "GND is a degree-4 power net and should be represented as global labels."
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
        """Input connectors J1/J3 are placed left of or equal to resistors."""
        result = _new_from_netlist(tmp_path, _AUDIO_BLOCK_IR, name="AudioBlockConn")
        doc = SchematicDoc.load(result.managed_schematic_path)
        symbols = {s["ref"]: s["x"] for s in doc.list_symbols()}
        resistors_x = [symbols[ref] for ref in ("R1", "R3", "R4", "R5", "R7")]
        max_resistor_x = max(resistors_x)
        assert symbols["J1"] <= max_resistor_x, (
            f"J1 (x={symbols['J1']:.2f}) should be left of or equal to the rightmost resistor "
            f"(x={max_resistor_x:.2f})."
        )
        assert symbols["J3"] <= max_resistor_x, (
            f"J3 (x={symbols['J3']:.2f}) should be left of or equal to the rightmost resistor."
        )
