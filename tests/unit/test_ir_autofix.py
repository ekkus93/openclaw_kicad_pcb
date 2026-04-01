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


def _raw_legacy_555_payload() -> dict[str, object]:
    return {
        "version": 2,
        "designName": "555_PWM_LED_Dimmer",
        "components": [
            {
                "ref": "U1",
                "name": "NE555",
                "description": "Timer IC",
                "footprint": "Package_DIP:DIP-8_W7.62mm",
                "pins": [{"num": 1, "name": "GND"}, {"num": 8, "name": "VCC"}],
            },
            {
                "ref": "R1",
                "name": "RES",
                "value": "1k",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                "pins": [{"num": 1, "name": "1"}, {"num": 2, "name": "2"}],
            },
            {
                "ref": "LED_LOAD",
                "name": "CONN_2",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                "pins": [{"num": 1, "name": "POS"}, {"num": 2, "name": "NEG"}],
            },
        ],
        "nets": [
            {
                "net": 0,
                "description": "GND",
                "connections": [{"component": "U1", "pin": 1}],
            },
            {
                "net": 1,
                "description": "+12V",
                "connections": [
                    {"component": "U1", "pin": 8},
                    {"component": "R1", "pin": 1},
                    {"component": "LED_LOAD", "pin": 1},
                ],
            },
        ],
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


def test_autofix_converts_legacy_connection_payload_to_circuit_ir() -> None:
    outcome = autofix_circuit_ir(_raw_legacy_555_payload())

    assert (
        "converted legacy component/name + net/connections payload into canonical Circuit IR"
        in (outcome.fixes_applied)
    )
    assert outcome.ir_dict["version"] == "1"
    assert outcome.ir_dict["components"] == [
        {
            "ref": "U1",
            "symbol": "Timer:NE555",
            "value": "NE555",
            "footprint": "Package_DIP:DIP-8_W7.62mm",
            "fields": {"LegacyDescription": "Timer IC"},
        },
        {
            "ref": "R1",
            "symbol": "Device:R",
            "value": "1k",
            "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
        },
        {
            "ref": "J1",
            "symbol": "Connector_Generic:Conn_01x02",
            "value": "LED_LOAD",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            "fields": {"LegacyRef": "LED_LOAD"},
        },
    ]
    assert outcome.ir_dict["nets"] == [
        {"name": "GND", "pins": [{"ref": "U1", "pin": "1"}]},
        {
            "name": "+12V",
            "pins": [
                {"ref": "U1", "pin": "8"},
                {"ref": "R1", "pin": "1"},
                {"ref": "J1", "pin": "1"},
            ],
        },
    ]
    assert not outcome.remaining_errors
