from __future__ import annotations

import hashlib
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.rendering import SchematicRenderArtifact
from kicad_pcb.refinement.vision_context import build_vision_object_map


def _n3_fixture() -> tuple[Path, CircuitIR]:
    root = (
        Path(__file__).parents[1]
        / "fixtures"
        / "model_corpus"
        / "12v-to-5v-3-3v-switching-regulator-module-aeonlabs-ai-volvo-mkii-open-hardware"
    )
    return root / "source_normalized.kicad_sch", CircuitIR.load(root / "circuit_ir.json")


def _render(path: Path) -> SchematicRenderArtifact:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SchematicRenderArtifact(
        schema_version="1.0",
        schematic_hash=digest,
        svg_hash="a" * 64,
        png_hash="b" * 64,
        kicad_version="9.0.0",
        sheet_id="1",
        width_px=2970,
        height_px=2100,
        svg_view_box_mm=(0.0, 0.0, 297.0, 210.0),
        pixels_per_mm_x=10.0,
        pixels_per_mm_y=10.0,
        svg_path=Path("/tmp/refinement.svg"),
        png_path=Path("/tmp/refinement.png"),
    )


def test_n3_fixture_accepts_kicad_atom_form_uuids() -> None:
    schematic, ir = _n3_fixture()
    context = build_vision_object_map(
        schematic,
        authoritative_ir=ir,
        render=_render(schematic),
        metrics=compute_refinement_metrics(schematic),
    )

    component_uuids = [component.uuid for component in context.components]
    wire_uuids = [wire.uuid for wire in context.wires]
    assert component_uuids
    assert wire_uuids
    assert all(component_uuids)
    assert all(wire_uuids)
    assert len(component_uuids) == len(set(component_uuids))
    assert len(wire_uuids) == len(set(wire_uuids))
    assert "00d98d68-ba17-4b44-9ee7-0e87387450de" in wire_uuids
