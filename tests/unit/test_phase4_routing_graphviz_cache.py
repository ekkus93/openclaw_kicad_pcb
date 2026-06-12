"""Phase 4 routing: graphviz layout cache, seed, timeout scaling, and dot source tests."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint import LintSeverity

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


def _minimal_ir(
    *,
    refs: list[str] | None = None,
    nets: list[dict] | None = None,
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *refs* defaults to ["R1", "R2"].
    *nets* is a list of dicts with keys ``name`` and ``pins`` (list of
    ``{"ref": ..., "pin": ...}`` dicts).
    """
    if refs is None:
        refs = ["R1", "R2"]
    if nets is None:
        nets = [
            {"name": "NET1", "pins": [{"ref": refs[0], "pin": "1"}, {"ref": refs[1], "pin": "1"}]}
        ]

    components = [ComponentIR(ref=r, symbol="Device:R", value="1k") for r in refs]
    ir_nets = [NetIR(name=n["name"], pins=[PinRefIR(**p) for p in n["pins"]]) for n in nets]
    return CircuitIR(version="1", components=components, nets=ir_nets)


def _simple_ir() -> CircuitIR:
    """Return a minimal two-component IR for cache tests."""
    components = [
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
    ]
    nets = [
        NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
        NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestGraphvizLayoutCacheHelpers:
    """Unit tests for the module-level cache helper functions."""

    def test_cache_key_is_stable(self) -> None:
        ir = _simple_ir()
        src = _gv_mod.build_dot_source(ir)
        key1 = _gv_mod.layout_cache_key(src)
        key2 = _gv_mod.layout_cache_key(src)
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex

    def test_cache_key_changes_with_different_source(self) -> None:
        key_a = _gv_mod.layout_cache_key("digraph A {}")
        key_b = _gv_mod.layout_cache_key("digraph B {}")
        assert key_a != key_b

    def test_load_cache_miss_when_file_absent(self, tmp_path: Path) -> None:
        result = _gv_mod.load_layout_cache(tmp_path / "nonexistent.json", "anykey")
        assert result is None

    def test_cache_roundtrip(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (30.0, 50.0, None),
            "C1": (40.0, 60.0, None),
        }
        key = "deadbeef" * 8  # 64 hex chars

        _gv_mod.save_layout_cache(cache_file, key, positions)
        loaded = _gv_mod.load_layout_cache(cache_file, key)

        assert loaded is not None
        assert loaded["R1"][0] == pytest.approx(30.0)
        assert loaded["R1"][1] == pytest.approx(50.0)
        assert loaded["C1"][0] == pytest.approx(40.0)
        assert loaded["C1"][1] == pytest.approx(60.0)

    def test_cache_entry_roundtrip_persists_decoupling_map(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (30.0, 50.0, 0.0),
            "C1": (40.0, 60.0, 90.0),
        }
        decoupling_map = {"C1": "U1"}
        key = "deadbeef" * 8

        _gv_mod.save_layout_cache(cache_file, key, positions, decoupling_map=decoupling_map)
        loaded_entry = _gv_mod.load_layout_cache_entry(cache_file, key)

        assert loaded_entry is not None
        assert loaded_entry.positions == positions
        assert loaded_entry.decoupling_map == decoupling_map

    def test_load_cache_miss_on_key_mismatch(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        _gv_mod.save_layout_cache(cache_file, "key-A" * 12 + "key-", {"R1": (1.0, 2.0, None)})
        result = _gv_mod.load_layout_cache(cache_file, "key-B" * 12 + "key-")
        assert result is None

    def test_load_cache_miss_on_version_mismatch(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps({"version": 999, "key": "k", "positions": {}}), encoding="utf-8"
        )
        assert _gv_mod.load_layout_cache(cache_file, "k") is None

    def test_save_cache_permission_error_raises(self, tmp_path: Path) -> None:
        """save_layout_cache must fail fast when the file cannot be written."""
        cache_file: Path = tmp_path / "layout.json"
        with (
            patch("pathlib.Path.write_text", side_effect=PermissionError("read-only")),
            pytest.raises(RuntimeError, match="Failed to write layout cache"),
        ):
            _gv_mod.save_layout_cache(cache_file, "k", {"R1": (1.0, 2.0, None)})

    def test_load_cache_invalid_json_raises(self, tmp_path: Path) -> None:
        """Invalid cache JSON must raise RuntimeError (no silent cache bypass)."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text("{not-json", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Invalid JSON in layout cache"):
            _gv_mod.load_layout_cache(cache_file, "k")

    def test_load_cache_invalid_positions_shape_raises(self, tmp_path: Path) -> None:
        """Malformed positions payload must raise RuntimeError."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps(
                {"version": 2, "key": "k", "positions": {"R1": "bad"}, "decoupling_map": {}}
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="Invalid layout cache entry"):
            _gv_mod.load_layout_cache(cache_file, "k")

    def test_load_cache_invalid_decoupling_map_shape_raises(self, tmp_path: Path) -> None:
        """Malformed decoupling_map payload must raise RuntimeError."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps(
                {
                    "version": 2,
                    "key": "k",
                    "positions": {"R1": [1.0, 2.0, None]},
                    "decoupling_map": {"C1": ["U1"]},
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="Invalid decoupling_map entry"):
            _gv_mod.load_layout_cache_entry(cache_file, "k")


# ---------------------------------------------------------------------------
# 4.18 GraphvizLayoutEngine cache integration
# ---------------------------------------------------------------------------


class TestGraphvizLayoutEngineCache:
    """Integration tests for seed and cache on GraphvizLayoutEngine."""

    def test_cache_hit_skips_dot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a valid cache entry exists, _run_dot must not be called."""
        ir = _simple_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (31.0, 51.0, None),
            "C1": (41.0, 61.0, None),
        }
        cache_file: Path = tmp_path / "layout.json"
        monkeypatch.setattr(
            _gv_mod._gv_engine,
            "_load_layout_cache_entry",
            lambda *_args: _gv_mod.LayoutCacheEntry(positions=positions, decoupling_map={}),
        )

        run_dot_called = False

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        result = engine.compute_symbol_positions(ir)

        assert not run_dot_called, "_run_dot was called despite a cache hit"
        assert result["R1"][0] == pytest.approx(31.0)
        assert result["C1"][1] == pytest.approx(61.0)

    def test_cache_written_after_dot_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a cache miss, the engine writes the new result to disk."""
        ir = _simple_ir()
        cache_file: Path = tmp_path / "layout.json"

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            return {"R1": (32.0, 52.0, None), "C1": (42.0, 62.0, None)}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        engine.compute_symbol_positions(ir)

        assert cache_file.exists(), "cache file was not written after dot run"

        # Second call with identical IR should hit the cache, not call dot.
        run_dot_called = False

        def fake_run_dot_2(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot_2)
        engine2 = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        engine2.compute_symbol_positions(ir)

        assert not run_dot_called, "second run should have been a cache hit"

    def test_no_cache_path_does_not_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without cache_path, no cache file is created."""

        ir = _simple_ir()

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            return {"R1": (1.0, 2.0, None), "C1": (3.0, 4.0, None)}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        before_json = {p.resolve() for p in Path().iterdir() if p.suffix == ".json"}
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")  # no cache_path
        engine.compute_symbol_positions(ir)

        after_json = {p.resolve() for p in Path().iterdir() if p.suffix == ".json"}
        created_json = sorted(str(p) for p in (after_json - before_json))
        assert not created_json, f"unexpected .json file created in cwd: {created_json}"

    def test_cache_hit_debug_dump_uses_cached_decoupling_map_without_recomputing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache-hit debug metadata should use the persisted refined decoupling map."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
                ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
            ],
            nets=[
                NetIR(
                    name="VEE",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="U2", pin="1"),
                        PinRefIR(ref="C1", pin="1"),
                    ],
                ),
                NetIR(
                    name="SIG_A",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="J1", pin="1")],
                ),
                NetIR(
                    name="SIG_B",
                    pins=[PinRefIR(ref="U2", pin="2"), PinRefIR(ref="J2", pin="1")],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="J1", pin="2"),
                        PinRefIR(ref="J2", pin="2"),
                    ],
                ),
            ],
        )
        cached_positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (88.9, 30.48, 0.0),
            "U2": (96.52, 83.82, 0.0),
            "C1": (88.9, 10.16, 90.0),
            "J1": (30.48, 30.48, 0.0),
            "J2": (30.48, 83.82, 180.0),
        }
        cache_file = tmp_path / "layout.json"
        debug_dump = tmp_path / "layout-debug.json"

        monkeypatch.setattr(
            _gv_mod._gv_engine,
            "_load_layout_cache_entry",
            lambda *_args: _gv_mod.LayoutCacheEntry(
                positions=cached_positions,
                decoupling_map={"C1": "U1"},
            ),
        )

        def fail_if_refine_called(*_args: object, **_kwargs: object) -> dict[str, str]:
            raise AssertionError("cache hit should not recompute decoupling_map refinement")

        monkeypatch.setattr(
            _gv_mod._gv_engine, "_refine_shared_rail_decoupling_map", fail_if_refine_called
        )

        run_dot_called = False

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(
            dot_path="dot",
            cache_path=cache_file,
            debug_dump_path=debug_dump,
        )
        result = engine.compute_symbol_positions(ir)

        assert not run_dot_called, "_run_dot was called despite a cache hit"
        payload = json.loads(debug_dump.read_text(encoding="utf-8"))
        assert payload["cache_hit"] is True
        assert payload["decoupling_map"] == {"C1": "U1"}
        assert payload["placement_constraints"]["decoupling_map"] == {"C1": "U1"}
        assert result["C1"][0] == pytest.approx(88.9)


# ---------------------------------------------------------------------------
# 4.19 Deterministic seed tests
# ---------------------------------------------------------------------------
