"""Phase 7 — UX / Controls tests.

Covers two main areas:

7.1  CLI flag wiring
    - TestStrictFieldWiring     : ``_ApplyNetlistRequest`` has a ``strict`` field
      that is passed to ``mutate_and_validate_sch``.
    - TestCLIParsers            : ``apply-netlist``, ``new-from-netlist``,
      ``compile-netlist``, and ``add-component`` all expose ``--strict``.
    - TestResolveLayout         : ``_resolve_layout()`` maps flag strings to
      engine instances; unknown names raise ``UserError``.
    - TestResolveRouting        : ``_resolve_routing()`` maps flag strings to
      ``use_bus`` bools; unknown names raise ``UserError``.
    - TestResolveValidateMode   : expanded ``_resolve_mode()`` handles all five
      values ``none|syntax|lint|kicad|full`` (plus legacy ``internal``).
        - TestCLINewFlags           : ``apply-netlist`` and ``new-from-netlist``
            expose ``--routing`` and ``--validate``.

7.2  Graphviz fail-fast behavior
    - TestGraphvizFailsLoud     : ``GraphvizLayoutEngine`` raises
      ``RuntimeError`` when ``dot`` fails or returns no positions.
    - TestWriteSymbolsFourTuple: ``_write_symbols`` returns a 4-tuple
      (positions, endpoints, missing, raw_layout).
    - TestGraphvizRequiredEndToEnd: end-to-end: when ``make_layout_engine``
      returns an engine with a broken ``dot`` path, ``_write_symbols`` raises
      ``RuntimeError`` (no silent fallback at the ``_write_symbols`` level).
        - TestResolveLayoutFailFast: ``_resolve_layout()`` in ``auto`` mode also
            fails fast when Graphviz is unavailable; no automatic downgrade.
        - TestLintSuggestions       : LAY001–LAY004 suggestions avoid stale
            engine-switch guidance and stay fail-fast oriented.
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
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.graphviz_layout import GraphvizLayoutEngine
from kicad_pcb.results import NewFromNetlistResult
from kicad_pcb.symbol_index import SymbolIndex

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
# 7.2  _write_symbols returns 5-tuple
# ---------------------------------------------------------------------------


class TestWriteSymbolsFiveTuple:
    """_write_symbols must return a 5-tuple including explicit pin anchors."""

    def test_returns_four_elements(self, tmp_path: Path) -> None:
        """The return value of _write_symbols is a 5-element tuple."""
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
        assert len(result) == 5, f"Expected 5-tuple, got {len(result)}-tuple"
        _positions, _endpoints, _anchors, _missing, _raw_layout = result

    def test_strict_raises_when_layout_rotation_missing(self) -> None:
        """Strict mode must fail fast when the layout engine returns None rotation."""
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
        index = SymbolIndex(symbols_dir=_FIXTURES_DIR if _FIXTURES_DIR.exists() else None)
        stats: dict = {
            "symbols": 0,
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        class _NoneRotationEngine:
            def compute_symbol_positions(self, _ir: CircuitIR):
                return {"R1": (10.0, 20.0, None)}

        with pytest.raises(UserError) as exc_info:
            _write_symbols(
                doc=doc,
                ir=ir,
                symbol_index=index,
                project_name="test",
                stats=stats,
                engine=_NoneRotationEngine(),
                strict=True,
            )

        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
        assert exc_info.value.details["refs_missing_rotation"] == ["R1"]

    def test_non_strict_still_allows_orientation_fallback(self) -> None:
        """Default mode keeps the existing compute_orientations fallback behavior."""
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
        index = SymbolIndex(symbols_dir=_FIXTURES_DIR if _FIXTURES_DIR.exists() else None)
        stats: dict = {
            "symbols": 0,
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        class _NoneRotationEngine:
            def compute_symbol_positions(self, _ir: CircuitIR):
                return {"R1": (10.0, 20.0, None)}

        result = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=index,
            project_name="test",
            stats=stats,
            engine=_NoneRotationEngine(),
        )

        assert isinstance(result, tuple)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# 7.2  Graphviz required end-to-end
# ---------------------------------------------------------------------------
