from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.symbol_index import SymbolIndex, resolve_symbol_dirs


@pytest.fixture
def fixture_symbols_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def test_symbol_index_reads_pins_from_explicit_dir(fixture_symbols_dir: Path) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    pins = index.get_pins("TestLib:R")

    assert pins == {"1", "2"}


def test_symbol_index_missing_symbol_raises_coded_error(fixture_symbols_dir: Path) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    with pytest.raises(UserError) as exc_info:
        index.get_pins("TestLib:DoesNotExist")

    assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND
    assert "searched_dirs" in exc_info.value.details


def test_resolve_symbol_dirs_prefers_explicit(fixture_symbols_dir: Path) -> None:
    resolved = resolve_symbol_dirs(symbols_dir=fixture_symbols_dir)

    assert resolved.dirs
    assert resolved.dirs[0] == fixture_symbols_dir.resolve()
