from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import (
    build_pin_membership_index,
    validate_circuit_ir,
    validate_ir_symbols,
)
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
    collision = exc_info.value.details["pin_collisions"][0]
    assert collision["ref"] == "R1"
    assert collision["pin"] == "1"
    assert collision["nets"] == ["N1", "N2"]
    assert collision["assignments"] == [
        {"net": "N1", "unit": None, "net_index": 0, "pin_index": 0},
        {"net": "N2", "unit": None, "net_index": 1, "pin_index": 0},
    ]


def test_build_pin_membership_index_tracks_assignment_sources() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp"}],
            "nets": [
                {"name": "IN_A", "pins": [{"ref": "U1", "pin": "1", "unit": "1"}]},
                {"name": "IN_B", "pins": [{"ref": "U1", "pin": "5", "unit": "2"}]},
            ],
        }
    )

    index = build_pin_membership_index(ir)

    assert index[("U1", "1")][0].net_name == "IN_A"
    assert index[("U1", "1")][0].unit == "1"
    assert index[("U1", "1")][0].net_index == 0
    assert index[("U1", "1")][0].pin_index == 0
    assert index[("U1", "5")][0].net_name == "IN_B"
    assert index[("U1", "5")][0].unit == "2"
    assert index[("U1", "5")][0].net_index == 1
    assert index[("U1", "5")][0].pin_index == 0


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


def test_validate_ir_symbols_accepts_valid_explicit_unit() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=fixtures_dir)
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "1"}]}],
        }
    )

    validate_ir_symbols(ir, symbol_index)


def test_validate_ir_symbols_accepts_unconnected_pin_free_symbol(tmp_path: Path) -> None:
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
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=tmp_path, fallback_dirs=[fixtures_dir])
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "H1", "symbol": "Mechanical:MountingHole"},
                {"ref": "R1", "symbol": "TestLib:R"},
                {"ref": "R2", "symbol": "TestLib:R"},
            ],
            "nets": [
                {
                    "name": "N1",
                    "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}],
                }
            ],
        }
    )

    validate_ir_symbols(ir, symbol_index)


def test_validate_ir_symbols_rejects_unknown_explicit_unit() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=fixtures_dir)
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "9"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_ir_symbols(ir, symbol_index)

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert exc_info.value.details["valid_units"] == ["1", "2", "3"]


def test_validate_ir_symbols_rejects_pin_outside_selected_unit() -> None:
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    symbol_index = SymbolIndex(symbols_dir=fixtures_dir)
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "2"}]}],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_ir_symbols(ir, symbol_index)

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert exc_info.value.details["valid_unit_pins"] == ["5", "6", "7"]
