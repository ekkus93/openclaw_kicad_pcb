"""CLI entry-point: argument parsing and sub-command dispatch."""

from __future__ import annotations

import argparse
import json
import sys

from .commands._sch_apply import SCHEMATIC_HEURISTIC_PROFILES
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
from .commands.netlist import (
    cmd_apply_netlist,
    cmd_fix_netlist,
    cmd_info_sch,
    cmd_new_from_netlist,
    cmd_validate_netlist,
)
from .commands.patterns import cmd_apply_pattern
from .commands.pcb import cmd_auto_place, cmd_auto_route, cmd_import_netlist, cmd_set_board_size
from .commands.preview import cmd_preview_pcb, cmd_preview_schematic
from .commands.project import cmd_info, cmd_new, cmd_open
from .commands.sch import cmd_add_component, cmd_add_net, cmd_connect
from .commands.search import cmd_build_symbol_index, cmd_debug_symbol, cmd_search_symbols
from .commands.session import cmd_close_session, cmd_new_session, cmd_session_info
from .commands.validation import cmd_drc, cmd_erc
from .errors import KiCadError
from .formatting import format_result, format_result_json
from .lint import LINT_SUGGESTIONS, LintError, LintSeverity
from .results import DoctorResult, LintFileResult, ValidateFileResult


