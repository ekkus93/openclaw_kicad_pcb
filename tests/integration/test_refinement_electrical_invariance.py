from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.runner import find_kicad_cli


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_real_kicad_export_preserves_generated_divider_electrically(tmp_path: Path) -> None:
    authoritative_payload = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {
                "name": "VMID",
                "pins": [
                    {"ref": "R1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }
    ir_path = tmp_path / "divider_ir.json"
    ir_path.write_text(json.dumps(authoritative_payload), encoding="utf-8")
    symbols_dir = Path(__file__).parents[1] / "fixtures" / "symbols"
    generated = cmd_new_from_netlist(
        Namespace(
            name="RefinementElectricalDivider",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(symbols_dir),
            mode="internal",
            layout="graphviz",
            label_mode="debug",
        )
    )

    authoritative = CircuitIR.load(ir_path)
    schematic = generated.managed_schematic_path
    baseline = build_schematic_electrical_baseline(authoritative, schematic)
    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())

    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=baseline,
        candidate_schematic=schematic,
        adapter=adapter,
        work_dir=tmp_path / "verification",
    )

    assert report.passed, report.mismatches
