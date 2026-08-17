from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.evaluation.refinement_baseline import (
    RefinementBaselineCaptureInputs,
    fixture_definition,
    write_refinement_baseline_bundle,
)
from kicad_pcb.refinement.electrical import (
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
)
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.rendering import (
    SchematicRenderArtifact,
    SchematicRenderRegionArtifact,
)

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
)


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inputs(tmp_path: Path) -> RefinementBaselineCaptureInputs:
    schematic = _FIXTURE / "baseline_generated.kicad_sch"
    authoritative = CircuitIR.load(_FIXTURE / "circuit_ir.json")
    baseline = build_schematic_electrical_baseline(authoritative, schematic)
    metrics = compute_refinement_metrics(schematic)

    svg = tmp_path / "source.svg"
    png = tmp_path / "source.png"
    region_svg = tmp_path / "region.svg"
    region_png = tmp_path / "region.png"
    svg.write_text('<svg viewBox="0 0 297 210"/>', encoding="utf-8")
    png.write_bytes(b"png")
    region_svg.write_text('<svg viewBox="0 0 297 210"/>', encoding="utf-8")
    region_png.write_bytes(b"region-png")
    region = SchematicRenderRegionArtifact(
        region_id="r00-c00",
        image_index=0,
        row=0,
        column=0,
        svg_hash=_hash(region_svg),
        png_hash=_hash(region_png),
        view_box_mm=(0.0, 0.0, 297.0, 210.0),
        width_px=2376,
        height_px=1680,
        pixels_per_mm_x=8.0,
        pixels_per_mm_y=8.0,
        svg_path=region_svg,
        png_path=region_png,
    )
    render = SchematicRenderArtifact(
        schema_version="1.1",
        schematic_hash=baseline.accepted_schematic_hash,
        svg_hash=_hash(svg),
        png_hash=_hash(png),
        kicad_version="9.0.0",
        sheet_id="1",
        width_px=297,
        height_px=210,
        svg_view_box_mm=(0.0, 0.0, 297.0, 210.0),
        pixels_per_mm_x=1.0,
        pixels_per_mm_y=1.0,
        svg_path=svg,
        png_path=png,
        review_regions=(region,),
    )
    electrical = SchematicElectricalVerificationReport(
        status="passed",
        authoritative_hash=baseline.authoritative_hash,
        accepted_schematic_hash=baseline.accepted_schematic_hash,
        candidate_schematic_hash=baseline.accepted_schematic_hash,
        candidate_fingerprint_hash=baseline.authoritative_hash,
        kicad_version="9.0.0",
    )
    return RefinementBaselineCaptureInputs(
        fixture=fixture_definition(
            fixture_id="fixture-001",
            source_fixture_id="source-001",
            categories=("crowded_layout",),
            known_visual_defects=("Dense local grouping reduces readability.",),
        ),
        electrical_baseline=baseline,
        electrical_report=electrical,
        metrics=metrics,
        render=render,
    )


def test_baseline_bundle_is_atomic_complete_and_path_sanitized(tmp_path: Path) -> None:
    output = write_refinement_baseline_bundle(tmp_path / "baselines", _inputs(tmp_path))

    assert {path.name for path in output.iterdir()} == {
        "electrical.json",
        "electrical_baseline.json",
        "manifest.json",
        "metrics.json",
        "render",
    }
    assert (output / "render" / "baseline.svg").is_file()
    assert (output / "render" / "baseline.png").is_file()
    assert (output / "render" / "review-regions" / "r00-c00.svg").is_file()
    assert (output / "render" / "review-regions" / "r00-c00.png").is_file()

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "1.0"
    assert manifest["electrical_status"] == "passed"
    assert manifest["categories"] == ["crowded_layout"]
    assert manifest["render"]["svg"] == "render/baseline.svg"
    assert manifest["render"]["review_regions"][0]["png"] == ("render/review-regions/r00-c00.png")
    assert str(tmp_path) not in json.dumps(manifest)
    assert not any(
        path.name.startswith(".refinement-baseline-") for path in output.parent.iterdir()
    )


def test_baseline_bundle_refuses_overwrite(tmp_path: Path) -> None:
    inputs = _inputs(tmp_path)
    output_root = tmp_path / "baselines"
    write_refinement_baseline_bundle(output_root, inputs)

    with pytest.raises(UserError) as exc_info:
        write_refinement_baseline_bundle(output_root, inputs)

    assert exc_info.value.code == "REFINEMENT_EVALUATION_BASELINE_EXISTS"


def test_fixture_definition_rejects_path_traversal() -> None:
    with pytest.raises(UserError) as exc_info:
        fixture_definition(
            fixture_id="../escape",
            source_fixture_id="source-001",
            categories=("crowded_layout",),
            known_visual_defects=("Defect note.",),
        )

    assert exc_info.value.code == "REFINEMENT_EVALUATION_BASELINE_INVALID"