def _build_parser() -> argparse.ArgumentParser:  # noqa: PLR0915
    """Build and return the top-level argument parser.

    Extracted from :func:`main` so the parser structure can be verified in
    unit tests without spawning a subprocess or touching ``sys.argv``.
    """
    parser = argparse.ArgumentParser(
        prog="kicad_pcb",
        description="🔧 KiCad PCB Automation — Design to Manufacturing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="output_json",
        default=False,
        help="Output result as JSON",
    )
    parser.add_argument(
        "--backup",
        action="store_true",
        dest="backup",
        default=False,
        help="Write <file>.bak before overwriting any .kicad_sch / .kicad_pcb file",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # new
    p_new = subparsers.add_parser("new", help="Create new project")
    p_new.add_argument("name", help="Project name")
    p_new.add_argument("-d", "--description", help="Project description")
    p_new.set_defaults(func=cmd_new)

    # info
    p_info = subparsers.add_parser("info", help="Show project info")
    p_info.set_defaults(func=cmd_info)

    # open
    p_open = subparsers.add_parser("open", help="Open existing project")
    p_open.add_argument("path", help="Project path")
    p_open.set_defaults(func=cmd_open)

    # new-session
    p_new_session = subparsers.add_parser(
        "new-session",
        help="Start a new design session (creates an isolated working directory)",
        description=(
            "Create a fresh session directory under {projects_dir}/sessions/ and make "
            "it the active session.  All subsequent netlist files, KiCad project "
            "directories, and zip outputs go into this directory, preventing stale "
            "files from previous runs from being picked up."
        ),
    )
    p_new_session.add_argument(
        "--name", "-n", default="session", help="Short human-readable name (default: session)"
    )
    p_new_session.add_argument("-d", "--description", default="", help="Optional description")
    p_new_session.set_defaults(func=cmd_new_session)

    # session-info
    p_session_info = subparsers.add_parser(
        "session-info",
        help="Show the current session directory and its contents",
    )
    p_session_info.set_defaults(func=cmd_session_info)

    # close-session
    p_close_session = subparsers.add_parser(
        "close-session",
        help="Deactivate the current session (directory is preserved)",
    )
    p_close_session.set_defaults(func=cmd_close_session)

    # info-sch
    p_info_sch = subparsers.add_parser(
        "info-sch",
        help="Show schematic introspection data (root + managed sheet)",
        description=(
            "Introspect the current project schematic. "
            "The root schematic (<name>.kicad_sch) is intentionally thin and contains "
            "only a sheet reference; all generated symbols, wires, and net labels live "
            "in OpenClaw_Managed.kicad_sch. Both paths and their AST counts are returned."
        ),
    )
    p_info_sch.set_defaults(func=cmd_info_sch)

    # validate-netlist
    p_val_netlist = subparsers.add_parser(
        "validate-netlist",
        help="Validate a Circuit IR JSON netlist without writing any files",
        description=(
            "Validate a Circuit IR JSON file through all three layers: "
            "(1) Pydantic schema — correct keys, no extra fields, version string; "
            "(2) semantic — no duplicate refs/nets, no zero-pin nets, all pin refs resolve; "
            "(3) symbol + pin — every symbol exists in the library index, every pin is valid. "
            "No files are written. Use this to check a netlist before calling new-from-netlist."
        ),
    )
    p_val_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_val_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_val_netlist.set_defaults(func=cmd_validate_netlist)

    # apply-netlist
    p_apply = subparsers.add_parser(
        "apply-netlist",
        help="Apply Circuit IR JSON to OpenClaw managed schematic",
        description=(
            "Apply a Circuit IR JSON netlist to the current project. "
            "Generated content is written to OpenClaw_Managed.kicad_sch (the managed sheet); "
            "the root schematic (<name>.kicad_sch) stays thin and references the managed sheet. "
            "Use --dry-run to validate without writing."
        ),
    )
    p_apply.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_apply.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_apply.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="internal",
        help="Validation mode — deprecated, prefer --validate (default: internal)",
    )
    p_apply.add_argument(
        "--validate",
        choices=["none", "syntax", "lint", "kicad", "full"],
        default=None,
        help="Validation level; overrides --mode when given (default: lint)",
    )
    p_apply.add_argument(
        "--routing",
        choices=["bus", "hub", "labels"],
        default="bus",
        help="Routing style: bus/hub = spine routing, labels = label stubs (default: bus)",
    )
    p_apply.add_argument(
        "--heuristic-profile",
        choices=sorted(SCHEMATIC_HEURISTIC_PROFILES),
        default=None,
        help=(
            "Named schematic heuristic profile for bundled layout and routing policies "
            "(default: analog_audio)"
        ),
    )
    p_apply.add_argument(
        "--debug-dump",
        help="Optional path for a JSON debug dump covering layout, unit splitting, and routing",
    )
    p_apply.add_argument("--force", action="store_true", help="Adopt non-owned schematic")
    p_apply.add_argument("--dry-run", action="store_true", help="Validate without writing")
    p_apply.add_argument("--strict", action="store_true", help="Treat lint warnings as errors")
    p_apply.set_defaults(func=cmd_apply_netlist)

    # new-from-netlist
    p_new_netlist = subparsers.add_parser(
        "new-from-netlist",
        help="Create a project and compile Circuit IR deterministically",
        description=(
            "Create a new KiCad project and compile a Circuit IR JSON netlist into it. "
            "The root schematic (<name>.kicad_sch) is thin and contains a sheet reference; "
            "all generated symbols, wires, and net labels are written to "
            "OpenClaw_Managed.kicad_sch (the managed sheet)."
        ),
    )
    p_new_netlist.add_argument("--name", required=True, help="Project name")
    p_new_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_new_netlist.add_argument("--out-dir", help="Project output base directory")
    p_new_netlist.add_argument("--description", help="Optional project description")
    p_new_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_new_netlist.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="kicad",
        help="Validation mode — deprecated, prefer --validate (default: kicad)",
    )
    p_new_netlist.add_argument(
        "--validate",
        choices=["none", "syntax", "lint", "kicad", "full"],
        default=None,
        help="Validation level; overrides --mode when given (default: kicad)",
    )
    p_new_netlist.add_argument(
        "--routing",
        choices=["bus", "hub", "labels"],
        default="bus",
        help="Routing style: bus/hub = spine routing, labels = label stubs (default: bus)",
    )
    p_new_netlist.add_argument(
        "--heuristic-profile",
        choices=sorted(SCHEMATIC_HEURISTIC_PROFILES),
        default=None,
        help=(
            "Named schematic heuristic profile for bundled layout and routing policies "
            "(default: analog_audio)"
        ),
    )
    p_new_netlist.add_argument(
        "--debug-dump",
        help="Optional path for a JSON debug dump covering layout, unit splitting, and routing",
    )
    p_new_netlist.add_argument(
        "--no-auto-fix",
        action="store_false",
        dest="auto_fix",
        default=True,
        help="Disable deterministic auto-fix on validation failure",
    )
    p_new_netlist.add_argument(
        "--strict", action="store_true", help="Treat lint warnings as errors"
    )
    p_new_netlist.set_defaults(func=cmd_new_from_netlist)

    # fix-netlist
    p_fix_netlist = subparsers.add_parser(
        "fix-netlist",
        help="Auto-fix a Circuit IR JSON file and write the corrected version",
        description=(
            "Apply deterministic fixes to a Circuit IR JSON file without any LLM calls. "
            "Fixes are applied in layers: "
            "(1) schema structure (version, wrappers, forbidden keys); "
            "(2) component fields (removes 'type', inline 'pins'); "
            "(3) net pin types (integer values → strings); "
            "(4) pin aliases ('+'→'1', '-'→'2', 'TIP'→'T', etc. via library lookup). "
            "The corrected JSON is always written even when errors remain, so you can inspect "
            "progress or pass it directly to new-from-netlist."
        ),
    )
    p_fix_netlist.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_fix_netlist.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_fix_netlist.add_argument(
        "--output", help="Path for the fixed JSON (default: <stem>.fixed.json)"
    )
    p_fix_netlist.set_defaults(func=cmd_fix_netlist)

    # search-symbols
    p_search = subparsers.add_parser(
        "search-symbols",
        help="Search installed KiCad symbol libraries by keyword",
        description=(
            "Search all .kicad_sym files in the resolved symbol directories for symbols "
            "whose name or description contains the given keywords. "
            "Use this before writing Circuit IR JSON to find the correct symbol IDs for "
            "the installed KiCad version."
        ),
    )
    p_search.add_argument("query", help="Space-separated keywords (e.g. 'polarized capacitor')")
    p_search.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_search.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of results (default: 20)",
    )
    p_search.set_defaults(func=cmd_search_symbols)

    # build-symbol-index
    p_build_idx = subparsers.add_parser(
        "build-symbol-index",
        help="Pre-populate the symbol search cache for faster search-symbols queries",
        description=(
            "Scan all .kicad_sym files in the resolved symbol directories and store "
            "parsed symbol metadata in a local SQLite cache. "
            "Run this once after installing KiCad to make search-symbols near-instant. "
            "The cache is stored in ~/.openclaw/kicad-pcb/symbol_index.db (or "
            "$KICAD_PCB_CACHE_DIR/symbol_index.db)."
        ),
    )
    p_build_idx.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_build_idx.set_defaults(func=cmd_build_symbol_index)

    # debug-symbol
    p_debug_sym = subparsers.add_parser(
        "debug-symbol",
        help="Show resolved pin list and extends chain for a single symbol",
        description=(
            "Display the fully-resolved pin numbers and extends-chain information for a "
            "single KiCad symbol.  Follows (extends ...) chains so inherited pins are "
            "shown.  Useful for diagnosing broken extends chains or verifying pin numbers "
            "before writing Circuit IR JSON."
        ),
    )
    p_debug_sym.add_argument(
        "symbol",
        help="Symbol to inspect in 'LibName:SymName' format (e.g. 'Device:R')",
    )
    p_debug_sym.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_debug_sym.set_defaults(func=cmd_debug_symbol)

    # compile-netlist (alias for new-from-netlist)
    p_compile = subparsers.add_parser(
        "compile-netlist",
        help="Alias for new-from-netlist: create project from Circuit IR (same args)",
        description=(
            "Alias for new-from-netlist. "
            "Creates a new KiCad project and compiles a Circuit IR JSON netlist into it. "
            "The root schematic (<name>.kicad_sch) is thin and contains a sheet reference; "
            "all generated symbols, wires, and net labels are written to "
            "OpenClaw_Managed.kicad_sch (the managed sheet)."
        ),
    )
    p_compile.add_argument("--name", required=True, help="Project name")
    p_compile.add_argument("--netlist", required=True, help="Path to Circuit IR JSON file")
    p_compile.add_argument("--out-dir", help="Project output base directory")
    p_compile.add_argument("--description", help="Optional project description")
    p_compile.add_argument("--symbols-dir", help="Optional symbol libraries directory")
    p_compile.add_argument(
        "--mode",
        choices=["internal", "kicad"],
        default="kicad",
        help="Validation mode (default: kicad)",
    )
    p_compile.add_argument("--strict", action="store_true", help="Treat lint warnings as errors")
    p_compile.set_defaults(func=cmd_new_from_netlist)

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

    # preview-schematic
    p_prev_sch = subparsers.add_parser("preview-schematic", help="Generate schematic preview")
    p_prev_sch.set_defaults(func=cmd_preview_schematic)

    # preview-pcb
    p_prev_pcb = subparsers.add_parser("preview-pcb", help="Generate PCB previews")
    p_prev_pcb.set_defaults(func=cmd_preview_pcb)

    # add-component
    p_add = subparsers.add_parser("add-component", help="Add component to schematic")
    p_add.add_argument("lib_sym", metavar="LIB:SYM", help="Symbol reference e.g. Device:R")
    p_add.add_argument("ref", metavar="REF", help="Reference designator e.g. R1")
    p_add.add_argument("--value", help="Component value (e.g. 10k)")
    p_add.add_argument("--footprint", help="Footprint reference (e.g. Resistor_SMD:R_0402)")
    p_add.add_argument(
        "--symbols-dir",
        dest="symbols_dir",
        metavar="PATH",
        help="Path to KiCad symbol library directory (overrides env/config discovery)",
    )
    p_add.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_add.add_argument("--strict", action="store_true", help="Treat lint warnings as errors")
    p_add.set_defaults(func=cmd_add_component)

    # apply-pattern
    p_pat = subparsers.add_parser(
        "apply-pattern",
        help="Apply a known-good circuit pattern to the schematic",
        description=(
            "Available patterns: resistor-divider, led-resistor, connector-breakout, decoupling-cap"
        ),
    )
    p_pat.add_argument(
        "--pattern",
        required=True,
        choices=["resistor-divider", "led-resistor", "connector-breakout", "decoupling-cap"],
        help="Pattern to apply",
    )
    # Shared optional args used by multiple patterns
    p_pat.add_argument("--r1", default="R1", metavar="REF", help="R1 ref (resistor-divider)")
    p_pat.add_argument("--r2", default="R2", metavar="REF", help="R2 ref (resistor-divider)")
    p_pat.add_argument(
        "--r1-value",
        default="10k",
        dest="r1_value",
        metavar="VAL",
        help="R1 value (resistor-divider, default 10k)",
    )
    p_pat.add_argument(
        "--r2-value",
        default="10k",
        dest="r2_value",
        metavar="VAL",
        help="R2 value (resistor-divider, default 10k)",
    )
    p_pat.add_argument(
        "--vin-net",
        default="VIN",
        dest="vin_net",
        metavar="NET",
        help="VIN net name (resistor-divider)",
    )
    p_pat.add_argument(
        "--vout-net",
        default="VOUT",
        dest="vout_net",
        metavar="NET",
        help="VOUT net name (resistor-divider)",
    )
    p_pat.add_argument("--r", default="R1", metavar="REF", help="Resistor ref (led-resistor)")
    p_pat.add_argument("--d", default="D1", metavar="REF", help="LED ref (led-resistor)")
    p_pat.add_argument(
        "--r-value",
        default="330",
        dest="r_value",
        metavar="VAL",
        help="Resistor value (led-resistor, default 330)",
    )
    p_pat.add_argument(
        "--d-value",
        default="LED",
        dest="d_value",
        metavar="VAL",
        help="LED value label (led-resistor)",
    )
    p_pat.add_argument("--conn", default="J1", metavar="REF", help="Connector ref")
    p_pat.add_argument(
        "--n-pins",
        default=4,
        type=int,
        dest="n_pins",
        metavar="N",
        help="Pin count (connector-breakout, default 4)",
    )
    p_pat.add_argument(
        "--net-prefix",
        default="IO",
        dest="net_prefix",
        metavar="PFX",
        help="Net prefix (connector-breakout, default IO)",
    )
    p_pat.add_argument("--c", default="C1", metavar="REF", help="Capacitor ref (decoupling-cap)")
    p_pat.add_argument(
        "--c-value",
        default="100nF",
        dest="c_value",
        metavar="VAL",
        help="Capacitor value (decoupling-cap, default 100nF)",
    )
    p_pat.add_argument(
        "--vcc-net",
        default="VCC",
        dest="vcc_net",
        metavar="NET",
        help="VCC net name (led-resistor / decoupling-cap)",
    )
    p_pat.add_argument(
        "--gnd-net",
        default="GND",
        dest="gnd_net",
        metavar="NET",
        help="GND net name (all patterns)",
    )
    p_pat.add_argument(
        "--symbols-dir",
        dest="symbols_dir",
        metavar="PATH",
        help="Path to KiCad symbol library directory",
    )
    p_pat.add_argument(
        "--require-footprints",
        action="store_true",
        dest="require_footprints",
        help="Fail if any component has no footprint assigned (required for PCB layout)",
    )
    p_pat.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_pat.set_defaults(func=cmd_apply_pattern)

    # add-net
    p_net = subparsers.add_parser("add-net", help="Add a named net label to schematic")
    p_net.add_argument("name", metavar="NAME", help="Net name e.g. VCC")
    p_net.add_argument("--x", type=float, help="X position in mm")
    p_net.add_argument("--y", type=float, help="Y position in mm")
    p_net.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_net.set_defaults(func=cmd_add_net)

    # connect
    p_conn = subparsers.add_parser("connect", help="Add a wire between two coordinates")
    p_conn.add_argument(
        "--from", dest="from_pt", required=True, metavar="X,Y", help="Start coord mm"
    )
    p_conn.add_argument("--to", dest="to_pt", required=True, metavar="X,Y", help="End coord mm")
    p_conn.add_argument(
        "--dry-run", action="store_true", dest="dry_run", help="Validate without writing"
    )
    p_conn.set_defaults(func=cmd_connect)

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

    return parser


