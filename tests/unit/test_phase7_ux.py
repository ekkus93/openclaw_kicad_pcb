"""Phase 7 — UX / Controls tests.

Covers two main areas:

7.1  CLI flag wiring
    - TestStrictFieldWiring     : ``_ApplyNetlistRequest`` has a ``strict`` field
      that is passed to ``mutate_and_validate_sch``.
    - TestCLIParsers            : ``apply-netlist``, ``new-from-netlist``,
      ``compile-netlist``, and ``add-component`` all expose ``--strict``.
    - TestHeuristicLayoutEngine : ``HeuristicLayoutEngine`` satisfies the
      ``LayoutEngine`` protocol and produces ``(x, y, None)`` placements.
    - TestResolveLayout         : ``_resolve_layout()`` maps flag strings to
      engine instances; unknown names raise ``UserError``.
    - TestResolveRouting        : ``_resolve_routing()`` maps flag strings to
      ``use_bus`` bools; unknown names raise ``UserError``.
    - TestResolveValidateMode   : expanded ``_resolve_mode()`` handles all five
      values ``none|syntax|lint|kicad|full`` (plus legacy ``internal``).
    - TestCLINewFlags           : ``apply-netlist`` and ``new-from-netlist``
      expose ``--layout``, ``--routing``, and ``--validate``.

7.2  Graphviz mandatory (no silent fallback)
    - TestGraphvizFailsLoud     : ``GraphvizLayoutEngine`` raises
      ``RuntimeError`` when ``dot`` fails or returns no positions.
    - TestWriteSymbolsFourTuple: ``_write_symbols`` returns a 4-tuple
      (positions, endpoints, missing, raw_layout).
    - TestGraphvizRequiredEndToEnd: end-to-end: when ``make_layout_engine``
      returns an engine with a broken ``dot`` path, ``_write_symbols`` raises
      ``RuntimeError`` (no silent warn/fallback).
"""

from __future__ import annotations

import dataclasses
import json
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._sch_apply import _resolve_layout, _resolve_mode, _resolve_routing
from kicad_pcb.commands.netlist import _ApplyNetlistRequest, cmd_new_from_netlist
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine
from kicad_pcb.layout_engine import HeuristicLayoutEngine, NoneLayoutEngine
from kicad_pcb.pipeline import ValidationMode

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
    """Check that relevant subcommands expose --strict."""

    @pytest.mark.parametrize(
        "subcommand,extra_required",
        [
            (["apply-netlist", "--netlist", "x.json", "--strict"], {}),
            (["new-from-netlist", "--name", "p", "--netlist", "x.json", "--strict"], {}),
            (["compile-netlist", "--name", "p", "--netlist", "x.json", "--strict"], {}),
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
# 7.2  Graphviz mandatory (no silent fallback)
# ---------------------------------------------------------------------------


class TestGraphvizFailsLoud:
    """GraphvizLayoutEngine raises RuntimeError when dot fails — no silent fallback."""

    def _make_engine(self) -> GraphvizLayoutEngine:
        return GraphvizLayoutEngine(dot_path="/nonexistent/dot")

    def test_raises_on_dot_failure(self) -> None:
        """When dot binary does not exist, compute_symbol_positions raises RuntimeError."""
        eng = self._make_engine()
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="R1", pin="1")])],
        )
        with pytest.raises(RuntimeError, match="dot.*failed|dot.*not found"):
            eng.compute_symbol_positions(ir)

    def test_no_last_fallback_info_attribute(self) -> None:
        """GraphvizLayoutEngine no longer exposes last_fallback_info."""
        eng = self._make_engine()
        assert not hasattr(eng, "last_fallback_info"), (
            "last_fallback_info should have been removed; Graphviz is now mandatory"
        )

    def test_succeeds_when_dot_mocked(self) -> None:
        """When _run_dot is mocked to succeed, compute_symbol_positions returns positions."""
        eng = self._make_engine()
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R")],
            nets=[NetIR(name="N1", pins=[PinRefIR(ref="R1", pin="1")])],
        )
        with patch.object(eng, "_run_dot", return_value={"R1": (10.0, 20.0, None)}):
            result = eng.compute_symbol_positions(ir)
        assert "R1" in result


# ---------------------------------------------------------------------------
# 7.2  _write_symbols returns 3-tuple
# ---------------------------------------------------------------------------


class TestWriteSymbolsFourTuple:
    """_write_symbols must return a 4-tuple (positions, endpoints, missing, raw_layout)."""

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
            cache_path=None,
        )

        assert isinstance(result, tuple), "Expected a tuple return value"
        assert len(result) == 4, f"Expected 4-tuple, got {len(result)}-tuple"
        _positions, _endpoints, _missing, _raw_layout = result


