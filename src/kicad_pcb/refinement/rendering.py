"""Deterministic single-sheet rendering for vision refinement."""

from __future__ import annotations

import hashlib
import logging
import math
import shutil
import struct
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.compat import KiCadVersion
from kicad_pcb.errors import ToolError, UserError
from kicad_pcb.sch_doc import SchematicDoc

from .page_geometry import schematic_page_bounds

LOGGER = logging.getLogger(__name__)

RENDER_SCHEMA_VERSION = "1.1"
REVIEW_PIXELS_PER_MM = 8.0
MAX_REVIEW_REGION_DIMENSION_PX = 3584
MAX_REVIEW_REGIONS = 4
REVIEW_REGION_OVERLAP_MM = 12.7


class SvgRasterizer(Protocol):
    def rasterize(self, svg_path: Path, png_path: Path) -> None: ...


class RsvgConvertRasterizer:
    def __init__(self, binary: str = "rsvg-convert") -> None:
        self.binary = binary

    def rasterize(self, svg_path: Path, png_path: Path) -> None:
        result = subprocess.run(
            [self.binary, "-f", "png", "-o", str(png_path), str(svg_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            raise ToolError("SVG rasterization failed")


@dataclass(frozen=True)
class SchematicRenderRegionArtifact:
    region_id: str
    image_index: int
    row: int
    column: int
    svg_hash: str
    png_hash: str
    view_box_mm: tuple[float, float, float, float]
    width_px: int
    height_px: int
    pixels_per_mm_x: float
    pixels_per_mm_y: float
    svg_path: Path
    png_path: Path


@dataclass(frozen=True)
class SchematicRenderArtifact:
    schema_version: str
    schematic_hash: str
    svg_hash: str
    png_hash: str
    kicad_version: str
    sheet_id: str
    width_px: int
    height_px: int
    svg_view_box_mm: tuple[float, float, float, float]
    pixels_per_mm_x: float
    pixels_per_mm_y: float
    svg_path: Path
    png_path: Path
    review_regions: tuple[SchematicRenderRegionArtifact, ...] = ()

    @property
    def review_image_paths(self) -> tuple[Path, ...]:
        if self.review_regions:
            return tuple(region.png_path for region in self.review_regions)
        return (self.png_path,)


def render_schematic_for_refinement(
    schematic: Path,
    output_dir: Path,
    *,
    adapter: KicadCliAdapter,
    rasterizer: SvgRasterizer | None = None,
) -> SchematicRenderArtifact:
    if not schematic.is_file():
        raise UserError("Schematic to render does not exist.", code="REFINEMENT_RENDER_FAILED")
    doc = SchematicDoc.load(schematic)
    page = schematic_page_bounds(doc)
    version = adapter.detected_version
    if version is None or version < KiCadVersion(9, 0, 0):
        raise UserError(
            "Visual refinement requires KiCad 9 or newer.",
            code="REFINEMENT_RENDER_FAILED",
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    rasterizer = rasterizer or RsvgConvertRasterizer()
    temp_dir = Path(tempfile.mkdtemp(prefix="refinement-render-", dir=output_dir))
    try:
        requested_svg = temp_dir / "render.svg"
        result = adapter.export_svg_sch(schematic, requested_svg)
        if not result.ok:
            raise ToolError(f"KiCad schematic SVG export failed with exit code {result.returncode}")
        candidates = sorted(temp_dir.glob("*.svg"))
        if len(candidates) != 1:
            raise UserError(
                "Refinement renderer requires exactly one schematic sheet.",
                code="REFINEMENT_RENDER_SHEET_AMBIGUOUS",
                details={"svg_count": len(candidates)},
            )
        viewbox = _svg_view_box(candidates[0])
        if abs(viewbox[2] - page.width_mm) > 0.1 or abs(viewbox[3] - page.height_mm) > 0.1:
            raise UserError(
                "Rendered SVG viewBox does not match schematic paper declaration.",
                code="REFINEMENT_RENDER_GEOMETRY_MISMATCH",
                details={
                    "view_box": viewbox,
                    "paper_mm": [page.width_mm, page.height_mm],
                },
            )
        svg_out = output_dir / "schematic.svg"
        png_out = output_dir / "schematic.png"
        shutil.copyfile(candidates[0], svg_out)
        rasterizer.rasterize(svg_out, png_out)
        width, height = _png_dimensions(png_out)
        if width <= 0 or height <= 0:
            raise UserError(
                "Rasterized refinement image is empty.",
                code="REFINEMENT_RENDER_FAILED",
            )
        review_regions = _render_review_regions(
            svg_out,
            output_dir,
            viewbox=viewbox,
            rasterizer=rasterizer,
        )
        return SchematicRenderArtifact(
            RENDER_SCHEMA_VERSION,
            _sha(schematic),
            _sha(svg_out),
            _sha(png_out),
            str(version),
            "1",
            width,
            height,
            viewbox,
            width / viewbox[2],
            height / viewbox[3],
            svg_out,
            png_out,
            review_regions,
        )
    finally:
        try:
            shutil.rmtree(temp_dir)
        except OSError as exc:
            LOGGER.warning(
                "failed to clean refinement render temp directory",
                extra={"error_type": type(exc).__name__},
            )


def _svg_view_box(path: Path) -> tuple[float, float, float, float]:
    try:
        root = ET.parse(path).getroot()
        raw = root.attrib.get("viewBox", "")
        values = tuple(float(value) for value in raw.replace(",", " ").split())
    except (ET.ParseError, OSError, ValueError) as exc:
        raise UserError(
            "Unable to parse rendered SVG viewBox.",
            code="REFINEMENT_RENDER_FAILED",
        ) from exc
    if len(values) != 4 or not all(math.isfinite(v) for v in values):
        raise UserError("Rendered SVG has invalid viewBox.", code="REFINEMENT_RENDER_FAILED")
    return values  # type: ignore[return-value]


def _render_review_regions(
    source_svg: Path,
    output_dir: Path,
    *,
    viewbox: tuple[float, float, float, float],
    rasterizer: SvgRasterizer,
) -> tuple[SchematicRenderRegionArtifact, ...]:
    x, y, width_mm, height_mm = viewbox
    max_span_mm = MAX_REVIEW_REGION_DIMENSION_PX / REVIEW_PIXELS_PER_MM
    x_windows = _axis_windows(x, width_mm, max_span_mm)
    y_windows = _axis_windows(y, height_mm, max_span_mm)
    region_count = len(x_windows) * len(y_windows)
    if region_count > MAX_REVIEW_REGIONS:
        raise UserError(
            "Schematic page requires too many readable vision-review tiles.",
            code="REFINEMENT_RENDER_TOO_LARGE",
            details={
                "required_regions": region_count,
                "max_regions": MAX_REVIEW_REGIONS,
                "review_pixels_per_mm": REVIEW_PIXELS_PER_MM,
                "max_region_dimension_px": MAX_REVIEW_REGION_DIMENSION_PX,
            },
        )

    review_dir = output_dir / "review-regions"
    review_dir.mkdir(parents=True, exist_ok=True)
    regions: list[SchematicRenderRegionArtifact] = []
    image_index = 0
    for row, (region_y, region_height) in enumerate(y_windows):
        for column, (region_x, region_width) in enumerate(x_windows):
            region_id = f"r{row:02d}-c{column:02d}"
            width_px = max(1, math.ceil(region_width * REVIEW_PIXELS_PER_MM))
            height_px = max(1, math.ceil(region_height * REVIEW_PIXELS_PER_MM))
            svg_path = review_dir / f"{region_id}.svg"
            png_path = review_dir / f"{region_id}.png"
            region_viewbox = (region_x, region_y, region_width, region_height)
            _write_region_svg(
                source_svg,
                svg_path,
                viewbox=region_viewbox,
                width_px=width_px,
                height_px=height_px,
            )
            rasterizer.rasterize(svg_path, png_path)
            actual_width, actual_height = _png_dimensions(png_path)
            if (actual_width, actual_height) != (width_px, height_px):
                raise UserError(
                    "Rasterized refinement review region has unexpected dimensions.",
                    code="REFINEMENT_RENDER_GEOMETRY_MISMATCH",
                    details={
                        "region_id": region_id,
                        "expected_px": [width_px, height_px],
                        "actual_px": [actual_width, actual_height],
                    },
                )
            regions.append(
                SchematicRenderRegionArtifact(
                    region_id=region_id,
                    image_index=image_index,
                    row=row,
                    column=column,
                    svg_hash=_sha(svg_path),
                    png_hash=_sha(png_path),
                    view_box_mm=region_viewbox,
                    width_px=width_px,
                    height_px=height_px,
                    pixels_per_mm_x=width_px / region_width,
                    pixels_per_mm_y=height_px / region_height,
                    svg_path=svg_path,
                    png_path=png_path,
                )
            )
            image_index += 1
    return tuple(regions)


def _axis_windows(
    origin: float,
    length: float,
    max_span: float,
) -> tuple[tuple[float, float], ...]:
    if length <= max_span:
        return ((origin, length),)
    usable_step = max_span - REVIEW_REGION_OVERLAP_MM
    if usable_step <= 0:
        raise RuntimeError("invalid refinement review tiling constants")
    count = math.ceil((length - REVIEW_REGION_OVERLAP_MM) / usable_step)
    span = (length + REVIEW_REGION_OVERLAP_MM * (count - 1)) / count
    step = span - REVIEW_REGION_OVERLAP_MM
    return tuple(
        (
            round(origin + index * step, 9),
            round(span, 9),
        )
        for index in range(count)
    )


def _write_region_svg(
    source: Path,
    destination: Path,
    *,
    viewbox: tuple[float, float, float, float],
    width_px: int,
    height_px: int,
) -> None:
    try:
        tree = ET.parse(source)
    except (ET.ParseError, OSError) as exc:
        raise UserError(
            "Unable to prepare rendered SVG review region.",
            code="REFINEMENT_RENDER_FAILED",
        ) from exc
    root = tree.getroot()
    root.set("viewBox", " ".join(_format_float(value) for value in viewbox))
    root.set("width", f"{width_px}px")
    root.set("height", f"{height_px}px")
    try:
        tree.write(destination, encoding="utf-8", xml_declaration=True)
    except OSError as exc:
        raise UserError(
            "Unable to write rendered SVG review region.",
            code="REFINEMENT_RENDER_FAILED",
        ) from exc


def _format_float(value: float) -> str:
    return f"{value:.9f}".rstrip("0").rstrip(".")


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise UserError("Rasterizer did not produce a valid PNG.", code="REFINEMENT_RENDER_FAILED")
    return struct.unpack(">II", data[16:24])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
