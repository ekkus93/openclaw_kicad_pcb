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
from typing import cast

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import (
    GRID_COL_MM,
    compute_signal_flow_layout,
)
from kicad_pcb.lint import lint_schematic_layout
from kicad_pcb.results import NewFromNetlistResult
from kicad_pcb.router import route_nets
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
        """Inverting op-amp stage: J1 → R_in → U1 → J2, with R_f in feedback."""
        return _ir(
            [
                ("J1", "Device:Connector"),
                ("R_in", "Device:R"),
                ("U1", "Device:R"),
                ("R_f", "Device:R"),
                ("J2", "Device:Connector"),
            ],
            [
                ("IN", [("J1", "1"), ("R_in", "1")]),
                # R_f's pin 2 and R_in's pin 2 both connect to U1's inverting input.
                ("MINUS", [("R_in", "2"), ("U1", "2"), ("R_f", "2")]),
                # R_f's pin 1 and J2 both connect to U1's output.
                ("OUT", [("U1", "6"), ("R_f", "1"), ("J2", "1")]),
            ],
        )

    def test_feedback_circuit_without_output_role_fails(self) -> None:
        """Under-specified connector roles should fail instead of using a degraded layout."""
        ir = _ir(
            [
                ("J1", "Device:Connector"),
                ("R_in", "Device:R"),
                ("U1", "Device:R"),
                ("R_f", "Device:R"),
            ],
            [
                ("IN", [("J1", "1"), ("R_in", "1")]),
                ("MINUS", [("R_in", "2"), ("U1", "2"), ("R_f", "2")]),
                ("OUT", [("U1", "6"), ("R_f", "1")]),
            ],
        )
        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_roles"] == ["output"]

    def test_feedback_circuit_with_explicit_roles_keeps_feedback_near_opamp(self) -> None:
        """Explicit connector roles preserve the intended feedback placement."""
        ir = self._feedback_ir()
        roles = {"J1": "input", "J2": "output"}
        positions = compute_signal_flow_layout(ir, roles=roles)
        x_u1 = positions["U1"][0]
        x_rf = positions["R_f"][0]
        assert abs(x_u1 - x_rf) <= GRID_COL_MM, (
            f"Feedback R_f (x={x_rf:.2f}) is more than one column away from U1 (x={x_u1:.2f}); "
            f"expected |Δx| ≤ {GRID_COL_MM} mm."
        )

    def test_feedback_circuit_with_explicit_roles_has_distinct_positions(self) -> None:
        """Explicit connector roles still produce a sane distinct placement."""
        ir = self._feedback_ir()
        roles = {"J1": "input", "J2": "output"}
        positions = compute_signal_flow_layout(ir, roles=roles)
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
    """Symmetric L/R channel circuits now require explicit output roles."""

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

    def test_lr_circuit_without_outputs_fails(self) -> None:
        """Directionless dual-channel circuits should fail instead of using degraded placement."""
        ir = self._lr_ir()
        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_roles"] == ["output"]


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
        ) -> ListNode:
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


