from __future__ import annotations

import pytest
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.ir.autofix import autofix_circuit_ir


class _SymbolIndexUserError:
    def get_pins(self, symbol_id: str) -> set[str]:
        raise UserError(
            f"symbol lookup failed for {symbol_id}",
            code=ErrorCode.SYMBOL_NOT_FOUND,
        )


class _SymbolIndexRuntimeError:
    def get_pins(self, symbol_id: str) -> set[str]:
        raise RuntimeError(f"unexpected resolver failure for {symbol_id}")


class _SymbolIndexOk:
    def get_pins(self, symbol_id: str) -> set[str]:
        return {"1", "2"}


def _raw_alias_payload() -> dict[str, object]:
    return {
        "version": "1",
        "components": [{"ref": "C1", "symbol": "Device:C"}],
        "nets": [{"name": "N1", "pins": [{"ref": "C1", "pin": "PLUS"}]}],
    }


def test_autofix_reports_symbol_lookup_user_error_in_remaining_errors() -> None:
    outcome = autofix_circuit_ir(_raw_alias_payload(), symbol_index=_SymbolIndexUserError())

    assert any("pin lookup failed" in err for err in outcome.remaining_errors), (
        "Expected alias layer to surface symbol lookup failure in remaining_errors"
    )


def test_autofix_does_not_swallow_unexpected_pin_lookup_exception() -> None:
    with pytest.raises(RuntimeError, match="unexpected resolver failure"):
        autofix_circuit_ir(_raw_alias_payload(), symbol_index=_SymbolIndexRuntimeError())


def test_autofix_still_applies_alias_when_pin_lookup_succeeds() -> None:
    outcome = autofix_circuit_ir(_raw_alias_payload(), symbol_index=_SymbolIndexOk())

    pin = outcome.ir_dict["nets"][0]["pins"][0]["pin"]  # type: ignore[index]
    assert pin == "1"
    assert not outcome.remaining_errors