# ---------------------------------------------------------------------------
# 7.2  Graphviz required end-to-end
# ---------------------------------------------------------------------------


class TestGraphvizRequiredEndToEnd:
    """When the engine's dot path is broken, _write_symbols raises RuntimeError."""

    def test_broken_dot_raises_runtime_error(self, tmp_path: Path) -> None:
        """End-to-end: a GraphvizLayoutEngine with a bad dot path raises RuntimeError
        from _write_symbols — no silent GRAPHVIZ_LAYOUT_FALLBACK warning.
        """
        from kicad_pcb.commands.netlist import _write_symbols  # noqa: PLC0415
        from kicad_pcb.sch_doc import SchematicDoc  # noqa: PLC0415
        from kicad_pcb.sexpr import parse as _parse  # noqa: PLC0415
        from kicad_pcb.sexpr.nodes import ListNode  # noqa: PLC0415
        from kicad_pcb.symbol_index import SymbolIndex  # noqa: PLC0415

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
            patch("kicad_pcb.commands._sch_apply.make_layout_engine", return_value=bad_engine),
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

    def test_no_fallback_warning_code(self, tmp_path: Path) -> None:
        """No GRAPHVIZ_LAYOUT_FALLBACK warning ever appears in results."""
        result = _new_from_netlist(tmp_path, _minimal_ir_payload())
        codes = [w.get("code") for w in result.warnings]  # type: ignore[attr-defined]
        assert "GRAPHVIZ_LAYOUT_FALLBACK" not in codes


# ---------------------------------------------------------------------------
# 7.1  New CLI flags — layout / routing / validate
# ---------------------------------------------------------------------------


class TestHeuristicLayoutEngine:
    """HeuristicLayoutEngine satisfies LayoutEngine and produces correct shapes."""

    def _make_ir(self) -> CircuitIR:
        return CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R"),
                ComponentIR(ref="C1", symbol="Device:C"),
            ],
            nets=[
                NetIR(
                    name="NET_A",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="NET_B",
                    pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")],
                ),
            ],
        )

    def test_returns_all_refs(self) -> None:
        """compute_symbol_positions covers every component in the IR."""
        engine = HeuristicLayoutEngine()
        ir = self._make_ir()
        positions = engine.compute_symbol_positions(ir)
        assert set(positions) == {"R1", "C1"}

    def test_positions_are_three_tuples_with_none_rotation(self) -> None:
        """Each value is (x, y, None) — heuristic engine provides no rotation."""
        engine = HeuristicLayoutEngine()
        ir = self._make_ir()
        positions = engine.compute_symbol_positions(ir)
        for ref, pos in positions.items():
            assert len(pos) == 3, f"{ref}: expected 3-tuple, got {len(pos)}-tuple"
            assert pos[2] is None, f"{ref}: rotation should be None, got {pos[2]}"


class TestResolveLayout:
    """_resolve_layout() maps CLI layout flag values to engine instances."""

    def test_none_flag_returns_none_engine(self) -> None:
        engine = _resolve_layout("none")
        assert isinstance(engine, NoneLayoutEngine)

    def test_heuristic_flag_returns_heuristic_engine(self) -> None:
        engine = _resolve_layout("heuristic")
        assert isinstance(engine, HeuristicLayoutEngine)

    def test_none_default_falls_back_gracefully(self) -> None:
        """None (no flag given) behaves like 'auto': returns some valid engine."""
        from kicad_pcb.layout_engine import LayoutEngine  # noqa: PLC0415

        engine = _resolve_layout(None)
        assert isinstance(engine, LayoutEngine)

    def test_auto_falls_back_to_heuristic_when_dot_missing(self) -> None:
        """When dot is absent, 'auto' silently returns HeuristicLayoutEngine."""
        _side_fx = RuntimeError("dot not found")
        with patch("kicad_pcb.commands._sch_apply.make_layout_engine", side_effect=_side_fx):
            engine = _resolve_layout("auto")
        assert isinstance(engine, HeuristicLayoutEngine)

    def test_graphviz_raises_when_dot_missing(self) -> None:
        """'graphviz' must raise RuntimeError when dot is unavailable."""
        _side_fx = RuntimeError("dot not found")
        with (
            patch("kicad_pcb.commands._sch_apply.make_layout_engine", side_effect=_side_fx),
            pytest.raises(RuntimeError, match="dot not found"),
        ):
            _resolve_layout("graphviz")

    def test_unknown_name_raises_user_error(self) -> None:
        from kicad_pcb.errors import UserError  # noqa: PLC0415

        with pytest.raises(UserError, match="Unknown layout engine"):
            _resolve_layout("banana")


