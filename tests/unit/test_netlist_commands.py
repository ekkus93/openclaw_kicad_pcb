from __future__ import annotations

import json
import math
from argparse import Namespace
from datetime import datetime
from pathlib import Path
from typing import cast

import pytest
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands._sch_apply import _cleanup_new_managed_file
from kicad_pcb.commands.netlist import (
    cmd_apply_netlist,
    cmd_fix_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    cmd_validate_netlist,
    resolve_schematic_paths,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout import compute_orientations
from kicad_pcb.models import ProjectRef
from kicad_pcb.sch_doc import SchematicDoc, read_lib_symbol_pin_at
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_all, find_first, walk


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


def _write_output_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "Driver"},
            {"ref": "C7", "symbol": "TestLib:R", "value": "220u"},
            {"ref": "R8", "symbol": "TestLib:R", "value": "47"},
            {"ref": "J2", "symbol": "TestLib:R", "value": "Output"},
        ],
        "nets": [
            {
                "name": "OUT_L_STAGE2_RAW",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "C7", "pin": "1"},
                    {"ref": "R8", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C7", "pin": "2"},
                    {"ref": "R8", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
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


def _write_feedback_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:R", "value": "Out"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "J1", "pin": "1"},
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


def _write_output_floating_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_short_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "P1", "symbol": "TestLib:R", "value": "Rail"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "VPLUS15",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "P1", "pin": "1"},
                ],
            },
            {
                "name": "BIAS",
                "pins": [
                    {"ref": "P1", "pin": "2"},
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


def test_fix_netlist_raises_when_symbol_index_init_fails(tmp_path: Path, monkeypatch) -> None:
    """A5: fix-netlist must fail fast if SymbolIndex construction fails."""
    netlist_path = tmp_path / "ir.json"
    _write_ir(netlist_path)
    output_path = tmp_path / "ir.fixed.json"

    class _BrokenSymbolIndex:
        def __init__(
            self,
            *,
            symbols_dir: Path | None = None,
            fallback_dirs: list[Path] | None = None,
        ) -> None:
            del symbols_dir, fallback_dirs
            raise UserError(
                "symbol index init failed",
                code=ErrorCode.SYMBOL_DIR_MISSING,
            )

    monkeypatch.setattr("kicad_pcb.commands.netlist.SymbolIndex", _BrokenSymbolIndex)

    with pytest.raises(UserError) as exc_info:
        cmd_fix_netlist(
            Namespace(
                netlist=str(netlist_path),
                symbols_dir=str(tmp_path / "symbols"),
                output=str(output_path),
            )
        )

    assert exc_info.value.code == ErrorCode.SYMBOL_DIR_MISSING
    assert not output_path.exists()


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

    root_doc = SchematicDoc.load(sch_path)
    assert root_doc.has_openclaw_marker() is True
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is True

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    symbols = managed_doc.list_symbols()
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
    assert report["managed_schematic_path"] == str(result.managed_schematic_path)


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
        "unit_splitting",
        "net_classification",
        "final_route_choices",
        "routing_heuristic_policy",
    ]
    assert dump["heuristic_profile_name"] == "analog_audio"
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


def test_cmd_apply_netlist_debug_dump_surfaces_profile_specific_output_tail_route(
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


def test_cmd_apply_netlist_debug_dump_surfaces_power_profile_ground_cluster_route(
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
    assert power_ground["heuristic_override"] == "compact_local_ground_cluster"
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
        )
    )

    assert captured_request is not None
    assert captured_request.heuristic_profile_name == "generic_digital"


def test_cmd_new_from_netlist_forwards_heuristic_profile_name(tmp_path: Path, monkeypatch) -> None:
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(
        name="proj",
        path=tmp_path / "out" / "proj",
        created=datetime.now().isoformat(),
    )
    captured_request = None

    monkeypatch.setattr("kicad_pcb.commands.netlist.full_validate", lambda *_args, **_kwargs: None)
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
            auto_fix=False,
            strict=False,
        )
    )

    assert captured_request is not None
    assert captured_request.heuristic_profile_name == "power_supply"


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


def test_cmd_apply_netlist_surfaces_decoupling_distance_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "decoupling_warning_ir.json"
    _write_decoupling_distance_warning_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
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
    assert "DECOUPLING_FAR_FROM_ACTIVE_DEVICE" in codes


