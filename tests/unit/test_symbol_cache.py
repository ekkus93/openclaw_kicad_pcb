"""Unit tests for the SQLite-backed symbol cache (Phase 8.3).

Covers:

* :class:`kicad_pcb.symbol_cache.SymbolCache` — cache miss, hit, mtime
  invalidation, bulk replace
* :func:`kicad_pcb.commands.search.cmd_build_symbol_index` — cold cache scan
* :func:`kicad_pcb.commands.search.cmd_search_symbols` — uses cache on warm
  subsequent call, scoped to explicit ``--symbols-dir``
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.commands.search import (
    _grep_matching_files,
    _parse_file_to_cached,
    _scan_dir_with_cache,
    cmd_build_symbol_index,
    cmd_search_symbols,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.symbol_cache import CachedSymbol, SymbolCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_KICAD_SYM = """\
(kicad_symbol_lib (version 20211014) (generator kicad_symbol_editor)
  (symbol "TestComp"
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "TestComp" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "ki_description" "A test component for unit tests" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (symbol "TestComp_0_1"
      (pin input line (at -5.08 0 0) (length 2.54)
        (name "IN" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin output line (at 5.08 0 0) (length 2.54)
        (name "OUT" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 0 -5.08 270) (length 2.54)
        (name "GND" (effects (font (size 1.27 1.27))))
        (number "3" (effects (font (size 1.27 1.27)))))
    )
  )
  (symbol "AnotherComp"
    (property "Reference" "R" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "Value" "AnotherComp" (at 0 0 0) (effects (font (size 1.27 1.27))))
    (property "ki_description" "Resistor placeholder" (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide))
    (symbol "AnotherComp_0_1"
      (pin passive line (at -2.54 0 0) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27)))))
      (pin passive line (at 2.54 0 0) (length 2.54)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27)))))
    )
  )
)
"""


def _make_sym_dir(tmp_path: Path, filename: str = "TestLib.kicad_sym") -> tuple[Path, Path]:
    """Create a minimal fake .kicad_sym library.  Returns (dir, lib_file)."""
    sym_dir = tmp_path / "symbols"
    sym_dir.mkdir(parents=True, exist_ok=True)
    lib_file = sym_dir / filename
    lib_file.write_text(_MINIMAL_KICAD_SYM, encoding="utf-8")
    return sym_dir, lib_file


# ---------------------------------------------------------------------------
# SymbolCache — unit tests
# ---------------------------------------------------------------------------


class TestSymbolCache:
    def test_cache_miss_returns_none(self, tmp_path: Path) -> None:
        """A freshly opened cache returns None for files it has never seen."""
        _, lib_file = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        result = cache.get_symbols(lib_file)
        assert result is None

    def test_store_and_hit(self, tmp_path: Path) -> None:
        """Symbols stored via store_symbols are returned by a subsequent get_symbols."""
        _, lib_file = _make_sym_dir(tmp_path)
        symbols = [CachedSymbol(lib_file=lib_file, sym_name="Foo", description="Desc", pin_count=2)]
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        cache.store_symbols(lib_file, symbols)

        result = cache.get_symbols(lib_file)
        assert result is not None
        assert len(result) == 1
        assert result[0].sym_name == "Foo"
        assert result[0].pin_count == 2

    def test_stale_entry_evicted_on_mtime_change(self, tmp_path: Path) -> None:
        """When a file's mtime changes, get_symbols treats the entry as a miss."""
        _, lib_file = _make_sym_dir(tmp_path)
        symbols = [CachedSymbol(lib_file=lib_file, sym_name="Old", description="", pin_count=1)]
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        cache.store_symbols(lib_file, symbols)

        # Advance mtime by rewriting the file
        time.sleep(0.01)
        lib_file.write_text(_MINIMAL_KICAD_SYM + "\n; updated\n", encoding="utf-8")

        result = cache.get_symbols(lib_file)
        assert result is None, "Cache should miss after file changes"

    def test_store_replaces_stale_entry(self, tmp_path: Path) -> None:
        """Calling store_symbols a second time for the same file replaces the old entry."""
        _, lib_file = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")

        cache.store_symbols(
            lib_file,
            [CachedSymbol(lib_file=lib_file, sym_name="Old", description="", pin_count=1)],
        )
        cache.store_symbols(
            lib_file,
            [CachedSymbol(lib_file=lib_file, sym_name="New", description="", pin_count=4)],
        )

        result = cache.get_symbols(lib_file)
        assert result is not None
        assert len(result) == 1
        assert result[0].sym_name == "New"

    def test_stats_reflect_stored_data(self, tmp_path: Path) -> None:
        """stats() returns correct indexed_files and indexed_symbols counts."""
        _, lib_file = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        syms = [
            CachedSymbol(lib_file=lib_file, sym_name="A", description="", pin_count=1),
            CachedSymbol(lib_file=lib_file, sym_name="B", description="", pin_count=2),
        ]
        cache.store_symbols(lib_file, syms)
        s = cache.stats()
        assert s["indexed_files"] == 1
        assert s["indexed_symbols"] == 2

    def test_empty_symbol_list_stored_and_retrieved(self, tmp_path: Path) -> None:
        """An empty symbol list (e.g. empty lib file) round-trips correctly."""
        _, lib_file = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        cache.store_symbols(lib_file, [])
        result = cache.get_symbols(lib_file)
        assert result == []

    def test_persist_across_instances(self, tmp_path: Path) -> None:
        """Cache data survives closing and reopening the SymbolCache."""
        _, lib_file = _make_sym_dir(tmp_path)
        db_path = tmp_path / "shared.db"

        cache1 = SymbolCache(db_path=db_path)
        cache1.store_symbols(
            lib_file,
            [CachedSymbol(lib_file=lib_file, sym_name="Persisted", description="X", pin_count=3)],
        )
        cache1.close()

        cache2 = SymbolCache(db_path=db_path)
        result = cache2.get_symbols(lib_file)
        assert result is not None
        assert result[0].sym_name == "Persisted"


