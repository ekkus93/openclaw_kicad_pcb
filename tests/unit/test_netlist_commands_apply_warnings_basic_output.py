"""Netlist commands: apply_netlist warning surface and explicit unit tests."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
)
from kicad_pcb.models import ProjectRef

pytestmark = pytest.mark.unit


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


def _write_output_load_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Driver"},
            {"ref": "C1", "symbol": "Device:C", "value": "220u"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "Output"},
        ],
        "nets": [
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
            {
                "name": "VIN",
                "pins": [{"ref": "U1", "pin": "1"}],
            },
            {
                "name": "U1_INV",
                "pins": [{"ref": "U1", "pin": "2"}],
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


def _write_shared_positive_rail_decoupling_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "UpperActive"},
            {"ref": "U2", "symbol": "TestLib:R", "value": "LowerActive"},
            {"ref": "C1", "symbol": "TestLib:R", "value": "100n"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Signal1"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "Signal2"},
        ],
        "nets": [
            {
                "name": "VCC",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "U2", "pin": "1"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "SIG_A",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "SIG_B",
                "pins": [
                    {"ref": "U2", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J1", "pin": "2"},
                    {"ref": "J2", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_shared_negative_rail_decoupling_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "UpperActive"},
            {"ref": "U2", "symbol": "TestLib:R", "value": "LowerActive"},
            {"ref": "C1", "symbol": "TestLib:R", "value": "100n"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Signal1"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "Signal2"},
        ],
        "nets": [
            {
                "name": "VEE",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "U2", "pin": "1"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "SIG_A",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "SIG_B",
                "pins": [
                    {"ref": "U2", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J1", "pin": "2"},
                    {"ref": "J2", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_stage_topology_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R2", "symbol": "Device:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "In"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "R2", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_explicit_unit_wrong_pin_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp", "value": "DualOpAmp"}],
        "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "2"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cmd_apply_netlist_surfaces_output_load_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "output_load_warning_ir.json"
    _write_output_load_warning_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=False,
        )
    )

    codes = {warning["code"] for warning in result.warnings}
    assert "OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED" in codes


def test_cmd_apply_netlist_surfaces_stage_topology_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "stage_topology_warning_ir.json"
    _write_stage_topology_warning_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=False,
        )
    )

    codes = {warning["code"] for warning in result.warnings}
    assert "OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN" in codes
