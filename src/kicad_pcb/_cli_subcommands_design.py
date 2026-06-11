"""Register project, session, symbol-search, and schematic-editing CLI subcommands."""

from __future__ import annotations

import argparse

from .commands.patterns import cmd_apply_pattern
from .commands.preview import cmd_preview_pcb, cmd_preview_schematic
from .commands.project import cmd_info, cmd_new, cmd_open
from .commands.sch import cmd_add_component, cmd_add_net, cmd_connect
from .commands.search import cmd_build_symbol_index, cmd_debug_symbol, cmd_search_symbols
from .commands.session import cmd_close_session, cmd_new_session, cmd_session_info


def _register_design_subcommands(  # noqa: PLR0915
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    """Register project, session, symbol, preview, and schematic-editing subcommands."""
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
