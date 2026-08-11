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
        return SchematicRenderArtifact(
            "1.0",
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


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise UserError("Rasterizer did not produce a valid PNG.", code="REFINEMENT_RENDER_FAILED")
    return struct.unpack(">II", data[16:24])


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
