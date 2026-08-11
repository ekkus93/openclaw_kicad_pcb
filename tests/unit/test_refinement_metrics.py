from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.page_geometry import schematic_page_bounds
from kicad_pcb.sch_doc import SchematicDoc


def test_a4_bounds_come_from_schematic_declaration() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    page = schematic_page_bounds(SchematicDoc.load(fixture))
    assert (page.width_mm, page.height_mm, page.paper) == (297.0, 210.0, "A4")


def test_readability_fixture_metrics_are_deterministic() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "readability"
        / "ne5532_headphone_amp_left_current"
        / "baseline_generated.kicad_sch"
    )
    first = compute_refinement_metrics(fixture)
    second = compute_refinement_metrics(fixture)
    assert first == second
    assert first.component_count > 0
    assert first.wire_segment_count > 0
    assert first.bend_count > 0
    assert first.total_wire_manhattan_length_mm > 0


def test_malformed_required_wire_geometry_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "bad.kicad_sch"
    path.write_text(
        '(kicad_sch (version 20230121) (paper "A4") '
        '(wire (uuid "w")) (sheet_instances (path "/" (page "1"))))',
        encoding="utf-8",
    )
    with pytest.raises(UserError, match="missing required pts"):
        compute_refinement_metrics(path)
