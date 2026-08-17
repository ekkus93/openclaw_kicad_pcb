"""Reproducible baseline evidence bundles for schematic-refinement evaluation."""

from __future__ import annotations

import re
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.corpus.reports import write_json_report
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.electrical import (
    SchematicElectricalBaseline,
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
    require_schematic_electrical_invariance,
)
from kicad_pcb.refinement.metrics import RefinementMetricReport, compute_refinement_metrics
from kicad_pcb.refinement.rendering import (
    SchematicRenderArtifact,
    SvgRasterizer,
    render_schematic_for_refinement,
)

REFINEMENT_BASELINE_SCHEMA_VERSION = "1.0"
_SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")


@dataclass(frozen=True)
class RefinementBaselineFixtureDefinition:
    fixture_id: str
    source_fixture_id: str
    categories: tuple[str, ...]
    known_visual_defects: tuple[str, ...]


@dataclass(frozen=True)
class RefinementBaselineCaptureInputs:
    fixture: RefinementBaselineFixtureDefinition
    electrical_baseline: SchematicElectricalBaseline
    electrical_report: SchematicElectricalVerificationReport
    metrics: RefinementMetricReport
    render: SchematicRenderArtifact


@dataclass(frozen=True)
class RefinementBaselineCaptureRequest:
    fixture: RefinementBaselineFixtureDefinition
    authoritative_ir: CircuitIR
    schematic: Path
    adapter: KicadCliAdapter
    rasterizer: SvgRasterizer | None = None


def capture_refinement_baseline(
    request: RefinementBaselineCaptureRequest,
    *,
    output_root: Path,
    work_root: Path,
) -> Path:
    """Capture one real schematic baseline and atomically publish its evidence bundle."""

    _validate_fixture(request.fixture)
    work_root.mkdir(parents=True, exist_ok=True)
    render_work = Path(tempfile.mkdtemp(prefix=f".{request.fixture.fixture_id}-", dir=work_root))
    try:
        baseline = build_schematic_electrical_baseline(request.authoritative_ir, request.schematic)
        electrical = require_schematic_electrical_invariance(
            authoritative_ir=request.authoritative_ir,
            baseline=baseline,
            candidate_schematic=request.schematic,
            adapter=request.adapter,
            work_dir=render_work / "electrical",
        )
        metrics = compute_refinement_metrics(request.schematic)
        render = render_schematic_for_refinement(
            request.schematic,
            render_work / "render",
            adapter=request.adapter,
            rasterizer=request.rasterizer,
        )
        return write_refinement_baseline_bundle(
            output_root,
            RefinementBaselineCaptureInputs(
                fixture=request.fixture,
                electrical_baseline=baseline,
                electrical_report=electrical,
                metrics=metrics,
                render=render,
            ),
        )
    finally:
        shutil.rmtree(render_work, ignore_errors=True)


