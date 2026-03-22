"""Netlist-oriented commands (CODE_REVIEW3 MVP slices)."""

from __future__ import annotations

import json
from pathlib import Path

from ..circuit_ir import CircuitIR
from ..config import (
    get_current_project,
    get_current_session,
)
from ..errors import ErrorCode, UserError
from ..ir.autofix import autofix_circuit_ir
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..results import (
    ApplyNetlistResult,
    FixNetlistResult,
    InfoSchResult,
    NewFromNetlistResult,
    ValidateNetlistResult,
)
from ..sch_doc import SchematicDoc
from ..symbol_index import SymbolIndex
from ._project import _create_project, _create_schematic_zip
from ._sch_apply import (
    MANAGED_SHEET_FILE,
    MANAGED_SHEET_NAME,  # noqa: F401 — re-export for callers
    _apply_netlist_to_project,
    _ApplyNetlistRequest,
    _write_symbols,  # noqa: F401 — re-export for callers (test_phase7_ux)
    resolve_schematic_paths,  # noqa: F401 — re-export for callers
)
from ._validate import advisory_warnings, full_validate


def cmd_info_sch(args) -> InfoSchResult:
    """Return schematic introspection data for the current project."""
    del args

    project = get_current_project()
    if project is None:
        raise UserError(
            "No project selected",
            code=ErrorCode.PROJECT_NOT_OPEN,
            details={"hint": "Use: kicad_pcb open <path> or kicad_pcb new <name>"},
        )

    sch_path = project.sch_file
    doc = SchematicDoc.load(sch_path)

    symbols = list(doc.list_symbols())
    pin_net_bindings = list(doc.extract_pin_label_bindings())
    root_symbol_count = doc.count_nodes("symbol")
    root_label_count = doc.count_nodes("label")

    managed_sch_path = project.path / MANAGED_SHEET_FILE
    managed_sch_path_result: Path | None = None
    managed_symbol_count = 0
    managed_label_count = 0
    if managed_sch_path.exists():
        managed_sch_path_result = managed_sch_path
        managed_doc = SchematicDoc.load(managed_sch_path)
        symbols.extend(managed_doc.list_symbols())
        pin_net_bindings.extend(managed_doc.extract_pin_label_bindings())
        managed_symbol_count = managed_doc.count_nodes("symbol")
        managed_label_count = managed_doc.count_nodes("label")

    symbols_sorted = tuple(sorted(symbols, key=lambda entry: str(entry.get("ref", ""))))
    bindings_sorted = tuple(
        sorted(pin_net_bindings, key=lambda entry: (entry["ref"], entry["pin"], entry["net_name"]))
    )
    warnings: list[dict[str, object]] = []

    if managed_sch_path_result is not None and managed_symbol_count == 0:
        warnings.append(
            {
                "code": "MANAGED_SHEET_EMPTY",
                "message": (
                    "Managed sheet exists but contains no placed symbols. "
                    "The schematic may not have been generated yet."
                ),
                "details": {
                    "managed_schematic_path": str(managed_sch_path_result),
                    "managed_symbol_count": 0,
                },
            }
        )

    if not bindings_sorted:
        warnings.append(
            {
                "code": "PIN_BINDINGS_UNAVAILABLE",
                "message": "Pin→net bindings are unavailable for this schematic in MVP mode.",
                "details": {
                    "reason": "no deterministic binding markers found",
                },
            }
        )

    return InfoSchResult(
        project_path=project.path,
        schematic_path=sch_path,
        owned_by_openclaw=doc.has_openclaw_marker(),
        symbols=symbols_sorted,
        pin_net_bindings=bindings_sorted,
        warnings=tuple(warnings),
        managed_schematic_path=managed_sch_path_result,
        symbol_count=root_symbol_count,
        label_count=root_label_count,
        managed_symbol_count=managed_symbol_count,
        managed_label_count=managed_label_count,
    )


def cmd_validate_netlist(args) -> ValidateNetlistResult:
    """Validate a Circuit IR JSON file without writing any files.

    Runs all three validation layers in order:
    1. Pydantic schema (``additionalProperties: false``, required keys, types)
    2. Semantic checks (duplicate refs/nets, zero-pin nets, unknown component refs)
    3. Symbol + pin checks (every symbol exists in index, every pin is valid)

    On success prints a summary with advisory warnings for common issues.
    On failure raises with a specific error message pointing to the exact problem.
    """
    netlist_path = Path(args.netlist)
    symbols_dir = Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None

    symbol_index = SymbolIndex(symbols_dir=symbols_dir)
    ir = full_validate(netlist_path, symbol_index)
    warnings = advisory_warnings(ir, symbol_index)

    return ValidateNetlistResult(
        valid=True,
        netlist_path=netlist_path,
        component_count=len(ir.components),
        net_count=len(ir.nets),
        warnings=tuple(warnings),
        symbols_dirs_used=tuple(str(d) for d in symbol_index.directories),
    )


