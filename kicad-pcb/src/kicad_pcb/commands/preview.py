"""Preview generation commands: preview-schematic, preview-pcb."""
from __future__ import annotations

from pathlib import Path

from ..config import get_current_project
from ..errors import UserError
from ..runner import check_kicad, run_kicad_cli


def cmd_preview_schematic(args) -> None:
    """Generate schematic preview image."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    project_dir = Path(project["path"])
    sch_file = project_dir / f"{project['name']}.kicad_sch"

    if not sch_file.exists():
        raise UserError(f"Schematic file not found: {sch_file}")

    output_file = project_dir / "schematic_preview.svg"

    print("🖼️  Generating schematic preview...")

    result = run_kicad_cli([
        "sch", "export", "svg",
        "--output", str(output_file),
        str(sch_file),
    ])

    if result.returncode == 0 and output_file.exists():
        print(f"✅ Preview saved: {output_file}")

        # Try to convert to PNG for easier viewing
        try:
            import cairosvg  # noqa: PLC0415

            png_file = project_dir / "schematic_preview.png"
            cairosvg.svg2png(url=str(output_file), write_to=str(png_file))
            print(f"   PNG: {png_file}")
        except ImportError:
            print("   (Install cairosvg for PNG conversion)")
    else:
        print("❌ Preview generation failed")


def cmd_preview_pcb(args) -> None:
    """Generate PCB preview images."""
    check_kicad()

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    project_dir = Path(project["path"])
    pcb_file = project_dir / f"{project['name']}.kicad_pcb"

    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    print("🖼️  Generating PCB previews...")

    # Export SVG for each major layer
    layers = ["F.Cu", "B.Cu", "F.Silkscreen", "Edge.Cuts"]

    for layer in layers:
        output_file = project_dir / f"pcb_preview_{layer.replace('.', '_')}.svg"
        result = run_kicad_cli([
            "pcb", "export", "svg",
            "--output", str(output_file),
            "--layers", layer,
            str(pcb_file),
        ])
        if result.returncode == 0:
            print(f"   ✅ {layer}: {output_file.name}")

    # Try 3D export
    glb_file = project_dir / "pcb_3d.glb"
    result = run_kicad_cli([
        "pcb", "export", "glb",
        "--output", str(glb_file),
        str(pcb_file),
    ])
    if result.returncode == 0 and glb_file.exists():
        print(f"   ✅ 3D: {glb_file.name}")
