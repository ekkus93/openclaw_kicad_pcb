"""PCB layout commands: set-board-size, import-netlist, auto-place, auto-route."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import ToolError, UserError
from ..fs import _atomic_write, _new_uuid
from ..models import BoardOutlineRect, FootprintMoveSpec
from ..runner import KICAD_CLI, check_kicad


def cmd_set_board_size(args) -> None:
    """Set board outline by writing an Edge.Cuts rectangle.

    Usage: set-board-size WxH   (dimensions in mm)
    Example: set-board-size 50x30
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    outline = BoardOutlineRect.from_args(args)
    lines = "\n".join(
        f'  (gr_line (start {s[0]:.3f} {s[1]:.3f}) (end {e[0]:.3f} {e[1]:.3f})\n'
        f'    (stroke (width 0.05) (type solid)) (layer "Edge.Cuts") (uuid "{_new_uuid()}"))'
        for s, e in outline.corners
    )

    text = pcb_file.read_text()
    # Remove any previous Edge.Cuts gr_line entries
    text = re.sub(
        r'\s*\(gr_line[^\n]*\n[^\n]*"Edge\.Cuts"[^\n]*\n[^)]*\)',
        "",
        text,
    )
    last_paren = text.rfind(")")
    text = text[:last_paren] + "\n" + lines + "\n)\n"
    _atomic_write(pcb_file, text, "kicad_pcb", operation="set-board-size")

    print(f"✅ Board outline: {outline.width} mm × {outline.height} mm")
    print(f"   Edge.Cuts rectangle written to {pcb_file.name}")


def cmd_import_netlist(args, *, cli: KicadCliAdapter | None = None) -> None:
    """Export netlist from schematic and report components for PCB placement.

    Note: KiCad 7+ links PCB and schematic via UUIDs — no separate netlist
    import is required. Use Tools → Update PCB from Schematic inside KiCad's
    PCB editor for the full sync. This command exports the netlist for inspection.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    output_file = project.path / f"{project.name}.net"
    print("📋 Exporting netlist...")

    result, xml_text = cli.export_netlist(sch_file, output_file)

    if result.returncode == 0 and xml_text:
        refs = re.findall(r"<ref>([^<]+)</ref>", xml_text)
        values = re.findall(r"<value>([^<]+)</value>", xml_text)
        footprints = re.findall(r"<footprint>([^<]*)</footprint>", xml_text)
        footprints += [""] * (len(refs) - len(footprints))  # pad if missing
        print(f"✅ Netlist: {output_file}")
        if refs:
            print(f"\n📦 Components ({len(refs)}):")
            for r, v, fp in zip(refs, values, footprints):
                tag = f"  [{fp}]" if fp else "  [NO FOOTPRINT ⚠️]"
                print(f"  {r:<6} {v:<20}{tag}")
            missing = [r for r, fp in zip(refs, footprints) if not fp]
            if missing:
                print(f"\n⚠️  Assign footprints to: {', '.join(missing)}")
        print("\n💡 Open PCB editor → Tools → Update PCB from Schematic to sync.")
    else:
        msg = "Netlist export failed — populate the schematic first."
        if result.stderr:
            msg += f"\n{result.stderr[:400]}"
        raise ToolError(msg)


def cmd_auto_place(args) -> None:
    """Arrange all footprints on the PCB in a grid layout.

    Usage: auto-place [--spacing N]   (spacing in mm, default 10)
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    spacing: float = float(args.spacing) if args.spacing else 10.0
    text = pcb_file.read_text()

    # Match footprint blocks: (footprint "lib:name" ... (at X Y ...) ...)
    fp_pattern = re.compile(
        r'(\(footprint "([^"]*)"(?:.*?\n)*?\s*\(at )([\d.-]+) ([\d.-]+)([^)]*\))',
        re.MULTILINE,
    )
    matches = list(fp_pattern.finditer(text))

    if not matches:
        print("ℹ️  No footprints found in PCB file.")
        print("   Add components to the schematic, then run import-netlist.")
        return

    placed: list[FootprintMoveSpec] = []
    col_size = 5
    col_width = spacing * 3

    def replacer(m: re.Match) -> str:
        idx = len(placed)
        col = idx // col_size
        row = idx % col_size
        nx = 10.0 + col * col_width
        ny = 10.0 + row * spacing
        placed.append(FootprintMoveSpec(ref=m.group(2), x=nx, y=ny))
        return f"{m.group(1)}{nx:.3f} {ny:.3f}{m.group(5)}"

    new_text = fp_pattern.sub(replacer, text)
    _atomic_write(pcb_file, new_text, "kicad_pcb", operation="auto-place")

    print(f"✅ Placed {len(placed)} footprint(s) (spacing {spacing} mm):")
    for spec in placed:
        label = spec.ref.split(":")[-1] if ":" in spec.ref else spec.ref
        print(f"   {label:<30} → ({spec.x:.1f}, {spec.y:.1f})")
    print("\n💡 Run `drc` to check, then route with `auto-route` or KiCad PCB editor.")


