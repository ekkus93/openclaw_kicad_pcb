"""PCB layout commands: set-board-size, import-netlist, auto-place, auto-route."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..config import get_current_project
from ..errors import ToolError, UserError
from ..models import BoardOutlineRect, FootprintMoveSpec
from ..pcb_doc import PcbDoc
from ..pipeline import mutate_and_validate_pcb
from ..results import AutoPlaceResult, AutoRouteResult, ImportNetlistResult, SetBoardSizeResult
from ..runner import check_kicad, find_kicad_cli


def cmd_set_board_size(args) -> SetBoardSizeResult:
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

    def _mutate(doc: PcbDoc) -> None:
        doc.set_rect_outline(outline.width, outline.height)

    mutate_and_validate_pcb(
        pcb_file, _mutate, operation="set-board-size", dry_run=getattr(args, "dry_run", False)
    )
    return SetBoardSizeResult(
        width=outline.width,
        height=outline.height,
        pcb_file_name=pcb_file.name,
        dry_run=getattr(args, "dry_run", False),
    )


def cmd_import_netlist(args, *, cli: KicadCliAdapter | None = None) -> ImportNetlistResult:
    """Export netlist from schematic and report components for PCB placement.

    Note: KiCad 7+ links PCB and schematic via UUIDs — no separate netlist
    import is required. Use Tools → Update PCB from Schematic inside KiCad's
    PCB editor for the full sync. This command exports the netlist for inspection.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    output_file = project.path / f"{project.name}.net"

    result, xml_text = cli.export_netlist(sch_file, output_file)

    if result.returncode != 0 or not xml_text:
        msg = "Netlist export failed — populate the schematic first."
        if result.stderr:
            msg += f"\n{result.stderr[:400]}"
        raise ToolError(msg)

    refs = re.findall(r"<ref>([^<]+)</ref>", xml_text)
    values = re.findall(r"<value>([^<]+)</value>", xml_text)
    footprints_raw = re.findall(r"<footprint>([^<]*)</footprint>", xml_text)
    footprints_raw += [""] * (len(refs) - len(footprints_raw))
    components = tuple(zip(refs, values, footprints_raw))
    return ImportNetlistResult(netlist_file=output_file, components=components)


def cmd_auto_place(args) -> AutoPlaceResult:
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
    doc = PcbDoc.load(pcb_file)

    footprints = doc.all_footprints()
    if not footprints:
        raise UserError(
            "No footprints found in PCB file.\n"
            "   Add components to the schematic, then run import-netlist."
        )

    placements: list[FootprintMoveSpec] = []
    col_size = 5
    col_width = spacing * 3
    for idx, (ref, _node) in enumerate(footprints):
        col = idx // col_size
        row = idx % col_size
        nx = 10.0 + col * col_width
        ny = 10.0 + row * spacing
        placements.append(FootprintMoveSpec(ref=ref, x=nx, y=ny))

    def _mutate(doc: PcbDoc) -> None:
        for spec in placements:
            doc.move_footprint(spec.ref, spec.x, spec.y)

    mutate_and_validate_pcb(
        pcb_file, _mutate, operation="auto-place", dry_run=getattr(args, "dry_run", False)
    )
    return AutoPlaceResult(
        placed=tuple(placements), spacing=spacing, dry_run=getattr(args, "dry_run", False)
    )


def cmd_auto_route(args, *, cli: KicadCliAdapter | None = None) -> AutoRouteResult:  # noqa: PLR0912
    """Auto-route the PCB using Freerouting (requires Java + Freerouting JAR).

    Install Freerouting: https://github.com/freerouting/freerouting/releases
    Save the JAR to ~/freerouting.jar, then re-run this command.

    *cli* is an optional injectable :class:`~kicad_pcb.adapters.KicadCliAdapter`
    used for the kicad-cli DSN export/import steps.
    """
    if cli is None:
        check_kicad()
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())

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
        raise UserError(
            "Freerouting JAR not found.\n\n"
            "Install:\n"
            "  1. https://github.com/freerouting/freerouting/releases\n"
            "  2. Save as ~/freerouting.jar\n"
            "  3. Re-run: auto-route --jar ~/freerouting.jar\n\n"
            "Alternative: Route manually in KiCad PCB editor (Route menu)."
        )

    java = shutil.which("java")
    if not java:
        raise UserError("Java not found. Install: sudo apt install openjdk-17-jre")

    dsn_file = project.path / f"{project.name}.dsn"
    ses_file = project.path / f"{project.name}.ses"

    dsn_result = cli.export_specctra_dsn(pcb_file, dsn_file)
    if dsn_result.returncode != 0:
        msg = "DSN export failed — ensure PCB has components placed and netlist set."
        if dsn_result.stderr:
            msg += f"\n{dsn_result.stderr[:300]}"
        raise ToolError(msg)

    try:
        fr_result = subprocess.run(
            [
                java,
                "-jar",
                freerouting_jar,
                "-de",
                str(dsn_file),
                "-do",
                str(ses_file),
                "-mp",
                "100",
            ],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ToolError("Freerouting timed out after 5 minutes.") from exc

    if fr_result.returncode != 0 or not ses_file.exists():
        msg = "Freerouting failed."
        if fr_result.stderr:
            msg += f"\n{fr_result.stderr[:300]}"
        raise ToolError(msg)

    imp = cli.import_specctra_ses(ses_file, pcb_file)
    return AutoRouteResult(ses_file_name=ses_file.name, routes_imported=imp.returncode == 0)
