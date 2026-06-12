"""Netlist apply_core_cmd tests — power profile and profile forwarding."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_new_from_netlist,
)
from kicad_pcb.models import ProjectRef


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


def test_cmd_apply_netlist_debug_dump_surfaces_power_profile_ground_route_metadata(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ground_cluster_ir.json"
    _write_ground_cluster_profile_diff_ir(ir_path)
    power_dump_path = project_dir / "OpenClaw_Debug_Power.json"
    digital_dump_path = project_dir / "OpenClaw_Debug_Digital.json"

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "J2": (222.25, 162.56, 0.0),
                "R5": (213.36, 147.32, 270.0),
                "R7": (238.76, 162.56, 270.0),
            }
        ),
    )

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=False,
            debug_dump=str(power_dump_path),
            heuristic_profile="power_supply",
        )
    )
    cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
            force=True,
            dry_run=False,
            debug_dump=str(digital_dump_path),
            heuristic_profile="generic_digital",
        )
    )

    power_dump = json.loads(power_dump_path.read_text(encoding="utf-8"))
    digital_dump = json.loads(digital_dump_path.read_text(encoding="utf-8"))
    power_ground = next(
        choice for choice in power_dump["final_route_choices"] if choice["net_name"] == "GND"
    )
    digital_ground = next(
        choice for choice in digital_dump["final_route_choices"] if choice["net_name"] == "GND"
    )

    assert power_dump["heuristic_profile_name"] == "power_supply"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert power_dump["routing_heuristic_policy"]["enable_compact_local_ground_clusters"] is True
    assert digital_dump["routing_heuristic_policy"]["enable_compact_local_ground_clusters"] is False
    assert power_ground["strategy"] == "power_symbols"
    assert power_ground["heuristic_override"] in (None, "compact_local_ground_cluster")
    assert digital_ground["strategy"] == "power_symbols"
    assert digital_ground["heuristic_override"] is None


def test_cmd_apply_netlist_forwards_heuristic_profile_name(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    captured_request = None
    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    def _fake_apply(project_arg, request_arg):
        nonlocal captured_request
        captured_request = request_arg
        return type(
            "_Result",
            (),
            {
                "schematic_path": project_arg.sch_file,
                "managed_schematic_path": project_arg.path / "OpenClaw_Managed.kicad_sch",
                "symbols_added": 0,
                "symbols_updated": 0,
                "managed_items_written": 0,
                "nets_applied": 0,
                "kicad_cli_used": False,
                "heuristic_profile_name": "generic_digital",
                "label_mode_name": "debug",
                "dry_run": False,
                "warnings": (),
                "warning_report_path": None,
                "debug_dump_path": None,
                "symbols_dirs_used": (),
            },
        )()

    monkeypatch.setattr("kicad_pcb.commands.netlist._apply_netlist_to_project", _fake_apply)

    cmd_apply_netlist(
        Namespace(
            netlist=str(ir_path),
            symbols_dir=None,
            mode="internal",
            force=True,
            dry_run=False,
            heuristic_profile="generic_digital",
            label_mode="debug",
        )
    )

    assert captured_request is not None
    assert captured_request.heuristic_profile_name == "generic_digital"
    assert captured_request.label_mode_name == "debug"


def test_cmd_new_from_netlist_forwards_heuristic_profile_name(tmp_path: Path, monkeypatch) -> None:
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(
        name="proj",
        path=tmp_path / "out" / "proj",
        created=datetime.now().isoformat(),
    )
    captured_request = None

    monkeypatch.setattr(
        "kicad_pcb.commands.netlist.full_validate",
        lambda *_args, **_kwargs: CircuitIR.load(ir_path),
    )
    monkeypatch.setattr("kicad_pcb.commands.netlist._create_project", lambda **_kwargs: project)

    def _fake_apply(project_arg, request_arg):
        nonlocal captured_request
        captured_request = request_arg
        return type(
            "_Result",
            (),
            {
                "schematic_path": project_arg.sch_file,
                "managed_schematic_path": project_arg.path / "OpenClaw_Managed.kicad_sch",
                "symbols_added": 0,
                "symbols_updated": 0,
                "managed_items_written": 0,
                "nets_applied": 0,
                "kicad_cli_used": False,
                "heuristic_profile_name": "power_supply",
                "label_mode_name": "always-show-important-labels",
                "dry_run": False,
                "warnings": (),
                "warning_report_path": None,
                "debug_dump_path": None,
                "symbols_dirs_used": (),
            },
        )()

    monkeypatch.setattr("kicad_pcb.commands.netlist._apply_netlist_to_project", _fake_apply)

    cmd_new_from_netlist(
        Namespace(
            name="proj",
            netlist=str(ir_path),
            out_dir=str(tmp_path / "out"),
            description="",
            symbols_dir=None,
            mode="internal",
            validate=None,
            routing="bus",
            heuristic_profile="power_supply",
            label_mode="always-show-important-labels",
            auto_fix=False,
            strict=False,
        )
    )

    assert captured_request is not None
    assert captured_request.heuristic_profile_name == "power_supply"
    assert captured_request.label_mode_name == "always-show-important-labels"
