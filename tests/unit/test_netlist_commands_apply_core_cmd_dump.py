"""Netlist commands: resolve_schematic_paths and cmd_apply_netlist tests."""

from __future__ import annotations

import json
from argparse import Namespace
from datetime import datetime
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    resolve_schematic_paths,
)
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


def _write_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
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


# ---------------------------------------------------------------------------
# P0 — resolve_schematic_paths helper
# ---------------------------------------------------------------------------


def test_resolve_schematic_paths_returns_both_paths(tmp_path: Path) -> None:
    """P0: resolve_schematic_paths returns root and managed paths for a project."""
    project = ProjectRef(name="myproj", path=tmp_path, created=datetime.now().isoformat())

    root_sch, managed_sch = resolve_schematic_paths(project)

    assert root_sch == project.sch_file
    assert managed_sch == tmp_path / "OpenClaw_Managed.kicad_sch"
    # Paths are deterministic and do not need to exist on disk
    assert root_sch.name == "myproj.kicad_sch"
    assert managed_sch.name == "OpenClaw_Managed.kicad_sch"


def test_cmd_apply_netlist_creates_managed_schematic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

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

    assert result.symbols_added == 1
    assert result.nets_applied == 1
    assert result.managed_schematic_path.exists()
    assert result.symbols_dirs_used  # non-empty tuple of resolved dirs

    # Flat layout: circuit is written directly into the root schematic,
    # no OpenClaw_Managed sub-sheet is created.
    root_doc = SchematicDoc.load(sch_path)
    assert root_doc.has_openclaw_marker() is True
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is False

    # managed_schematic_path == root schematic in flat mode
    assert result.managed_schematic_path == sch_path
    symbols = root_doc.list_symbols()
    assert len(symbols) == 1
    assert symbols[0]["ref"] == "R1"


def test_cmd_apply_netlist_surfaces_input_coupling_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "warning_ir.json"
    _write_input_bypass_warning_ir(ir_path)

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
    assert "INPUT_COUPLING_BYPASSED_BY_RESISTOR" in codes
    assert result.warning_report_path is not None
    report = json.loads(result.warning_report_path.read_text(encoding="utf-8"))
    report_codes = {warning["code"] for warning in report["warnings"]}
    assert codes <= report_codes
    assert report["schematic_path"] == str(result.managed_schematic_path)
    assert report["validation_mode"] == "internal"
    assert report["generated_schematic_diagnostics"] is not None
    assert report["generated_schematic_diagnostics"]["symbol_count"] >= 1


def test_cmd_apply_netlist_writes_debug_dump(tmp_path: Path, monkeypatch) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "debug_ir.json"
    _write_explicit_unit_valid_ir(ir_path)
    debug_dump_path = project_dir / "OpenClaw_Debug.json"

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "U1A": (50.8, 76.2, 0.0),
                "R1": (101.6, 76.2, 0.0),
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
            debug_dump=str(debug_dump_path),
        )
    )

    assert result.debug_dump_path == debug_dump_path
    dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))
    assert dump["unit_splitting"]["expanded_device_count"] == 1
    assert dump["unit_splitting"]["expanded_devices"][0]["source_ref"] == "U1"
    assert dump["schematic_debug_artifacts"] == [
        "heuristic_profile_name",
        "label_mode_name",
        "validated_pipeline_path",
        "pipeline_stage_markers",
        "unit_splitting",
        "net_classification",
        "final_route_choices",
        "routing_heuristic_policy",
    ]
    assert dump["heuristic_profile_name"] == "analog_audio"
    assert dump["label_mode_name"] == "minimal"
    assert dump["validated_pipeline_path"] == {
        "entrypoint": "apply-netlist",
        "schema_validation": "CircuitIR.load",
        "semantic_validation": "validate_circuit_ir",
        "symbol_pin_validation": "validate_ir_symbols",
        "schematic_emission": "mutate_and_validate_sch",
        "post_generation_reparse": "validate_generated_schematic",
        "artifact_finalize": "warning_report_or_dry_run",
    }
    assert [marker["stage"] for marker in dump["pipeline_stage_markers"]] == [
        "ir_creation",
        "semantic_validation",
        "schematic_emission",
        "post_generation_reparse",
        "artifact_finalize",
    ]
    assert dump["net_classification"] == [
        {
            "classification": "signal_chain",
            "known_pin_count": 2,
            "net_name": "IN_A",
            "pin_count": 2,
            "unknown_pin_count": 0,
        },
        {
            "classification": "signal_chain",
            "known_pin_count": 2,
            "net_name": "OUT_A",
            "pin_count": 2,
            "unknown_pin_count": 0,
        },
    ]
    assert [choice["strategy"] for choice in dump["final_route_choices"]] == ["direct", "direct"]
    assert dump["routing_heuristic_policy"]["enable_compact_output_tails"] is True


def test_cmd_apply_netlist_debug_dump_surfaces_profile_specific_local_output_route(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "profile_diff_ir.json"
    _write_output_tail_profile_diff_ir(ir_path)
    analog_dump_path = project_dir / "OpenClaw_Debug_Analog.json"
    digital_dump_path = project_dir / "OpenClaw_Debug_Digital.json"

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "R6": (213.36, 179.07, 270.0),
                "C7": (213.36, 133.35, 270.0),
                "R7": (238.76, 166.37, 270.0),
                "J2": (217.17, 165.10, 0.0),
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
            debug_dump=str(analog_dump_path),
            heuristic_profile="analog_audio",
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

    analog_dump = json.loads(analog_dump_path.read_text(encoding="utf-8"))
    digital_dump = json.loads(digital_dump_path.read_text(encoding="utf-8"))
    analog_hp_out = next(
        choice for choice in analog_dump["final_route_choices"] if choice["net_name"] == "HP_L_OUT"
    )
    digital_hp_out = next(
        choice for choice in digital_dump["final_route_choices"] if choice["net_name"] == "HP_L_OUT"
    )

    assert analog_dump["heuristic_profile_name"] == "analog_audio"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert analog_dump["routing_heuristic_policy"]["enable_compact_output_tails"] is True
    assert digital_dump["routing_heuristic_policy"]["enable_compact_output_tails"] is False
    assert analog_hp_out["strategy"] == "compact_signal_tail"
    assert analog_hp_out["heuristic_override"] == "compact_output_tail"
    assert digital_hp_out["strategy"] == "shared_lane"
    assert digital_hp_out["heuristic_override"] is None
