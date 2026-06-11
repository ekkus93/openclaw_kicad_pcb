from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    cmd_validate_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.utils import find_all
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


def _write_footprint_warning_ir(path: Path) -> None:
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
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
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
                "value": "LOAD",
                "footprint": "Connector_Generic:Conn_01x02",
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
            {
                "name": "POT_A",
                "pins": [{"ref": "RV1", "pin": "1"}, {"ref": "D1", "pin": "2"}],
            },
            {
                "name": "POT_B",
                "pins": [{"ref": "RV1", "pin": "3"}, {"ref": "D2", "pin": "1"}],
            },
            {
                "name": "CTRL",
                "pins": [{"ref": "U1", "pin": "5"}, {"ref": "C4", "pin": "1"}],
            },
            {
                "name": "OUT_DRV",
                "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R2", "pin": "1"}],
            },
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


def _write_decoupling_distance_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "Active"},
            {"ref": "C1", "symbol": "TestLib:R", "value": "100n"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Signal"},
        ],
        "nets": [
            {
                "name": "VCC",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "SIG",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_connector_ambiguity_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Stereo-ish"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
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


def _write_explicit_unit_unknown_unit_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp", "value": "DualOpAmp"}],
        "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "9"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_explicit_unit_wrong_pin_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp", "value": "DualOpAmp"}],
        "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "2"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cmd_apply_netlist_rejects_pin_collision_before_write(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "pin_collision.json"
    _write_pin_collision_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    with pytest.raises(UserError) as exc_info:
        cmd_apply_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                force=True,
                dry_run=False,
            )
        )

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert exc_info.value.details["pin_collisions"][0]["nets"] == ["N1", "N2"]
    assert not (project_dir / "OpenClaw_Managed.kicad_sch").exists()


def test_cmd_new_from_netlist_creates_project_and_applies(tmp_path: Path) -> None:
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="NetlistProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.path.exists()
    assert result.schematic_path.exists()
    assert result.managed_schematic_path.exists()
    assert result.symbols_added == 1
    assert result.nets_applied == 1


def test_cmd_new_from_netlist_preserves_input_coupling_warning(tmp_path: Path) -> None:
    ir_path = tmp_path / "warning_ir.json"
    _write_input_bypass_warning_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="WarningProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    codes = {warning["code"] for warning in result.warnings}
    assert "INPUT_COUPLING_BYPASSED_BY_RESISTOR" in codes
    assert result.warning_report_path is not None
    report = json.loads(result.warning_report_path.read_text(encoding="utf-8"))
    assert report["project_path"] == str(result.path)
    assert report["warning_count"] == len(report["warnings"])
    assert any(
        warning["code"] == "INPUT_COUPLING_BYPASSED_BY_RESISTOR" for warning in report["warnings"]
    )


def test_cmd_new_from_netlist_preserves_decoupling_distance_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ir_path = tmp_path / "decoupling_warning_ir.json"
    _write_decoupling_distance_warning_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "U1": (50.8, 76.2, 0.0),
                "C1": (127.0, 76.2, 0.0),
                "J1": (30.48, 76.2, 0.0),
            }
        ),
    )

    result = cmd_new_from_netlist(
        Namespace(
            name="DecouplingWarningProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    codes = {warning["code"] for warning in result.warnings}
    assert "DECOUPLING_FAR_FROM_ACTIVE_DEVICE" in codes


def test_cmd_new_from_netlist_marks_unused_connector_pins_with_no_connects(
    tmp_path: Path,
) -> None:
    ir_path = tmp_path / "connector_warning_ir.json"
    _write_connector_ambiguity_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ConnectorNoConnectProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    no_connects = find_all(managed_doc.root, "no_connect")

    assert len(no_connects) == 1


def test_cmd_new_from_netlist_supports_explicit_unit_generation(tmp_path: Path) -> None:
    ir_path = tmp_path / "explicit_unit_ir.json"
    _write_explicit_unit_valid_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExplicitUnitProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.symbols_added == 2
    assert result.nets_applied == 2

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}
    assert set(placed_symbols) >= {"R1", "U1A"}
    assert placed_symbols["U1A"]["unit"] == "1"

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    assert binding_index[("U1A", "1")] == "IN_A"
    assert binding_index[("U1A", "3")] == "OUT_A"


def test_cmd_new_from_netlist_rejects_mismatched_explicit_unit_input(tmp_path: Path) -> None:
    ir_path = tmp_path / "explicit_unit_wrong_pin.json"
    _write_explicit_unit_wrong_pin_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    project_path = tmp_path / "ExplicitUnitInvalidProj"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="ExplicitUnitInvalidProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
            )
        )

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert exc_info.value.details["valid_unit_pins"] == ["5", "6", "7"]
    assert not project_path.exists()


