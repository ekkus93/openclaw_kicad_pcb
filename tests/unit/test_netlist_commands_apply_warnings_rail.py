"""Netlist apply-warnings tests — rail decoupling, explicit units."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc


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


def _write_explicit_unit_wrong_pin_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "U1", "symbol": "TestLib:DualOpAmp", "value": "DualOpAmp"}],
        "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1", "unit": "2"}]}],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_cmd_apply_netlist_prefers_positive_rail_device_below_decoupler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "shared_positive_rail_decoupling_ir.json"
    _write_shared_positive_rail_decoupling_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "U1": (88.9, 68.58, 0.0),
                "U2": (96.52, 121.92, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 68.58, 0.0),
                "J2": (30.48, 121.92, 0.0),
            }
        ),
    )

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

    decoupling_warning = next(
        warning
        for warning in result.warnings
        if warning["code"] == "DECOUPLING_FAR_FROM_ACTIVE_DEVICE"
    )
    details = cast(dict[str, object], decoupling_warning["details"])
    assert details["rail_polarity"] == "positive"
    assert details["nearest_active_ref"] == "U2"


def test_cmd_apply_netlist_prefers_negative_rail_device_above_decoupler(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "shared_negative_rail_decoupling_ir.json"
    _write_shared_negative_rail_decoupling_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "U1": (88.9, 30.48, 0.0),
                "U2": (96.52, 83.82, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            }
        ),
    )

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

    decoupling_warning = next(
        warning
        for warning in result.warnings
        if warning["code"] == "DECOUPLING_FAR_FROM_ACTIVE_DEVICE"
    )
    details = cast(dict[str, object], decoupling_warning["details"])
    assert details["rail_polarity"] == "negative"
    assert details["nearest_active_ref"] == "U1"


def test_cmd_apply_netlist_supports_explicit_unit_generation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "explicit_unit_ir.json"
    _write_explicit_unit_valid_ir(ir_path)

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


def test_cmd_apply_netlist_rejects_mismatched_explicit_unit_input(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "explicit_unit_wrong_pin.json"
    _write_explicit_unit_wrong_pin_ir(ir_path)

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

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert exc_info.value.details["valid_unit_pins"] == ["5", "6", "7"]
    assert not (project_dir / "OpenClaw_Managed.kicad_sch").exists()
