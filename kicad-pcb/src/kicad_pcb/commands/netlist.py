"""Netlist-oriented commands (CODE_REVIEW3 MVP slices)."""

from __future__ import annotations

import contextlib
import json
import math
import shutil
import zipfile as _zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..circuit_ir import CircuitIR
from ..config import (
    PROJECTS_DIR,
    get_current_project,
    get_current_session,
    load_config,
    set_current_project,
)
from ..errors import ErrorCode, ToolError, UserError
from ..fs import _atomic_write, _new_uuid
from ..ir_autofix import autofix_circuit_ir
from ..ir_validate import validate_circuit_ir, validate_ir_symbols
from ..layout import compute_orientations
from ..layout_engine import make_layout_engine
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..results import (
    ApplyNetlistResult,
    FixNetlistResult,
    InfoSchResult,
    NewFromNetlistResult,
    ValidateNetlistResult,
)
from ..router import route_nets, write_routing
from ..runner import find_kicad_cli
from ..sch_doc import SchematicDoc, read_lib_symbol_def_flat, read_lib_symbol_pin_at
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..symbol_index import SymbolIndex
from ..tier import assign_tiers

MANAGED_SHEET_NAME = "OpenClaw_Managed"
MANAGED_SHEET_FILE = "OpenClaw_Managed.kicad_sch"
MIN_COMPONENT_PLACEMENT_RATIO = 0.8


def resolve_schematic_paths(project: ProjectRef) -> tuple[Path, Path]:
    """Return ``(root_sch_path, managed_sch_path)`` for *project*.

    The root schematic (``<name>.kicad_sch``) is intentionally **thin** — it
    contains only the project header and a ``(sheet ...)`` reference to the
    managed sheet.  All generated content (symbols, wires, net labels) lives
    in the managed sheet (``OpenClaw_Managed.kicad_sch``).

    Neither path is guaranteed to exist on disk; callers should check with
    ``Path.exists()`` before loading.
    """
    return project.sch_file, project.path / MANAGED_SHEET_FILE


@dataclass(frozen=True)
class _ApplyNetlistRequest:
    netlist_path: Path
    symbols_dir: Path | None
    mode_name: str | None
    force: bool
    dry_run: bool
    backup: bool = False
    strict: bool = False


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

    # Layer 1: schema (raises UserError(IR_SCHEMA_INVALID) on failure)
    ir = CircuitIR.load(netlist_path)

    # Layer 2: semantic (raises UserError(IR_SEMANTIC_INVALID) on failure)
    validate_circuit_ir(ir)

    # Layer 3: symbol + pin (raises UserError(SYMBOL_NOT_FOUND / PIN_INVALID) on failure)
    symbol_index = SymbolIndex(symbols_dir=symbols_dir)
    validate_ir_symbols(ir, symbol_index)

    # Advisory warnings — do not block success, but flag common mistakes
    warnings: list[dict[str, object]] = []

    refs_in_nets: set[str] = {pin_ref.ref for net in ir.nets for pin_ref in net.pins}
    component_refs = {component.ref for component in ir.components}
    unreferenced = sorted(component_refs - refs_in_nets)
    if unreferenced:
        warnings.append(
            {
                "code": "COMPONENT_NOT_IN_ANY_NET",
                "message": (
                    f"{len(unreferenced)} component(s) are not referenced in any net "
                    "and will be floating in the schematic."
                ),
                "details": {"refs": unreferenced},
            }
        )

    single_pin_nets = [net.name for net in ir.nets if len(net.pins) == 1]
    if single_pin_nets:
        warnings.append(
            {
                "code": "SINGLE_PIN_NET",
                "message": (
                    f"{len(single_pin_nets)} net(s) have only one connected pin. "
                    "This is usually a wiring mistake."
                ),
                "details": {"nets": single_pin_nets},
            }
        )

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
            mode_name=getattr(args, "mode", None),
            force=bool(getattr(args, "force", False)),
            dry_run=bool(getattr(args, "dry_run", False)),
            backup=bool(getattr(args, "backup", False)),
            strict=bool(getattr(args, "strict", False)),
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
        with contextlib.suppress(UserError):
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

    def _load_and_validate(path: Path) -> CircuitIR:
        ir = CircuitIR.load(path)  # Layer 1: schema
        validate_circuit_ir(ir)  # Layer 2: semantic
        validate_ir_symbols(ir, symbol_index)  # Layer 3: symbol + pin
        return ir

    try:
        _load_and_validate(netlist_path)
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
            _load_and_validate(fixed_path)
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
            mode_name=getattr(args, "mode", "kicad"),
            force=True,
            dry_run=False,
            strict=bool(getattr(args, "strict", False)),
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
        warnings=apply_result.warnings,
        symbols_dirs_used=apply_result.symbols_dirs_used,
        zip_path=zip_path,
        session_path=session.path if session is not None else None,
    )


