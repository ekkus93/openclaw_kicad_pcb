from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import (
    build_pin_membership_index,
    find_pin_membership_collisions,
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


# ---------------------------------------------------------------------------
# Section 1.1 — validate_circuit_ir: unqualified symbol ID check
# ---------------------------------------------------------------------------


def test_validate_circuit_ir_rejects_unqualified_symbol() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "CD4017", "value": "v"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
        }
    )
    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)
    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert "unqualified_symbols" in exc_info.value.details


def test_validate_circuit_ir_unqualified_details_contain_ref_and_symbol() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "CD4017", "value": "v"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
        }
    )
    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)
    entry = exc_info.value.details["unqualified_symbols"][0]
    assert entry["ref"] == "U1"
    assert entry["symbol"] == "CD4017"


def test_validate_circuit_ir_reports_all_unqualified_symbols() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "U1", "symbol": "CD4017", "value": "v"},
                {"ref": "U2", "symbol": "NE555", "value": "v"},
                {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            ],
            "nets": [
                {"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]},
                {"name": "N2", "pins": [{"ref": "U2", "pin": "8"}]},
                {"name": "N3", "pins": [{"ref": "R1", "pin": "1"}]},
            ],
        }
    )
    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)
    refs = {e["ref"] for e in exc_info.value.details["unqualified_symbols"]}
    assert refs == {"U1", "U2"}


def test_validate_circuit_ir_reports_only_unqualified_not_qualified() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "U1", "symbol": "CD4017", "value": "v"},
                {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            ],
            "nets": [
                {"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]},
                {"name": "N2", "pins": [{"ref": "R1", "pin": "1"}]},
            ],
        }
    )
    with pytest.raises(UserError) as exc_info:
        validate_circuit_ir(ir)
    syms = {e["ref"] for e in exc_info.value.details["unqualified_symbols"]}
    assert "R1" not in syms
    assert "U1" in syms


def test_validate_circuit_ir_accepts_qualified_symbol() -> None:
    ir = CircuitIR.model_validate(
        {
            "version": "1",
            "components": [{"ref": "U1", "symbol": "4xxx:CD4017BE", "value": "v"}],
            "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
        }
    )
    # Should not raise for symbol format; may raise for other reasons but not
    # unqualified_symbols
    try:
        validate_circuit_ir(ir)
    except UserError as exc:
        assert "unqualified_symbols" not in exc.details


# ---------------------------------------------------------------------------
# Section 1.2 — find_pin_membership_collisions: direct tests
# ---------------------------------------------------------------------------


def _ir_from_nets(components: list[dict], nets: list[dict]) -> CircuitIR:
    return CircuitIR.model_validate({"version": "1", "components": components, "nets": nets})


def test_find_pin_membership_collisions_no_collision() -> None:

    ir = _ir_from_nets(
        [{"ref": "R1", "symbol": "TestLib:R"}],
        [
            {"name": "A", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    assert find_pin_membership_collisions(index) == []


def test_find_pin_membership_collisions_single_collision() -> None:

    ir = _ir_from_nets(
        [{"ref": "R1", "symbol": "TestLib:R"}],
        [
            {"name": "A", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": "1"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    collisions = find_pin_membership_collisions(index)
    assert len(collisions) == 1
    assert collisions[0]["ref"] == "R1"
    assert collisions[0]["pin"] == "1"
    assert collisions[0]["nets"] == ["A", "B"]
    assert len(collisions[0]["assignments"]) == 2


def test_find_pin_membership_collisions_multiple_independent() -> None:

    ir = _ir_from_nets(
        [
            {"ref": "R1", "symbol": "TestLib:R"},
            {"ref": "R2", "symbol": "TestLib:R"},
        ],
        [
            {"name": "A", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    collisions = find_pin_membership_collisions(index)
    assert len(collisions) == 2
    refs = {c["ref"] for c in collisions}
    assert refs == {"R1", "R2"}


def test_find_pin_membership_collisions_three_nets() -> None:

    ir = _ir_from_nets(
        [{"ref": "U1", "symbol": "TestLib:IC"}],
        [
            {"name": "X", "pins": [{"ref": "U1", "pin": "3"}]},
            {"name": "Y", "pins": [{"ref": "U1", "pin": "3"}]},
            {"name": "Z", "pins": [{"ref": "U1", "pin": "3"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    collisions = find_pin_membership_collisions(index)
    assert len(collisions) == 1
    assert collisions[0]["nets"] == ["X", "Y", "Z"]
    assert len(collisions[0]["assignments"]) == 3


def test_find_pin_membership_collisions_assignment_metadata() -> None:

    ir = _ir_from_nets(
        [{"ref": "U1", "symbol": "TestLib:IC"}],
        [
            {"name": "NET0", "pins": [{"ref": "U1", "pin": "5"}]},
            {"name": "NET1", "pins": [{"ref": "U1", "pin": "5"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    collisions = find_pin_membership_collisions(index)
    assignments = collisions[0]["assignments"]
    assert assignments[0]["net"] == "NET0"
    assert assignments[0]["net_index"] == 0
    assert assignments[0]["pin_index"] == 0
    assert assignments[1]["net"] == "NET1"
    assert assignments[1]["net_index"] == 1


# ---------------------------------------------------------------------------
# Section 1.3 — build_pin_membership_index: edge cases
# ---------------------------------------------------------------------------


def test_build_pin_membership_index_single_occurrence() -> None:
    ir = _ir_from_nets(
        [{"ref": "R1", "symbol": "TestLib:R"}],
        [{"name": "ONLY", "pins": [{"ref": "R1", "pin": "1"}]}],
    )
    index = build_pin_membership_index(ir)
    assert len(index[("R1", "1")]) == 1
    assert index[("R1", "1")][0].net_name == "ONLY"


def test_build_pin_membership_index_preserves_unit() -> None:
    ir = _ir_from_nets(
        [{"ref": "U1", "symbol": "TestLib:IC"}],
        [{"name": "A", "pins": [{"ref": "U1", "pin": "1", "unit": "2"}]}],
    )
    index = build_pin_membership_index(ir)
    assert index[("U1", "1")][0].unit == "2"


def test_build_pin_membership_index_distinct_pins_no_collision() -> None:
    ir = _ir_from_nets(
        [{"ref": "R1", "symbol": "TestLib:R"}],
        [
            {"name": "A", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "B", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    )
    index = build_pin_membership_index(ir)
    assert ("R1", "1") in index
    assert ("R1", "2") in index
    assert len(index[("R1", "1")]) == 1
    assert len(index[("R1", "2")]) == 1


def test_build_pin_membership_index_single_component_single_net() -> None:
    """Index has exactly one key when one pin appears in one net."""
    ir = _ir_from_nets(
        [{"ref": "C1", "symbol": "TestLib:C"}],
        [{"name": "VCC", "pins": [{"ref": "C1", "pin": "1"}]}],
    )
    index = build_pin_membership_index(ir)
    assert list(index.keys()) == [("C1", "1")]
    assert len(index[("C1", "1")]) == 1


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