def write_refinement_baseline_bundle(
    output_root: Path,
    inputs: RefinementBaselineCaptureInputs,
) -> Path:
    """Atomically write one path-sanitized Phase N baseline evidence bundle."""

    _validate_fixture(inputs.fixture)
    if not inputs.electrical_report.passed:
        raise UserError(
            "Refinement evaluation baseline must pass electrical verification.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )
    if inputs.metrics.schematic_hash != inputs.electrical_baseline.accepted_schematic_hash:
        raise UserError(
            "Refinement evaluation metrics are not bound to the accepted baseline schematic.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )
    if inputs.render.schematic_hash != inputs.electrical_baseline.accepted_schematic_hash:
        raise UserError(
            "Refinement evaluation render is not bound to the accepted baseline schematic.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )

    output_root.mkdir(parents=True, exist_ok=True)
    final_dir = output_root / inputs.fixture.fixture_id
    if final_dir.exists():
        raise UserError(
            "Refinement evaluation baseline already exists.",
            code="REFINEMENT_EVALUATION_BASELINE_EXISTS",
            details={"fixture_id": inputs.fixture.fixture_id},
        )

    temp_dir = Path(tempfile.mkdtemp(prefix=".refinement-baseline-", dir=output_root))
    try:
        render_dir = temp_dir / "render"
        _copy_render(inputs.render, render_dir)
        write_json_report(temp_dir / "electrical_baseline.json", asdict(inputs.electrical_baseline))
        write_json_report(temp_dir / "electrical.json", asdict(inputs.electrical_report))
        write_json_report(temp_dir / "metrics.json", inputs.metrics.to_dict())
        write_json_report(temp_dir / "manifest.json", _manifest_payload(inputs))
        if final_dir.exists():
            raise UserError(
                "Refinement evaluation baseline already exists.",
                code="REFINEMENT_EVALUATION_BASELINE_EXISTS",
                details={"fixture_id": inputs.fixture.fixture_id},
            )
        temp_dir.replace(final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return final_dir


def _manifest_payload(inputs: RefinementBaselineCaptureInputs) -> dict[str, object]:
    return {
        "schema_version": REFINEMENT_BASELINE_SCHEMA_VERSION,
        "fixture_id": inputs.fixture.fixture_id,
        "source_fixture_id": inputs.fixture.source_fixture_id,
        "categories": list(inputs.fixture.categories),
        "known_visual_defects": list(inputs.fixture.known_visual_defects),
        "source_schematic_sha256": inputs.electrical_baseline.accepted_schematic_hash,
        "authoritative_ir_sha256": inputs.electrical_baseline.authoritative_hash,
        "electrical_status": inputs.electrical_report.status,
        "kicad_version": inputs.electrical_report.kicad_version,
        "metrics": {
            "schema_version": inputs.metrics.schema_version,
            "schematic_hash": inputs.metrics.schematic_hash,
        },
        "render": _render_metadata(inputs.render),
    }


def _render_metadata(render: SchematicRenderArtifact) -> dict[str, object]:
    return {
        "schema_version": render.schema_version,
        "schematic_hash": render.schematic_hash,
        "svg_hash": render.svg_hash,
        "png_hash": render.png_hash,
        "sheet_id": render.sheet_id,
        "width_px": render.width_px,
        "height_px": render.height_px,
        "view_box_mm": list(render.svg_view_box_mm),
        "pixels_per_mm_x": render.pixels_per_mm_x,
        "pixels_per_mm_y": render.pixels_per_mm_y,
        "svg": "render/baseline.svg",
        "png": "render/baseline.png",
        "review_regions": [
            {
                "region_id": region.region_id,
                "image_index": region.image_index,
                "row": region.row,
                "column": region.column,
                "svg_hash": region.svg_hash,
                "png_hash": region.png_hash,
                "view_box_mm": list(region.view_box_mm),
                "width_px": region.width_px,
                "height_px": region.height_px,
                "pixels_per_mm_x": region.pixels_per_mm_x,
                "pixels_per_mm_y": region.pixels_per_mm_y,
                "svg": f"render/review-regions/{region.region_id}.svg",
                "png": f"render/review-regions/{region.region_id}.png",
            }
            for region in render.review_regions
        ],
    }


def _copy_render(render: SchematicRenderArtifact, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(render.svg_path, destination / "baseline.svg")
    shutil.copyfile(render.png_path, destination / "baseline.png")
    if not render.review_regions:
        return
    regions_dir = destination / "review-regions"
    regions_dir.mkdir(parents=True, exist_ok=True)
    for region in render.review_regions:
        shutil.copyfile(region.svg_path, regions_dir / f"{region.region_id}.svg")
        shutil.copyfile(region.png_path, regions_dir / f"{region.region_id}.png")


def _validate_fixture(fixture: RefinementBaselineFixtureDefinition) -> None:
    _validate_identifier("fixture_id", fixture.fixture_id)
    _validate_identifier("source_fixture_id", fixture.source_fixture_id)
    if not fixture.categories or not all(_valid_text(item) for item in fixture.categories):
        raise UserError(
            "Refinement evaluation fixture categories are invalid.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )
    if not fixture.known_visual_defects or not all(
        _valid_text(item) for item in fixture.known_visual_defects
    ):
        raise UserError(
            "Refinement evaluation fixture defect notes are invalid.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )


def _validate_identifier(field: str, value: str) -> None:
    if not _SAFE_ID.fullmatch(value):
        raise UserError(
            f"Refinement evaluation {field} is unsafe.",
            code="REFINEMENT_EVALUATION_BASELINE_INVALID",
        )


def _valid_text(value: str) -> bool:
    return bool(value.strip()) and len(value) <= 1000 and "\x00" not in value


def fixture_definition(
    *,
    fixture_id: str,
    source_fixture_id: str,
    categories: Sequence[str],
    known_visual_defects: Sequence[str],
) -> RefinementBaselineFixtureDefinition:
    """Build an immutable validated fixture definition from manifest values."""

    fixture = RefinementBaselineFixtureDefinition(
        fixture_id=fixture_id,
        source_fixture_id=source_fixture_id,
        categories=tuple(categories),
        known_visual_defects=tuple(known_visual_defects),
    )
    _validate_fixture(fixture)
    return fixture