def _create_schematic_zip(project_path: Path, dest_dir: Path, name: str) -> Path:
    """Zip all ``*.kicad_sch`` files in *project_path* into *dest_dir*/<name>_schematic.zip.

    Returns the path of the created zip file.  Existing zips with the same name
    are overwritten so that re-running ``new-from-netlist`` always reflects the
    latest generation.
    """
    sch_files = sorted(project_path.glob("*.kicad_sch"))
    zip_path = dest_dir / f"{name}_schematic.zip"
    with _zipfile.ZipFile(zip_path, "w", _zipfile.ZIP_DEFLATED) as zf:
        for sch_file in sch_files:
            zf.write(sch_file, sch_file.name)
    return zip_path


def _apply_netlist_to_project(
    project: ProjectRef,
    request: _ApplyNetlistRequest,
) -> ApplyNetlistResult:
    ir = CircuitIR.load(request.netlist_path)
    validate_circuit_ir(ir)

    mode = _resolve_mode(request.mode_name, default=ValidationMode.LINT)
    warnings: list[dict[str, object]] = []

    symbol_index = SymbolIndex(symbols_dir=request.symbols_dir)
    validate_ir_symbols(ir, symbol_index)

    sheet_uuid = _ensure_project_root_owned(project, force=request.force, dry_run=request.dry_run)

    managed_sch_path = project.path / MANAGED_SHEET_FILE
    _ensure_managed_file_exists(managed_sch_path, dry_run=request.dry_run)

    cli: KicadCliAdapter | None = None
    if mode >= ValidationMode.KICAD:
        if shutil.which("kicad-cli") is None:
            raise ToolError(
                "kicad-cli is required for --mode kicad",
                code=ErrorCode.KICAD_CLI_MISSING,
                details={
                    "hint": "Install KiCad or use --mode internal",
                },
            )
        cli = KicadCliAdapter(kicad_cli=find_kicad_cli())
    else:
        warnings.append(
            {
                "code": "VALIDATION_MODE_INTERNAL",
                "message": "Using internal validation mode (kicad-cli not required).",
                "details": {"kicad_cli_used": False},
            }
        )

    stats: dict[str, int] = {
        "symbols": 0,
        "wires": 0,
        "labels": 0,
        "global_labels": 0,
        "junctions": 0,
        "binding_markers": 0,
    }

    def _mutate_managed(doc: SchematicDoc) -> None:
        # Authoritative sync for managed sheet file: replace all generated
        # content by reconstructing from IR each run.
        root = parse(_minimal_schematic_text())
        if not isinstance(root, ListNode):
            raise UserError("Managed schematic template parse failed", code=ErrorCode.PARSE_ERROR)
        doc.root = root

        symbol_positions, pin_endpoints, symbol_defs_missing = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=symbol_index,
            project_name=project.name,
            stats=stats,
            cache_path=project.path / "openclaw_layout_cache.json",
        )
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        write_routing(doc=doc, routing=routing, new_uuid=_new_uuid, stats=stats)

        # Post-mutation AST invariants: a non-empty IR must produce symbols in
        # the managed sheet. Check the live AST, not the stats counters.
        found_symbols = len(doc.list_symbols())
        expected_components = len(ir.components)

        if expected_components and found_symbols == 0:
            if not request.dry_run:
                raise UserError(
                    "Generation produced an empty managed schematic for a non-empty IR",
                    code=ErrorCode.EMPTY_GENERATION,
                    details={
                        "managed_schematic_path": str(managed_sch_path),
                        "expected_components": expected_components,
                        "found_symbols": 0,
                    },
                )
            else:
                warnings.append(
                    {
                        "code": ErrorCode.EMPTY_GENERATION,
                        "message": (
                            "Dry-run: managed schematic would be empty despite non-empty IR"
                            f" ({expected_components} component(s) expected)."
                        ),
                        "details": {
                            "expected_components": expected_components,
                            "found_symbols": 0,
                            "dry_run": True,
                        },
                    }
                )

        if expected_components:
            placement_ratio = found_symbols / expected_components
            if placement_ratio < MIN_COMPONENT_PLACEMENT_RATIO:
                min_required = int(expected_components * MIN_COMPONENT_PLACEMENT_RATIO)
                if min_required * 1.0 / expected_components < MIN_COMPONENT_PLACEMENT_RATIO:
                    min_required += 1
                raise UserError(
                    "Generated schematic contains fewer placed symbols than required",
                    code=ErrorCode.EMPTY_GENERATION,
                    details={
                        "managed_schematic_path": str(managed_sch_path),
                        "expected_components": expected_components,
                        "found_symbols": found_symbols,
                        "min_component_placement_ratio": MIN_COMPONENT_PLACEMENT_RATIO,
                        "min_required_symbols": min_required,
                        "placement_ratio": round(placement_ratio, 4),
                    },
                )

        if symbol_defs_missing:
            warnings.append(
                {
                    "code": "SYMBOL_DEF_NOT_EMBEDDED",
                    "message": "Some symbol definitions could not be embedded.",
                    "details": {"symbols": sorted(symbol_defs_missing)},
                }
            )

        # Qualify all bare (path "/" …) entries so KiCad can resolve the
        # sub-sheet hierarchy and assign correct reference annotations.
        doc.update_managed_path(sheet_uuid)

    mutate_and_validate_sch(
        managed_sch_path,
        _mutate_managed,
        mode=mode,
        cli=cli,
        operation="apply-netlist",
        dry_run=request.dry_run,
        backup=request.backup,
        strict=request.strict,
    )

    if request.dry_run:
        warnings.append(
            {
                "code": "DRY_RUN_NO_WRITE",
                "message": ("Dry-run mode: validation passed but no changes were written to disk."),
                "details": {
                    "root_schematic_path": str(project.sch_file),
                    "managed_schematic_path": str(managed_sch_path),
                    "symbols_validated": stats["symbols"],
                    "nets_validated": len(ir.nets),
                },
            }
        )

    return ApplyNetlistResult(
        schematic_path=project.sch_file,
        managed_schematic_path=managed_sch_path,
        symbols_added=stats["symbols"],
        symbols_updated=0,
        managed_items_written=(
            stats["symbols"] + stats["wires"] + stats["labels"] + stats["binding_markers"]
        ),
        nets_applied=len(ir.nets),
        kicad_cli_used=cli is not None,
        dry_run=request.dry_run,
        warnings=tuple(warnings),
        symbols_dirs_used=tuple(str(d) for d in symbol_index.directories),
    )