def cmd_auto_route(args, *, cli: KicadCliAdapter | None = None) -> None:  # noqa: PLR0912
    """Auto-route the PCB using Freerouting (requires Java + Freerouting JAR).

    Install Freerouting: https://github.com/freerouting/freerouting/releases
    Save the JAR to ~/freerouting.jar, then re-run this command.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`
    used for the kicad-cli DSN export/import steps.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=KICAD_CLI)

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    pcb_file = project.pcb_file
    if not pcb_file.exists():
        raise UserError(f"PCB file not found: {pcb_file}")

    # Locate Freerouting JAR
    jar_arg = getattr(args, "jar", None)
    freerouting_jar: str | None = jar_arg
    if not freerouting_jar:
        for candidate in [
            Path.home() / "freerouting.jar",
            Path.home() / ".local/bin/freerouting.jar",
            Path("/opt/freerouting/freerouting.jar"),
        ]:
            if candidate.exists():
                freerouting_jar = str(candidate)
                break

    if not freerouting_jar:
        print("❌ Freerouting JAR not found.")
        print("\nInstall:")
        print("  1. https://github.com/freerouting/freerouting/releases")
        print("  2. Save as ~/freerouting.jar")
        print("  3. Re-run: auto-route --jar ~/freerouting.jar")
        print("\nAlternative: Route manually in KiCad PCB editor (Route menu).")
        return

    java = shutil.which("java")
    if not java:
        print("❌ Java not found. Install: sudo apt install openjdk-17-jre")
        return

    dsn_file = project.path / f"{project.name}.dsn"
    ses_file = project.path / f"{project.name}.ses"

    print("📤 Exporting Specctra DSN...")
    dsn_result = cli.export_specctra_dsn(pcb_file, dsn_file)
    if dsn_result.returncode != 0:
        print("❌ DSN export failed — ensure PCB has components placed and netlist set.")
        if dsn_result.stderr:
            print(dsn_result.stderr[:300])
        return

    print("🔀 Running Freerouting auto-router (this may take a minute)...")
    try:
        fr_result = subprocess.run(
            [java, "-jar", freerouting_jar,
             "-de", str(dsn_file), "-do", str(ses_file), "-mp", "100"],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except subprocess.TimeoutExpired:
        print("⚠️  Freerouting timed out after 5 minutes.")
        return

    if fr_result.returncode == 0 and ses_file.exists():
        print(f"✅ Routes complete: {ses_file.name}")
        imp = cli.import_specctra_ses(ses_file, pcb_file)
        if imp.returncode == 0:
            print(f"✅ Routes imported into {pcb_file.name}")
        else:
            print("⚠️  Manual import: File → Import → Specctra Session in KiCad PCB editor")
    else:
        print("❌ Freerouting failed.")
        if fr_result.stderr:
            print(fr_result.stderr[:300])