def cmd_apply_netlist(args) -> ApplyNetlistResult:
    """Apply Circuit IR JSON to the OpenClaw-managed schematic region."""
    project = get_current_project()
    if project is None:
        raise UserError(
            "No project selected",
            code=ErrorCode.PROJECT_NOT_OPEN,
            details={"hint": "Use: kicad_pcb open <path> or kicad_pcb new <name>"},
        )

    return _apply_netlist_to_project(
        project,
        _ApplyNetlistRequest(
            netlist_path=Path(args.netlist),
            symbols_dir=Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None,
            mode_name=getattr(args, "validate", None) or getattr(args, "mode", None),
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            backup=bool(getattr(args, "backup", False)),
            strict=bool(getattr(args, "strict", False)),
            layout_name=getattr(args, "layout", None),
            routing_name=getattr(args, "routing", None),
            heuristic_profile_name=getattr(args, "heuristic_profile", None),
            debug_dump_path=(
                Path(getattr(args, "debug_dump")) if getattr(args, "debug_dump", None) else None
            ),
        ),
    )


def cmd_fix_netlist(args) -> FixNetlistResult:
    """Auto-fix a Circuit IR JSON file and write the corrected version.

    Applies deterministic fixes in layers (no LLM calls):

    1. Schema — version type, wrapper removal, forbidden top-level keys.
    2. Components — removes forbidden fields (``type``, inline ``pins``).
    3. Nets — integer pin values coerced to strings.
    4. Aliases — wrong pin names mapped to correct ones via library lookup
       (only when ``--symbols-dir`` is provided or a default library is found).

    The corrected JSON is always written (even if not fully fixed) so the bot
    can inspect or pass it to ``new-from-netlist``.
    """
    netlist_path = Path(args.netlist)
    symbols_dir = Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None
    output_path = (
        Path(args.output)
        if getattr(args, "output", None)
        else (netlist_path.parent / (netlist_path.stem + ".fixed.json"))
    )

    # Parse raw JSON (not CircuitIR — it may be malformed)
    try:
        raw = json.loads(netlist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UserError(
            f"JSON parse error in {netlist_path}: {exc}",
            code=ErrorCode.IR_SCHEMA_INVALID,
        ) from exc
    if not isinstance(raw, dict):
        raise UserError(
            f"Circuit IR must be a JSON object, got {type(raw).__name__}",
            code=ErrorCode.IR_SCHEMA_INVALID,
        )

    # Build symbol index if possible (needed for alias fixes).
    # Only attempt this when --symbols-dir was explicitly provided so the command
    # stays fast by default; the auto-detect path can scan very large library files.
    symbol_index: SymbolIndex | None = None
    if symbols_dir is not None:
        symbol_index = SymbolIndex(symbols_dir=symbols_dir)

    # Run all fix layers
    outcome = autofix_circuit_ir(raw, symbol_index=symbol_index)

    # Try full validation on the fixed dict to collect remaining errors
    all_remaining = list(outcome.remaining_errors)
    component_count = 0
    net_count = 0
    try:
        ir = CircuitIR.model_validate(outcome.ir_dict)
        validate_circuit_ir(ir)
        component_count = len(ir.components)
        net_count = len(ir.nets)
        if symbol_index is not None:
            validate_ir_symbols(ir, symbol_index)
    except (UserError, Exception) as exc:
        all_remaining.append(str(exc))

    fixed = len(all_remaining) == 0

    # Always write — even when not fully fixed — so the caller can see partial progress
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(outcome.ir_dict, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    return FixNetlistResult(
        fixed=fixed,
        output_path=output_path,
        fixes_applied=outcome.fixes_applied,
        remaining_errors=tuple(all_remaining),
        component_count=component_count,
        net_count=net_count,
        pin_validation_skipped=(symbol_index is None),
    )


def cmd_new_from_netlist(args) -> NewFromNetlistResult:
    """Create a new project and compile Circuit IR into managed schematic.

    Validation runs before any files are written: schema → semantic → symbol+pin.
    When ``--auto-fix`` is enabled (the default), a deterministic fixer runs on the
    first validation failure.  If the fixer resolves all errors the project is
    created from the fixed JSON; if errors remain, a clear error is raised showing
    what was fixed and what still needs manual correction.
    If validation fails the project directory is never created.

    Session integration
    -------------------
    When a session is active (set via ``new-session``), three things happen
    automatically:

    1. **Project location** — the KiCad project sub-directory is created inside
       the session directory (unless ``--out-dir`` is given explicitly).
    2. **Netlist resolution** — if ``--netlist`` names a file that does not exist
       at the given path but *does* exist inside the session directory, the
       session-local copy is used automatically.
    3. **Auto-zip** — after successful generation, all ``*.kicad_sch`` files in
       the project directory are zipped into ``<session_dir>/<name>_schematic.zip``.
    """
    # ------------------------------------------------------------------
    # Session: resolve netlist path and output dir
    # ------------------------------------------------------------------
    session = get_current_session()

    raw_netlist_arg: str = args.netlist
    netlist_path = Path(raw_netlist_arg)

    # If the path doesn't resolve but a session is active, try looking for the
    # file inside the session directory so the bot can pass just a filename.
    if not netlist_path.exists() and session is not None:
        candidate = session.path / netlist_path.name
        if candidate.exists():
            netlist_path = candidate

    if not netlist_path.exists():
        raise UserError(
            f"Netlist file not found: {raw_netlist_arg}",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(netlist_path),
                "hint": (
                    f"If a session is active, place the file inside '{session.path}' "
                    "and pass just the filename."
                )
                if session
                else "Check the path and try again.",
            },
        )

    # Determine where to create the project: explicit flag > active session > default.
    explicit_out_dir = Path(args.out_dir) if getattr(args, "out_dir", None) else None
    out_dir: Path | None = (
        explicit_out_dir
        if explicit_out_dir is not None
        else (session.path if session is not None else None)
    )

    # ------------------------------------------------------------------
    # Pre-flight: validate all 3 layers before touching the filesystem.
    # ------------------------------------------------------------------
    symbols_dir = Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None
    auto_fix: bool = getattr(args, "auto_fix", True)
    symbol_index = SymbolIndex(symbols_dir=symbols_dir)

    try:
        full_validate(netlist_path, symbol_index)
    except (UserError, Exception) as first_err:
        if not auto_fix:
            raise
        # --auto-fix: attempt deterministic repair and retry
        try:
            raw = json.loads(netlist_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise first_err
        if not isinstance(raw, dict):
            raise first_err
        outcome = autofix_circuit_ir(raw, symbol_index=symbol_index)
        if not outcome.fixes_applied:
            raise first_err  # Nothing the fixer could touch; surface original error
        # Write the fixed JSON alongside the original
        fixed_path = netlist_path.parent / (netlist_path.stem + ".autofix.json")
        fixed_path.write_text(
            json.dumps(outcome.ir_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        # Retry validation on the repaired JSON
        try:
            full_validate(fixed_path, symbol_index)
        except (UserError, Exception) as retry_err:
            fixes_summary = "\n".join(f"  • {f}" for f in outcome.fixes_applied) or "  (none)"
            raise UserError(
                f"Auto-fix applied {len(outcome.fixes_applied)} change(s) but "
                f"validation still fails:\n{fixes_summary}\n"
                f"Remaining error: {retry_err}\n"
                f"Partially-fixed JSON written to: {fixed_path}\n"
                "Correct the remaining issues and pass that file to new-from-netlist.",
                code=ErrorCode.IR_SCHEMA_INVALID,
            ) from retry_err
        # All errors resolved — proceed with the fixed netlist
        netlist_path = fixed_path

    project = _create_project(
        name=args.name,
        out_dir=out_dir,
        description=getattr(args, "description", "") or "",
    )

    apply_result = _apply_netlist_to_project(
        project,
        _ApplyNetlistRequest(
            netlist_path=netlist_path,  # may be the auto-fixed path
            symbols_dir=Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None,
            mode_name=getattr(args, "validate", None) or getattr(args, "mode", "kicad"),
            force=True,
            dry_run=False,
            strict=bool(getattr(args, "strict", False)),
            layout_name=getattr(args, "layout", None),
            routing_name=getattr(args, "routing", None),
            heuristic_profile_name=getattr(args, "heuristic_profile", None),
            debug_dump_path=(
                Path(getattr(args, "debug_dump")) if getattr(args, "debug_dump", None) else None
            ),
        ),
    )

    # ------------------------------------------------------------------
    # Session: auto-zip all schematic files into the session directory.
    # ------------------------------------------------------------------
    zip_path: Path | None = None
    if session is not None:
        zip_path = _create_schematic_zip(project.path, session.path, project.name)

    return NewFromNetlistResult(
        name=project.name,
        path=project.path,
        schematic_path=apply_result.schematic_path,
        managed_schematic_path=apply_result.managed_schematic_path,
        symbols_added=apply_result.symbols_added,
        nets_applied=apply_result.nets_applied,
        kicad_cli_used=apply_result.kicad_cli_used,
        heuristic_profile_name=apply_result.heuristic_profile_name,
        warnings=apply_result.warnings,
        warning_report_path=apply_result.warning_report_path,
        debug_dump_path=apply_result.debug_dump_path,
        symbols_dirs_used=apply_result.symbols_dirs_used,
        zip_path=zip_path,
        session_path=session.path if session is not None else None,
    )