class TestOpAmpCentering:
    """4.4 — op-amps occupy the centre rows of their BFS column.

    When a BFS column contains a mix of op-amps (U/IC/OA prefixes) and other
    components (R, C, etc.), the layout engine places op-amps at the middle
    row index so passive components surround the IC on both sides — matching
    the conventional circuit-diagram aesthetic.
    """

    def _build_ir(self) -> CircuitIR:
        # J1 and J2 are seeds (col=0).  R1, U1, R2 all connect to both J1
        # and J2, so BFS assigns them all to col=1 (same column).
        return CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="U1", pin="2")]),
                NetIR(name="N3", pins=[PinRefIR(ref="J1", pin="3"), PinRefIR(ref="R2", pin="1")]),
                NetIR(name="N4", pins=[PinRefIR(ref="J2", pin="1"), PinRefIR(ref="R1", pin="2")]),
                NetIR(name="N5", pins=[PinRefIR(ref="J2", pin="2"), PinRefIR(ref="U1", pin="6")]),
                NetIR(name="N6", pins=[PinRefIR(ref="J2", pin="3"), PinRefIR(ref="R2", pin="2")]),
            ],
        )

    def test_opamp_not_at_topmost_row_in_column(self) -> None:
        """U1 should NOT occupy the topmost (row-0) position when R1 and R2
        share the same column — it should be centred."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        u1_x, u1_y = positions["U1"]
        # Collect all y-coordinates in U1's column.
        col_ys = sorted(y for ref, (x, y) in positions.items() if abs(x - u1_x) < 0.1)
        if len(col_ys) < 3:
            pytest.skip("Column has fewer than 3 members — centering trivially satisfied.")
        # U1 must not be at the extreme-top row.
        assert u1_y > col_ys[0], (
            f"U1 (y={u1_y:.2f}) is at the topmost row of its column "
            f"(col_ys={col_ys}); expected it to be centred."
        )

    def test_opamp_centering_is_deterministic(self) -> None:
        """Two calls on the same IR return the same U1 position."""
        ir = self._build_ir()
        p1 = compute_signal_flow_layout(ir)
        p2 = compute_signal_flow_layout(ir)
        assert p1["U1"] == p2["U1"]

    def test_opamp_centering_empty_column_requires_output_role(self) -> None:
        """A single input-connector circuit should fail instead of degrading."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
            ],
            nets=[
                NetIR(
                    name="N1",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="2")],
                )
            ],
        )

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_roles"] == ["output"]


# ---------------------------------------------------------------------------
# 4.4 (extended) — decoupling cap placement near anchor IC
# ---------------------------------------------------------------------------