def build_parser() -> argparse.ArgumentParser:
    """Public alias for :func:`_build_parser`; used by tests and tooling."""
    return _build_parser()


def main() -> None:  # noqa: PLR0912 PLR0915
    """Parse CLI arguments and dispatch to the appropriate command handler."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    try:
        result = args.func(args)
        if getattr(args, "output_json", False):
            result_payload = json.loads(format_result_json(result))
            print(json.dumps({"ok": True, "result": result_payload, "warnings": []}, indent=2))
        else:
            for line in format_result(result):
                print(line)
        # Doctor exits non-zero when checks fail
        if isinstance(result, DoctorResult) and not result.overall_ok:
            sys.exit(1)
        # Lint/validate exits non-zero when issues found
        if isinstance(result, (LintFileResult, ValidateFileResult)) and not result.ok:
            sys.exit(1)
    except LintError as exc:
        if getattr(args, "output_json", False):
            print(
                json.dumps(
                    {
                        "ok": False,
                        "error": {
                            "code": "VALIDATION_FAILED",
                            "message": str(exc),
                            "details": {
                                "issues": [
                                    {
                                        "code": i.code,
                                        "severity": i.severity.value,
                                        "message": i.message,
                                        "path": i.path,
                                        "suggestion": LINT_SUGGESTIONS.get(i.code),
                                    }
                                    for i in exc.issues
                                ]
                            },
                        },
                    },
                    indent=2,
                )
            )
        else:
            print(f"❌ Validation failed: {len(exc.issues)} issue(s)")
            for issue in exc.issues:
                icon = "❌" if issue.severity is LintSeverity.ERROR else "⚠️ "
                print(f"  {icon} [{issue.code}] {issue.message}")
                if issue.path:
                    print(f"       path: {issue.path}")
                suggestion = LINT_SUGGESTIONS.get(issue.code)
                if suggestion:
                    print(f"       💡 {suggestion}")
            # If any layout issue (LAY*) was reported, add a generic layout hint.
            lay_codes = {i.code for i in exc.issues if i.code.startswith("LAY")}
            if lay_codes:
                print(
                    "  💡 Tip: layout issues are fail-fast; "
                    "fix the reported issue and re-run apply-netlist/new-from-netlist."
                )
        sys.exit(1)
    except KiCadError as exc:
        if getattr(args, "output_json", False):
            print(json.dumps({"ok": False, "error": exc.as_dict()}, indent=2))
        else:
            print(f"❌ {exc}")
            hint = exc.details.get("hint") if exc.details else None
            if hint:
                print(f"   💡 {hint}")
        sys.exit(1)
