"""Phase 1 regression-path instrumentation tests.

These tests validate the new production-layout debug dump without requiring a
real Graphviz installation.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine, _analyze_legacy_sds_fallback
from kicad_pcb.graphviz_layout.dot_builder import _safe_id


def _build_headphone_amp_ir() -> CircuitIR:
    components = [
        ComponentIR(ref="J_IN", symbol="Connector_Generic:Conn_01x01", value="IN"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
        ComponentIR(ref="J_OUT", symbol="Connector_Generic:Conn_01x01", value="OUT"),
    ]
    nets = [
        NetIR(
            name="IN_NET",
            pins=[PinRefIR(ref="J_IN", pin="1"), PinRefIR(ref="R1", pin="1")],
        ),
        NetIR(
            name="MID_NET",
            pins=[
                PinRefIR(ref="R1", pin="2"),
                PinRefIR(ref="U1", pin="2"),
                PinRefIR(ref="C1", pin="1"),
            ],
        ),
        NetIR(
            name="OUT_NET",
            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J_OUT", pin="1")],
        ),
        NetIR(
            name="GND",
            pins=[PinRefIR(ref="C1", pin="2")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


def test_legacy_sds_fallback_summary_marks_complete_roles() -> None:
    result = _analyze_legacy_sds_fallback({"J_IN": "input", "J_OUT": "output", "J_PWR": "power"})

    assert result["missing_roles"] == []
    assert result["would_trigger_legacy_bfs_fallback"] is False
    assert result["legacy_mode"] == "sds_recursive_halving"
    assert result["power_refs"] == ["J_PWR"]


def test_legacy_sds_fallback_summary_marks_missing_output() -> None:
    result = _analyze_legacy_sds_fallback({"J_IN": "input", "J_TAP": "unknown"})

    assert result["missing_roles"] == ["output"]
    assert result["would_trigger_legacy_bfs_fallback"] is True
    assert result["legacy_mode"] == "bfs_fallback"


def test_graphviz_engine_writes_phase1_debug_dump(tmp_path: Path, monkeypatch) -> None:
    ir = _build_headphone_amp_ir()
    debug_dump_path = tmp_path / "layout_debug.json"
    engine = GraphvizLayoutEngine(
        dot_path="dot",
        debug_dump_path=debug_dump_path,
        heuristic_profile_name="generic_digital",
    )

    fake_positions = {
        _safe_id("J_IN"): (50.8, 76.2, None),
        _safe_id("R1"): (101.6, 76.2, None),
        _safe_id("C1"): (101.6, 101.6, None),
        _safe_id("U1"): (152.4, 76.2, None),
        _safe_id("J_OUT"): (203.2, 76.2, None),
    }
    monkeypatch.setattr(engine, "_run_dot", lambda dot_source: fake_positions)

    result = engine.compute_symbol_positions(ir)
    dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))

    assert dump["raw_graphviz_positions"]["J_IN"]["x"] == 50.8
    assert dump["raw_graphviz_positions"]["J_OUT"]["x"] == 203.2
    assert dump["post_snap_positions"]["J_IN"]["x"] == result["J_IN"][0]
    assert dump["post_snap_positions"]["J_OUT"]["x"] == result["J_OUT"][0]
    assert result["J_IN"][0] < result["U1"][0] < result["J_OUT"][0]
    assert dump["cache_hit"] is False
    assert dump["artifact_manifest"] == {
        "version": 1,
        "artifacts": [
            "tiers",
            "connector_roles",
            "connector_role_summary",
            "diagnostics",
            "block_layout",
            "heuristic_profile_name",
            "layout_heuristic_policy",
            "placement_constraints",
            "halo_map",
            "halo_alignment",
            "decoupling_map",
            "sds_scores",
            "sds_columns",
            "dot_source",
            "raw_graphviz_positions",
            "post_snap_positions",
            "final_positions",
        ],
    }
    assert dump["connector_role_summary"]["would_trigger_legacy_bfs_fallback"] is False
    assert dump["diagnostics"] == []
    assert dump["connector_roles"]["J_IN"] == "input"
    assert dump["connector_roles"]["J_OUT"] == "output"
    assert dump["heuristic_profile_name"] == "generic_digital"
    assert dump["tiers"]["J_IN"] == 0
    assert "dot_source" in dump
    assert dump["layout_heuristic_policy"]["enable_decoupling_snap"] is True
    assert "sds_columns" in dump
    assert "halo_map" in dump
    assert "placement_constraints" in dump
    assert "decoupling_map" in dump
    assert "raw_graphviz_positions" in dump
    assert "post_snap_positions" in dump
    assert "final_positions" in dump
    assert set(dump["raw_graphviz_positions"]) == {"C1", "J_IN", "J_OUT", "R1", "U1"}
    assert dump["placement_constraints"]["halo_map"] == dump["halo_map"]


def test_graphviz_engine_records_role_degradation_diagnostic(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:
    ir = _build_headphone_amp_ir()
    debug_dump_path = tmp_path / "layout_diag.json"
    engine = GraphvizLayoutEngine(dot_path="dot", debug_dump_path=debug_dump_path)

    fake_positions = {
        _safe_id("J_IN"): (50.8, 76.2, None),
        _safe_id("R1"): (101.6, 76.2, None),
        _safe_id("C1"): (101.6, 101.6, None),
        _safe_id("U1"): (152.4, 76.2, None),
        _safe_id("J_OUT"): (203.2, 76.2, None),
    }
    monkeypatch.setattr(engine, "_run_dot", lambda dot_source: fake_positions)
    monkeypatch.setattr(
        _gv_mod,
        "_classify_connector_roles",
        lambda refs, tiers, ir=None: {"J_IN": "input", "J_OUT": "unknown"},
    )

    with caplog.at_level(logging.WARNING, logger="kicad_pcb.graphviz_layout"):
        engine.compute_symbol_positions(ir)

    dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))
    assert dump["connector_role_summary"]["would_trigger_legacy_bfs_fallback"] is True
    diagnostic = dump["diagnostics"][0]
    assert diagnostic["code"] == "LAYDBG001"
    assert diagnostic["severity"] == "warning"
    assert diagnostic["details"]["missing_roles"] == ["output"]
    assert diagnostic["details"]["unknown_refs"] == ["J_OUT"]
    assert "current Graphviz pipeline stays active" in diagnostic["message"]
    assert "would have degraded to BFS fallback" in diagnostic["message"]
    assert "LAYDBG001" in caplog.text
