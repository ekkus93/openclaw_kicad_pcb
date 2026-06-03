"""Tests for _embed_symbol_if_found in kicad_pcb.commands._sch_apply."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb import placeholder_symbol
from kicad_pcb.commands._sch_apply import _embed_symbol_if_found
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import StringNode
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.utils import find_first
from kicad_pcb.symbol_index import SymbolIndex

_FIXTURE_SYMBOLS = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def _blank_doc() -> SchematicDoc:
    return SchematicDoc(parse('(kicad_sch (version 20231120) (generator "test"))'))


def _has_lib_symbol(doc: SchematicDoc, symbol_id: str) -> bool:
    """Return True if symbol_id appears in the doc's lib_symbols section."""
    lib_symbols = find_first(doc.root, "lib_symbols")
    if lib_symbols is None:
        return False
    for item in lib_symbols.items:
        if not hasattr(item, "items"):
            continue
        if (
            len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == symbol_id
        ):
            return True
    return False


def _make_placeholder(symbol_id: str, pins: list[str]) -> placeholder_symbol.PlaceholderSymbol:
    return placeholder_symbol.build(symbol_id, frozenset(pins))


# ---------------------------------------------------------------------------
# 6.1 Real symbol found in library
# ---------------------------------------------------------------------------


def test_embed_real_symbol_returns_true() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    result = _embed_symbol_if_found(doc=doc, symbol="TestLib:R", symbol_index=idx)
    assert result is True


def test_embed_real_symbol_appears_in_lib_symbols() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    _embed_symbol_if_found(doc=doc, symbol="TestLib:R", symbol_index=idx)
    assert _has_lib_symbol(doc, "TestLib:R")


def test_embed_real_symbol_idempotent() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    r1 = _embed_symbol_if_found(doc=doc, symbol="TestLib:R", symbol_index=idx)
    r2 = _embed_symbol_if_found(doc=doc, symbol="TestLib:R", symbol_index=idx)
    assert r1 is True
    assert r2 is True
    lib_syms = find_first(doc.root, "lib_symbols")
    count = sum(
        1
        for item in (lib_syms.items if lib_syms else [])
        if hasattr(item, "key") and item.key == "symbol"
    )
    assert count == 1


# ---------------------------------------------------------------------------
# 6.2 Symbol not found, no placeholder
# ---------------------------------------------------------------------------


def test_embed_missing_symbol_no_placeholder_returns_false() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    result = _embed_symbol_if_found(
        doc=doc, symbol="NoSuchLib:NoSuchPart", symbol_index=idx, placeholder=None
    )
    assert result is False


def test_embed_missing_symbol_leaves_doc_unchanged() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    _embed_symbol_if_found(doc=doc, symbol="NoSuchLib:NoSuchPart", symbol_index=idx)
    assert not _has_lib_symbol(doc, "NoSuchLib:NoSuchPart")


# ---------------------------------------------------------------------------
# 6.3 Symbol not found, placeholder provided
# ---------------------------------------------------------------------------


def test_embed_with_placeholder_returns_true() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    ph = _make_placeholder("FakeLib:FakePart", ["1", "2", "3"])
    result = _embed_symbol_if_found(
        doc=doc, symbol="FakeLib:FakePart", symbol_index=idx, placeholder=ph
    )
    assert result is True


def test_embed_with_placeholder_appears_in_lib_symbols() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    ph = _make_placeholder("FakeLib:FakePart", ["1", "2"])
    _embed_symbol_if_found(doc=doc, symbol="FakeLib:FakePart", symbol_index=idx, placeholder=ph)
    assert _has_lib_symbol(doc, "FakeLib:FakePart")


def test_embed_with_placeholder_idempotent() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    ph = _make_placeholder("FakeLib:FakePart", ["1"])
    r1 = _embed_symbol_if_found(
        doc=doc, symbol="FakeLib:FakePart", symbol_index=idx, placeholder=ph
    )
    r2 = _embed_symbol_if_found(
        doc=doc, symbol="FakeLib:FakePart", symbol_index=idx, placeholder=ph
    )
    assert r1 is True
    assert r2 is True


# ---------------------------------------------------------------------------
# 6.4 Unqualified symbol ID (no ':' in symbol string)
# ---------------------------------------------------------------------------


def test_embed_unqualified_no_placeholder_returns_false() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    result = _embed_symbol_if_found(doc=doc, symbol="CD4017", symbol_index=idx, placeholder=None)
    assert result is False


def test_embed_unqualified_no_placeholder_does_not_raise() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    _embed_symbol_if_found(doc=doc, symbol="CD4017", symbol_index=idx)


def test_embed_unqualified_with_placeholder_returns_true() -> None:
    doc = _blank_doc()
    idx = SymbolIndex(symbols_dir=_FIXTURE_SYMBOLS)
    ph = _make_placeholder("Custom:CD4017", ["1", "2", "3"])
    result = _embed_symbol_if_found(doc=doc, symbol="CD4017", symbol_index=idx, placeholder=ph)
    assert result is True
