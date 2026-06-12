"""Schematic parsing, cmd_info_sch, and cmd_validate_netlist tests."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from kicad_pcb.commands.netlist import (
    cmd_info_sch,
    cmd_new_from_netlist,
)
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc


def _write_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
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
