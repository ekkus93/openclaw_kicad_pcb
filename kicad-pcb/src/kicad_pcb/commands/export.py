"""Export commands: gerbers, drill, BOM, fab package, position file, 3D."""
from __future__ import annotations

import zipfile

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import ToolError, UserError
from ..runner import KICAD_CLI, check_kicad


def cmd_export_gerbers(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export Gerber files for manufacturing."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_dir = project.path / "gerbers"

    print("📤 Exporting Gerbers...")

    result, gerber_files = cli.export_gerbers(pcb_file, output_dir)

    if result.returncode != 0:
        msg = "Gerber export failed"
        if result.stderr:
            msg += f"\n{result.stderr}"
        raise ToolError(msg)

    print(f"✅ Exported {len(gerber_files)} Gerber files to {output_dir}")
    for f in gerber_files:
        print(f"   {f.name}")


def cmd_export_drill(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export drill files."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    output_dir = project.path / "gerbers"

    print("📤 Exporting drill files...")

    result = cli.export_drill(pcb_file, output_dir)

    if result.returncode == 0:
        print(f"✅ Drill files exported to {output_dir}")
    else:
        print("❌ Drill export failed")


def cmd_export_bom(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export bill of materials using kicad-cli."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    output_file = project.path / "bom.csv"
    print("📤 Exporting BOM...")

    result, lines = cli.export_bom(sch_file, output_file)

    if result.returncode == 0 and lines:
        print(f"✅ BOM exported: {output_file}")
        print(f"   {max(0, len(lines) - 1)} component line(s)")
        for line in lines[:20]:
            print(f"  {line.rstrip()}")
    else:
        msg = "BOM export failed — is the schematic populated?"
        if result.stderr:
            msg += f"\n{result.stderr[:400]}"
        raise ToolError(msg)


def cmd_package_for_fab(args) -> None:
    """Create ZIP with all fabrication files."""
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    gerber_dir = project.path / "gerbers"

    if not gerber_dir.exists() or not list(gerber_dir.glob("*")):
        print("⚠️  No Gerber files found. Running export first...")
        # Would call cmd_export_gerbers here

    output_name = args.output or f"{project.name}_fab.zip"
    output_path = project.path / output_name

    print("📦 Creating fabrication package...")

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        if gerber_dir.exists():
            for f in gerber_dir.iterdir():
                zf.write(f, f.name)

    size_kb = output_path.stat().st_size / 1024
    print(f"✅ Created: {output_path}")
    print(f"   Size: {size_kb:.1f} KB")
    print("\n📤 Ready to upload to PCBWay!")


def cmd_export_pos(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export component position (pick-and-place) file."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}-pos.csv"
    print("📤 Exporting position file...")

    result, lines = cli.export_pos(pcb_file, output_file)

    if result.returncode == 0:
        print(f"✅ Position file: {output_file}")
        print(f"   {max(0, len(lines) - 1)} component(s)")
    else:
        msg = "Position export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)


def cmd_export_3d(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export PCB as STEP 3D model."""
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}.step"
    print("📤 Exporting STEP 3D model...")

    result, size_bytes = cli.export_step(pcb_file, output_file)

    if result.returncode == 0 and size_bytes > 0:
        size_kb = size_bytes / 1024
        print(f"✅ STEP model: {output_file}")
        print(f"   Size: {size_kb:.1f} KB")
        print("   Open with FreeCAD, Fusion 360, or any STEP viewer.")
    else:
        msg = "STEP export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)