def test_cmd_new_from_netlist_rejects_pin_collision_before_project_create(tmp_path: Path) -> None:
    ir_path = tmp_path / "pin_collision.json"
    _write_pin_collision_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    project_path = tmp_path / "PinCollisionProj"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="PinCollisionProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                auto_fix=False,
            )
        )

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert exc_info.value.details["pin_collisions"][0]["nets"] == ["N1", "N2"]
    assert not project_path.exists()


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


# ---------------------------------------------------------------------------
# P7.2 — Integration test: new-from-netlist in internal mode
# ---------------------------------------------------------------------------


def test_new_from_netlist_schematic_parses_and_ownership_marker_present(
    tmp_path: Path,
) -> None:
    """P7.2: the generated schematic must parse cleanly and carry ownership markers."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="P72Proj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    # Root schematic must exist and parse without exception.
    root_doc = SchematicDoc.load(result.schematic_path)
    assert root_doc is not None

    # Root must carry OpenClaw ownership marker.
    assert root_doc.has_openclaw_marker() is True

    # Flat layout: no sub-sheet, circuit is directly in the root schematic.
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is False

    # managed_schematic_path == root schematic in flat mode; contains the component.
    symbols = root_doc.list_symbols()
    refs = [s["ref"] for s in symbols]
    assert "R1" in refs


def test_new_from_netlist_info_sch_returns_owned_and_symbols(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P7.2: info-sch for generated projects returns ownership, symbols, and pin bindings."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="P72InfoProj",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    project = ProjectRef(
        name=result.name,
        path=result.path,
        created=datetime.now().isoformat(),
    )
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    info = cmd_info_sch(Namespace())
    assert info.owned_by_openclaw is True
    assert len(info.symbols) == 1
    assert info.symbols[0]["ref"] == "R1"
    assert info.pin_net_bindings == ({"ref": "R1", "pin": "1", "net_name": "N1"},)
    assert info.schematic_path == result.schematic_path
    # Flat layout: all symbols are in the root schematic; no sub-sheet.
    assert info.managed_schematic_path is None  # no OpenClaw_Managed.kicad_sch
    assert info.symbol_count >= 1  # root carries the generated symbols


def test_cmd_validate_netlist_accepts_valid_explicit_unit(tmp_path: Path) -> None:
    ir_path = tmp_path / "explicit_unit_valid.json"
    _write_explicit_unit_valid_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_validate_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
        )
    )

    assert result.valid is True
    assert result.component_count == 2
    assert result.net_count == 2


def test_cmd_validate_netlist_rejects_unknown_explicit_unit(tmp_path: Path) -> None:
    ir_path = tmp_path / "explicit_unit_unknown.json"
    _write_explicit_unit_unknown_unit_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    with pytest.raises(UserError) as exc_info:
        cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    assert exc_info.value.details["valid_units"] == ["1", "2", "3"]


def test_cmd_validate_netlist_rejects_pin_outside_selected_unit(tmp_path: Path) -> None:
    ir_path = tmp_path / "explicit_unit_wrong_pin.json"
    _write_explicit_unit_wrong_pin_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    with pytest.raises(UserError) as exc_info:
        cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert exc_info.value.details["valid_unit_pins"] == ["5", "6", "7"]


def test_cmd_validate_netlist_rejects_blocking_555_lints(tmp_path: Path) -> None:
    ir_path = tmp_path / "invalid_555_pwm.json"
    _write_invalid_555_pwm_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    with pytest.raises(UserError) as exc_info:
        cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

    assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID
    blocking_codes = {finding["code"] for finding in exc_info.value.details["blocking_lints"]}
    assert {
        "TIMER555_TIMING_NODE_SPLIT",
        "TIMER555_CTRL_CAP_WRONG_TARGET",
        "TIMER555_STEERING_NETWORK_INVALID",
        "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
    } <= blocking_codes


def test_cmd_validate_netlist_reports_footprint_quality_warnings(tmp_path: Path) -> None:
    ir_path = tmp_path / "footprint_warnings.json"
    _write_footprint_warning_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_validate_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
        )
    )

    warning_codes = {warning["code"] for warning in result.warnings}
    assert "FOOTPRINT_CLASS_MISMATCH" in warning_codes
    assert "FOOTPRINT_LOOKS_PLACEHOLDER_OR_SYMBOL_ID" in warning_codes


# ---------------------------------------------------------------------------
# P7.4 — Idempotency test (structural/semantic)
# ---------------------------------------------------------------------------
