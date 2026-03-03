"""kicad_pcb — KiCad PCB automation skill package.

This ``__init__.py`` re-exports all symbols that external consumers (tests,
scripts) have historically accessed via ``import kicad_pcb``, preserving
backward compatibility after the Phase 2.1 module split.
"""

from __future__ import annotations

# Injectable adapters (Phase 2.3)
from .adapters import (
    FakeFs,
    FakeRunner,
    FsProtocol,
    KicadCliAdapter,
    RealFs,
    RunnerProtocol,
    RunResult,
    SubprocessRunner,
)

# Circuit IR + semantic validation
from .circuit_ir import CircuitIR, ComponentIR, NetIR, OptionsIR, PinRefIR

# CLI entry-point
from .cli import main

# Command implementations
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
from .commands.pcb import (
    cmd_auto_place,
    cmd_auto_route,
    cmd_import_netlist,
    cmd_set_board_size,
)
from .commands.preview import cmd_preview_pcb, cmd_preview_schematic
from .commands.project import cmd_info, cmd_new, cmd_open
from .commands.sch import (
    KICAD_SYMBOLS_DIR,
    cmd_add_component,
    cmd_add_net,
    cmd_connect,
)
from .commands.search import cmd_build_symbol_index, cmd_debug_symbol, cmd_search_symbols
from .commands.session import cmd_close_session, cmd_new_session, cmd_session_info
from .commands.validation import cmd_drc, cmd_erc

# Version compatibility (Phase 8.1)
from .compat import (
    CAPABILITY_MAP,
    MINIMUM_VERSION,
    CliCapability,
    KiCadVersion,
    parse_version,
    require_capability,
)

# Config constants and helpers
from .config import (
    CONFIG_DIR,
    CONFIG_FILE,
    CURRENT_PROJECT_FILE,
    CURRENT_SESSION_FILE,
    DEFAULT_PCB_OPTIONS,
    PROJECTS_DIR,
    SESSIONS_SUBDIR,
    SYMBOLS_CANDIDATES,
    SymbolsDir,
    clear_current_session,
    discover_symbols_dir,
    ensure_dirs,
    get_current_project,
    get_current_session,
    get_sessions_base_dir,
    get_symbols_dir_config,
    load_config,
    save_config,
    set_current_project,
    set_current_session,
    set_symbols_dir_config,
)

# Errors
from .errors import (
    DocLintError,
    DocSyntaxError,
    ErrorCode,
    KicadCliValidationError,
    KiCadError,
    ParseError,
    SExprParseError,
    SExprTokenizeError,
    ToolError,
    UserError,
)

# CLI formatting
from .formatting import format_result, format_result_json

# File-system utilities
from .fs import SUPPORTED_ROOTS, _atomic_write, _check_sexp, _new_uuid, _write_temp_text
from .ir.validate import validate_circuit_ir, validate_ir_symbols
from .lint import LINT_SUGGESTIONS, LintError, LintSeverity, lint_pcb, lint_schematic

# Typed domain models
from .models import (
    BoardOutlineRect,
    ComponentSpec,
    FootprintMoveSpec,
    LintIssue,
    NetLabelSpec,
    ProjectRef,
    SessionRef,
    ValidationResult,
    WireSegment,
)

