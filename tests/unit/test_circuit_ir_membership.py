from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.validate import (
    build_pin_membership_index,
    find_pin_membership_collisions,
    validate_ir_symbols,
)
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit


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
