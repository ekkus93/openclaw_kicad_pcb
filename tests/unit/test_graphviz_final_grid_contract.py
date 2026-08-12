"""Regression coverage for Graphviz final-placement grid invariants."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import kicad_pcb.graphviz_layout._gv_engine as gv_engine_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR

pytestmark = pytest.mark.unit


def _tiny_ir() -> CircuitIR:
    return CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="R1", symbol="TestLib:R", value="10k"),
            ComponentIR(ref="R2", symbol="TestLib:R", value="10k"),
        ],
        nets=[],
    )


def _patch_layout_preparation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gv_engine_mod,
        "_prepare_layout_inputs",
        lambda ir, refs, *, tiers: (None, {}, {}, set(), {}, {}, [], {}, set(), []),
    )
    monkeypatch.setattr(gv_engine_mod, "_classify_connector_roles", lambda *args, **kwargs: {})
    monkeypatch.setattr(gv_engine_mod, "_compute_signal_distance_scores", lambda *args: {})
    monkeypatch.setattr(gv_engine_mod, "_compute_affinity_groups", lambda *args: [])
    monkeypatch.setattr(gv_engine_mod, "_build_dot_source", lambda *args, **kwargs: "digraph {}")


def test_graphviz_cache_hit_reasserts_final_50_mil_grid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_layout_preparation(monkeypatch)
    cache_key = "cached-grid-key"
    monkeypatch.setattr(gv_engine_mod, "_layout_cache_key", lambda _source: cache_key)
    cache_path = tmp_path / "layout-cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "version": 2,
                "key": cache_key,
                "positions": {
                    "R1": [130.81, 105.41, 270.0],
                    "R2": [130.48, 120.65, 90.0],
                },
                "decoupling_map": {},
            }
        ),
        encoding="utf-8",
    )

    engine = gv_engine_mod.GraphvizLayoutEngine(
        dot_path="unused-dot",
        cache_path=cache_path,
        tiers={"R1": 0, "R2": 1},
    )

    result = engine.compute_symbol_positions(_tiny_ir())

    assert result["R1"] == pytest.approx((130.81, 105.41, 270.0))
    assert result["R2"] == pytest.approx((130.81, 120.65, 90.0))


def test_graphviz_fresh_result_reasserts_final_50_mil_grid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_layout_preparation(monkeypatch)
    off_grid = {
        "R1": (130.81, 105.41, None),
        "R2": (130.48, 120.65, None),
    }
    monkeypatch.setattr(gv_engine_mod, "_apply_post_layout_snaps", lambda *args, **kwargs: off_grid)
    monkeypatch.setattr(
        gv_engine_mod,
        "_compute_orientations",
        lambda *args, **kwargs: {"R1": 270, "R2": 90},
    )
    monkeypatch.setattr(
        gv_engine_mod,
        "_snap_input_connector_signal_attachment",
        lambda result, *args, **kwargs: result,
    )

    engine = gv_engine_mod.GraphvizLayoutEngine(
        dot_path="unused-dot",
        tiers={"R1": 0, "R2": 1},
    )
    monkeypatch.setattr(engine, "_run_dot", lambda _source: off_grid)

    result = engine.compute_symbol_positions(_tiny_ir())

    assert result["R1"] == pytest.approx((130.81, 105.41, 270.0))
    assert result["R2"] == pytest.approx((130.81, 120.65, 90.0))