def _write_symbols(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    project_name: str,
    stats: dict[str, int],
    cache_path: Path | None = None,
) -> tuple[
    dict[str, tuple[float, float]],
    dict[tuple[str, str], tuple[float, float, float]],
    set[str],
]:
    """Place all symbols from *ir* into *doc*.

    Returns a triple of:
    * ``symbol_positions``  — ``{ref: (x, y)}`` placed-symbol origins.
    * ``pin_endpoints``     — ``{(ref, pin_num): (x, y, angle)}`` actual
      pin connection-point coordinates in schematic space, derived from the
      library symbol’s ``(pin ... (at x y angle) ...)`` data translated by
      the symbol placement position.  *angle* is the KiCad pin direction
      (0=right, 90=down, 180=left, 270=up) pointing **from the endpoint
      toward the symbol body** — wire stubs extend in the opposite direction.
    * ``symbol_defs_missing`` — set of symbol ids whose library def was
      not found (embedded as best-effort empty stubs).
    """
    symbol_positions: dict[str, tuple[float, float]] = {}
    # (ref, pin_number) -> (schematic_x, schematic_y, pin_angle)
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {}
    symbol_defs_missing: set[str] = set()

    engine = make_layout_engine(cache_path=cache_path)
    raw_layout = engine.compute_symbol_positions(ir)
    # Build plain (x, y) map for coordinate lookup and orientation computation.
    layout: dict[str, tuple[float, float]] = {
        ref: (pos[0], pos[1]) for ref, pos in raw_layout.items()
    }
    tiers: dict[str, int] = assign_tiers(ir)
    orientations: dict[str, int] = compute_orientations(ir, layout, tiers)

    for component in sorted(ir.components, key=lambda c: c.ref):
        x, y = layout[component.ref]
        valid_pins = sorted(symbol_index.get_pins(component.symbol))
        pin_uuids = [_new_uuid() for _ in valid_pins]

        if not _embed_symbol_if_found(doc=doc, symbol=component.symbol, symbol_index=symbol_index):
            symbol_defs_missing.add(component.symbol)

        doc.add_symbol(
            component.symbol,
            component.ref,
            component.value or component.ref,
            component.footprint or "",
            x,
            y,
            _new_uuid(),
            valid_pins,
            pin_uuids,
            project_name,
            rotation=orientations.get(component.ref, 0),
        )
        symbol_positions[component.ref] = (x, y)

        # Compute pin endpoint positions in schematic space, applying the
        # symbol's rotation.
        # Pin (at px py angle) in library space is transformed by rotation θ:
        #   schematic_x = x + cos(θ)*px - sin(θ)*py
        #   schematic_y = y + sin(θ)*px + cos(θ)*py
        #   schematic_angle = (pa + θ) % 360
        # When θ=0 this reduces to the identity transform.
        rotation = orientations.get(component.ref, 0)
        lib_name, sym_name = component.symbol.split(":", 1)
        for directory in symbol_index.directories:
            pin_at = read_lib_symbol_pin_at(lib_name, sym_name, symbols_dir=directory)
            if pin_at:
                if rotation == 0:
                    for pin_num, (px, py, pa) in pin_at.items():
                        pin_endpoints[(component.ref, pin_num)] = (x + px, y + py, pa)
                else:
                    theta = math.radians(rotation)
                    cos_t = math.cos(theta)
                    sin_t = math.sin(theta)
                    for pin_num, (px, py, pa) in pin_at.items():
                        rpx = cos_t * px - sin_t * py
                        rpy = sin_t * px + cos_t * py
                        rpa = (pa + rotation) % 360
                        pin_endpoints[(component.ref, pin_num)] = (x + rpx, y + rpy, rpa)
                break  # use first directory that has the symbol

        stats["symbols"] += 1

    return symbol_positions, pin_endpoints, symbol_defs_missing


