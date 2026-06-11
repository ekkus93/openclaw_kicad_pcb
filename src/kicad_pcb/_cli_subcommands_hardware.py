"""Register PCB, export, validation, lint, format, and misc CLI subcommands."""

from __future__ import annotations

import argparse

from .commands.doctor import cmd_doctor
from .commands.export import (
    cmd_export_3d,
    cmd_export_bom,
    cmd_export_drill,
    cmd_export_gerbers,
    cmd_export_pos,
    cmd_package_for_fab,
)
from .commands.external import cmd_pcbway_quote
from .commands.lint import (
    cmd_format_pcb,
    cmd_format_sch,
    cmd_lint_pcb,
    cmd_lint_sch,
    cmd_validate_pcb,
    cmd_validate_sch,
)
from .commands.pcb import cmd_auto_place, cmd_auto_route, cmd_import_netlist, cmd_set_board_size
from .commands.validation import cmd_drc, cmd_erc


def _register_hardware_subcommands(  # noqa: PLR0915
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register PCB, export, validation, lint, format, and misc subcommands."""
    # drc
    p_drc = subparsers.add_parser("drc", help="Run design rules check")
    p_drc.add_argument("--strict", action="store_true", help="Strict mode")
    p_drc.set_defaults(func=cmd_drc)

    # erc
    p_erc = subparsers.add_parser("erc", help="Run electrical rules check")
    p_erc.set_defaults(func=cmd_erc)

    # export-gerbers
    p_gerbers = subparsers.add_parser("export-gerbers", help="Export Gerber files")
    p_gerbers.set_defaults(func=cmd_export_gerbers)

    # export-drill
    p_drill = subparsers.add_parser("export-drill", help="Export drill files")
    p_drill.set_defaults(func=cmd_export_drill)

    # export-bom
    p_bom = subparsers.add_parser("export-bom", help="Export bill of materials")
    p_bom.set_defaults(func=cmd_export_bom)

    # package-for-fab
    p_package = subparsers.add_parser("package-for-fab", help="Create fab ZIP")
    p_package.add_argument("-o", "--output", help="Output filename")
    p_package.set_defaults(func=cmd_package_for_fab)

    # set-board-size
    p_size = subparsers.add_parser("set-board-size", help="Set board outline (Edge.Cuts rectangle)")
    p_size.add_argument("size", metavar="WxH", help="Board dimensions in mm e.g. 50x30")
    p_size.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_size.set_defaults(func=cmd_set_board_size)

    # import-netlist
    p_nl = subparsers.add_parser(
        "import-netlist", help="Export netlist and report components for PCB"
    )
    p_nl.set_defaults(func=cmd_import_netlist)

    # auto-place
    p_ap = subparsers.add_parser("auto-place", help="Grid-place footprints on PCB")
    p_ap.add_argument("--spacing", type=float, default=10.0, help="Grid spacing in mm (default 10)")
    p_ap.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_ap.set_defaults(func=cmd_auto_place)

    # auto-route
    p_ar = subparsers.add_parser("auto-route", help="Auto-route PCB via Freerouting")
    p_ar.add_argument("--jar", help="Path to freerouting.jar")
    p_ar.set_defaults(func=cmd_auto_route)

    # export-pos
    p_pos = subparsers.add_parser("export-pos", help="Export pick-and-place position file")
    p_pos.set_defaults(func=cmd_export_pos)

    # export-3d
    p_3d = subparsers.add_parser("export-3d", help="Export STEP 3D model")
    p_3d.set_defaults(func=cmd_export_3d)

    # pcbway-quote
    p_quote = subparsers.add_parser("pcbway-quote", help="Get PCBWay quote")
    p_quote.add_argument("-q", "--quantity", type=int, default=5, help="Quantity")
    p_quote.add_argument("-l", "--layers", type=int, default=2, help="Layer count")
    p_quote.add_argument("-t", "--thickness", type=float, default=1.6, help="Thickness mm")
    p_quote.set_defaults(func=cmd_pcbway_quote)

    # doctor
    p_doctor = subparsers.add_parser("doctor", help="Check system config and diagnose issues")
    p_doctor.set_defaults(func=cmd_doctor)

    # lint-sch
    p_ls = subparsers.add_parser("lint-sch", help="Lint a .kicad_sch file")
    p_ls.add_argument("path", help="Path to .kicad_sch file")
    p_ls.set_defaults(func=cmd_lint_sch)

    # lint-pcb
    p_lp = subparsers.add_parser("lint-pcb", help="Lint a .kicad_pcb file")
    p_lp.add_argument("path", help="Path to .kicad_pcb file")
    p_lp.set_defaults(func=cmd_lint_pcb)

    # validate-sch
    p_vs = subparsers.add_parser("validate-sch", help="Validate syntax + lint a .kicad_sch file")
    p_vs.add_argument("path", help="Path to .kicad_sch file")
    p_vs.set_defaults(func=cmd_validate_sch)

    # validate-pcb
    p_vp = subparsers.add_parser("validate-pcb", help="Validate syntax + lint a .kicad_pcb file")
    p_vp.add_argument("path", help="Path to .kicad_pcb file")
    p_vp.set_defaults(func=cmd_validate_pcb)

    # format-sch
    p_fs = subparsers.add_parser("format-sch", help="Canonicalise a .kicad_sch file in-place")
    p_fs.add_argument("path", help="Path to .kicad_sch file")
    p_fs.set_defaults(func=cmd_format_sch)

    # format-pcb
    p_fp = subparsers.add_parser("format-pcb", help="Canonicalise a .kicad_pcb file in-place")
    p_fp.add_argument("path", help="Path to .kicad_pcb file")
    p_fp.set_defaults(func=cmd_format_pcb)