# ---------------------------------------------------------------------------
# _parse_file_to_cached
# ---------------------------------------------------------------------------


class TestParseFileToCached:
    def test_parses_symbols_from_file(self, tmp_path: Path) -> None:
        _, lib_file = _make_sym_dir(tmp_path)
        symbols = _parse_file_to_cached(lib_file)
        names = {s.sym_name for s in symbols}
        assert names == {"TestComp", "AnotherComp"}

    def test_pin_counts_are_correct(self, tmp_path: Path) -> None:
        _, lib_file = _make_sym_dir(tmp_path)
        symbols = _parse_file_to_cached(lib_file)
        by_name = {s.sym_name: s for s in symbols}
        assert by_name["TestComp"].pin_count == 3
        assert by_name["AnotherComp"].pin_count == 2

    def test_description_extracted(self, tmp_path: Path) -> None:
        _, lib_file = _make_sym_dir(tmp_path)
        symbols = _parse_file_to_cached(lib_file)
        by_name = {s.sym_name: s for s in symbols}
        assert "test component" in by_name["TestComp"].description.lower()

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        nonexistent = tmp_path / "doesnotexist.kicad_sym"
        result = _parse_file_to_cached(nonexistent)
        assert result == []


# ---------------------------------------------------------------------------
# _scan_dir_with_cache
# ---------------------------------------------------------------------------


class TestScanDirWithCache:
    def test_returns_matching_symbols(self, tmp_path: Path) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        results = _scan_dir_with_cache(sym_dir, ["testcomp"], cache)
        names = {s.sym_name for s in results}
        assert "TestComp" in names

    def test_populates_cache_on_miss(self, tmp_path: Path) -> None:
        sym_dir, lib_file = _make_sym_dir(tmp_path)
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        assert cache.get_symbols(lib_file) is None  # cold

        _scan_dir_with_cache(sym_dir, ["testcomp"], cache)
        assert cache.get_symbols(lib_file) is not None  # warm after scan

    def test_empty_dir_returns_empty(self, tmp_path: Path) -> None:
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        cache = SymbolCache(db_path=tmp_path / "cache.db")
        assert _scan_dir_with_cache(empty_dir, ["anything"], cache) == []


class TestGrepMatchingFiles:
    def test_grep_timeout_raises_user_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)

        def _run_timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="grep", timeout=10)

        monkeypatch.setattr(subprocess, "run", _run_timeout)

        with pytest.raises(UserError) as exc_info:
            _grep_matching_files(sym_dir, ["testcomp"])

        assert exc_info.value.code == ErrorCode.IO_ERROR

    def test_grep_exec_failure_raises_user_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)

        def _run_oserror(*args, **kwargs):
            raise OSError("grep unavailable")

        monkeypatch.setattr(subprocess, "run", _run_oserror)

        with pytest.raises(UserError) as exc_info:
            _grep_matching_files(sym_dir, ["testcomp"])

        assert exc_info.value.code == ErrorCode.IO_ERROR


# ---------------------------------------------------------------------------
# cmd_build_symbol_index
# ---------------------------------------------------------------------------


