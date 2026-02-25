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
from .commands.validation import cmd_drc, cmd_erc

# Config constants and helpers
from .config import (
    CONFIG_DIR,
    CONFIG_FILE,
    CURRENT_PROJECT_FILE,
    DEFAULT_PCB_OPTIONS,
    PROJECTS_DIR,
    ensure_dirs,
    get_current_project,
    load_config,
    save_config,
    set_current_project,
)

# Errors
from .errors import KiCadError, ParseError, ToolError, UserError

# CLI formatting
from .formatting import format_result

# File-system utilities
from .fs import _atomic_write, _check_sexp, _new_uuid
from .lint import LintError, LintSeverity, lint_pcb, lint_schematic

# Typed domain models
from .models import (
    BoardOutlineRect,
    ComponentSpec,
    FootprintMoveSpec,
    LintIssue,
    NetLabelSpec,
    ProjectRef,
    ValidationResult,
    WireSegment,
)
from .pcb_doc import PcbDoc, make_gr_line_node
from .pipeline import ValidationMode, mutate_and_validate_pcb, mutate_and_validate_sch

# Typed result objects (Phase 2.4)
from .results import (
    AddComponentResult,
    AddNetResult,
    AutoPlaceResult,
    AutoRouteResult,
    ConnectResult,
    DoctorCheckItem,
    DoctorResult,
    DrcResult,
    ErcResult,
    Export3dResult,
    ExportBomResult,
    ExportDrillResult,
    ExportGerbersResult,
    ExportPosResult,
    ImportNetlistResult,
    InfoResult,
    NewProjectResult,
    OpenResult,
    PackageFabResult,
    PcbwayQuoteResult,
    PreviewPcbResult,
    PreviewSchematicResult,
    SetBoardSizeResult,
)

# CLI runner
from .runner import KICAD_CLI, check_kicad, run_kicad_cli

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

__all__ = [
    # errors
    "KiCadError",
    "UserError",
    "ToolError",
    "ParseError",
    # models
    "ProjectRef",
    "ComponentSpec",
    "WireSegment",
    "NetLabelSpec",
    "BoardOutlineRect",
    "FootprintMoveSpec",
    "LintIssue",
    "ValidationResult",
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
    "DEFAULT_PCB_OPTIONS",
    "ensure_dirs",
    "load_config",
    "save_config",
    "get_current_project",
    "set_current_project",
    # runner
    "KICAD_CLI",
    "check_kicad",
    "run_kicad_cli",
    # fs
    "_check_sexp",
    "_atomic_write",
    "_new_uuid",
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
    "cmd_set_board_size",
    "cmd_import_netlist",
    "cmd_auto_place",
    "cmd_auto_route",
    "cmd_export_pos",
    "cmd_export_3d",
    "cmd_pcbway_quote",
    "cmd_doctor",
    # cli
    "main",
    # results
    "NewProjectResult",
    "InfoResult",
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
    "DoctorCheckItem",
    "DoctorResult",
    "PcbwayQuoteResult",
    # formatting
    "format_result",
    # lint (Phase 5)
    "LintError",
    "LintSeverity",
    "lint_schematic",
    "lint_pcb",
    # pipeline (Phase 5)
    "ValidationMode",
    "mutate_and_validate_sch",
    "mutate_and_validate_pcb",
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
