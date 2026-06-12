"""Phase 6: heuristic placement, stability, label policy, and golden basic tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import (
    GRID_COL_MM,
    compute_signal_flow_layout,
)
from kicad_pcb.results import NewFromNetlistResult

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
