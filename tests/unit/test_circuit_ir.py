from __future__ import annotations

import json
from pathlib import Path

import pytest
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import validate_circuit_ir, validate_ir_symbols
from kicad_pcb.symbol_index import SymbolIndex


def _write(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_circuit_ir_load_valid(tmp_path: Path) -> None:
    ir_path = _write(
        tmp_path / "ok.json",
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
        },
    )

    ir = CircuitIR.load(ir_path)

    assert ir.version == "1"
    assert ir.components[0].ref == "R1"
    assert "\n" in ir.dumps()


def test_circuit_ir_load_invalid_schema(tmp_path: Path) -> None:
    ir_path = _write(tmp_path / "bad.json", {"version": "1", "components": [], "nets": []})

    with pytest.raises(UserError) as exc_info:
        CircuitIR.load(ir_path)

    assert exc_info.value.code == ErrorCode.IR_SCHEMA_INVALID


def test_validate_circuit_ir_duplicate_refs() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "R1", "symbol": "TestLib:R"},
                {"ref": "R1", "symbol": "TestLib:R"},
            ],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID


def test_validate_circuit_ir_pin_collision() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "TestLib:R"}],
            "nets": [
                {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]},
                {"name": "N2", "pins": [{"ref": "R1", "pin": "1"}]},
            ],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert "pin_collisions" in exc_info.value.details


def test_validate_circuit_ir_unknown_component_ref() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "TestLib:R"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R9", "pin": "1"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert "missing_component_refs" in exc_info.value.details


def test_validate_ir_symbols_rejects_invalid_pin() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=fixtures_dir)
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "TestLib:R"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "99"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_ir_symbols(ir, symbol_index)

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert exc_info.value.details["symbol"] == "TestLib:R"


def test_validate_ir_symbols_rejects_non_null_unit() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=fixtures_dir)
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "TestLib:R"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1", "unit": "A"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_ir_symbols(ir, symbol_index)

    assert exc_info.value.code == ErrorCode.MULTI_UNIT_UNSUPPORTED