class TestResolveRouting:
    """_resolve_routing() maps CLI routing flag values to use_bus bool."""

    @pytest.mark.parametrize("name", ["bus", "hub", None])
    def test_bus_hub_none_returns_true(self, name: str | None) -> None:
        assert _resolve_routing(name) is True

    def test_labels_returns_false(self) -> None:
        assert _resolve_routing("labels") is False

    def test_unknown_name_raises_user_error(self) -> None:
        from kicad_pcb.errors import UserError  # noqa: PLC0415

        with pytest.raises(UserError, match="Unknown routing style"):
            _resolve_routing("catbus")


class TestResolveValidateMode:
    """Expanded _resolve_mode() handles all five validation levels."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("none", ValidationMode.NONE),
            ("syntax", ValidationMode.SYNTAX),
            ("lint", ValidationMode.LINT),
            ("internal", ValidationMode.LINT),  # legacy alias
            ("kicad", ValidationMode.KICAD),
            ("full", ValidationMode.FULL),
        ],
    )
    def test_known_names(self, name: str, expected: ValidationMode) -> None:
        result = _resolve_mode(name, default=ValidationMode.LINT)
        assert result == expected

    def test_none_returns_default(self) -> None:
        assert _resolve_mode(None, default=ValidationMode.KICAD) == ValidationMode.KICAD

    def test_unknown_name_raises_user_error(self) -> None:
        from kicad_pcb.errors import UserError  # noqa: PLC0415

        with pytest.raises(UserError, match="Unknown mode"):
            _resolve_mode("turbo", default=ValidationMode.LINT)

    def test_error_lists_all_five_allowed_values(self) -> None:
        from kicad_pcb.errors import UserError  # noqa: PLC0415

        with pytest.raises(UserError) as exc_info:
            _resolve_mode("bad", default=ValidationMode.LINT)
        details = exc_info.value.details or {}
        allowed = details.get("allowed", [])
        assert set(allowed) == {"none", "syntax", "lint", "kicad", "full"}


class TestCLINewFlags:
    """apply-netlist and new-from-netlist expose --layout, --routing, --validate."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["apply-netlist", "--netlist", "x.json", "--layout", "heuristic"],
            ["apply-netlist", "--netlist", "x.json", "--layout", "none"],
            ["apply-netlist", "--netlist", "x.json", "--routing", "labels"],
            ["apply-netlist", "--netlist", "x.json", "--routing", "bus"],
            ["apply-netlist", "--netlist", "x.json", "--validate", "lint"],
            ["apply-netlist", "--netlist", "x.json", "--validate", "full"],
            ["apply-netlist", "--netlist", "x.json", "--validate", "none"],
            ["new-from-netlist", "--name", "p", "--netlist", "x.json", "--layout", "heuristic"],
            ["new-from-netlist", "--name", "p", "--netlist", "x.json", "--routing", "labels"],
            ["new-from-netlist", "--name", "p", "--netlist", "x.json", "--validate", "syntax"],
        ],
    )
    def test_flag_accepted_by_argparse(self, argv: list[str]) -> None:
        """argparse must not reject the flag (exit code 2 = argparse error)."""
        import sys  # noqa: PLC0415

        import kicad_pcb.cli as cli_mod  # noqa: PLC0415

        with patch.object(sys, "argv", ["kicad_pcb"] + argv):
            try:
                cli_mod.main()
            except SystemExit as exc:
                code = exc.args[0] if exc.args else 0
                assert code != 2, f"argparse rejected flag(s) in: {argv!r}"
            except Exception:  # noqa: BLE001
                pass  # non-argparse error = flag was accepted

    def test_layout_default_is_auto(self) -> None:
        """When --layout is omitted, the parsed namespace has layout='auto'."""
        import kicad_pcb.cli as cli_mod  # noqa: PLC0415

        parser = cli_mod.build_parser()
        ns = parser.parse_args(["apply-netlist", "--netlist", "x.json"])
        assert ns.layout == "auto"

    def test_routing_default_is_bus(self) -> None:
        """When --routing is omitted, the parsed namespace has routing='bus'."""
        import kicad_pcb.cli as cli_mod  # noqa: PLC0415

        parser = cli_mod.build_parser()
        ns = parser.parse_args(["apply-netlist", "--netlist", "x.json"])
        assert ns.routing == "bus"

    def test_validate_default_is_none(self) -> None:
        """When --validate is omitted, the parsed namespace has validate=None."""
        import kicad_pcb.cli as cli_mod  # noqa: PLC0415

        parser = cli_mod.build_parser()
        ns = parser.parse_args(["apply-netlist", "--netlist", "x.json"])
        assert ns.validate is None
