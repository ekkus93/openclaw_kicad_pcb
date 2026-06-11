"""cmd_new_from_netlist 555-timer autofix and lint rejection tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import SchematicDoc
from tests import SYMBOLS_FIXTURE_DIR


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


def _write_valid_555_pwm_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {
                "ref": "U1",
                "symbol": "Timer:NE555",
                "value": "NE555",
                "footprint": "Package_DIP:DIP-8_W7.62mm",
            },
            {
                "ref": "Q1",
                "symbol": "Transistor_FET:Q_NMOS_GSD",
                "value": "AO3400",
                "footprint": "Package_TO_SOT_SMD:SOT-23",
            },
            {
                "ref": "RV1",
                "symbol": "Device:R_Potentiometer",
                "value": "100k",
                "footprint": "Potentiometer_THT:Potentiometer_Bourns_3386P_Vertical",
            },
            {
                "ref": "D1",
                "symbol": "Device:D",
                "value": "1N4148",
                "footprint": "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
            },
            {
                "ref": "D2",
                "symbol": "Device:D",
                "value": "1N4148",
                "footprint": "Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
            },
            {
                "ref": "R1",
                "symbol": "Device:R",
                "value": "1k",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
            },
            {
                "ref": "R2",
                "symbol": "Device:R",
                "value": "100",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
            },
            {
                "ref": "R3",
                "symbol": "Device:R",
                "value": "100k",
                "footprint": "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
            },
            {
                "ref": "C1",
                "symbol": "Device:C",
                "value": "22nF",
                "footprint": "Capacitor_THT:C_Disc_D3.0mm_W1.6mm_P2.50mm",
            },
            {
                "ref": "C2",
                "symbol": "Device:C",
                "value": "100nF",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
            },
            {
                "ref": "C3",
                "symbol": "Device:C_Polarized",
                "value": "47uF",
                "footprint": "Capacitor_THT:CP_Radial_D5.0mm_P2.00mm",
            },
            {
                "ref": "C4",
                "symbol": "Device:C",
                "value": "10nF",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
            },
            {
                "ref": "J1",
                "symbol": "Connector_Generic:Conn_01x02",
                "value": "LED_LOAD",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
        ],
        "nets": [
            {
                "name": "GND",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "Q1", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                    {"ref": "C1", "pin": "2"},
                    {"ref": "C2", "pin": "2"},
                    {"ref": "C3", "pin": "2"},
                    {"ref": "C4", "pin": "2"},
                ],
            },
            {
                "name": "+12V",
                "pins": [
                    {"ref": "U1", "pin": "8"},
                    {"ref": "U1", "pin": "4"},
                    {"ref": "R1", "pin": "1"},
                    {"ref": "C2", "pin": "1"},
                    {"ref": "C3", "pin": "1"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "TIMING",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "U1", "pin": "6"},
                    {"ref": "RV1", "pin": "2"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "DISCH",
                "pins": [
                    {"ref": "U1", "pin": "7"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "D1", "pin": "1"},
                    {"ref": "D2", "pin": "2"},
                ],
            },
            {"name": "POT_A", "pins": [{"ref": "RV1", "pin": "1"}, {"ref": "D1", "pin": "2"}]},
            {"name": "POT_B", "pins": [{"ref": "RV1", "pin": "3"}, {"ref": "D2", "pin": "1"}]},
            {"name": "CTRL", "pins": [{"ref": "U1", "pin": "5"}, {"ref": "C4", "pin": "1"}]},
            {"name": "OUT_DRV", "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R2", "pin": "1"}]},
            {
                "name": "GATE",
                "pins": [
                    {"ref": "R2", "pin": "2"},
                    {"ref": "Q1", "pin": "1"},
                    {"ref": "R3", "pin": "1"},
                ],
            },
            {"name": "LED_NEG", "pins": [{"ref": "Q1", "pin": "3"}, {"ref": "J1", "pin": "2"}]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_invalid_555_pwm_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "Timer:NE555", "value": "NE555"},
            {"ref": "Q1", "symbol": "Transistor_FET:Q_NMOS_GSD", "value": "AO3400"},
            {"ref": "RV1", "symbol": "Device:R_Potentiometer", "value": "100k"},
            {"ref": "D1", "symbol": "Device:D", "value": "1N4148"},
            {"ref": "R1", "symbol": "Device:R", "value": "1k"},
            {"ref": "R2", "symbol": "Device:R", "value": "100"},
            {"ref": "R3", "symbol": "Device:R", "value": "100k"},
            {"ref": "C1", "symbol": "Device:C", "value": "10uF"},
            {"ref": "C2", "symbol": "Device:C", "value": "100nF"},
            {"ref": "J1", "symbol": "Connector_Generic:Conn_01x02", "value": "LED_LOAD"},
        ],
        "nets": [
            {
                "name": "GND",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "Q1", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J1", "pin": "2"},
                ],
            },
            {
                "name": "+12V",
                "pins": [
                    {"ref": "U1", "pin": "8"},
                    {"ref": "U1", "pin": "4"},
                    {"ref": "R1", "pin": "1"},
                    {"ref": "C1", "pin": "1"},
                    {"ref": "C2", "pin": "1"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {"name": "TRIG_ONLY", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "RV1", "pin": "2"}]},
            {"name": "THRESH_ONLY", "pins": [{"ref": "U1", "pin": "6"}]},
            {"name": "CTRL", "pins": [{"ref": "U1", "pin": "5"}, {"ref": "C2", "pin": "2"}]},
            {
                "name": "DISCH",
                "pins": [
                    {"ref": "U1", "pin": "7"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "D1", "pin": "1"},
                ],
            },
            {"name": "OUT_DRV", "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R2", "pin": "1"}]},
            {
                "name": "GATE",
                "pins": [
                    {"ref": "R2", "pin": "2"},
                    {"ref": "Q1", "pin": "1"},
                    {"ref": "R3", "pin": "1"},
                ],
            },
            {"name": "LED_NEG", "pins": [{"ref": "Q1", "pin": "3"}]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cmd_new_from_netlist_autofixes_legacy_555_side_format(tmp_path: Path) -> None:
    legacy_path = tmp_path / "555_PWM_LED_Dimmer.net"
    _write_legacy_555_side_format(legacy_path)
    project_path = tmp_path / "PwmDimmer555"

    result = cmd_new_from_netlist(
        Namespace(
            name="PwmDimmer555",
            out_dir=str(tmp_path),
            description="",
            netlist=str(legacy_path),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
            mode="internal",
            auto_fix=True,
            strict=False,
        )
    )

    assert result.path == project_path
    assert result.managed_schematic_path.exists()
    fixed_path = legacy_path.with_suffix(".autofix.json")
    assert fixed_path.exists()
    fixed_payload = json.loads(fixed_path.read_text(encoding="utf-8"))
    assert fixed_payload["components"][0]["symbol"] == "Timer:NE555"
    fixed_nets = {net["name"]: net["pins"] for net in fixed_payload["nets"]}
    assert {"TIMING", "DISCH", "POT_A", "POT_B", "CTRL", "GATE", "LED_NEG"} <= set(fixed_nets)
    assert {"ref": "C2", "pin": "1"} in fixed_nets["CTRL"]
    assert {"ref": "C2", "pin": "2"} in fixed_nets["GND"]
    assert {"ref": "D1", "pin": "1"} in fixed_nets["DISCH"]
    assert {"ref": "D2", "pin": "2"} in fixed_nets["DISCH"]
    assert {"ref": "RV1", "pin": "1"} in fixed_nets["POT_A"]
    assert {"ref": "RV1", "pin": "3"} in fixed_nets["POT_B"]
    assert result.generated_schematic_diagnostics is not None
    assert result.generated_schematic_diagnostics.symbol_count > 0
    codes = {warning["code"] for warning in result.warnings}
    assert "TIMER555_TIMING_NODE_SPLIT" not in codes
    assert "TIMER555_GATE_RESISTOR_MISSING" not in codes


def test_cmd_new_from_netlist_generates_valid_canonical_555_pwm_design(tmp_path: Path) -> None:
    ir_path = tmp_path / "555_pwm_ir.json"
    _write_valid_555_pwm_ir(ir_path)

    result = cmd_new_from_netlist(
        Namespace(
            name="Canonical555Pwm",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(SYMBOLS_FIXTURE_DIR),
            mode="internal",
            auto_fix=False,
            strict=False,
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    refs = {entry["ref"] for entry in managed_doc.list_symbols() if "ref" in entry}

    assert {"U1", "Q1", "RV1", "J1"} <= refs
    assert result.generated_schematic_diagnostics is not None
    assert result.generated_schematic_diagnostics.symbol_count >= 13
    codes = {warning["code"] for warning in result.warnings}
    assert codes.isdisjoint(
        {
            "TIMER555_TIMING_NODE_SPLIT",
            "TIMER555_TIMING_CAP_NOT_TO_GROUND",
            "TIMER555_CTRL_CAP_WRONG_TARGET",
            "TIMER555_STEERING_NETWORK_INVALID",
            "TIMER555_GATE_RESISTOR_MISSING",
            "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
            "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
        }
    )


def test_cmd_new_from_netlist_rejects_blocking_555_lints_before_project_create(
    tmp_path: Path,
) -> None:
    ir_path = tmp_path / "invalid_555_pwm.json"
    _write_invalid_555_pwm_ir(ir_path)
    project_path = tmp_path / "Invalid555Pwm"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="Invalid555Pwm",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(SYMBOLS_FIXTURE_DIR),
                mode="internal",
                auto_fix=False,
                strict=False,
            )
        )

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    blocking_codes = {finding["code"] for finding in exc_info.value.details["blocking_lints"]}
    assert "TIMER555_TIMING_NODE_SPLIT" in blocking_codes
    assert not project_path.exists()
