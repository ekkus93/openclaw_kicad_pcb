from __future__ import annotations

from pathlib import Path

import pytest

import kicad_pcb.symbol_index as si_mod
from kicad_pcb import placeholder_symbol
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import validate_ir_symbols
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


def test_symbol_index_reads_power_unit_from_explicit_dir(fixture_symbols_dir: Path) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    assert index.get_power_unit("TestLib:DualOpAmp") == "3"


def test_symbol_index_returns_empty_unit_pins_for_single_unit_symbol(
    fixture_symbols_dir: Path,
) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    assert index.get_unit_pins("TestLib:R") == {}


def test_symbol_index_returns_none_when_symbol_has_no_power_unit(
    fixture_symbols_dir: Path,
) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    assert index.get_power_unit("TestLib:R") is None


def test_symbol_index_missing_symbol_raises_coded_error(fixture_symbols_dir: Path) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)

    with pytest.raises(UserError) as exc_info:
        index.get_pins("TestLib:DoesNotExist")

    assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND
    assert "searched_dirs" in exc_info.value.details


def test_symbol_index_accepts_declared_pin_free_symbol(tmp_path: Path) -> None:
    (tmp_path / "Mechanical.kicad_sym").write_text(
        """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "MountingHole"
    (property "Reference" "H" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "MountingHole" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
""",
        encoding="utf-8",
    )
    index = SymbolIndex(symbols_dir=tmp_path)

    assert index.get_pins("Mechanical:MountingHole") == set()


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
    monkeypatch.setattr(si_mod, "read_lib_symbol_def_chain", lambda *args, **kwargs: [])

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


def test_symbol_index_caches_power_unit_reads(
    fixture_symbols_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    index = SymbolIndex(symbols_dir=fixture_symbols_dir)
    calls = {"count": 0}
    original = si_mod.read_lib_symbol_power_unit

    def _wrapped(*args, **kwargs):
        calls["count"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(si_mod, "read_lib_symbol_power_unit", _wrapped)

    first = index.get_power_unit("TestLib:DualOpAmp")
    second = index.get_power_unit("TestLib:DualOpAmp")

    assert first == second == "3"
    assert calls["count"] == 1


# ---------------------------------------------------------------------------
# Section 7.1 — register_placeholder: cache population
# ---------------------------------------------------------------------------


def _build_placeholder(sym_id: str, pins: list[str]) -> placeholder_symbol.PlaceholderSymbol:
    return placeholder_symbol.build(sym_id, frozenset(pins))


def test_register_placeholder_get_pins(fixture_symbols_dir: Path) -> None:
    idx = SymbolIndex(symbols_dir=fixture_symbols_dir)
    ph = _build_placeholder("FakeLib:FakePart", ["1", "2", "3"])
    idx.register_placeholder("FakeLib:FakePart", ph.pin_numbers, ph.pin_at)
    assert idx.get_pins("FakeLib:FakePart") == {"1", "2", "3"}


def test_register_placeholder_get_unit_pins(fixture_symbols_dir: Path) -> None:
    idx = SymbolIndex(symbols_dir=fixture_symbols_dir)
    ph = _build_placeholder("FakeLib:FakePart", ["A", "B"])
    idx.register_placeholder("FakeLib:FakePart", ph.pin_numbers, ph.pin_at)
    unit_pins = idx.get_unit_pins("FakeLib:FakePart")
    assert "1" in unit_pins
    assert set(unit_pins["1"]) == {"A", "B"}


def test_register_placeholder_get_unit_pin_at(fixture_symbols_dir: Path) -> None:
    idx = SymbolIndex(symbols_dir=fixture_symbols_dir)
    ph = _build_placeholder("FakeLib:FakePart", ["1", "2", "3", "4"])
    idx.register_placeholder("FakeLib:FakePart", ph.pin_numbers, ph.pin_at)
    unit_pin_at = idx.get_unit_pin_at("FakeLib:FakePart")
    assert "1" in unit_pin_at
    assert set(unit_pin_at["1"].keys()) == {"1", "2", "3", "4"}
    for _pin_num, (x, y, angle) in unit_pin_at["1"].items():
        assert isinstance(x, float)
        assert isinstance(y, float)
        assert angle in (0.0, 180.0)


def test_register_placeholder_overwrites_cache(fixture_symbols_dir: Path) -> None:
    """Re-registering a placeholder replaces the cached data."""
    idx = SymbolIndex(symbols_dir=fixture_symbols_dir)
    ph1 = _build_placeholder("FakeLib:FakePart", ["1", "2"])
    ph2 = _build_placeholder("FakeLib:FakePart", ["1", "2", "3"])
    idx.register_placeholder("FakeLib:FakePart", ph1.pin_numbers, ph1.pin_at)
    idx.register_placeholder("FakeLib:FakePart", ph2.pin_numbers, ph2.pin_at)
    assert idx.get_pins("FakeLib:FakePart") == {"1", "2", "3"}


# ---------------------------------------------------------------------------
# Section 7.2 — register_placeholder: downstream validate_ir_symbols
# ---------------------------------------------------------------------------


def test_register_placeholder_not_in_unknown_symbols_after_registration(
    fixture_symbols_dir: Path,
) -> None:
    sym_id = "NoSuchLib:NoSuchPart"
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": sym_id, "value": "v"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "5"}]}],
        }
    )
    idx = SymbolIndex(symbols_dir=fixture_symbols_dir)

    # Before registration: symbol appears as unknown
    result_before = validate_ir_symbols(ir, idx)
    assert sym_id in result_before.unknown_symbols

    # Register placeholder and re-run
    ph = _build_placeholder(sym_id, result_before.unknown_symbols[sym_id])
    idx.register_placeholder(sym_id, result_before.unknown_symbols[sym_id], ph.pin_at)

    result_after = validate_ir_symbols(ir, idx)
    assert sym_id not in result_after.unknown_symbols
