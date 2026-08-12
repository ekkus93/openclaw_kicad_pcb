"""Netlist commands: cmd_apply_netlist debug dump tests."""

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

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


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
    # With compact analog-tail routing disabled, the corrected outward-facing
    # pin stubs now take the router's documented generic 3-pin bus fallback.
    assert digital_hp_out["strategy"] == "spine"
    assert digital_hp_out["heuristic_override"] is None
