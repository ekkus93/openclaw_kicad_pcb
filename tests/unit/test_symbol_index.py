from __future__ import annotations

from pathlib import Path

import kicad_pcb.symbol_index as si_mod
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


def test_symbol_index_reads_unit_pins_from_explicit_dir(fixture_symbols_dir: Path) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    unit_pins = index.get_unit_pins("TestLib:DualOpAmp")

    assert unit_pins == {
        "1": ("1", "2", "3"),
        "2": ("5", "6", "7"),
        "3": ("4", "8"),
    }


def test_symbol_index_returns_empty_unit_pins_for_single_unit_symbol(
    fixture_symbols_dir: Path,
) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    assert index.get_unit_pins("TestLib:R") == {}


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


def test_symbol_index_raises_symbol_dir_missing_when_no_dirs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SymbolIndex must raise SYMBOL_DIR_MISSING when no symbol directories resolve."""
    monkeypatch.setattr(si_mod, "REPO_LOCAL_SYMBOLS_DIR", Path("/nonexistent_repo_local"))
    monkeypatch.setattr(si_mod, "SYMBOLS_CANDIDATES", ())

    with pytest.raises(UserError) as exc_info:
        SymbolIndex()

    assert exc_info.value.code == ErrorCode.SYMBOL_DIR_MISSING
    assert "searched_candidates" in exc_info.value.details
    assert "hint" in exc_info.value.details


def test_symbol_index_raises_io_error_when_declaration_probe_read_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lib_file = tmp_path / "TestLib.kicad_sym"
    lib_file.write_text("(kicad_symbol_lib (version 20230121) (generator test))", encoding="utf-8")

    index = SymbolIndex(symbols_dir=tmp_path)

    monkeypatch.setattr(si_mod, "read_lib_symbol_pins", lambda *args, **kwargs: [])

    original_read_text = Path.read_text

    def _read_text_raise(self: Path, *args, **kwargs) -> str:
        if self == lib_file:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _read_text_raise)

    with pytest.raises(UserError) as exc_info:
        index.get_pins("TestLib:R")

    assert exc_info.value.code == ErrorCode.IO_ERROR
    assert exc_info.value.details["symbol"] == "TestLib:R"
    assert exc_info.value.details["lib_file"] == str(lib_file)


def test_symbol_index_caches_unit_pin_reads(
    fixture_symbols_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)
    calls = {"count": 0}
    original = si_mod.read_lib_symbol_unit_pins

    def _wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(si_mod, "read_lib_symbol_unit_pins", _wrapped)

    first = index.get_unit_pins("TestLib:DualOpAmp")
    second = index.get_unit_pins("TestLib:DualOpAmp")

    assert first == second
    assert calls["count"] == 1
