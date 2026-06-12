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
