from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.rendering import (
    SchematicRenderArtifact,
    SchematicRenderRegionArtifact,
)
from kicad_pcb.refinement.vision_context import build_vision_object_map


def _fixture() -> tuple[Path, CircuitIR]:
    root = (
        Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
    )
    return root / "baseline_generated.kicad_sch", CircuitIR.load(root / "circuit_ir.json")


def _render(path: Path, *, schematic_hash: str | None = None) -> SchematicRenderArtifact:
    digest = schematic_hash or hashlib.sha256(path.read_bytes()).hexdigest()
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


def test_object_map_binds_stable_ids_hashes_and_pixel_coordinates() -> None:
    schematic, ir = _fixture()
    metrics = compute_refinement_metrics(schematic)
    context = build_vision_object_map(
        schematic,
        authoritative_ir=ir,
        render=_render(schematic),
        metrics=metrics,
    )

    assert context.source_schematic_hash == metrics.schematic_hash
    assert context.schema_version == "1.1"
    assert context.render_png_hash == "b" * 64
    assert context.page_mm == (297.0, 210.0)
    assert context.image_px == (2970, 2100)
    assert len(context.object_ids) == (
        len(context.components)
        + len(context.pins)
        + len(context.wires)
        + len(context.labels)
        + len(context.junctions)
        + len(context.nets)
    )
    first = context.components[0]
    assert first.object_id == f"component:{first.uuid}"
    assert first.x_px == pytest.approx(first.x_mm * 10.0)
    assert first.y_px == pytest.approx(first.y_mm * 10.0)
    assert all(pin.object_id.startswith("pin:") for pin in context.pins)
    assert all(net.object_id == f"net:{net.name}" for net in context.nets)
    assert len(context.review_regions) == 1
    assert context.review_regions[0].region_id == "r00-c00"
    assert context.review_regions[0].view_box_mm == (0.0, 0.0, 297.0, 210.0)
    assert context.review_regions[0].png_hash == "b" * 64


def test_object_map_exposes_path_free_multi_region_coordinate_mapping() -> None:
    schematic, ir = _fixture()
    metrics = compute_refinement_metrics(schematic)
    render = _render(schematic)
    regions = (
        SchematicRenderRegionArtifact(
            region_id="r00-c00",
            image_index=0,
            row=0,
            column=0,
            svg_hash="c" * 64,
            png_hash="d" * 64,
            view_box_mm=(0.0, 0.0, 154.85, 210.0),
            width_px=1239,
            height_px=1680,
            pixels_per_mm_x=8.001,
            pixels_per_mm_y=8.0,
            svg_path=Path("/private/region-0.svg"),
            png_path=Path("/private/region-0.png"),
        ),
        SchematicRenderRegionArtifact(
            region_id="r00-c01",
            image_index=1,
            row=0,
            column=1,
            svg_hash="e" * 64,
            png_hash="f" * 64,
            view_box_mm=(142.15, 0.0, 154.85, 210.0),
            width_px=1239,
            height_px=1680,
            pixels_per_mm_x=8.001,
            pixels_per_mm_y=8.0,
            svg_path=Path("/private/region-1.svg"),
            png_path=Path("/private/region-1.png"),
        ),
    )
    render = SchematicRenderArtifact(**{**render.__dict__, "review_regions": regions})

    context = build_vision_object_map(
        schematic,
        authoritative_ir=ir,
        render=render,
        metrics=metrics,
    )

    assert [region.image_index for region in context.review_regions] == [0, 1]
    assert [region.png_hash for region in context.review_regions] == ["d" * 64, "f" * 64]
    assert context.review_regions[1].view_box_mm == (142.15, 0.0, 154.85, 210.0)
    serialized = context.to_dict()
    assert "/private/" not in str(serialized)


def test_object_map_rejects_stale_render_hash() -> None:
    schematic, ir = _fixture()
    metrics = compute_refinement_metrics(schematic)
    with pytest.raises(UserError, match="different schematic bytes"):
        build_vision_object_map(
            schematic,
            authoritative_ir=ir,
            render=_render(schematic, schematic_hash="0" * 64),
            metrics=metrics,
        )


def test_object_map_rejects_schematic_changed_after_capture(tmp_path: Path) -> None:
    schematic, ir = _fixture()
    candidate = tmp_path / "changed.kicad_sch"
    candidate.write_bytes(schematic.read_bytes())
    metrics = compute_refinement_metrics(candidate)
    render = _render(candidate)
    candidate.write_text(candidate.read_text() + "\n", encoding="utf-8")

    with pytest.raises(UserError, match="changed after render"):
        build_vision_object_map(candidate, authoritative_ir=ir, render=render, metrics=metrics)


def test_object_map_rejects_wire_without_stable_uuid(tmp_path: Path) -> None:
    schematic, ir = _fixture()
    text = schematic.read_text(encoding="utf-8")
    changed, count = re.subn(
        r"(\(wire\b.*?)(\s+\(uuid\s+[^\n]+\))",
        r"\1",
        text,
        count=1,
        flags=re.DOTALL,
    )
    assert count == 1
    candidate = tmp_path / "missing-wire-uuid.kicad_sch"
    candidate.write_text(changed, encoding="utf-8")
    metrics = compute_refinement_metrics(candidate)

    with pytest.raises(UserError, match="stable wire UUIDs"):
        build_vision_object_map(
            candidate,
            authoritative_ir=ir,
            render=_render(candidate),
            metrics=metrics,
        )
