"""Netlist-oriented commands (CODE_REVIEW3 MVP slices)."""

from __future__ import annotations

import json
import math
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..circuit_ir import CircuitIR
from ..config import PROJECTS_DIR, get_current_project, load_config, set_current_project
from ..errors import ErrorCode, ToolError, UserError
from ..fs import _atomic_write, _new_uuid
from ..ir_validate import validate_circuit_ir, validate_ir_symbols
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..results import ApplyNetlistResult, InfoSchResult, NewFromNetlistResult
from ..runner import find_kicad_cli
from ..sch_doc import SchematicDoc, read_lib_symbol_def_chain, read_lib_symbol_pin_at
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..symbol_index import SymbolIndex

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
        ),
    )


def cmd_new_from_netlist(args) -> NewFromNetlistResult:
    """Create a new project and compile Circuit IR into managed schematic."""
    project = _create_project(
        name=args.name,
        out_dir=Path(args.out_dir) if getattr(args, "out_dir", None) else None,
        description=getattr(args, "description", "") or "",
    )

    apply_result = _apply_netlist_to_project(
        project,
        _ApplyNetlistRequest(
            netlist_path=Path(args.netlist),
            symbols_dir=Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None,
            mode_name=getattr(args, "mode", "kicad"),
            force=True,
            dry_run=False,
        ),
    )

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
    )


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
        )
        _write_nets(doc=doc, ir=ir, pin_endpoints=pin_endpoints, stats=stats)

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


def _write_symbols(
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    project_name: str,
    stats: dict[str, int],
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

    for index, component in enumerate(sorted(ir.components, key=lambda c: c.ref)):
        x, y = _symbol_position(index)
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
        )
        symbol_positions[component.ref] = (x, y)

        # Compute pin endpoint positions in schematic space.
        # Pin (at px py angle) in library space translates to (x+px, y+py)
        # in schematic space when the symbol is placed at (x, y) at angle=0.
        lib_name, sym_name = component.symbol.split(":", 1)
        for directory in symbol_index.directories:
            pin_at = read_lib_symbol_pin_at(lib_name, sym_name, symbols_dir=directory)
            if pin_at:
                for pin_num, (px, py, pa) in pin_at.items():
                    pin_endpoints[(component.ref, pin_num)] = (x + px, y + py, pa)
                break  # use first directory that has the symbol

        stats["symbols"] += 1

    return symbol_positions, pin_endpoints, symbol_defs_missing


def _embed_symbol_if_found(*, doc: SchematicDoc, symbol: str, symbol_index: SymbolIndex) -> bool:
    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        sym_defs = read_lib_symbol_def_chain(lib_name, sym_name, symbols_dir=directory)
        if sym_defs:
            for sym_def in sym_defs:
                doc.embed_lib_symbol(sym_def)
            return True
    return False


def _write_nets(
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]],
    stats: dict[str, int],
) -> None:
    """Write net labels wired to the actual pin connection-point positions.

    For each pin reference in each net a short wire stub is emitted starting
    exactly at the pin endpoint (so KiCad recognises the electrical
    connection), extending 5.08 mm outward (away from the symbol body).  A
    net label with the net name is placed at the far end of the stub.

    If the pin endpoint is unknown (e.g. the library symbol could not be
    resolved — caught earlier by ``validate_ir_symbols``) the wire coords
    fall back to a floating off-canvas position so the schematic at least
    records the binding metadata.
    """
    WIRE_EXTEND_MM = 5.08  # one standard 200-mil grid unit
    fallback_y = -1500.0  # off-canvas fallback — should never be reached
    marker_idx = 0

    for net in sorted(ir.nets, key=lambda n: n.name):
        for pin_ref in sorted(net.pins, key=lambda p: (p.ref, p.pin)):
            key = (pin_ref.ref, pin_ref.pin)
            if key in pin_endpoints:
                wx, wy, wa = pin_endpoints[key]
                # outward direction: opposite of pin angle (which points
                # from endpoint toward the symbol body).
                angle_rad = math.radians(wa)
                ex = wx - math.cos(angle_rad) * WIRE_EXTEND_MM
                ey = wy - math.sin(angle_rad) * WIRE_EXTEND_MM
                # Label angle = outward direction so the stub and label align.
                label_angle = int((wa + 180) % 360)
            else:
                # Fallback: floating off-canvas stub (should not occur after
                # validate_ir_symbols has passed).
                wx, wy = -1200.0, fallback_y
                ex, ey = wx + WIRE_EXTEND_MM, wy
                label_angle = 0
                fallback_y -= 10.0

            doc.add_wire(wx, wy, ex, ey, _new_uuid())
            doc.add_label(net.name, ex, ey, _new_uuid(), angle=label_angle)
            binding_marker = "OpenClaw:bind=" + json.dumps(
                {
                    "ref": pin_ref.ref,
                    "pin": pin_ref.pin,
                    "net_name": net.name,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
            doc.add_text(
                binding_marker,
                -1200.0,
                -1500.0 - 10.0 * marker_idx,
                hidden=True,
            )
            marker_idx += 1
            stats["wires"] += 1
            stats["labels"] += 1
            stats["binding_markers"] += 1


def _symbol_position(index: int) -> tuple[float, float]:
    col = index % 6
    row = index // 6
    x = 50.8 + (col * 30.48)
    y = 76.2 + (row * 30.48)
    return x, y


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