def _embed_symbol_if_found(*, doc: SchematicDoc, symbol: str, symbol_index: SymbolIndex) -> bool:
    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=directory)
        if sym_def is not None:
            doc.embed_lib_symbol(sym_def)
            return True
    return False


def _resolve_mode(mode_name: str | None, *, default: ValidationMode) -> ValidationMode:
    if mode_name is None:
        return default
    raw = mode_name.strip().lower()
    if raw in {"internal", "syntax_lint", "lint"}:
        return ValidationMode.LINT
    if raw == "kicad":
        return ValidationMode.KICAD
    raise UserError(
        f"Unknown mode '{mode_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["internal", "kicad"]},
    )


def _minimal_schematic_text() -> str:
    return f'''(kicad_sch (version 20230121) (generator eeschema)
    (uuid "{_new_uuid()}")
    (paper "A4")
    (lib_symbols)
    (sheet_instances
        (path "/" (page "1"))
    )
)
'''


def _ensure_managed_file_exists(path: Path, *, dry_run: bool) -> None:
    if path.exists():
        return
    if dry_run:
        raise UserError(
            "Managed schematic file does not exist for dry-run",
            code=ErrorCode.IO_ERROR,
            details={
                "path": str(path),
                "hint": "Run once without --dry-run to initialise OpenClaw managed sheet",
            },
        )
    _atomic_write(
        path,
        _minimal_schematic_text(),
        root="kicad_sch",
        operation="create-managed-sheet",
    )


