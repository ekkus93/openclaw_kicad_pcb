"""Phase 7 — UX / Controls tests.

Covers two main areas:

7.1  CLI flag wiring
    - TestStrictFieldWiring     : ``_ApplyNetlistRequest`` has a ``strict`` field
      that is passed to ``mutate_and_validate_sch``.
    - TestCLIParsers            : ``apply-netlist``, ``new-from-netlist``,
      ``compile-netlist``, and ``add-component`` all expose ``--strict``; and
      ``compile-netlist`` also exposes ``--layout``.

7.2  Graphviz fallback diagnostics
    - TestGraphvizFallbackInfo  : ``GraphvizLayoutEngine.last_fallback_info`` is
      ``None`` initially and is populated with ``command``, ``error``, and
      ``fallback`` keys when ``dot`` fails.
    - TestWriteSymbolsFallback  : ``_write_symbols`` returns a 4-tuple whose
      fourth element carries the fallback info (or ``None`` when no fallback).
    - TestFallbackWarningInResult: end-to-end: when graphviz is forced to fail,
      ``cmd_new_from_netlist`` includes a ``GRAPHVIZ_LAYOUT_FALLBACK`` warning
      in the ``ApplyNetlistResult``.
"""

from __future__ import annotations

import dataclasses
import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands.netlist import _ApplyNetlistRequest, cmd_new_from_netlist
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _new_from_netlist(tmp_path: Path, ir_payload: dict, *, name: str = "proj", **extra) -> object:
    """Run cmd_new_from_netlist in internal/heuristic mode."""
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
        layout="heuristic",
        auto_fix=False,
        strict=False,
    )
    defaults.update(extra)
    return cmd_new_from_netlist(Namespace(**defaults))


# ---------------------------------------------------------------------------
# 7.1  Strict-field wiring
# ---------------------------------------------------------------------------


class TestStrictFieldWiring:
    """_ApplyNetlistRequest exposes a ``strict`` field."""

    def test_request_has_strict_field(self) -> None:
        """_ApplyNetlistRequest dataclass has a strict: bool field."""
        fields = {f.name: f for f in dataclasses.fields(_ApplyNetlistRequest)}
        assert "strict" in fields, "strict field missing from _ApplyNetlistRequest"
        assert fields["strict"].default is False, "strict should default to False"

    def test_request_strict_true(self, tmp_path: Path) -> None:
        """Constructing _ApplyNetlistRequest with strict=True works."""
        req = _ApplyNetlistRequest(
            netlist_path=tmp_path / "fake.json",
            symbols_dir=None,
            mode_name=None,
            force=False,
            dry_run=False,
            strict=True,
        )
        assert req.strict is True

    def test_request_strict_default_false(self, tmp_path: Path) -> None:
        """strict defaults to False."""
        req = _ApplyNetlistRequest(
            netlist_path=tmp_path / "fake.json",
            symbols_dir=None,
            mode_name=None,
            force=False,
            dry_run=False,
        )
        assert req.strict is False


# ---------------------------------------------------------------------------
# 7.1  CLI parsers
# ---------------------------------------------------------------------------


class TestCLIParsers:
    """Check that relevant subcommands expose --strict (and compile-netlist exposes --layout)."""

    @pytest.mark.parametrize(
        "subcommand,extra_required",
        [
            (["apply-netlist", "--netlist", "x.json", "--strict"], {}),
            (["new-from-netlist", "--name", "p", "--netlist", "x.json", "--strict"], {}),
            (["compile-netlist", "--name", "p", "--netlist", "x.json", "--strict"], {}),
            (
                ["compile-netlist", "--name", "p", "--netlist", "x.json", "--layout", "heuristic"],
                {},
            ),
            (["add-component", "Device:R", "R1", "--strict"], {}),
        ],
    )
    def test_subcommand_accepts_flag(self, subcommand: list[str], extra_required: dict) -> None:
        """Each subcommand should accept the given flag without argparse errors.

        A SystemExit with code 2 means argparse rejected the flag. Any other
        outcome (success, non-zero exit, or non-argparse exception) means the
        flag was recognised.
        """
        import sys  # noqa: PLC0415

        import kicad_pcb.cli as cli_mod  # noqa: PLC0415

        with patch.object(sys, "argv", ["kicad_pcb"] + subcommand):
            try:
                cli_mod.main()
            except SystemExit as exc:
                code = exc.args[0] if exc.args else 0
                assert code != 2, f"argparse rejected flag(s) in: {subcommand!r}"
            except Exception:  # noqa: BLE001
                pass  # non-argparse exception = flag was accepted by argparse
            # No exception = command ran + succeeded = flag accepted


# ---------------------------------------------------------------------------
# 7.2  Graphviz fallback diagnostics
# ---------------------------------------------------------------------------