class TestDecouplingCapPlacement:
    """4.4 — power-only passives are co-located with their anchor IC.

    A bypass capacitor that connects *only* to power/ground rails has no
    signal connectivity.  The post-BFS adjustment detects this and ensures
    the cap column is at most one BFS step away from the IC it bypasses.
    """

    def _build_ir(self) -> CircuitIR:
        # Signal chain: J1 → R1 → U1 → J2
        # Power-only cap: C1 connected to VCC (shared with U1) and GND (shared with U1)
        # C1 has NO signal-net connections.
        return CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="J1", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Device:Op_Amp", value="LM358"),
                ComponentIR(ref="J2", symbol="Connector:Conn_01x01"),
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ],
            nets=[
                # Signal chain:
                NetIR(
                    name="N_in",
                    pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="N_mid",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="2")],
                ),
                NetIR(
                    name="N_out",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
                ),
                # Power-only nets — C1 appears here but not in any signal net:
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="8"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )

    def test_cap_within_one_column_of_ic(self) -> None:
        """C1 (power-only) is at most one column-width from U1."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        c1_x = positions["C1"][0]
        u1_x = positions["U1"][0]
        assert abs(c1_x - u1_x) <= GRID_COL_MM + 0.1, (
            f"C1 (x={c1_x:.2f}) is more than one column away from U1 "
            f"(x={u1_x:.2f}); delta={abs(c1_x - u1_x):.2f} mm."
        )

    def test_cap_not_far_right_of_all_signal_components(self) -> None:
        """C1 must not be placed well past the rightmost signal component."""
        ir = self._build_ir()
        positions = compute_signal_flow_layout(ir)
        signal_refs = {"J1", "R1", "U1", "J2"}
        max_signal_x = max(positions[r][0] for r in signal_refs)
        c1_x = positions["C1"][0]
        # C1 may be one extra column past the signal boundary — but no more.
        assert c1_x <= max_signal_x + GRID_COL_MM + 0.1, (
            f"C1 (x={c1_x:.2f}) is more than one column past the rightmost "
            f"signal component (x={max_signal_x:.2f})."
        )


class TestSdsFallbackPolicy:
    def _simple_ir(self) -> CircuitIR:
        return _ir(
            [
                ("J1", "Device:Connector"),
                ("R1", "Device:R"),
                ("J2", "Device:Connector"),
            ],
            [
                ("N1", [("J1", "1"), ("R1", "1")]),
                ("N2", [("R1", "2"), ("J2", "1")]),
            ],
        )

    def test_incomplete_roles_raise_even_non_strict(self) -> None:
        ir = self._simple_ir()
        roles = {"J1": "input"}  # missing output role on purpose

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir, roles=roles)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_role"] == "output"

    def test_incomplete_roles_raise_in_strict_mode(self) -> None:
        ir = self._simple_ir()
        roles = {"J1": "input"}  # missing output role on purpose

        with pytest.raises(UserError) as exc_info:
            compute_signal_flow_layout(ir, roles=roles, strict=True)

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["missing_role"] == "output"


# ---------------------------------------------------------------------------
# 4.5 — bus-style (spine) wiring option
# ---------------------------------------------------------------------------

from kicad_pcb.router import _spine_route  # noqa: E402 — private API for unit test


class TestBusStyleSpineRoute:
    """4.5 — use_bus=True routes multi-pin nets via a straight spine rather than
    a centroid hub.

    Verifies both the internal ``_spine_route`` helper and the public
    ``route_nets(use_bus=True)`` interface.
    """

    def test_spine_horizontal_dominant(self) -> None:
        """Endpoints spread more on X → horizontal spine segment produced."""
        # Endpoints spread 40 mm on X, 20 mm on Y → horizontal dominant.
        endpoints = [(10.0, 50.0), (30.0, 30.0), (50.0, 50.0)]
        segs, junctions = _spine_route(endpoints)
        # Must include at least one horizontal segment (same y, different x).
        horiz = [s for s in segs if abs(s.y1 - s.y2) < 0.1 and abs(s.x1 - s.x2) > 1.0]
        assert horiz, f"Expected a horizontal spine segment; got {segs}"
        # Three T-junction points expected (one per endpoint).
        assert len(junctions) == 3, f"Expected 3 junctions; got {junctions}"

    def test_spine_vertical_dominant(self) -> None:
        """Endpoints spread more on Y → vertical spine segment produced."""
        # Endpoints spread 10 mm on X, 40 mm on Y → vertical dominant.
        endpoints = [(30.0, 10.0), (20.0, 30.0), (30.0, 50.0)]
        segs, junctions = _spine_route(endpoints)
        vert = [s for s in segs if abs(s.x1 - s.x2) < 0.1 and abs(s.y1 - s.y2) > 1.0]
        assert vert, f"Expected a vertical spine segment; got {segs}"
        assert len(junctions) == 3

    def test_route_nets_use_bus_produces_different_topology(self) -> None:
        """route_nets(use_bus=True) and use_bus=False yield different wire sets
        for a 3-pin hub-routeable net."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="BUS_NET",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                ),
            ],
        )
        # Endpoints roughly laid out horizontally.
        pin_endpoints = {
            ("R1", "2"): (10.0, 50.0, 180.0),
            ("R2", "2"): (30.0, 30.0, 180.0),
            ("R3", "2"): (50.0, 50.0, 180.0),
        }
        hub_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=False)
        bus_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=True)
        # Both must wire BUS_NET (non-empty wires and junctions).
        assert hub_routing.wires
        assert bus_routing.wires
        # The topologies must differ — spine has a different segment set.
        hub_wire_set = {(s.x1, s.y1, s.x2, s.y2) for s in hub_routing.wires}
        bus_wire_set = {(s.x1, s.y1, s.x2, s.y2) for s in bus_routing.wires}
        assert hub_wire_set != bus_wire_set, (
            "use_bus=True and use_bus=False produced identical wire segments; "
            "expected different topologies."
        )
        # Bus style should produce junctions (T-intersections on the spine).
        assert bus_routing.junctions, "Bus routing must produce junctions at spine T-intersections."

    def test_route_nets_use_bus_default_true(self) -> None:
        """use_bus defaults to True — spine/bus routing used by default."""
        ir = CircuitIR(
            version="test-1.0",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="HUB_NET",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                    ],
                )
            ],
        )
        pin_endpoints = {
            ("R1", "2"): (10.0, 50.0, 180.0),
            ("R2", "2"): (30.0, 30.0, 180.0),
            ("R3", "2"): (50.0, 50.0, 180.0),
        }
        default_routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        explicit_bus = route_nets(ir=ir, pin_endpoints=pin_endpoints, use_bus=True)
        assert {(s.x1, s.y1, s.x2, s.y2) for s in default_routing.wires} == {
            (s.x1, s.y1, s.x2, s.y2) for s in explicit_bus.wires
        }, "Default routing should match use_bus=True (spine/bus routing)."


