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
    _append_to_schematic,
    _embed_lib_symbol,
    _extract_balanced,
    _find_symbol_def,
    _find_symbol_pins,
    _next_component_position,
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

# File-system utilities
from .fs import _atomic_write, _check_sexp, _new_uuid

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

# CLI runner
from .runner import KICAD_CLI, check_kicad, run_kicad_cli

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
    "_extract_balanced",
    "_find_symbol_def",
    "_find_symbol_pins",
    "_embed_lib_symbol",
    "_next_component_position",
    "_append_to_schematic",
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
]
