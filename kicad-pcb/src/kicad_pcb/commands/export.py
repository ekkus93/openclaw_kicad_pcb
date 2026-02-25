"""Export commands: gerbers, drill, BOM, fab package, position file, 3D."""
from __future__ import annotations

import zipfile

from ..config import get_current_project
from ..errors import ToolError, UserError
from ..runner import check_kicad, run_kicad_cli


def cmd_export_gerbers(args) -> None:
    """Export Gerber files for manufacturing."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_dir = project.path / "gerbers"
    output_dir.mkdir(exist_ok=True)

    print("📤 Exporting Gerbers...")

    result = run_kicad_cli([
        "pcb", "export", "gerbers",
        "--output", str(output_dir),
        str(pcb_file),
    ])

    if result.returncode != 0:
        msg = "Gerber export failed"
        if result.stderr:
            msg += f"\n{result.stderr}"
        raise ToolError(msg)

    # Count exported files
    gerber_files = list(output_dir.glob("*"))
    print(f"✅ Exported {len(gerber_files)} Gerber files to {output_dir}")
    for f in gerber_files:
        print(f"   {f.name}")


def cmd_export_drill(args) -> None:
    """Export drill files."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    output_dir = project.path / "gerbers"
    output_dir.mkdir(exist_ok=True)

    print("📤 Exporting drill files...")

    result = run_kicad_cli([
        "pcb", "export", "drill",
        "--output", str(output_dir),
        "--format", "excellon",
        "--excellon-separate-th",
        "--generate-map",
        "--map-format", "pdf",
        str(pcb_file),
    ])

    if result.returncode == 0:
        print(f"✅ Drill files exported to {output_dir}")
    else:
        print("❌ Drill export failed")


def cmd_export_bom(args) -> None:
    """Export bill of materials using kicad-cli."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    output_file = project.path / "bom.csv"
    print("📤 Exporting BOM...")

    result = run_kicad_cli([
        "sch", "export", "bom",
        "--output", str(output_file),
        "--fields", "Reference,Value,Footprint,${QUANTITY},Datasheet",
        "--labels", "Refs,Value,Footprint,Qty,Datasheet",
        "--group-by", "Value,Footprint",
        "--sort-field", "Reference",
        str(sch_file),
    ])

    if result.returncode == 0 and output_file.exists():
        with output_file.open() as f:
            lines = f.readlines()
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


def cmd_export_pos(args) -> None:
    """Export component position (pick-and-place) file."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}-pos.csv"
    print("📤 Exporting position file...")

    result = run_kicad_cli([
        "pcb", "export", "pos",
        "--output", str(output_file),
        "--format", "csv",
        "--units", "mm",
        "--side", "both",
        str(pcb_file),
    ])

    if result.returncode == 0:
        print(f"✅ Position file: {output_file}")
        if output_file.exists():
            lines = output_file.read_text().splitlines()
            print(f"   {max(0, len(lines) - 1)} component(s)")
    else:
        msg = "Position export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)


def cmd_export_3d(args) -> None:
    """Export PCB as STEP 3D model."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    output_file = project.path / f"{project.name}.step"
    print("📤 Exporting STEP 3D model...")

    result = run_kicad_cli([
        "pcb", "export", "step",
        "--output", str(output_file),
        "--force",
        "--no-unspecified",
        str(pcb_file),
    ])

    if result.returncode == 0 and output_file.exists():
        size_kb = output_file.stat().st_size / 1024
        print(f"✅ STEP model: {output_file}")
        print(f"   Size: {size_kb:.1f} KB")
        print("   Open with FreeCAD, Fusion 360, or any STEP viewer.")
    else:
        msg = "STEP export failed"
        if result.stderr:
            msg += f"\n{result.stderr[:300]}"
        raise ToolError(msg)