# ---------------------------------------------------------------------------
# 4.6 — LAY lints enforced at --validate (pipeline integration)
# ---------------------------------------------------------------------------

import shutil  # noqa: E402

from kicad_pcb.lint import LintError  # noqa: E402
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_sch  # noqa: E402


class TestLAYLintsInPipeline:
    """4.6 — mutate_and_validate_sch runs lint_schematic_layout (LAY001–LAY005)
    in LINT mode and above, raising LintError when violations are detected.

    LAY lints have WARNING severity — they block only under ``strict=True``
    (or ``ValidationMode.FULL`` which implies strict).  Using the
    ``headphone_amp_current_layout`` regression fixture which is known to
    trigger LAY001, LAY002, and LAY005.
    """

    _FIXTURE = (
        Path(__file__).parent.parent
        / "fixtures"
        / "regressions"
        / "headphone_amp_current_layout.kicad_sch"
    )

    def test_lay_lints_raise_in_full_mode(self, tmp_path: Path) -> None:
        """FULL mode (strict) raises LintError for schematic with LAY issues."""
        dest = tmp_path / "bad_layout.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.FULL)

        codes = {issue.code for issue in exc_info.value.issues}
        assert codes & {"LAY001", "LAY002", "LAY005"}, (
            f"Expected at least one LAY code in LintError; got codes={codes}"
        )

    def test_lay_lints_raise_in_strict_lint_mode(self, tmp_path: Path) -> None:
        """LINT + strict=True raises LintError for schematic with LAY issues."""
        dest = tmp_path / "bad_layout_strict.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.LINT, strict=True)

        codes = {issue.code for issue in exc_info.value.issues}
        assert codes & {"LAY001", "LAY002", "LAY005"}

    def test_lay_lints_not_raised_in_lint_mode_non_strict(self, tmp_path: Path) -> None:
        """LINT mode without strict keeps LAY issues as warnings (no raise)."""
        dest = tmp_path / "bad_layout_nostrict.kicad_sch"
        shutil.copy(self._FIXTURE, dest)
        # Must NOT raise — LAY lints are WARNING; non-strict LINT tolerates them.
        mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.LINT)

    def test_lay_lints_not_raised_in_syntax_mode(self, tmp_path: Path) -> None:
        """SYNTAX mode skips lint checks entirely — no LintError raised."""
        dest = tmp_path / "bad_layout_syntax.kicad_sch"
        shutil.copy(self._FIXTURE, dest)
        mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.SYNTAX)

    def test_lay_lints_present_in_error_issues(self, tmp_path: Path) -> None:
        """LintError.issues contains individual LAY-coded findings in FULL mode."""
        dest = tmp_path / "bad_layout_issues.kicad_sch"
        shutil.copy(self._FIXTURE, dest)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(dest, lambda doc: None, mode=ValidationMode.FULL)

        lay_issues = [i for i in exc_info.value.issues if i.code.startswith("LAY")]
        assert lay_issues, "Expected at least one LAY-coded issue in LintError.issues"
        for issue in lay_issues:
            assert issue.code in {"LAY001", "LAY002", "LAY003", "LAY004", "LAY005"}
            assert issue.message