def test_cmd_apply_netlist_skips_decoupling_distance_warning_when_local(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "decoupling_warning_ir.json"
    _write_decoupling_distance_warning_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)
    monkeypatch.setattr(
        "kicad_pcb.commands._sch_apply._resolve_layout",
        lambda *args, **kwargs: _FakeLayoutEngine(
            {
                "U1": (50.8, 76.2, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 76.2, 0.0),
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

    codes = {warning["code"] for warning in result.warnings}
    assert "DECOUPLING_FAR_FROM_ACTIVE_DEVICE" not in codes


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

    # Root must reference the managed sheet.
    assert root_doc.has_managed_sheet(sheet_name="OpenClaw_Managed") is True

    # Managed sheet must exist, parse, and contain the generated component.
    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    symbols = managed_doc.list_symbols()
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
    # P5/P7 new fields
    assert info.managed_schematic_path is not None
    assert info.managed_symbol_count >= 1
    assert info.managed_label_count >= 1
    assert info.symbol_count == 0  # root is thin (no placed symbols)


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


# ---------------------------------------------------------------------------
# P7.4 — Idempotency test (structural/semantic)
# ---------------------------------------------------------------------------


def _extract_managed_model(managed_path: Path) -> tuple[list[str], list[str]]:
    """Return normalized (sorted refs, sorted symbol_ids) for the managed sheet."""
    doc = SchematicDoc.load(managed_path)
    symbols = doc.list_symbols()
    refs: list[str] = sorted(cast(str, s["ref"]) for s in symbols)
    symbol_ids: list[str] = sorted(cast(str, s["symbol_id"]) for s in symbols)
    return refs, symbol_ids


def test_apply_netlist_idempotent_apply_twice(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P7.4: applying the same IR twice yields the same managed region."""
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

    args = Namespace(
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
        force=True,
        dry_run=False,
    )

    result1 = cmd_apply_netlist(args)
    model1 = _extract_managed_model(result1.managed_schematic_path)

    result2 = cmd_apply_netlist(args)
    model2 = _extract_managed_model(result2.managed_schematic_path)

    # Structural idempotency: same refs and symbol IDs after two applications.
    assert model1 == model2

    # No duplicates: after two applications the ref list should be unique.
    refs, _ = model2
    assert len(refs) == len(set(refs))


def test_new_from_netlist_idempotency_via_two_projects(tmp_path: Path) -> None:
    """P7.4: two new-from-netlist calls with the same IR produce equivalent managed regions."""
    ir_path = tmp_path / "ir.json"
    _write_ir(ir_path)
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    args_a = Namespace(
        name="Idem_A",
        out_dir=str(tmp_path),
        description="",
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
    )
    args_b = Namespace(
        name="Idem_B",
        out_dir=str(tmp_path),
        description="",
        netlist=str(ir_path),
        symbols_dir=str(fixtures_dir),
        mode="internal",
    )

    result_a = cmd_new_from_netlist(args_a)
    result_b = cmd_new_from_netlist(args_b)

    model_a = _extract_managed_model(result_a.managed_schematic_path)
    model_b = _extract_managed_model(result_b.managed_schematic_path)

    assert model_a == model_b


# ---------------------------------------------------------------------------
# P6.4 — Regression: empty generation triggers EMPTY_GENERATION error
# ---------------------------------------------------------------------------


def test_empty_generation_invariant_raises_coded_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P6.4: if the IR is non-empty but no symbols are placed, EMPTY_GENERATION is raised.

    Simulates the bug path: _write_symbols runs without error (symbol found in
    fixture lib) but add_symbol is a no-op, leaving the managed AST empty.
    The post-mutation invariant must then raise UserError(EMPTY_GENERATION).
    """
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    # Patch add_symbol to a no-op so the AST stays empty while the rest of the
    # pipeline (pin resolution, position tracking, wire/label writing) still runs.
    monkeypatch.setattr(SchematicDoc, "add_symbol", lambda *args, **kwargs: None)

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

    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert "expected_components" in exc_info.value.details
    assert "found_symbols" in exc_info.value.details
    assert exc_info.value.details["found_symbols"] == 0


# ---------------------------------------------------------------------------
# P1.2 — DRY_RUN_NO_WRITE warning
# ---------------------------------------------------------------------------


def test_apply_netlist_dry_run_emits_no_write_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """P1.2: dry-run must include DRY_RUN_NO_WRITE warning; managed schematic unchanged."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")

    # Pre-create the managed schematic so _ensure_managed_file_exists doesn't
    # raise when called with dry_run=True (it only creates the file non-dry-run).
    managed_sch_path = project_dir / "OpenClaw_Managed.kicad_sch"
    _write_minimal_sch(managed_sch_path)
    content_before = managed_sch_path.read_text(encoding="utf-8")

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
            dry_run=True,
        )
    )

    assert result.dry_run is True

    warning_codes = [w["code"] for w in result.warnings]
    assert "DRY_RUN_NO_WRITE" in warning_codes, f"Expected DRY_RUN_NO_WRITE in {warning_codes}"

    no_write_w = next(w for w in result.warnings if w["code"] == "DRY_RUN_NO_WRITE")
    details_no_write = no_write_w["details"]
    assert isinstance(details_no_write, dict)
    assert "symbols_validated" in details_no_write
    assert details_no_write["symbols_validated"] == 1
    assert "nets_validated" in details_no_write

    # Pipeline must NOT have modified the managed schematic file in dry-run mode.
    assert managed_sch_path.read_text(encoding="utf-8") == content_before


def test_apply_netlist_cleanup_suppresses_race_unlink_error_and_reraises_original(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Cleanup suppresses race-like missing-file unlink errors, then re-raises original failure."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"
    _write_ir(ir_path)

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("apply exploded")

    monkeypatch.setattr("kicad_pcb.commands._sch_apply.mutate_and_validate_sch", _boom)

    path_cls = type(project_dir)
    original_unlink = path_cls.unlink

    def _unlink_race(self: Path, *, missing_ok: bool = False):
        if self.name == "OpenClaw_Managed.kicad_sch":
            raise FileNotFoundError("simulated concurrent delete")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(path_cls, "unlink", _unlink_race)

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    with pytest.raises(RuntimeError, match="apply exploded") as exc_info:
        cmd_apply_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
                force=True,
                dry_run=False,
            )
        )

    assert getattr(exc_info.value, "__notes__", []) == []


def test_cleanup_new_managed_file_non_race_error_is_noted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Non-race unlink failures are attached to the original exception as notes."""
    managed_sch = tmp_path / "OpenClaw_Managed.kicad_sch"
    managed_sch.write_text("(kicad_sch)", encoding="utf-8")
    original_error = RuntimeError("apply exploded")

    path_cls = type(managed_sch)
    original_unlink = path_cls.unlink

    def _unlink_permission(self: Path, *, missing_ok: bool = False):
        if self.name == "OpenClaw_Managed.kicad_sch":
            raise PermissionError("simulated permission denied")
        return original_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(path_cls, "unlink", _unlink_permission)

    _cleanup_new_managed_file(managed_sch, original_error)

    notes = getattr(original_error, "__notes__", [])
    cleanup_note = getattr(original_error, "cleanup_note", "")
    assert any("Managed-sheet cleanup failed" in note for note in notes) or (
        "Managed-sheet cleanup failed" in cleanup_note
    )


def test_apply_netlist_requires_at_least_80_percent_components_placed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Fail when fewer than 80% of IR components are placed into the managed sheet."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir(parents=True)
    sch_path = project_dir / "proj.kicad_sch"
    _write_minimal_sch(sch_path)
    (project_dir / "proj.kicad_pcb").write_text("(kicad_pcb (version 20230121))", encoding="utf-8")
    ir_path = project_dir / "ir.json"

    payload = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "1k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "2k"},
            {"ref": "R3", "symbol": "TestLib:R", "value": "3k"},
            {"ref": "R4", "symbol": "TestLib:R", "value": "4k"},
            {"ref": "R5", "symbol": "TestLib:R", "value": "5k"},
        ],
        "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    ir_path.write_text(json.dumps(payload), encoding="utf-8")

    project = ProjectRef(name="proj", path=project_dir, created=datetime.now().isoformat())
    monkeypatch.setattr("kicad_pcb.commands.netlist.get_current_project", lambda: project)

    original_add_symbol = SchematicDoc.add_symbol

    def _add_symbol_with_drops(self, symbol, ref, *args, **kwargs):
        if ref in {"R4", "R5"}:
            return None
        return original_add_symbol(self, symbol, ref, *args, **kwargs)

    monkeypatch.setattr(SchematicDoc, "add_symbol", _add_symbol_with_drops)

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

    assert exc_info.value.code == ErrorCode.EMPTY_GENERATION
    assert exc_info.value.details["expected_components"] == 5
    assert exc_info.value.details["found_symbols"] == 3
    assert exc_info.value.details["min_component_placement_ratio"] == 0.8
    assert exc_info.value.details["min_required_symbols"] == 4


# ---------------------------------------------------------------------------
# Extends chain — lib_symbols embedding, pin correctness, net binding
#
# These tests guard against the NE5532 regression where (extends "BaseName")
# caused three failures:
#   1. Only the derived node was embedded — base absent → KiCad blank box.
#   2. read_lib_symbol_pins returned [] → fallback to ["1","2"] → wrong wiring.
#   3. validate_ir_symbols raised SYMBOL_NOT_FOUND for all derived symbols.
# ---------------------------------------------------------------------------


def _get_lib_symbol_ids(doc: SchematicDoc) -> list[str]:
    """Return sorted list of symbol IDs embedded in (lib_symbols)."""
    lib_syms = find_first(doc.root, "lib_symbols")
    if lib_syms is None:
        return []
    ids: list[str] = []
    for item in lib_syms.items:
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            ids.append(item.items[1].value)
    return sorted(ids)


def _get_instance_pin_numbers(doc: SchematicDoc, ref: str) -> list[str]:
    """Return sorted pin numbers declared on the placed symbol instance for *ref*."""
    pins: list[str] = []
    for item in doc.root.items:
        if not (isinstance(item, ListNode) and item.key == "symbol"):
            continue
        # Match instance by Reference property value.
        instance_ref: str | None = None
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "property"
                and len(child.items) >= 3
                and isinstance(child.items[1], StringNode)
                and child.items[1].value == "Reference"
                and isinstance(child.items[2], StringNode)
            ):
                instance_ref = child.items[2].value
        if instance_ref != ref:
            continue
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "pin"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                pins.append(child.items[1].value)
    return sorted(pins)


def test_extends_symbol_embeds_flat_derived_in_lib_symbols(tmp_path: Path) -> None:
    """Extends chain: only the derived symbol is embedded, as a fully flat node.

    The flattening fix (read_lib_symbol_def_flat) merges the parent's geometry
    sub-symbols into the derived node, renames them, and removes the
    (extends ...) attribute.  Only the derived node is written to lib_symbols —
    KiCad can render it without needing the base symbol separately.

    Regression guard for the NE5532 bug: the old code embedded only the derived
    node WITHOUT parent geometry → KiCad renders a blank box with no pins.
    The current code merges parent geometry in so KiCad sees a complete symbol
    with no unresolved extends reference.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsEmbed",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    embedded_ids = _get_lib_symbol_ids(managed_doc)

    # Only the derived symbol is embedded — the base was merged in, not kept separately.
    assert "TestLib:DerivedOpAmp" in embedded_ids, (
        f"Derived 'TestLib:DerivedOpAmp' missing from lib_symbols; got: {embedded_ids}"
    )
    assert "TestLib:OpAmp" not in embedded_ids, (
        f"Base 'TestLib:OpAmp' should not be embedded separately (geometry was merged "
        f"into DerivedOpAmp by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded derived symbol must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    derived_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "TestLib:DerivedOpAmp"
        ),
        None,
    )
    assert derived_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in derived_node.items)
    assert not extends_found, (
        "Flattened derived symbol still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


def test_extends_symbol_instance_carries_all_inherited_pins(tmp_path: Path) -> None:
    """Extends chain: the placed instance must declare all pins inherited from the base.

    With the old code, read_lib_symbol_pins returned [] for a derived symbol,
    causing the placed instance to record no pins (or a wrong two-pin fallback).
    The result was a schematic where U1 had no net connections and was
    electrically wrong even though it opened without errors in KiCad.
    """
    # DerivedOpAmp inherits pins 1, 2, 3, 6 from OpAmp in TestLib.kicad_sym.
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsPins",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    pin_numbers = _get_instance_pin_numbers(managed_doc, "U1")

    # Old broken code produced [] or ["1", "2"]; correct code gives all four.
    assert pin_numbers == ["1", "2", "3", "6"], (
        f"Expected inherited pins ['1','2','3','6']; got {pin_numbers}"
    )


def test_extends_symbol_nets_on_inherited_pins_validate_and_bind(tmp_path: Path) -> None:
    """Extends chain: nets on inherited pins must pass validation and produce bindings.

    Pin '6' exists only on the base OpAmp — not declared on DerivedOpAmp directly.
    The old code raised SYMBOL_NOT_FOUND before writing anything because pin
    resolution returned [] for the derived symbol.  The fixed code must:
    1) accept pin '6' as valid for DerivedOpAmp during IR validation,
    2) record an OpenClaw:bind= marker for each net connection.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [
                    {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]},
                    {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}]},
                    {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}]},  # only on base OpAmp
                ],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsNets",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.nets_applied == 3

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    bindings = managed_doc.extract_pin_label_bindings()
    bound_pins = {b["pin"] for b in bindings if b["ref"] == "U1"}
    net_by_pin = {b["pin"]: b["net_name"] for b in bindings if b["ref"] == "U1"}

    # All three nets must be bound; inherited pin '6' is the critical one.
    assert "1" in bound_pins, f"Pin '1' not bound; bindings: {bindings}"
    assert "2" in bound_pins, f"Pin '2' not bound; bindings: {bindings}"
    assert "6" in bound_pins, (
        f"Pin '6' (inherited from base OpAmp) not bound; bound_pins: {bound_pins}"
    )
    assert net_by_pin["6"] == "OUT", f"Pin '6' bound to wrong net: {net_by_pin}"