def _ensure_project_root_owned(project: ProjectRef, *, force: bool, dry_run: bool) -> str:
    """Prepare the root schematic for managed-sheet use; return the managed sheet UUID."""
    _captured_uuid: list[str] = []

    def _mutate(doc: SchematicDoc) -> None:
        if not doc.has_openclaw_marker() and not force:
            raise UserError(
                "Schematic is not OpenClaw-managed. Use --force to adopt.",
                code=ErrorCode.NOT_OWNED,
                details={"path": str(project.sch_file)},
            )
        doc.ensure_openclaw_marker()
        uuid = doc.ensure_managed_sheet(
            sheet_name=MANAGED_SHEET_NAME,
            sheet_file=MANAGED_SHEET_FILE,
            sheet_uuid=_new_uuid(),
        )
        _captured_uuid.append(uuid)

    mutate_and_validate_sch(
        project.sch_file,
        _mutate,
        mode=ValidationMode.LINT,
        operation="prepare-managed-sheet",
        dry_run=dry_run,
    )
    # _mutate is always called exactly once by mutate_and_validate_sch
    # (dry_run skips the write but still calls the mutator for validation).
    return _captured_uuid[0] if _captured_uuid else _new_uuid()


def _create_project(*, name: str, out_dir: Path | None, description: str) -> ProjectRef:
    slug = name.replace(" ", "_")
    if out_dir is None:
        cfg = load_config()
        base = Path(cfg.get("projects_dir", PROJECTS_DIR))
    else:
        base = out_dir

    project_dir = base / slug
    if project_dir.exists():
        raise UserError(f"Project already exists: {project_dir}")

    project_dir.mkdir(parents=True, exist_ok=False)
    pro_file = project_dir / f"{slug}.kicad_pro"
    sch_file = project_dir / f"{slug}.kicad_sch"
    pcb_file = project_dir / f"{slug}.kicad_pcb"

    _atomic_write(
        pro_file,
        json.dumps(
            {
                "board": {"design_settings": {}},
                "meta": {"filename": f"{slug}.kicad_pro", "version": 1},
                "schematic": {"drawing": {}},
                "sheets": [[f"{slug}.kicad_sch", ""]],
            },
            indent=2,
        ),
        operation="new-from-netlist",
    )
    _atomic_write(
        sch_file,
        _minimal_schematic_text(),
        root="kicad_sch",
        operation="new-from-netlist",
    )
    _atomic_write(
        pcb_file,
        """(kicad_pcb (version 20230121) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
)
""",
        root="kicad_pcb",
        operation="new-from-netlist",
    )

    project = ProjectRef(
        name=slug,
        path=project_dir,
        created=datetime.now().isoformat(),
        description=description,
    )
    set_current_project(project)
    return project
