"""Phase 7 — UX: end-to-end Graphviz tests, layout/routing/mode resolution, lint suggestions."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import (
    ANALOG_AUDIO_HEURISTIC_PROFILE,
    DEFAULT_SCHEMATIC_HEURISTIC_PROFILE,
    DENSE_DEBUG_HEURISTIC_PROFILE,
    GENERIC_DIGITAL_HEURISTIC_PROFILE,
    POWER_SUPPLY_HEURISTIC_PROFILE,
    SCHEMATIC_HEURISTIC_PROFILES,
    SchematicHeuristicProfile,
    _resolve_layout,
)
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.errors import UserError
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine, LayoutHeuristicPolicy
from kicad_pcb.results import NewFromNetlistResult
from kicad_pcb.router import RoutingHeuristicPolicy
from kicad_pcb.symbol_index import SymbolIndex

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def _minimal_ir_payload() -> dict:
    return {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
        "nets": [
            {"name": "NET_A", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "NET_B", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    }


def _new_from_netlist(
    tmp_path: Path, ir_payload: dict, *, name: str = "proj", **extra
) -> NewFromNetlistResult:
    """Run cmd_new_from_netlist in internal mode."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_payload), encoding="utf-8")
    defaults: dict = dict(
        name=name,
        netlist=str(ir_path),
        out_dir=str(tmp_path / "out"),
        description="",
        symbols_dir=str(_FIXTURES_DIR) if _FIXTURES_DIR.exists() else None,
        mode="internal",
        auto_fix=False,
        strict=False,
    )
    defaults.update(extra)
    return cmd_new_from_netlist(Namespace(**defaults))