class TestCmdBuildSymbolIndex:
    def test_indexes_all_files_on_cold_cache(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", str(sym_dir))
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(symbols_dir=str(sym_dir))
        result = cmd_build_symbol_index(args)

        assert result.files_scanned >= 1
        assert result.files_updated >= 1
        assert result.total_indexed_symbols >= 2  # TestComp + AnotherComp

    def test_no_update_on_warm_cache(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(symbols_dir=str(sym_dir))
        result1 = cmd_build_symbol_index(args)
        result2 = cmd_build_symbol_index(args)

        assert result1.files_updated >= 1
        assert result2.files_updated == 0  # already cached

    def test_dirs_scanned_listed_in_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(symbols_dir=str(sym_dir))
        result = cmd_build_symbol_index(args)

        assert str(sym_dir) in result.dirs_scanned


# ---------------------------------------------------------------------------
# cmd_search_symbols — cache integration
# ---------------------------------------------------------------------------


class TestCmdSearchSymbolsCacheIntegration:
    def test_finds_symbol_via_cached_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(query="testcomp", symbols_dir=str(sym_dir), limit=20)
        result = cmd_search_symbols(args)

        sym_ids = [m.symbol_id for m in result.matches]
        assert any("TestLib:TestComp" in sid for sid in sym_ids)

    def test_warm_cache_gives_same_result_as_cold(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir, _ = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(query="testcomp", symbols_dir=str(sym_dir), limit=20)
        result_cold = cmd_search_symbols(args)
        result_warm = cmd_search_symbols(args)

        assert result_cold.matches == result_warm.matches

    def test_explicit_symbols_dir_scoped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --symbols-dir is given, only that directory is searched."""
        sym_dir, _ = _make_sym_dir(tmp_path)
        other_dir = tmp_path / "other"
        other_dir.mkdir()
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(query="testcomp", symbols_dir=str(other_dir), limit=20)
        result = cmd_search_symbols(args)

        # other_dir is empty, so no matches
        assert result.matches == ()
        assert result.symbols_dirs == (str(other_dir),)


# ---------------------------------------------------------------------------
# Extends-symbol pin-count (P0-A regression tests)
# ---------------------------------------------------------------------------

# The TestLib fixture contains:
#   - OpAmp         – 4 pins (in-body pin declarations)
#   - DerivedOpAmp  – 0 own pins; inherits from OpAmp via (extends "OpAmp")
# Before the fix, _count_pins_in_block returned 0 for DerivedOpAmp because its
# block has no "(pin " entries.  After the fix, read_lib_symbol_pins walks the
# extends chain and produces the correct count.
_FIXTURE_SYMBOLS_DIR = Path(__file__).parent.parent / "fixtures" / "symbols"
_FIXTURE_LIB = _FIXTURE_SYMBOLS_DIR / "TestLib.kicad_sym"


class TestExtendsSymbolPinCount:
    def test_parse_extends_symbol_reports_parent_pin_count(self) -> None:
        """_parse_file_to_cached must follow (extends ...) and count parent pins."""
        symbols = _parse_file_to_cached(_FIXTURE_LIB)
        by_name = {s.sym_name: s for s in symbols}
        assert "DerivedOpAmp" in by_name, "fixture is missing DerivedOpAmp"
        assert by_name["DerivedOpAmp"].pin_count == 4, (
            f"expected 4 (inherited from OpAmp), got {by_name['DerivedOpAmp'].pin_count}"
        )

    def test_search_symbols_extends_pin_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """cmd_search_symbols must return a non-zero pin_count for an extends symbol."""
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        args = SimpleNamespace(
            query="derivedopamp",
            symbols_dir=str(_FIXTURE_SYMBOLS_DIR),
            limit=20,
        )
        result = cmd_search_symbols(args)

        matches = {m.symbol_id: m for m in result.matches}
        assert any("DerivedOpAmp" in sid for sid in matches), (
            f"DerivedOpAmp not found in results: {list(matches)}"
        )
        derived = next(m for sid, m in matches.items() if "DerivedOpAmp" in sid)
        assert derived.pin_count == 4, f"expected pin_count=4 (inherited), got {derived.pin_count}"

    def test_build_index_then_search_extends_pin_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Full pipeline: build-symbol-index followed by search must yield correct pin count."""
        monkeypatch.setenv("KICAD_PCB_CACHE_DIR", str(tmp_path / "cache"))

        build_args = SimpleNamespace(symbols_dir=str(_FIXTURE_SYMBOLS_DIR))
        build_result = cmd_build_symbol_index(build_args)
        assert build_result.files_updated >= 1, "expected at least one file indexed"

        search_args = SimpleNamespace(
            query="derivedopamp",
            symbols_dir=str(_FIXTURE_SYMBOLS_DIR),
            limit=20,
        )
        result = cmd_search_symbols(search_args)

        matches = {m.symbol_id: m for m in result.matches}
        assert any("DerivedOpAmp" in sid for sid in matches), (
            f"DerivedOpAmp not found after index build: {list(matches)}"
        )
        derived = next(m for sid, m in matches.items() if "DerivedOpAmp" in sid)
        assert derived.pin_count == 4, (
            f"expected pin_count=4 after warm cache, got {derived.pin_count}"
        )
