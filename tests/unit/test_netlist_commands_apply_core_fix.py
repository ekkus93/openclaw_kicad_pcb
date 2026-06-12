"""Netlist commands: fix_netlist, resolve_schematic_paths, and apply_netlist core tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_fix_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from tests import SYMBOLS_FIXTURE_DIR


class _FakeLayoutEngine:
    def __init__(self, placements: dict[str, tuple[float, float, float | None]]) -> None:
        self._placements = placements

    def compute_symbol_positions(
        self,
        ir: CircuitIR,
    ) -> dict[str, tuple[float, float, float | None]]:
        return {
            component.ref: self._placements.get(component.ref, (50.8, 76.2, 0.0))
            for component in ir.components
        }


def _write_minimal_sch(path: Path) -> None:
    path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "12345678-1234-1234-1234-123456789012")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
""",
        encoding="utf-8",
    )


def _write_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_pin_collision_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [
            {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "N2", "pins": [{"ref": "R1", "pin": "1"}]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_legacy_555_side_format(path: Path) -> None:
    payload = {
        "version": 2,
        "designName": "555_PWM_LED_Dimmer",
        "components": [
            {
                "ref": "U1",
                "name": "NE555",
                "description": "Timer IC",
                "footprint": "Package_DIP:DIP-8_W7.62mm",
                "pins": [
                    {"num": 1, "name": "GND"},
                    {"num": 2, "name": "TRIG"},
                    {"num": 3, "name": "OUT"},
                    {"num": 4, "name": "RESET"},
                    {"num": 5, "name": "CTRL"},
                    {"num": 6, "name": "THRES"},
                    {"num": 7, "name": "DISCH"},
                    {"num": 8, "name": "VCC"},
                ],
            },
            {
                "ref": "Q1",
                "name": "AO3400",
                "description": "Logic-level N-MOSFET",
                "footprint": "Package_TO_SOT_SMD:SOT-23",
                "pins": [
                    {"num": 1, "name": "G"},
                    {"num": 2, "name": "S"},
                    {"num": 3, "name": "D"},
                ],
            },
            {
                "ref": "RV1",
                "name": "POT",
                "description": "100k potentiometer",
                "footprint": "Potentiometer_THT:Potentiometer_Bourns_3386P_Vertical",
                "value": "100k",
                "pins": [{"num": 1, "name": "1"}, {"num": 2, "name": "2"}, {"num": 3, "name": "3"}],
            },
            {
                "ref": "D1",
                "name": "1N4148",
                "description": "Diode",
                "footprint": "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
                "pins": [{"num": 1, "name": "K"}, {"num": 2, "name": "A"}],
            },
            {
                "ref": "D2",
                "name": "1N4148",
                "description": "Diode",
                "footprint": "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
                "pins": [{"num": 1, "name": "K"}, {"num": 2, "name": "A"}],
            },
            {
                "ref": "R1",
                "name": "RES",
                "description": "Series resistor",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                "value": "1k",
                "pins": [{"num": 1, "name": "1"}, {"num": 2, "name": "2"}],
            },
            {
                "ref": "R2",
                "name": "RES",
                "description": "Gate resistor",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                "value": "100",
                "pins": [{"num": 1, "name": "1"}, {"num": 2, "name": "2"}],
            },
            {
                "ref": "R3",
                "name": "RES",
                "description": "Gate pulldown",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                "value": "100k",
                "pins": [{"num": 1, "name": "1"}, {"num": 2, "name": "2"}],
            },
            {
                "ref": "C1",
                "name": "CAP",
                "description": "Timing capacitor",
                "footprint": "Capacitor_THT:C_Disc_D3.0mm_W1.6mm_P2.50mm",
                "value": "22nF",
                "pins": [{"num": 1, "name": "+"}, {"num": 2, "name": "-"}],
            },
            {
                "ref": "C2",
                "name": "CAP",
                "description": "555 decoupling capacitor",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                "value": "100nF",
                "pins": [{"num": 1, "name": "+"}, {"num": 2, "name": "-"}],
            },
            {
                "ref": "C3",
                "name": "CAP",
                "description": "Bulk capacitor",
                "footprint": "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm",
                "value": "47uF",
                "pins": [{"num": 1, "name": "+"}, {"num": 2, "name": "-"}],
            },
            {
                "ref": "LED_LOAD",
                "name": "CONN_2",
                "description": "LED load connector",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                "pins": [{"num": 1, "name": "POS"}, {"num": 2, "name": "NEG"}],
            },
        ],
        "nets": [
            {
                "net": 0,
                "description": "GND",
                "connections": [
                    {"component": "U1", "pin": 1},
                    {"component": "Q1", "pin": 2},
                    {"component": "R3", "pin": 2},
                    {"component": "C1", "pin": 2},
                    {"component": "C2", "pin": 2},
                    {"component": "C3", "pin": 2},
                ],
            },
            {
                "net": 1,
                "description": "+12V",
                "connections": [
                    {"component": "U1", "pin": 8},
                    {"component": "U1", "pin": 4},
                    {"component": "R1", "pin": 1},
                    {"component": "C2", "pin": 1},
                    {"component": "C3", "pin": 1},
                    {"component": "LED_LOAD", "pin": 1},
                ],
            },
            {
                "net": 2,
                "description": "TIMING",
                "connections": [
                    {"component": "U1", "pin": 2},
                    {"component": "U1", "pin": 6},
                    {"component": "RV1", "pin": 2},
                    {"component": "C1", "pin": 1},
                ],
            },
            {
                "net": 3,
                "description": "DISCH",
                "connections": [
                    {"component": "U1", "pin": 7},
                    {"component": "R1", "pin": 2},
                    {"component": "D1", "pin": 1},
                    {"component": "D2", "pin": 2},
                ],
            },
            {
                "net": 4,
                "description": "POT_A",
                "connections": [{"component": "RV1", "pin": 1}, {"component": "D1", "pin": 2}],
            },
            {
                "net": 5,
                "description": "POT_B",
                "connections": [{"component": "RV1", "pin": 3}, {"component": "D2", "pin": 1}],
            },
            {"net": 6, "description": "CTRL", "connections": [{"component": "U1", "pin": 5}]},
            {
                "net": 7,
                "description": "OUT_DRV",
                "connections": [{"component": "U1", "pin": 3}, {"component": "R2", "pin": 1}],
            },
            {
                "net": 8,
                "description": "GATE",
                "connections": [
                    {"component": "R2", "pin": 2},
                    {"component": "Q1", "pin": 1},
                    {"component": "R3", "pin": 1},
                ],
            },
            {
                "net": 9,
                "description": "LED_NEG",
                "connections": [{"component": "Q1", "pin": 3}, {"component": "LED_LOAD", "pin": 2}],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_input_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:R", "value": "Input"},
            {"ref": "C5", "symbol": "TestLib:R", "value": "1u"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "100k"},
            {"ref": "RV1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "LEFT_IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "C5", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "IN_L_AC",
                "pins": [
                    {"ref": "C5", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "RV1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "RV1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_tail_profile_diff_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "R6", "symbol": "TestLib:R", "value": "47"},
            {"ref": "C7", "symbol": "TestLib:R", "value": "100n"},
            {"ref": "R7", "symbol": "TestLib:R", "value": "100"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "OUT"},
        ],
        "nets": [
            {
                "name": "AFTER_R6",
                "pins": [
                    {"ref": "R6", "pin": "2"},
                    {"ref": "C7", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C7", "pin": "2"},
                    {"ref": "R7", "pin": "1"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_ground_cluster_profile_diff_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "OUT"},
            {"ref": "R5", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R7", "symbol": "TestLib:R", "value": "100"},
        ],
        "nets": [
            {
                "name": "GND",
                "pins": [
                    {"ref": "J2", "pin": "3"},
                    {"ref": "R5", "pin": "2"},
                    {"ref": "R7", "pin": "2"},
                ],
            }
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_explicit_unit_valid_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:DualOpAmp", "value": "DualOpAmp"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "IN_A",
                "pins": [
                    {"ref": "U1", "pin": "1", "unit": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "OUT_A",
                "pins": [
                    {"ref": "U1", "pin": "3", "unit": "1"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_fix_netlist_raises_when_symbol_index_init_fails(tmp_path: Path, monkeypatch) -> None:
    """A5: fix-netlist must fail fast if SymbolIndex construction fails."""
    netlist_path = tmp_path / "ir.json"
    _write_ir(netlist_path)
    output_path = tmp_path / "ir.fixed.json"

    class _BrokenSymbolIndex:
        def __init__(
            self,
            *,
            symbols_dir: Path | None = None,
            fallback_dirs: list[Path] | None = None,
        ) -> None:
            del symbols_dir, fallback_dirs
            raise UserError(
                "symbol index init failed",
                code=ErrorCode.SYMBOL_DIR_MISSING,
            )

    monkeypatch.setattr("kicad_pcb.commands.netlist.SymbolIndex", _BrokenSymbolIndex)

    with pytest.raises(UserError) as exc_info:
        cmd_fix_netlist(
            Namespace(
                netlist=str(netlist_path),
                symbols_dir=str(tmp_path / "symbols"),
                output=str(output_path),
            )
        )

    assert exc_info.value.code == ErrorCode.SYMBOL_DIR_MISSING
    assert not output_path.exists()


def test_fix_netlist_converts_legacy_555_side_format(tmp_path: Path) -> None:
    legacy_path = tmp_path / "555_PWM_LED_Dimmer.net"
    output_path = tmp_path / "555_PWM_LED_Dimmer.fixed.json"
    _write_legacy_555_side_format(legacy_path)

    result = cmd_fix_netlist(
        Namespace(
            netlist=str(legacy_path),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
            output=str(output_path),
        )
    )

    assert result.fixed is True
    fixed_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert fixed_payload["components"][0]["symbol"] == "Timer:NE555"
    assert fixed_payload["components"][1]["symbol"] == "Transistor_FET:Q_NMOS_GSD"
    assert fixed_payload["components"][2]["symbol"] == "Device:R_Potentiometer"
    assert fixed_payload["components"][-1]["symbol"] == "Connector_Generic:Conn_01x02"
    assert fixed_payload["nets"][0]["name"] == "GND"
    assert fixed_payload["nets"][0]["pins"][0] == {"ref": "U1", "pin": "1"}
    assert any(
        "converted legacy component/name + net/connections payload into canonical Circuit IR" in fix
        for fix in result.fixes_applied
    )
