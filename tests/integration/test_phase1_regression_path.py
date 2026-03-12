"""Phase 1 integration coverage for regression-path instrumentation."""

from __future__ import annotations

import json
from pathlib import Path

import kicad_pcb.graphviz_layout as _gv_mod
import pytest
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.layout_engine import make_layout_engine

pytestmark = pytest.mark.integration

_dot_available = _gv_mod.find_dot_binary() is not None
requires_graphviz = pytest.mark.skipif(
    not _dot_available,
    reason="graphviz dot not found on PATH or GRAPHVIZ_DOT",
)

_TEST_ROOT = Path(__file__).resolve().parent.parent
_FIXTURE_DIR = _TEST_ROOT / "fixtures" / "readability" / "ne5532_headphone_amp_left_regressed"
_REGRESSED_IR_PATH = _FIXTURE_DIR / "circuit_ir.json"


@pytest.mark.skipif(not _REGRESSED_IR_PATH.exists(), reason="Phase 0 regression IR missing")
class TestPhase1RegressionPath:
    @requires_graphviz
    def test_regressed_fixture_emits_phase1_debug_dump(self, tmp_path: Path) -> None:
        ir = CircuitIR(**json.loads(_REGRESSED_IR_PATH.read_text(encoding="utf-8")))
        debug_dump_path = tmp_path / "ne5532_phase1_debug.json"

        engine = make_layout_engine(debug_dump_path=debug_dump_path)
        engine.compute_symbol_positions(ir)

        dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))
        post_snap_positions = dump["post_snap_positions"]
        u1_x = float(post_snap_positions["U1"]["x"])

        refs_in_u1_column = sorted(
            ref
            for ref, pos in post_snap_positions.items()
            if ref != "U1" and abs(float(pos["x"]) - u1_x) <= 0.5
        )
        forced_u1_halo_refs = sorted(
            ref
            for ref, details in dump["halo_alignment"].items()
            if details["anchor_ref"] == "U1" and details["post_snap_same_column"] is True
        )

        assert dump["connector_role_summary"]["would_trigger_legacy_bfs_fallback"] is False
        assert dump["connector_role_summary"]["legacy_mode"] == "sds_recursive_halving"
        assert dump["connector_role_summary"]["missing_roles"] == []
        assert dump["connector_role_summary"]["power_refs"] == ["J3"]
        assert dump["artifact_manifest"]["version"] == 1
        assert "block_layout" in dump["artifact_manifest"]["artifacts"]
        assert "final_positions" in dump["artifact_manifest"]["artifacts"]
        assert dump["connector_roles"] == {"J1": "input", "J2": "output", "J3": "power"}
        assert dump["halo_map"] == {"C6": "U1", "R2": "U1"}
        assert dump["raw_graphviz_positions"]
        assert dump["post_snap_positions"]
        assert set(dump["halo_alignment"]) == {"C6", "R2"}
        assert set(forced_u1_halo_refs).issubset(refs_in_u1_column)

    @requires_graphviz
    def test_phase2_reduces_exact_u1_column_crowding(self, tmp_path: Path) -> None:
        ir = CircuitIR(**json.loads(_REGRESSED_IR_PATH.read_text(encoding="utf-8")))
        debug_dump_path = tmp_path / "ne5532_phase2_debug.json"

        engine = make_layout_engine(debug_dump_path=debug_dump_path)
        engine.compute_symbol_positions(ir)

        dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))
        post_snap_positions = dump["post_snap_positions"]
        u1_x = float(post_snap_positions["U1"]["x"])

        refs_in_u1_column = sorted(
            ref
            for ref, pos in post_snap_positions.items()
            if ref != "U1" and abs(float(pos["x"]) - u1_x) <= 0.5
        )

        assert dump["forced_same_column_halo_refs"] == []
        assert len(refs_in_u1_column) <= 7