# Circuit pattern library (Phase 9.2)
from .patterns import (
    PATTERNS,
    PatternOutcome,
    PlacedComponent,
    pattern_connector_breakout,
    pattern_decoupling_cap,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from .pcb_doc import PcbDoc, make_gr_line_node
from .pipeline import ValidationMode, mutate_and_validate_pcb, mutate_and_validate_sch

# Preflight semantic checks (Phase 9.3)
from .preflight import (
    check_footprints_assigned,
    check_net_names_valid,
    check_no_duplicate_refs,
    check_refs_unique_in_request,
    check_symbol_accessible,
    collect_existing_net_names,
    collect_existing_refs,
)

# Typed result objects (Phase 2.4)
from .results import (
    AddComponentResult,
    AddNetResult,
    ApplyNetlistResult,
    ApplyPatternResult,
    AutoPlaceResult,
    AutoRouteResult,
    BuildSymbolIndexResult,
    ConnectResult,
    DebugSymbolResult,
    DoctorCheckItem,
    DoctorResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    FixNetlistResult,
    FormatFileResult,
    ImportNetlistResult,
    InfoResult,
    InfoSchResult,
    LintFileResult,
    NewFromNetlistResult,
    NewProjectResult,
    NewSessionResult,
    OpenResult,
    PackageFabResult,
    PcbwayQuoteResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SearchSymbolsResult,
    SessionInfoResult,
    SetBoardSizeResult,
    SymbolMatch,
    ValidateFileResult,
    ValidateNetlistResult,
)

# CLI runner
from .runner import KICAD_CLI, check_kicad, find_kicad_cli, run_kicad_cli

# KiCad document wrappers (Phase 4)
from .sch_doc import (
    SchematicDoc,
    make_label_node,
    make_symbol_node,
    make_wire_node,
    read_lib_symbol_def,
    read_lib_symbol_pins,
)

# S-expression sub-package (Phase 3) — expose top-level symbols
from .sexpr import (
    NO_POS,
    AtomNode,
    ListNode,
    Node,
    Position,
    StringNode,
    Token,
    TokenKind,
    append_to_section,
    find_all,
    find_first,
    node_path,
    parse,
    parse_file,
    replace_section,
    serialize,
    serialize_file,
    tokenize,
    walk,
)
from .sexpr.builder import L, atom, fnum, string
from .symbol_cache import CachedSymbol, SymbolCache

# Symbol metadata index
from .symbol_index import (
    REPO_LOCAL_SYMBOLS_DIR,
    SymbolIndex,
    SymbolsResolution,
    resolve_symbol_dirs,
)

__all__ = [
    # errors
    "KiCadError",
    "ErrorCode",
    "UserError",
    "ToolError",
    "ParseError",
    "SExprTokenizeError",
    "SExprParseError",
    "DocSyntaxError",
    "DocLintError",
    "KicadCliValidationError",
    # compat (Phase 8.1)
    "KiCadVersion",
    "MINIMUM_VERSION",
    "CliCapability",
    "CAPABILITY_MAP",
    "parse_version",
    "require_capability",
    # models
    "ProjectRef",
    "ComponentSpec",
    "WireSegment",
    "NetLabelSpec",
    "BoardOutlineRect",
    "FootprintMoveSpec",
    "LintIssue",
    "ValidationResult",
    # circuit IR
    "CircuitIR",
    "ComponentIR",
    "NetIR",
    "PinRefIR",
    "OptionsIR",
    "validate_circuit_ir",
    "validate_ir_symbols",
    # symbol index
    "SymbolIndex",
    "SymbolsResolution",
    "resolve_symbol_dirs",
    "REPO_LOCAL_SYMBOLS_DIR",
    # adapters
    "RunResult",
    "RunnerProtocol",
    "SubprocessRunner",
    "FakeRunner",
    "FsProtocol",
    "RealFs",
    "FakeFs",
    "KicadCliAdapter",
    # config
    "CONFIG_DIR",
    "CONFIG_FILE",
    "PROJECTS_DIR",
    "CURRENT_PROJECT_FILE",
    "CURRENT_SESSION_FILE",
    "SESSIONS_SUBDIR",
    "DEFAULT_PCB_OPTIONS",
    "ensure_dirs",
    "load_config",
    "save_config",
    "get_current_project",
    "set_current_project",
    "get_current_session",
    "set_current_session",
    "clear_current_session",
    "get_sessions_base_dir",
    # symbol library discovery (Phase 8.2)
    "SYMBOLS_CANDIDATES",
    "SymbolsDir",
    "discover_symbols_dir",
    "get_symbols_dir_config",
    "set_symbols_dir_config",
    # runner
    "KICAD_CLI",
    "check_kicad",
    "find_kicad_cli",
    "run_kicad_cli",
    # fs
    "SUPPORTED_ROOTS",
    "_check_sexp",
    "_atomic_write",
    "_new_uuid",
    "_write_temp_text",
    # sch helpers
    "KICAD_SYMBOLS_DIR",
    # document wrappers (Phase 4)
    "SchematicDoc",
    "PcbDoc",
    "read_lib_symbol_def",
    "read_lib_symbol_pins",
    "make_symbol_node",
    "make_wire_node",
    "make_label_node",
    "make_gr_line_node",
    # sexpr builder (Phase 4)
    "atom",
    "string",
    "L",
    "fnum",
    # commands
    "cmd_new",
    "cmd_info",
    "cmd_open",
    "cmd_apply_netlist",
    "cmd_info_sch",
    "cmd_new_from_netlist",
    "cmd_validate_netlist",
    "cmd_debug_symbol",
    "cmd_search_symbols",
    "cmd_drc",
    "cmd_erc",
    "cmd_export_gerbers",
    "cmd_export_drill",
    "cmd_export_bom",
    "cmd_package_for_fab",
    "cmd_preview_schematic",
    "cmd_preview_pcb",
    "cmd_add_component",
    "cmd_add_net",
    "cmd_connect",
    "cmd_apply_pattern",
    "cmd_set_board_size",
    "cmd_import_netlist",
    "cmd_auto_place",
    "cmd_auto_route",
    "cmd_export_pos",
    "cmd_export_3d",
    "cmd_pcbway_quote",
    "cmd_doctor",
    # lint/validate/format (Phase 6)
    "cmd_lint_sch",
    "cmd_lint_pcb",
    "cmd_validate_sch",
    "cmd_validate_pcb",
    "cmd_format_sch",
    "cmd_format_pcb",
    # cli
    "main",
    # results
    "NewProjectResult",
    "InfoResult",
    "InfoSchResult",
    "OpenResult",
    "DrcResult",
    "ErcResult",
    "ExportGerbersResult",
    "ExportDrillResult",
    "ExportBomResult",
    "PackageFabResult",
    "ExportPosResult",
    "Export3dResult",
    "PreviewSchematicResult",
    "PreviewPcbResult",
    "SetBoardSizeResult",
    "ImportNetlistResult",
    "AutoPlaceResult",
    "AutoRouteResult",
    "AddComponentResult",
    "AddNetResult",
    "ConnectResult",
    "ApplyNetlistResult",
    "ApplyPatternResult",
    "DoctorCheckItem",
    "DoctorResult",
    "PcbwayQuoteResult",
    # Phase 6 result types
    "LintFileResult",
    "ValidateFileResult",
    "FormatFileResult",
    "NewFromNetlistResult",
    "ValidateNetlistResult",
    "FixNetlistResult",
    "cmd_fix_netlist",
    "BuildSymbolIndexResult",
    "CachedSymbol",
    "cmd_build_symbol_index",
    "DebugSymbolResult",
    "SearchSymbolsResult",
    "SymbolCache",
    "SymbolMatch",
    # session management
    "SessionRef",
    "NewSessionResult",
    "SessionInfoResult",
    "cmd_new_session",
    "cmd_session_info",
    "cmd_close_session",
    # formatting
    "format_result",
    "format_result_json",
    # lint (Phase 5)
    "LintError",
    "LintSeverity",
    "LINT_SUGGESTIONS",
    "lint_schematic",
    "lint_pcb",
    # pipeline (Phase 5)
    "ValidationMode",
    "mutate_and_validate_sch",
    "mutate_and_validate_pcb",
    # patterns (Phase 9.2)
    "PATTERNS",
    "PatternOutcome",
    "PlacedComponent",
    "pattern_resistor_divider",
    "pattern_led_resistor",
    "pattern_connector_breakout",
    "pattern_decoupling_cap",
    # preflight checks (Phase 9.3)
    "collect_existing_refs",
    "collect_existing_net_names",
    "check_no_duplicate_refs",
    "check_refs_unique_in_request",
    "check_net_names_valid",
    "check_symbol_accessible",
    "check_footprints_assigned",
    # sexpr (Phase 3)
    "Position",
    "NO_POS",
    "AtomNode",
    "StringNode",
    "ListNode",
    "Node",
    "Token",
    "TokenKind",
    "tokenize",
    "parse",
    "parse_file",
    "serialize",
    "serialize_file",
    "walk",
    "find_first",
    "find_all",
    "replace_section",
    "append_to_section",
    "node_path",
]
