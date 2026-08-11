from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.runner import find_kicad_cli


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_real_kicad_export_preserves_readability_fixture_electrically(tmp_path: Path) -> None:
    fixture = (
        Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
    )
    schematic = fixture / "baseline_generated.kicad_sch"
    authoritative = CircuitIR.load(fixture / "circuit_ir.json")
    baseline = build_schematic_electrical_baseline(authoritative, schematic)
    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())

    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=baseline,
        candidate_schematic=schematic,
        adapter=adapter,
        work_dir=tmp_path,
    )

    assert report.passed, report.mismatches
