"""Preview generation commands: preview-schematic, preview-pcb."""

from __future__ import annotations

import importlib
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import ToolError, UserError
from ..results import PreviewPcbResult, PreviewSchematicResult
from ..runner import check_kicad, find_kicad_cli


def cmd_preview_schematic(args, *, cli: KicadCliAdapter | None = None) -> PreviewSchematicResult:
    """Generate schematic preview image."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project.path / "schematic_preview.svg"

    result = cli.export_svg_sch(sch_file, output_file)

    if result.returncode != 0 or not output_file.exists():
        raise ToolError("Schematic preview generation failed")

    # Optional PNG conversion — cairosvg is an optional dependency.
    png_file = None
    try:
        cairosvg = importlib.import_module("cairosvg")
        png_path = project.path / "schematic_preview.png"
        cairosvg.svg2png(url=str(output_file), write_to=str(png_path))
        png_file = png_path
    except ImportError:
        pass

    return PreviewSchematicResult(svg_file=output_file, png_file=png_file)


def cmd_preview_pcb(args, *, cli: KicadCliAdapter | None = None) -> PreviewPcbResult:
    """Generate PCB preview images."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    layers = ["F.Cu", "B.Cu", "F.Silkscreen", "Edge.Cuts"]
    layer_files: list[tuple[str, Path]] = []

    for layer in layers:
        output_file = project.path / f"pcb_preview_{layer.replace('.', '_')}.svg"
        result = cli.export_svg_pcb(pcb_file, output_file, layer)
        if result.returncode == 0:
            layer_files.append((layer, output_file))

    glb_file = project.path / "pcb_3d.glb"
    glb_result = cli.export_glb(pcb_file, glb_file)
    resolved_glb = glb_file if (glb_result.returncode == 0 and glb_file.exists()) else None

    return PreviewPcbResult(
        layer_files=tuple(layer_files),
        glb_file=resolved_glb,
    )
