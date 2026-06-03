"""Tests for validate_ir_symbols unknown-symbol handling (placeholder path)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import (
    IrSymbolValidationResult,
    validate_circuit_ir,
    validate_ir_symbols,
)
from kicad_pcb.symbol_index import SymbolIndex


def _load_ir(netlist_dict: dict) -> CircuitIR:
    return CircuitIR.model_validate(netlist_dict)


def _make_index(symbols_dir: Path | None = None) -> SymbolIndex:
    return SymbolIndex(symbols_dir=symbols_dir)


def _minimal_ir(symbol_id: str, pins: list[str]) -> dict:
    nets = [{"name": f"N{i}", "pins": [{"ref": "U1", "pin": p}]} for i, p in enumerate(pins)]
    return {
        "version": "1",
        "components": [{"ref": "U1", "symbol": symbol_id, "value": "test"}],
        "nets": nets,
    }


# ---------------------------------------------------------------------------
# Unknown symbol → warning result, not raise
# ---------------------------------------------------------------------------


def test_unknown_symbol_returns_result_not_raises() -> None:
    ir = _load_ir(_minimal_ir("NonExistentLib:NoSuchPart", ["1", "2"]))
    result = validate_ir_symbols(ir, _make_index())
    assert isinstance(result, IrSymbolValidationResult)
    assert "NonExistentLib:NoSuchPart" in result.unknown_symbols


def test_unknown_symbol_pins_from_ir() -> None:
    ir = _load_ir(_minimal_ir("Fake:Part", ["3", "7", "14"]))
    result = validate_ir_symbols(ir, _make_index())
    assert result.unknown_symbols["Fake:Part"] == frozenset({"3", "7", "14"})


def test_unknown_symbol_uses_only_ir_pins() -> None:
    ir = _load_ir(_minimal_ir("Fake:Part", ["1", "2"]))
    result = validate_ir_symbols(ir, _make_index())
    assert result.unknown_symbols["Fake:Part"] == frozenset({"1", "2"})


def test_no_unknown_symbols_returns_empty_dict() -> None:
    symbols_dir = Path("tests/fixtures/symbols")
    if not symbols_dir.is_dir():
        pytest.skip("Test symbols directory not available")
    ir = _load_ir({
        "version": "1",
        "components": [{"ref": "R1", "symbol": "Device:R", "value": "10k"}],
        "nets": [
            {"name": "A", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    })
    result = validate_ir_symbols(ir, _make_index(symbols_dir))
    assert result.unknown_symbols == {}


def test_multiple_unknown_symbols_all_collected() -> None:
    ir = _load_ir({
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "FakeLib:PartA", "value": "A"},
            {"ref": "U2", "symbol": "FakeLib:PartB", "value": "B"},
        ],
        "nets": [
            {"name": "NET1", "pins": [{"ref": "U1", "pin": "1"}]},
            {"name": "NET2", "pins": [{"ref": "U2", "pin": "2"}]},
        ],
    })
    result = validate_ir_symbols(ir, _make_index())
    assert "FakeLib:PartA" in result.unknown_symbols
    assert "FakeLib:PartB" in result.unknown_symbols


# ---------------------------------------------------------------------------
# Pin collision still caught at validate_circuit_ir layer (before symbols)
# ---------------------------------------------------------------------------


def test_pin_collision_raises_before_symbol_lookup() -> None:
    ir = _load_ir({
        "version": "1",
        "components": [{"ref": "U1", "symbol": "Fake:Part", "value": "v"}],
        "nets": [
            {"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]},
            {"name": "N2", "pins": [{"ref": "U1", "pin": "1"}]},
        ],
    })
    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)
    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID


# ---------------------------------------------------------------------------
# Non-SYMBOL_NOT_FOUND errors still propagate
# ---------------------------------------------------------------------------


def test_non_symbol_not_found_error_propagates() -> None:
    ir = _load_ir(_minimal_ir("Lib:Part", ["1"]))
    idx = _make_index()

    def _raise_io(*_args, **_kwargs):
        raise UserError("io problem", code=ErrorCode.IO_ERROR, details={})

    with patch.object(idx, "get_pins", side_effect=_raise_io):
        with pytest.raises(UserError) as exc_info:
            validate_ir_symbols(ir, idx)
        assert exc_info.value.code == ErrorCode.IO_ERROR


# ---------------------------------------------------------------------------
# After registering placeholder, symbol no longer appears as unknown
# ---------------------------------------------------------------------------


def test_registered_placeholder_not_in_unknown_symbols() -> None:
    from kicad_pcb import placeholder_symbol

    sym_id = "FakeLib:FakePart"
    ir = _load_ir(_minimal_ir(sym_id, ["1", "2", "3"]))
    idx = _make_index()

    result1 = validate_ir_symbols(ir, idx)
    assert sym_id in result1.unknown_symbols

    ph = placeholder_symbol.build(sym_id, result1.unknown_symbols[sym_id])
    idx.register_placeholder(sym_id, result1.unknown_symbols[sym_id], ph.pin_at)

    result2 = validate_ir_symbols(ir, idx)
    assert sym_id not in result2.unknown_symbols