def test_broken_extends_chain_raises_symbol_has_no_pins(tmp_path: Path) -> None:
    """Broken extends chain must abort with SYMBOL_HAS_NO_PINS before writing anything.

    A symbol that IS declared in the library file but whose extends base does
    not exist resolves to 0 pins.  The error must be SYMBOL_HAS_NO_PINS (not
    SYMBOL_NOT_FOUND) to distinguish "symbol present but broken" from "symbol
    absent entirely".
    """
    # Library with a derived symbol whose base is intentionally absent.
    broken_lib = tmp_path / "BrokenLib.kicad_sym"
    broken_lib.write_text(
        """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Orphan" (extends "NonExistentBase")
    (property "Reference" "U" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "Orphan" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
""",
        encoding="utf-8",
    )
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "U1", "symbol": "BrokenLib:Orphan", "value": "Orphan"}],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="BrokenChainProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(tmp_path),
                mode="internal",
            )
        )

    # Broken chain → symbol in file but 0 pins → SYMBOL_HAS_NO_PINS.
    assert exc_info.value.code == ErrorCode.SYMBOL_HAS_NO_PINS
    # Error details must identify the offending symbol.
    assert "BrokenLib:Orphan" in str(exc_info.value)


def test_apply_netlist_aborts_on_invalid_pin_ref(tmp_path: Path) -> None:
    """Invalid pin reference must abort with PIN_INVALID before writing anything.

    `TestLib:R` only has pins '1' and '2'.  Referencing non-existent pin '99'
    must raise at `validate_ir_symbols` time — before the managed schematic is
    created or mutated — so partial / corrupt output is never written to disk.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
                "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "99"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    managed_sch = tmp_path / "InvalidPinProj" / "OpenClaw_Managed.kicad_sch"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="InvalidPinProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
            )
        )

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert "99" in str(exc_info.value)
    assert "TestLib:R" in str(exc_info.value)
    # Confirm the managed schematic was NOT written (preflight fired pre-write).
    assert not managed_sch.exists(), (
        "Managed schematic must not be created when preflight validation fails"
    )


# ---------------------------------------------------------------------------
# Circuit fidelity — general helper + tests
#
# These tests answer the core question: "Given a Circuit IR, does the
# generated schematic actually represent that circuit?"
#
# Checks performed by _check_circuit_fidelity:
#   1. Every component ref in the IR is present as a placed symbol instance.
#   2. Every (ref, pin, net_name) triple in the IR has a matching
#      OpenClaw:bind= marker in the managed schematic — meaning the tool
#      recorded the net connection for that specific pin.
#
# This is stricter than the EMPTY_GENERATION guard: a schematic could have
# the right number of symbols but wire them to wrong nets, or omit a pin.
# ---------------------------------------------------------------------------

# Path to the system KiCad symbol libraries (installed by kicad package).
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_REVIEW_NETLIST = (
    Path(__file__).resolve().parents[2] / "code_review" / "ne5532_headphone_amp_netlist.json"
)

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)


def _normalize_warning_entries(
    warnings: tuple[dict[str, object], ...],
) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    normalized: list[tuple[str, tuple[tuple[str, object], ...]]] = []
    for warning in warnings:
        code = warning.get("code")
        if not isinstance(code, str):
            continue
        details_obj = warning.get("details")
        details = cast(dict[str, object], details_obj) if isinstance(details_obj, dict) else {}
        normalized.append((code, tuple(sorted(details.items()))))
    return sorted(normalized)


def _summarize_route_choices(
    dump: dict[str, object],
) -> tuple[dict[str, int], dict[str, list[str]]]:
    counts: dict[str, int] = {}
    overrides: dict[str, list[str]] = {}
    route_choices = cast(list[dict[str, object]], dump["final_route_choices"])
    for choice in route_choices:
        strategy = cast(str, choice["strategy"])
        counts[strategy] = counts.get(strategy, 0) + 1
        heuristic_override = choice.get("heuristic_override")
        net_name = cast(str, choice["net_name"])
        if isinstance(heuristic_override, str):
            overrides.setdefault(heuristic_override, []).append(net_name)
    return counts, overrides


# ---------------------------------------------------------------------------
# Phase 1 — warning suite
# ---------------------------------------------------------------------------


class TestPhase1WarningSuite:
    @pytest.mark.parametrize(
        ("filename", "writer", "expected_codes"),
        [
            (
                "warning_ir.json",
                _write_input_bypass_warning_ir,
                {"INPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "output_warning_ir.json",
                _write_output_bypass_warning_ir,
                {"OUTPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "connector_warning_ir.json",
                _write_connector_ambiguity_ir,
                {"CONNECTOR_UNUSED_PINS_AMBIGUOUS"},
            ),
            (
                "feedback_warning_ir.json",
                _write_feedback_warning_ir,
                {"OPAMP_FEEDBACK_MISSING_OR_NONLOCAL"},
            ),
            (
                "stage_topology_warning_ir.json",
                _write_stage_topology_warning_ir,
                {"OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN"},
            ),
            (
                "output_load_warning_ir.json",
                _write_output_load_warning_ir,
                {"OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED"},
            ),
            (
                "output_floating_warning_ir.json",
                _write_output_floating_warning_ir,
                {"OPAMP_OUTPUT_FLOATING"},
            ),
            (
                "output_short_warning_ir.json",
                _write_output_short_warning_ir,
                {"OPAMP_OUTPUT_SHORTED_TO_RAIL"},
            ),
        ],
        ids=[
            "input-coupling",
            "output-coupling",
            "connector-ambiguity",
            "missing-feedback",
            "stage-topology-likely-mistaken",
            "output-cap-no-load-or-bleed",
            "output-floating",
            "output-shorted-to-rail",
        ],
    )
    def test_synthetic_warning_fixtures_cover_each_phase1_family(
        self,
        tmp_path: Path,
        filename: str,
        writer,
        expected_codes: set[str],
    ) -> None:
        ir_path = tmp_path / filename
        writer(ir_path)
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

        result = cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

        codes = {warning["code"] for warning in result.warnings}
        assert expected_codes <= codes

    @_skip_no_system_symbols
    def test_real_ne5532_fixture_warning_set_does_not_drift(self) -> None:
        """The real NE5532 review fixture should keep the current exact warning mix."""
        result = cmd_validate_netlist(
            Namespace(
                netlist=str(_REAL_NE5532_REVIEW_NETLIST),
                symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            )
        )

        assert _normalize_warning_entries(result.warnings) == [
            (
                "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                (
                    ("ref", "J1"),
                    ("symbol", "Connector:AudioJack3"),
                    ("unused_pins", ["R"]),
                    ("used_pins", ["S", "T"]),
                ),
            ),
            (
                "CONNECTOR_UNUSED_PINS_AMBIGUOUS",
                (
                    ("ref", "J2"),
                    ("symbol", "Connector:AudioJack3"),
                    ("unused_pins", ["R"]),
                    ("used_pins", ["S", "T"]),
                ),
            ),
            (
                "INPUT_COUPLING_BYPASSED_BY_RESISTOR",
                (
                    ("bridge_component_refs", ["C5", "R1"]),
                    ("capacitor_refs", ["C5"]),
                    ("nets", ["IN_L_AC", "LEFT_IN"]),
                    ("resistor_refs", ["R1"]),
                ),
            ),
        ]


@_skip_no_system_symbols
def test_new_from_real_ne5532_fixture_marks_unused_trs_ring_pins(tmp_path: Path) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532ConnectorClarity",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    assert len(find_all(managed_doc.root, "no_connect")) == 2


@_skip_no_system_symbols
def test_new_from_real_ne5532_fixture_splits_u1_into_explicit_units(tmp_path: Path) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532UnitSplit",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}
    assert "U1" not in placed_symbols
    assert {"U1A", "U1B", "U1P"} <= set(placed_symbols)
    assert placed_symbols["U1A"]["unit"] == "1"
    assert placed_symbols["U1B"]["unit"] == "2"
    assert placed_symbols["U1P"]["unit"] == "3"

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    assert binding_index[("U1P", "8")] == "VPLUS15"
    assert binding_index[("U1P", "4")] == "VMINUS15"
    assert binding_index[("U1A", "3")] == "VOL_L_OUT"
    assert binding_index[("U1A", "2")] == "U1A_INV"
    assert binding_index[("U1A", "1")] == "OUT_L_STAGE1"
    assert binding_index[("U1B", "5")] == "BUF_L_IN"
    assert binding_index[("U1B", "6")] == "OUT_L_STAGE2_RAW"
    assert binding_index[("U1B", "7")] == "OUT_L_STAGE2_RAW"


@_skip_no_system_symbols
def test_new_from_real_ne5532_fixture_managed_schematic_structure_is_stable(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532Structure",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    assert managed_doc.has_openclaw_marker() is True

    fixture = json.loads(_REAL_NE5532_REVIEW_NETLIST.read_text(encoding="utf-8"))
    source_component_refs = {
        str(component["ref"])
        for component in fixture["components"]
        if str(component["ref"]) != "U1"
    }

    symbols = managed_doc.list_symbols()
    non_power_symbols = [sym for sym in symbols if not str(sym["ref"]).startswith("#")]
    non_power_refs = {str(sym["ref"]) for sym in non_power_symbols}

    assert len(non_power_symbols) == len(source_component_refs) + 3
    assert non_power_refs == source_component_refs | {"U1A", "U1B", "U1P"}

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    expected_bindings = {
        ("J1", "T"): "LEFT_IN",
        ("C5", "1"): "LEFT_IN",
        ("C5", "2"): "IN_L_AC",
        ("RV1", "2"): "VOL_L_OUT",
        ("U1A", "3"): "VOL_L_OUT",
        ("U1A", "1"): "OUT_L_STAGE1",
        ("C6", "1"): "OUT_L_STAGE1",
        ("C6", "2"): "BUF_L_IN",
        ("U1B", "5"): "BUF_L_IN",
        ("U1B", "7"): "OUT_L_STAGE2_RAW",
        ("R6", "2"): "AFTER_R6",
        ("C7", "2"): "HP_L_OUT",
        ("J2", "T"): "HP_L_OUT",
    }
    for pin_ref, net_name in expected_bindings.items():
        assert binding_index[pin_ref] == net_name

    assert ("J1", "R") not in binding_index
    assert ("J2", "R") not in binding_index


@_skip_no_system_symbols
def test_real_ne5532_fixture_profile_debug_dump_summary_diff(tmp_path: Path) -> None:
    analog_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532AnalogProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
            heuristic_profile="analog_audio",
            debug_dump=str(tmp_path / "analog_audio_debug.json"),
        )
    )
    digital_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DigitalProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
            heuristic_profile="generic_digital",
            debug_dump=str(tmp_path / "generic_digital_debug.json"),
        )
    )

    analog_dump = json.loads(cast(Path, analog_result.debug_dump_path).read_text(encoding="utf-8"))
    digital_dump = json.loads(
        cast(Path, digital_result.debug_dump_path).read_text(encoding="utf-8")
    )
    analog_counts, analog_overrides = _summarize_route_choices(cast(dict[str, object], analog_dump))
    digital_counts, digital_overrides = _summarize_route_choices(
        cast(dict[str, object], digital_dump)
    )

    assert analog_dump["heuristic_profile_name"] == "analog_audio"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert analog_counts != digital_counts
    assert analog_counts.get("shared_lane", 0) < digital_counts.get("shared_lane", 0)
    assert analog_counts.get("chain", 0) > digital_counts.get("chain", 0)
    assert analog_overrides == {
        "compact_local_ground_cluster": ["GND"],
        "small_analog_local_routing": ["BUF_L_IN", "IN_L_AC", "LEFT_IN", "U1A_INV"],
    }
    assert digital_overrides == {}


@_skip_no_system_symbols
def test_real_ne5532_power_profile_debug_dump_surfaces_ground_cluster_diff(
    tmp_path: Path,
) -> None:
    power_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532PowerProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
            heuristic_profile="power_supply",
            debug_dump=str(tmp_path / "power_supply_debug.json"),
        )
    )
    digital_result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DigitalProfile",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
            heuristic_profile="generic_digital",
            debug_dump=str(tmp_path / "generic_digital_debug.json"),
        )
    )

    power_dump = json.loads(cast(Path, power_result.debug_dump_path).read_text(encoding="utf-8"))
    digital_dump = json.loads(
        cast(Path, digital_result.debug_dump_path).read_text(encoding="utf-8")
    )
    power_counts, power_overrides = _summarize_route_choices(cast(dict[str, object], power_dump))
    digital_counts, digital_overrides = _summarize_route_choices(
        cast(dict[str, object], digital_dump)
    )

    assert power_dump["heuristic_profile_name"] == "power_supply"
    assert digital_dump["heuristic_profile_name"] == "generic_digital"
    assert power_dump["net_classification"] == digital_dump["net_classification"]
    assert power_counts == digital_counts
    assert power_dump["routing_heuristic_policy"] == {
        "enable_compact_local_ground_clusters": True,
        "enable_compact_output_tails": False,
    }
    assert digital_dump["routing_heuristic_policy"] == {
        "enable_compact_local_ground_clusters": False,
        "enable_compact_output_tails": False,
    }
    assert power_overrides == {}
    assert digital_overrides == {}


def _check_circuit_fidelity(ir_data: dict, managed_doc: SchematicDoc) -> None:
    """Assert that *managed_doc* faithfully represents *ir_data*.

    Raises ``AssertionError`` with a descriptive message on the first mismatch.

    Parameters
    ----------
    ir_data:
        Parsed Circuit IR dict (``version``, ``components``, ``nets`` keys).
    managed_doc:
        The generated managed schematic loaded as a :class:`SchematicDoc`.
    """
    # --- component placement check ---
    placed_refs = {str(s["ref"]) for s in managed_doc.list_symbols()}
    for component in ir_data["components"]:
        ref = component["ref"]
        assert ref in placed_refs, (
            f"Component {ref!r} (symbol {component['symbol']!r}) "
            f"is missing from the generated schematic. "
            f"Placed refs: {sorted(placed_refs)}"
        )

    # --- net binding check ---
    # Build a lookup: (ref, pin) → net_name from the generated binding markers.
    binding_index: dict[tuple[str, str], str] = {
        (b["ref"], b["pin"]): b["net_name"] for b in managed_doc.extract_pin_label_bindings()
    }
    for net in ir_data["nets"]:
        net_name = net["name"]
        for pin_ref in net["pins"]:
            key = (pin_ref["ref"], pin_ref["pin"])
            assert key in binding_index, (
                f"No OpenClaw:bind= marker found for {pin_ref['ref']} pin {pin_ref['pin']!r} "
                f"(expected net {net_name!r}). "
                f"Bindings present: {sorted(binding_index.keys())}"
            )
            actual_net = binding_index[key]
            assert actual_net == net_name, (
                f"{pin_ref['ref']} pin {pin_ref['pin']!r}: "
                f"expected net {net_name!r} but schematic records {actual_net!r}"
            )


def test_circuit_fidelity_multi_component_testlib(tmp_path: Path) -> None:
    """Circuit fidelity: a 3-component, 4-net circuit with TestLib symbols.

    Uses R, OpAmp (flat), and DerivedOpAmp (extends OpAmp) together.
    Verifies that every component is placed and every net/pin binding is
    recorded correctly — including pins inherited by DerivedOpAmp.

    This test runs without system KiCad libraries and is always executed in CI.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "22k"},
            {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
        ],
        "nets": [
            # IN+ (pin 1 of DerivedOpAmp, inherited from OpAmp) through R1
            {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
            # IN- (pin 2, inherited) through R2
            {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            # Feedback: OUT (pin 6, inherited) back to IN- via R2
            {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R2", "pin": "2"}]},
            # Input bias
            {"name": "GND", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="FidelityTestLib",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.symbols_added == 3
    assert result.nets_applied == 4

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    _check_circuit_fidelity(ir_data, managed_doc)


def test_wires_connect_at_pin_endpoints(tmp_path: Path) -> None:  # noqa: PLR0912
    """P1: wires in the managed schematic start at the actual library pin endpoints.

    Before the P1 fix, _write_nets used arbitrary symbol-relative offsets
    (sym_x + 5.08, sym_y + 2.54*index) regardless of which pin was being
    wired.  After the fix, each wire must start at the exact (x, y) derived
    from the pin's ``(at X Y angle)`` in the library, translated by the
    symbol placement position.

    Circuit: R1 and R2 in series (VCC→R1→MID→R2→GND).
    TestLib:R pin positions:
      pin 1 at (at 0 0 0)   → endpoint at symbol_origin + (0, 0)
      pin 2 at (at 5.08 0 180) → endpoint at symbol_origin + (5.08, 0)
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "MID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="WireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all wire start points from the AST.
    wire_starts: set[tuple[float, float]] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        # items: [atom("pts"), ListNode("xy", x1, y1), ListNode("xy", x2, y2)]
        xy1 = pts.items[1]
        if isinstance(xy1, ListNode) and xy1.key == "xy" and len(xy1.items) >= 3:
            try:
                x = round(float(xy1.items[1].value), 2)  # type: ignore[union-attr]
                y = round(float(xy1.items[2].value), 2)  # type: ignore[union-attr]
                wire_starts.add((x, y))
            except (ValueError, AttributeError):
                pass

    # Compute expected pin endpoints from the ACTUAL symbol positions in the
    # generated schematic, applying the same rotation logic as _write_symbols.
    pin_at = read_lib_symbol_pin_at("TestLib", "R", symbols_dir=fixtures_dir)
    assert pin_at, "TestLib:R pin positions not found in fixture library"

    ir = CircuitIR.load(ir_path)
    layout: dict[str, tuple[float, float]] = {
        str(sym["ref"]): (cast(float, sym["x"]), cast(float, sym["y"]))
        for sym in managed_doc.list_symbols()
    }
    orientations = compute_orientations(ir, layout)

    expected_endpoints: dict[tuple[str, str], tuple[float, float]] = {}
    for sym in managed_doc.list_symbols():
        ref = str(sym["ref"])
        sx, sy = cast(float, sym["x"]), cast(float, sym["y"])
        rotation = orientations.get(ref, 0)
        if rotation == 0:
            for pin_num, (px, py, _pa) in pin_at.items():
                expected_endpoints[(ref, pin_num)] = (round(sx + px, 2), round(sy + py, 2))
        else:
            theta = math.radians(rotation)
            cos_t = math.cos(theta)
            sin_t = math.sin(theta)
            for pin_num, (px, py, _pa) in pin_at.items():
                rpx = cos_t * px - sin_t * py
                rpy = sin_t * px + cos_t * py
                expected_endpoints[(ref, pin_num)] = (round(sx + rpx, 2), round(sy + rpy, 2))

    # Verify every expected pin endpoint has a wire starting there.
    # Skip power symbols (#PWR* refs) — they are placed at stub ends and do
    # not need outgoing wires of their own.
    missing: list[str] = []
    for (ref, pin), (ex, ey) in sorted(expected_endpoints.items()):
        if ref.startswith("#"):
            continue  # power symbol — no outgoing wire expected
        if (ex, ey) not in wire_starts:
            missing.append(f"{ref} pin {pin}: expected wire start at ({ex}, {ey})")

    assert not missing, (
        "Wire(s) do not start at pin endpoints — wiring is disconnected:\n"
        + "\n".join(f"  {m}" for m in missing)
        + f"\nActual wire starts: {sorted(wire_starts)}"
    )


def test_direct_wiring_not_all_label_only(tmp_path: Path) -> None:
    """Router: a 2-pin net within routing range must be wired directly, not via label.

    A two-resistor voltage-divider (VCC→R1→MID→R2→GND) has three nets:
     - VCC  (power)  → gets a power:VCC symbol (Phase 3 strategy)
     - MID  (2-pin)  → R1-pin2 and R2-pin1 are adjacent-tier (tier distance=1)
                       and within the 200 mm manhattan cap; router must emit
                       an L-shaped wire, NOT a net label
     - GND  (power)  → gets a power:GND symbol (Phase 3 strategy)

    Assertions
    ----------
    1. No ``(label "MID" …)`` node exists in the managed schematic.
    2. At least 5 wire segments are present (4 pin stubs + ≥1 L-route bridge) —
       a direct-wire bridge was actually generated between R1 and R2.
    3. ``power:VCC`` and ``power:GND`` symbol instances exist (Phase 3) —
       power net routing uses symbols, not global labels.
    """
    ir_path = tmp_path / "divider.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
                    {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
                ],
                "nets": [
                    {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
                    {
                        "name": "MID",
                        "pins": [
                            {"ref": "R1", "pin": "2"},
                            {"ref": "R2", "pin": "1"},
                        ],
                    },
                    {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_new_from_netlist(
        Namespace(
            name="DirectWireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )
    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all net-label text values from the schematic AST.
    label_names: set[str] = set()
    for node in walk(managed_doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
        ):
            label_names.add(node.items[1].value)

    # Assertion 1: MID must NOT appear as a label — it must be directly wired.
    assert "MID" not in label_names, (
        f"Net 'MID' found as a schematic label; expected direct wire routing. "
        f"All labels present: {sorted(label_names)}"
    )

    # Assertion 2: a direct-wire bridge between R1 and R2 must have been emitted.
    # For direct routing the router adds 2 stubs per MID pin + 1–2 L-route bridge
    # segments.  For only stub fallback it would have added a local net label for
    # MID (caught by assertion 1).  We verify that at least one bridge wire
    # exists in addition to the 4 pin-stub wires (VCC, GND, R1-pin2, R2-pin1).
    all_wire_segments: list[tuple[float, float, float, float]] = []
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None or len(pts.items) < 3:  # noqa: PLR2004
            continue
        xy1, xy2 = pts.items[1], pts.items[2]
        if isinstance(xy1, ListNode) and isinstance(xy2, ListNode):
            try:
                x1 = float(xy1.items[1].value)  # type: ignore[union-attr]
                y1 = float(xy1.items[2].value)  # type: ignore[union-attr]
                x2 = float(xy2.items[1].value)  # type: ignore[union-attr]
                y2 = float(xy2.items[2].value)  # type: ignore[union-attr]
                all_wire_segments.append((x1, y1, x2, y2))
            except (ValueError, AttributeError, IndexError):
                pass

    # 4 stub wires (VCC stub, GND stub, R1-pin2 stub, R2-pin1 stub) + at least
    # one L-route bridge = minimum 5 wire segments for a direct-wire routing.
    assert len(all_wire_segments) >= 5, (  # noqa: PLR2004
        f"Expected ≥5 wire segments for direct-wire routing; found {len(all_wire_segments)}. "
        "The router may not have emitted an L-route bridge between R1 and R2."
    )

    # Assertion 3 (Phase 3): Single-pin power nets must get power symbol nodes
    # (power:VCC / power:GND), not local labels or global labels.
    power_lib_ids: set[str] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "symbol"):
            continue
        lib_id_node = find_first(node, "lib_id")
        if (
            lib_id_node is not None
            and len(lib_id_node.items) >= 2  # noqa: PLR2004
            and isinstance(lib_id_node.items[1], StringNode)
        ):
            power_lib_ids.add(lib_id_node.items[1].value)
    assert "power:VCC" in power_lib_ids, (
        f"Expected power:VCC symbol for power net 'VCC'; lib_ids found: {sorted(power_lib_ids)}"
    )
    assert "power:GND" in power_lib_ids, (
        f"Expected power:GND symbol for power net 'GND'; lib_ids found: {sorted(power_lib_ids)}"
    )


@_skip_no_system_symbols
def test_ne5532_full_circuit_fidelity_with_system_libraries(tmp_path: Path) -> None:
    """Circuit fidelity: NE5532 op-amp circuit using real KiCad system libraries.

    NE5532 uses (extends "LM2904") in the KiCad library.  This test verifies
    the complete pipeline on a realistic circuit:

    - U1 NE5532 (dual op-amp, 8 pins, all inherited from LM2904)
    - R1-R4 Device:R

    The circuit exercises BOTH op-amp units inside U1:
      Unit A: pins 3 (IN+), 2 (IN-), 1 (OUT)
      Unit B: pins 5 (IN+), 6 (IN-), 7 (OUT)
      Power:  pins 8 (V+), 4 (V-)

    Fidelity assertions:
    1. The managed schematic places explicit KiCad units ``U1A``, ``U1B``, ``U1P``.
    2. All 8 nets have correct OpenClaw:bind= markers against those unit refs.
    3. NE5532 is embedded as a flat (non-extends) symbol — read_lib_symbol_def_flat
       merges LM2904's geometry into the NE5532 node so KiCad renders it correctly
       without needing a separate LM2904 entry in lib_symbols.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "Amplifier_Operational:NE5532", "value": "NE5532"},
            {"ref": "R1", "symbol": "Device:R", "value": "10k"},
            {"ref": "R2", "symbol": "Device:R", "value": "100k"},
            {"ref": "R3", "symbol": "Device:R", "value": "10k"},
            {"ref": "R4", "symbol": "Device:R", "value": "100k"},
        ],
        "nets": [
            # Power rails
            {"name": "VCC", "pins": [{"ref": "U1", "pin": "8"}]},
            {
                "name": "GND",
                "pins": [
                    {"ref": "U1", "pin": "4"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                ],
            },
            # Unit A: inverting amplifier (pins 1, 2, 3)
            {"name": "IN_A", "pins": [{"ref": "U1", "pin": "3"}, {"ref": "R1", "pin": "1"}]},
            {"name": "IN_N_A", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "OUT_A", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R2", "pin": "2"}]},
            # Unit B: inverting amplifier (pins 5, 6, 7)
            {"name": "IN_B", "pins": [{"ref": "U1", "pin": "5"}, {"ref": "R3", "pin": "1"}]},
            {"name": "IN_N_B", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R4", "pin": "1"}]},
            {"name": "OUT_B", "pins": [{"ref": "U1", "pin": "7"}, {"ref": "R4", "pin": "2"}]},
        ],
    }
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="NE5532Circuit",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
            mode="internal",
        )
    )

    assert result.symbols_added == 7
    assert result.nets_applied == 8

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}
    assert {"R1", "R2", "R3", "R4", "U1A", "U1B", "U1P"} <= set(placed_symbols)
    assert placed_symbols["U1A"]["unit"] == "1"
    assert placed_symbols["U1B"]["unit"] == "2"
    assert placed_symbols["U1P"]["unit"] == "3"

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    assert binding_index[("U1P", "8")] == "VCC"
    assert binding_index[("U1P", "4")] == "GND"
    assert binding_index[("U1A", "3")] == "IN_A"
    assert binding_index[("U1A", "2")] == "IN_N_A"
    assert binding_index[("U1A", "1")] == "OUT_A"
    assert binding_index[("U1B", "5")] == "IN_B"
    assert binding_index[("U1B", "6")] == "IN_N_B"
    assert binding_index[("U1B", "7")] == "OUT_B"

    # Extends-chain specific: NE5532 is embedded as a flat symbol; LM2904 is NOT
    # embedded separately — its geometry was merged into the NE5532 node.
    embedded_ids = _get_lib_symbol_ids(managed_doc)
    assert "Amplifier_Operational:NE5532" in embedded_ids, (
        f"Derived symbol NE5532 missing from lib_symbols. Embedded: {embedded_ids}"
    )
    assert "Amplifier_Operational:LM2904" not in embedded_ids, (
        f"Base symbol LM2904 should not be embedded separately (geometry was merged "
        f"into NE5532 by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded NE5532 node must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    ne5532_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "Amplifier_Operational:NE5532"
        ),
        None,
    )
    assert ne5532_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in ne5532_node.items)
    assert not extends_found, (
        "NE5532 lib_symbols entry still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


# ---------------------------------------------------------------------------
# search-symbols / debug-symbol tests
# ---------------------------------------------------------------------------

from kicad_pcb.commands.search import cmd_debug_symbol, cmd_search_symbols  # noqa: E402
from kicad_pcb.results import DebugSymbolResult, SearchSymbolsResult  # noqa: E402

_TESTLIB_SYMBOLS_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def test_search_symbols_finds_exact_match() -> None:
    """Exact symbol name in query returns that symbol."""
    args = Namespace(query="OpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert isinstance(result, SearchSymbolsResult)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:OpAmp" in ids


def test_search_symbols_finds_derived_symbol() -> None:
    """Searching 'DerivedOpAmp' finds both derived and possibly base symbol."""
    args = Namespace(query="DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    ids = [m.symbol_id for m in result.matches]
    assert "TestLib:DerivedOpAmp" in ids


def test_search_symbols_no_match_returns_empty() -> None:
    """Query with no matches returns an empty matches tuple."""
    args = Namespace(query="zzz_no_such_thing_xyz", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_empty_query_returns_empty() -> None:
    """Blank query returns empty results without error."""
    args = Namespace(query="   ", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert result.matches == ()


def test_search_symbols_result_fields() -> None:
    """SymbolMatch fields are correctly populated."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    r_match = next((m for m in result.matches if m.symbol_id == "TestLib:R"), None)
    assert r_match is not None, "TestLib:R not found in results"
    assert isinstance(r_match.pin_count, int)
    assert r_match.pin_count >= 0
    assert isinstance(r_match.description, str)


def test_search_symbols_limit_respected() -> None:
    """--limit caps the number of results returned."""
    args = Namespace(query="Op", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=1)
    result = cmd_search_symbols(args)
    assert len(result.matches) <= 1


def test_search_symbols_searched_dirs_reported() -> None:
    """symbols_dirs field reflects the directory that was searched."""
    args = Namespace(query="R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR), limit=20)
    result = cmd_search_symbols(args)
    assert any(str(_TESTLIB_SYMBOLS_DIR) in d for d in result.symbols_dirs)


@_skip_no_system_symbols
def test_search_symbols_kicad9_renamed_symbols() -> None:
    """Verify KiCad 9 renames: C_Polarized and R_Potentiometer exist; legacy names do not."""
    args_cp = Namespace(
        query="polarized capacitor",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=20,
    )
    result_cp = cmd_search_symbols(args_cp)
    ids_cp = [m.symbol_id for m in result_cp.matches]
    assert "Device:C_Polarized" in ids_cp, f"C_Polarized missing; got {ids_cp}"
    assert "Device:CP" not in ids_cp, "KiCad-8 legacy Device:CP should not appear in KiCad 9"

    args_pot = Namespace(
        query="potentiometer",
        symbols_dir=str(_KICAD_SYSTEM_SYMBOLS),
        limit=5,
    )
    result_pot = cmd_search_symbols(args_pot)
    ids_pot = [m.symbol_id for m in result_pot.matches]
    assert "Device:R_Potentiometer" in ids_pot, f"R_Potentiometer missing; got {ids_pot}"


# ---------------------------------------------------------------------------
# debug-symbol tests (P2)
# ---------------------------------------------------------------------------


def test_debug_symbol_standalone() -> None:
    """A symbol with its own pins reports correct list and no extends_base."""
    args = Namespace(symbol="TestLib:R", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:R"
    assert result.extends_base is None
    assert result.pin_count == 2
    assert set(result.pin_numbers) == {"1", "2"}


def test_debug_symbol_extends() -> None:
    """An extends symbol reports extends_base and inherits parent pin count."""
    args = Namespace(symbol="TestLib:DerivedOpAmp", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    result = cmd_debug_symbol(args)
    assert isinstance(result, DebugSymbolResult)
    assert result.symbol_id == "TestLib:DerivedOpAmp"
    assert result.extends_base == "TestLib:OpAmp"
    assert result.pin_count == 4  # inherited from OpAmp
    assert set(result.pin_numbers) == {"1", "2", "3", "6"}


def test_debug_symbol_not_found() -> None:
    """A non-existent symbol raises UserError with SYMBOL_NOT_FOUND code."""
    args = Namespace(symbol="TestLib:NoSuchSymbol", symbols_dir=str(_TESTLIB_SYMBOLS_DIR))
    with pytest.raises(UserError) as exc_info:
        cmd_debug_symbol(args)
    assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND


# ---------------------------------------------------------------------------
# Hierarchy path tests (fix: sheet_instances and symbol instances use parent UUID)
# ---------------------------------------------------------------------------


def test_update_managed_path_qualifies_sheet_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in sheet_instances."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa-bbbb-cccc")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "2"))
  )
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "11111111-2222-3333-4444-555555555555"
    doc.update_managed_path(test_uuid)

    si = find_first(doc.root, "sheet_instances")
    assert si is not None
    path_node = find_first(si, "path")
    assert path_node is not None
    assert isinstance(path_node.items[1], StringNode)
    assert path_node.items[1].value == f"/{test_uuid}/"


def test_update_managed_path_qualifies_symbol_instances(tmp_path: Path) -> None:
    """update_managed_path replaces '/' with '/{uuid}/' in symbol instance paths."""
    sch_path = tmp_path / "managed.kicad_sch"
    sch_path.write_text(
        """(kicad_sch (version 20230121) (generator eeschema)
  (uuid "aaaa")
  (paper "A4")
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50.80 76.20 0) (unit 1)
    (uuid "sym-uuid-1")
    (instances
      (project "myproj" (path "/" (reference "R1") (unit 1)))
    )
  )
  (sheet_instances (path "/" (page "1")))
)
""",
        encoding="utf-8",
    )
    doc = SchematicDoc.load(sch_path)
    test_uuid = "aaaabbbb-cccc-dddd-eeee-ffffffffffff"
    doc.update_managed_path(test_uuid)

    # All (path ...) nodes in the document must be qualified — none may remain "/".
    for node in walk(doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "path"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            assert node.items[1].value != "/", (
                "Found unqualified '/' path after update_managed_path"
            )
            assert node.items[1].value == f"/{test_uuid}/", (
                f"Expected /{test_uuid}/, got {node.items[1].value!r}"
            )


def test_managed_schematic_hierarchy_paths_match_parent_sheet_uuid(tmp_path: Path) -> None:
    """Integration: managed schematic sheet_instances and symbol instances
    carry the UUID of the (sheet ...) entry in the parent root schematic.
    """
    ir_data = {
        "version": "1",
        "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
        "nets": [{"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]}],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="HierTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    # Extract the managed-sheet UUID from the parent (root) schematic.
    parent_doc = SchematicDoc.load(result.schematic_path)
    sheet_uuid: str | None = None
    for item in parent_doc.root.items:
        if not (isinstance(item, ListNode) and item.key == "sheet"):
            continue
        for sub in item.items:
            if (
                isinstance(sub, ListNode)
                and sub.key == "uuid"
                and len(sub.items) >= 2
                and isinstance(sub.items[1], StringNode)
            ):
                sheet_uuid = sub.items[1].value
                break
        if sheet_uuid:
            break
    assert sheet_uuid is not None, "Parent schematic has no (sheet (uuid ...)) node"

    expected_path = f"/{sheet_uuid}/"

    # Check the managed schematic: all (path ...) nodes must be qualified.
    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    unqualified: list[str] = []
    wrong_uuid: list[str] = []
    for node in walk(managed_doc.root):
        if not (
            isinstance(node, ListNode)
            and node.key == "path"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            continue
        val = node.items[1].value
        if val == "/":
            unqualified.append(repr(node))
        elif val != expected_path:
            wrong_uuid.append(f"{val!r} (expected {expected_path!r})")

    assert not unqualified, f"Unqualified '/' paths remain: {unqualified}"
    assert not wrong_uuid, f"Paths with wrong UUID: {wrong_uuid}"
