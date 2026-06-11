"""Core cmd_apply_netlist and cmd_new_from_netlist tests (warnings, connectors, explicit units)."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_new_from_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.utils import find_all


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
