"""Export commands: gerbers, drill, BOM, fab package, position file, 3D."""

from __future__ import annotations

import zipfile

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import ToolError, UserError
from ..results import (
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    PackageFabResult,
)
from ..runner import check_kicad, find_kicad_cli


def cmd_export_gerbers(args, *, cli: KicadCliAdapter | None = None) -> ExportGerbersResult:
    """Export Gerber files for manufacturing."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_dir = project.path / "gerbers"

    result, gerber_files = cli.export_gerbers(pcb_file, output_dir)

    if result.returncode != 0:
        msg = "Gerber export failed"
        if result.stderr:
            msg += f"\n{result.stderr}"
        raise ToolError(msg)

    return ExportGerbersResult(output_dir=output_dir, files=tuple(gerber_files))


def cmd_export_drill(args, *, cli: KicadCliAdapter | None = None) -> ExportDrillResult:
    """Export drill files."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    output_dir = project.path / "gerbers"

    result = cli.export_drill(pcb_file, output_dir)

    if result.returncode != 0:
        raise ToolError("Drill export failed")
    return ExportDrillResult(output_dir=output_dir)


def cmd_export_bom(args, *, cli: KicadCliAdapter | None = None) -> ExportBomResult:
    """Export bill of materials using kicad-cli."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    output_file = project.path / "bom.csv"

    result, lines = cli.export_bom(sch_file, output_file)

    if result.returncode != 0 or not lines:
        msg = "BOM export failed — is the schematic populated?"
        if result.stderr:
            msg += f"\n{result.stderr[:400]}"
        raise ToolError(msg)

    return ExportBomResult(output_file=output_file, lines=tuple(lines))


def cmd_package_for_fab(args) -> PackageFabResult:
    """Create ZIP with all fabrication files."""
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    gerber_dir = project.path / "gerbers"

    if not gerber_dir.exists() or not list(gerber_dir.glob("*")):
        raise UserError("No Gerber files found. Run `export-gerbers` first.")

    output_name = args.output or f"{project.name}_fab.zip"
    output_path = project.path / output_name

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in gerber_dir.iterdir():
            zf.write(f, f.name)

    return PackageFabResult(output_path=output_path, size_bytes=output_path.stat().st_size)


def cmd_export_pos(args, *, cli: KicadCliAdapter | None = None) -> ExportPosResult:
    """Export component position (pick-and-place) file."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}-pos.csv"

    result, lines = cli.export_pos(pcb_file, output_file)

    if result.returncode != 0:
        msg = "Position export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)

    return ExportPosResult(output_file=output_file, component_count=max(0, len(lines) - 1))


def cmd_export_3d(args, *, cli: KicadCliAdapter | None = None) -> Export3dResult:
    """Export PCB as STEP 3D model."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}.step"

    result, size_bytes = cli.export_step(pcb_file, output_file)

    if result.returncode != 0 or size_bytes == 0:
        msg = "STEP export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)

    return Export3dResult(output_file=output_file, size_bytes=size_bytes)
