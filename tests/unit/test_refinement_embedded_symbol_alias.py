from __future__ import annotations

from pathlib import Path

from kicad_pcb.refinement.schematic_semantics import (
    extract_schematic_semantics_from_doc,
    resolve_component_pin_position_candidates,
)
from kicad_pcb.sch_doc import SchematicDoc


def test_model_corpus_embedded_lib_name_alias_resolves_pin_geometry() -> None:
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "model_corpus"
        / "4-channel-switched-constant-current-source"
        / "source_normalized.kicad_sch"
    )
    doc = SchematicDoc.load(fixture)
    snapshot = extract_schematic_semantics_from_doc(doc)
    regulators = {
        component.ref: component
        for component in snapshot.components
        if component.ref in {"PS3", "PS4", "PS5", "PS6"}
    }

    assert set(regulators) == {"PS3", "PS4", "PS5", "PS6"}
    assert {component.embedded_symbol_name for component in regulators.values()} == {
        "NCV317MBSTT3G_1"
    }
    for component in regulators.values():
        candidates = resolve_component_pin_position_candidates(doc, component)
        assert {terminal.pin for terminal in candidates} == {"1", "2", "3", "4"}

    ps3 = regulators["PS3"]
    ps3_positions = {
        terminal.pin: positions
        for terminal, positions in resolve_component_pin_position_candidates(doc, ps3).items()
    }
    assert ps3_positions == {
        "1": ((87.63, 54.61),),
        "2": ((101.6, 41.91),),
        "3": ((78.74, 41.91),),
        "4": ((101.6, 44.45),),
    }