class TestGraphvizRequiredEndToEnd:
    """When the engine's dot path is broken, _write_symbols raises RuntimeError."""

    def test_broken_dot_raises_runtime_error(self, tmp_path: Path) -> None:
        """End-to-end: a GraphvizLayoutEngine with a bad dot path raises RuntimeError
        from _write_symbols — no silent fallback.
        """
        from kicad_pcb.commands.netlist import _write_symbols  # noqa: PLC0415
        from kicad_pcb.sch_doc import SchematicDoc  # noqa: PLC0415
        from kicad_pcb.sexpr import parse as _parse  # noqa: PLC0415
        from kicad_pcb.sexpr.nodes import ListNode  # noqa: PLC0415

        minimal_sch = (
            "(kicad_sch (version 20230121) (generator eeschema)\n"
            '  (uuid "00000000-0000-0000-0000-000000000001")\n'
            '  (paper "A4"))\n'
        )
        root = _parse(minimal_sch)
        assert isinstance(root, ListNode)
        doc = SchematicDoc(root)

        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="R1", pin="1")])],
        )
        index = SymbolIndex(symbols_dir=None)
        stats: dict = {
            "symbols": 0,
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        bad_engine = GraphvizLayoutEngine(dot_path="/nonexistent/dot")
        with (
            patch(
                "kicad_pcb.commands._sch_apply_write.make_layout_engine",
                return_value=bad_engine,
            ),
            pytest.raises(RuntimeError, match="dot.*failed|dot.*not found"),
        ):
            _write_symbols(
                doc=doc,
                ir=ir,
                symbol_index=index,
                project_name="test",
                stats=stats,
                cache_path=None,
            )

    def test_no_fallback_warning_when_graphviz_succeeds(self, tmp_path: Path) -> None:
        """When Graphviz succeeds, no fallback warning code is emitted."""
        result = _new_from_netlist(tmp_path, _minimal_ir_payload())
        codes = [w.get("code") for w in result.warnings]  # type: ignore[attr-defined]
        assert "GRAPHVIZ_LAYOUT_FALLBACK" not in codes, (
            "Fail-fast mode should never emit GRAPHVIZ_LAYOUT_FALLBACK; "
            "Graphviz success should complete without fallback diagnostics."
        )


class TestResolveLayout:
    """_resolve_layout() accepts graphviz-only values and fails otherwise."""

    def test_none_default_returns_graphviz_engine(self) -> None:
        """None (no flag given) resolves to Graphviz."""
        from kicad_pcb.layout_engine import LayoutEngine  # noqa: PLC0415

        engine = _resolve_layout(None)
        assert isinstance(engine, LayoutEngine)

    def test_none_default_raises_when_dot_missing(self) -> None:
        """Default (None) fails fast when dot is unavailable."""
        _side_fx = RuntimeError("dot not found")
        with (
            patch("kicad_pcb.commands._sch_apply_resolve.make_layout_engine", side_effect=_side_fx),
            pytest.raises(RuntimeError, match="dot not found"),
        ):
            _resolve_layout(None)

    def test_graphviz_raises_when_dot_missing(self) -> None:
        """'graphviz' must raise RuntimeError when dot is unavailable."""
        _side_fx = RuntimeError("dot not found")
        with (
            patch("kicad_pcb.commands._sch_apply_resolve.make_layout_engine", side_effect=_side_fx),
            pytest.raises(RuntimeError, match="dot not found"),
        ):
            _resolve_layout("graphviz")

    def test_unknown_name_raises_user_error(self) -> None:

        with pytest.raises(UserError, match="Unknown layout engine"):
            _resolve_layout("banana")

    def test_strict_flag_is_forwarded_to_make_layout_engine(self) -> None:
        with patch("kicad_pcb.commands._sch_apply_resolve.make_layout_engine") as mocked_factory:
            _resolve_layout("graphviz", strict=True)
        mocked_factory.assert_called_once_with(
            cache_path=None,
            debug_dump_path=None,
            heuristic_profile_name=DEFAULT_SCHEMATIC_HEURISTIC_PROFILE.name,
            layout_heuristic_policy=DEFAULT_SCHEMATIC_HEURISTIC_PROFILE.layout_policy,
            strict=True,
        )

    def test_default_profile_bundles_layout_and_routing_policies(self) -> None:
        profile = DEFAULT_SCHEMATIC_HEURISTIC_PROFILE

        assert isinstance(profile, SchematicHeuristicProfile)
        assert profile.name == "analog_audio"
        assert isinstance(profile.layout_policy, LayoutHeuristicPolicy)
        assert isinstance(profile.routing_policy, RoutingHeuristicPolicy)

    def test_named_profiles_are_registered(self) -> None:
        assert SCHEMATIC_HEURISTIC_PROFILES == {
            "analog_audio": ANALOG_AUDIO_HEURISTIC_PROFILE,
            "generic_digital": GENERIC_DIGITAL_HEURISTIC_PROFILE,
            "power_supply": POWER_SUPPLY_HEURISTIC_PROFILE,
            "dense_debug": DENSE_DEBUG_HEURISTIC_PROFILE,
        }

    def test_generic_digital_profile_disables_analog_specific_heuristics(self) -> None:
        profile = GENERIC_DIGITAL_HEURISTIC_PROFILE

        assert profile.layout_policy.enable_decoupling_snap is False
        assert profile.layout_policy.enable_opamp_locality is False
        assert profile.layout_policy.enable_input_stage_cohesion is False
        assert profile.layout_policy.enable_output_stage_cohesion is False
        assert profile.routing_policy.enable_compact_output_tails is False
        assert profile.routing_policy.enable_compact_local_ground_clusters is False

    def test_power_supply_profile_keeps_ground_cluster_compaction_only(self) -> None:
        profile = POWER_SUPPLY_HEURISTIC_PROFILE

        assert profile.layout_policy.enable_decoupling_snap is False
        assert profile.layout_policy.enable_opamp_locality is False
        assert profile.layout_policy.enable_input_stage_cohesion is False
        assert profile.layout_policy.enable_output_stage_cohesion is False
        assert profile.routing_policy.enable_compact_output_tails is False
        assert profile.routing_policy.enable_compact_local_ground_clusters is True

    def test_dense_debug_profile_uses_generic_heuristics(self) -> None:
        profile = DENSE_DEBUG_HEURISTIC_PROFILE

        assert profile.layout_policy.enable_decoupling_snap is False
        assert profile.layout_policy.enable_opamp_locality is False
        assert profile.layout_policy.enable_input_stage_cohesion is False
        assert profile.layout_policy.enable_output_stage_cohesion is False
        assert profile.routing_policy.enable_compact_output_tails is False
        assert profile.routing_policy.enable_compact_local_ground_clusters is False