class TestGraphvizFallbackInfo:
    """GraphvizLayoutEngine.last_fallback_info lifecycle."""

    def _make_engine(self) -> GraphvizLayoutEngine:
        return GraphvizLayoutEngine(dot_path="/nonexistent/dot")

    def test_initial_value_is_none(self) -> None:
        eng = self._make_engine()
        assert eng.last_fallback_info is None

    def test_fallback_info_populated_on_failure(self) -> None:
        """When dot fails, last_fallback_info is set with command/error/fallback keys."""
        eng = self._make_engine()
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="R1", pin="1")])],
        )
        # dot does not exist → run will fail; engine falls back to heuristic.
        eng.compute_symbol_positions(ir)

        assert eng.last_fallback_info is not None
        info = eng.last_fallback_info
        assert "command" in info
        assert "error" in info
        assert info.get("fallback") == "heuristic"
        # command string should contain the dot path and -Tplain
        assert "-Tplain" in info["command"]

    def test_fallback_info_none_when_no_failure(self) -> None:
        """If the engine succeeds (mocked), last_fallback_info remains None."""
        eng = self._make_engine()
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="R1", pin="1")])],
        )
        # Patch _run_dot to succeed and return a valid position dict.
        with patch.object(eng, "_run_dot", return_value={"R1": (10.0, 20.0, None)}):
            eng.compute_symbol_positions(ir)

        assert eng.last_fallback_info is None


# ---------------------------------------------------------------------------
# 7.2  _write_symbols returns 4-tuple
# ---------------------------------------------------------------------------


class TestWriteSymbolsFourTuple:
    """_write_symbols must return a 4-tuple (positions, endpoints, missing, fallback_info)."""

    def test_returns_four_elements(self, tmp_path: Path) -> None:
        """The return value of _write_symbols is a 4-element tuple."""
        from kicad_pcb.commands.netlist import _write_symbols  # noqa: PLC0415
        from kicad_pcb.sch_doc import SchematicDoc  # noqa: PLC0415
        from kicad_pcb.sexpr import parse as _parse  # noqa: PLC0415
        from kicad_pcb.sexpr.nodes import ListNode  # noqa: PLC0415
        from kicad_pcb.symbol_index import SymbolIndex  # noqa: PLC0415

        # Build a tiny schematic doc.
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
            components=[ComponentIR(ref="U1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="U1", pin="1")])],
        )
        symbols_dir = _FIXTURES_DIR if _FIXTURES_DIR.exists() else None
        index = SymbolIndex(symbols_dir=symbols_dir)
        stats: dict = {
            "symbols": 0,
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        result = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=index,
            project_name="test",
            stats=stats,
            layout_mode="heuristic",
            cache_path=None,
        )

        assert isinstance(result, tuple), "Expected a tuple return value"
        assert len(result) == 4, f"Expected 4-tuple, got {len(result)}-tuple"
        # Fourth element is fallback_info: None for heuristic (no fallback)
        _positions, _endpoints, _missing, fallback_info = result
        assert fallback_info is None, "Heuristic engine should not set fallback_info"


# ---------------------------------------------------------------------------
# 7.2  GRAPHVIZ_LAYOUT_FALLBACK warning in result
# ---------------------------------------------------------------------------


class TestFallbackWarningInResult:
    """When graphviz fails, ApplyNetlistResult includes a GRAPHVIZ_LAYOUT_FALLBACK warning."""

    def test_fallback_warning_present_when_dot_fails(self, tmp_path: Path) -> None:
        """End-to-end: forced graphviz failure → GRAPHVIZ_LAYOUT_FALLBACK in warnings.

        We patch make_layout_engine (as imported in commands.netlist) to return a
        GraphvizLayoutEngine pointing at a nonexistent binary so the test is
        deterministic regardless of whether graphviz is installed.
        """
        bad_engine = GraphvizLayoutEngine(dot_path="/nonexistent/dot")
        with patch("kicad_pcb.commands.netlist.make_layout_engine", return_value=bad_engine):
            result = _new_from_netlist(tmp_path, _minimal_ir_payload(), layout="heuristic")

        codes = [w.get("code") for w in result.warnings]  # type: ignore[attr-defined]
        assert "GRAPHVIZ_LAYOUT_FALLBACK" in codes, (
            f"Expected GRAPHVIZ_LAYOUT_FALLBACK in warnings; got: {codes}"
        )
        w = next(w for w in result.warnings if w.get("code") == "GRAPHVIZ_LAYOUT_FALLBACK")  # type: ignore[attr-defined]
        assert "command" in w.get("details", {})
        assert "error" in w.get("details", {})
        assert w["details"]["fallback"] == "heuristic"

    def test_no_fallback_warning_with_heuristic_engine(self, tmp_path: Path) -> None:
        """Using layout=heuristic never produces a GRAPHVIZ_LAYOUT_FALLBACK warning."""
        result = _new_from_netlist(tmp_path, _minimal_ir_payload(), layout="heuristic")
        codes = [w.get("code") for w in result.warnings]  # type: ignore[attr-defined]
        assert "GRAPHVIZ_LAYOUT_FALLBACK" not in codes
