"""Schematic generation engine: symbol placement, pin transforms, managed-sheet lifecycle."""

from __future__ import annotations

import contextlib
import math
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ..adapters import KicadCliAdapter
from ..circuit_ir import CircuitIR
from ..errors import ErrorCode, ToolError, UserError
from ..fs import _atomic_write, _new_uuid
from ..ir.validate import validate_circuit_ir, validate_ir_symbols
from ..layout import compute_orientations
from ..layout_engine import (
    LayoutEngine,
    make_layout_engine,
)
from ..models import ProjectRef
from ..pipeline import ValidationMode, mutate_and_validate_sch
from ..results import ApplyNetlistResult
from ..router import route_nets, write_routing
from ..runner import find_kicad_cli
from ..sch_doc import SchematicDoc, read_lib_symbol_def_flat, read_lib_symbol_pin_at
from ..sexpr.nodes import ListNode
from ..sexpr.parser import parse
from ..symbol_index import SymbolIndex
from ..tier import assign_tiers
from ._project import minimal_schematic_text

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MANAGED_SHEET_NAME = "OpenClaw_Managed"
MANAGED_SHEET_FILE = "OpenClaw_Managed.kicad_sch"
MIN_COMPONENT_PLACEMENT_RATIO = 0.8


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Request dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ApplyNetlistRequest:
    netlist_path: Path
    symbols_dir: Path | None
    mode_name: str | None
    force: bool
    dry_run: bool
    backup: bool = False
    strict: bool = False
    layout_name: str | None = None
    routing_name: str | None = None


# ---------------------------------------------------------------------------
# Core apply logic
# ---------------------------------------------------------------------------


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

    # Pre-flight: check kicad-cli availability BEFORE touching the filesystem.
    # This prevents a half-initialised project where the root schematic has been
    # adopted and the managed schematic is an empty stub, but the generation
    # itself cannot run because a required tool is missing.
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

    sheet_uuid = _ensure_project_root_owned(project, force=request.force, dry_run=request.dry_run)

    managed_sch_path = project.path / MANAGED_SHEET_FILE
    # Track whether we are about to create the managed file for the first time.
    # If generation subsequently fails we delete the empty stub so the project
    # is left in a clean, retryable state rather than having a misleading
    # zero-content managed schematic on disk.
    managed_was_absent = not managed_sch_path.exists()
    _ensure_managed_file_exists(managed_sch_path, dry_run=request.dry_run)

    stats: dict[str, int] = {
        "symbols": 0,
        "wires": 0,
        "labels": 0,
        "global_labels": 0,
        "power_symbols": 0,
        "junctions": 0,
        "binding_markers": 0,
    }

    try:
        mutate_and_validate_sch(
            managed_sch_path,
            _build_managed_mutator(
                ir=ir,
                project=project,
                symbol_index=symbol_index,
                sheet_uuid=sheet_uuid,
                request=request,
                stats=stats,
                warnings=warnings,
                managed_sch_path=managed_sch_path,
            ),
            mode=mode,
            cli=cli,
            operation="apply-netlist",
            dry_run=request.dry_run,
            backup=request.backup,
            strict=request.strict,
        )
    except Exception:
        # If the managed schematic was newly created as an empty stub and the
        # mutation failed, remove it so that the project is left in a clean state.
        # A subsequent retry will reinitialise the file from scratch.
        # Suppress OSError so a filesystem race (e.g. concurrent deletion) does
        # not shadow the original exception.
        if managed_was_absent and not request.dry_run:
            with contextlib.suppress(OSError):
                managed_sch_path.unlink(missing_ok=True)
        raise

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


# ---------------------------------------------------------------------------
# Managed mutator factory
# ---------------------------------------------------------------------------


def _build_managed_mutator(  # noqa: PLR0913
    *,
    ir: CircuitIR,
    project: ProjectRef,
    symbol_index: SymbolIndex,
    sheet_uuid: str,
    request: _ApplyNetlistRequest,
    stats: dict[str, int],
    warnings: list[dict[str, object]],
    managed_sch_path: Path,
) -> Callable[[SchematicDoc], None]:
    """Return a ``SchematicDoc`` mutator that reconstructs the managed sheet from *ir*.

    All mutable state (*stats*, *warnings*) is passed explicitly so the factory
    parameters remain easy to inspect and test in isolation.
    """

    def _mutate(doc: SchematicDoc) -> None:
        # Authoritative sync for managed sheet file: replace all generated
        # content by reconstructing from IR each run.
        root = parse(minimal_schematic_text())
        if not isinstance(root, ListNode):
            raise UserError("Managed schematic template parse failed", code=ErrorCode.PARSE_ERROR)
        doc.root = root

        _engine = _resolve_layout(
            request.layout_name,
            cache_path=project.path / "openclaw_layout_cache.json",
        )
        symbol_positions, pin_endpoints, symbol_defs_missing, raw_layout = _write_symbols(
            doc=doc,
            ir=ir,
            symbol_index=symbol_index,
            project_name=project.name,
            stats=stats,
            engine=_engine,
        )
        _tiers = assign_tiers(ir)
        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            tiers=_tiers,
            positions=raw_layout,
            use_bus=_resolve_routing(request.routing_name),
            strict=request.strict,
        )
        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_new_uuid,
            stats=stats,
            # Power symbols come from the KiCad system library; pass None so
            # resolve_symbol_dirs() auto-discovers /usr/share/kicad/symbols.
            # The project's component symbols_dir is unrelated to power.kicad_sym.
            symbols_dir=None,
            project_name=project.name,
        )

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

    return _mutate


# ---------------------------------------------------------------------------
# Symbol placement helpers
# ---------------------------------------------------------------------------


def _transform_pin_at(
    pin_at: dict[str, tuple[float, float, float]],
    origin_x: float,
    origin_y: float,
    rotation: int,
) -> dict[str, tuple[float, float, float]]:
    """Apply *rotation* and origin translation to a library-space pin map.

    ``pin_at`` maps ``pin_num -> (px, py, pa)`` in library space.
    Returns ``{pin_num: (schematic_x, schematic_y, schematic_angle)}``.

    When *rotation* is 0 this reduces to a pure translation (no trig):
      ``schematic_{x,y} = origin_{x,y} + p_{x,y}``.

    For non-zero *rotation* θ (degrees), the standard 2-D rotation is applied:
      ``schematic_x = origin_x + cos(θ)·px − sin(θ)·py``
      ``schematic_y = origin_y + sin(θ)·px + cos(θ)·py``
      ``schematic_angle = (pa + θ) % 360``
    """
    if rotation == 0:
        return {
            pin_num: (origin_x + px, origin_y + py, pa) for pin_num, (px, py, pa) in pin_at.items()
        }
    theta = math.radians(rotation)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    return {
        pin_num: (
            origin_x + cos_t * px - sin_t * py,
            origin_y + sin_t * px + cos_t * py,
            (pa + rotation) % 360,
        )
        for pin_num, (px, py, pa) in pin_at.items()
    }


def _write_symbols(  # noqa: PLR0913
    *,
    doc: SchematicDoc,
    ir: CircuitIR,
    symbol_index: SymbolIndex,
    project_name: str,
    stats: dict[str, int],
    engine: LayoutEngine | None = None,
    cache_path: Path | None = None,
) -> tuple[
    dict[str, tuple[float, float]],
    dict[tuple[str, str], tuple[float, float, float]],
    set[str],
    dict[str, tuple[float, float, float | None]],
]:
    """Place all symbols from *ir* into *doc*.

    Returns a 4-tuple of:
    * ``symbol_positions``  — ``{ref: (x, y)}`` placed-symbol origins.
    * ``pin_endpoints``     — ``{(ref, pin_num): (x, y, angle)}`` actual
      pin connection-point coordinates in schematic space, derived from the
      library symbol's ``(pin ... (at x y angle) ...)`` data translated by
      the symbol placement position.  *angle* is the KiCad pin direction
      (0=right, 90=down, 180=left, 270=up) pointing **from the endpoint
      toward the symbol body** — wire stubs extend in the opposite direction.
    * ``symbol_defs_missing`` — set of symbol ids whose library def was
      not found (embedded as best-effort empty stubs).
    * ``raw_layout``        — ``{ref: (x, y, rotation)}`` full layout positions
      (including rotation) as returned by the layout engine; used by the router
      for body-crossing avoidance.
    """
    symbol_positions: dict[str, tuple[float, float]] = {}
    # (ref, pin_number) -> (schematic_x, schematic_y, pin_angle)
    pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {}
    symbol_defs_missing: set[str] = set()

    if engine is None:
        engine = make_layout_engine(cache_path=cache_path)
    raw_layout = engine.compute_symbol_positions(ir)
    # Build plain (x, y) map for coordinate lookup and orientation computation.
    layout: dict[str, tuple[float, float]] = {
        ref: (pos[0], pos[1]) for ref, pos in raw_layout.items()
    }
    # Prefer engine-provided rotation (non-None) over computing it separately.
    # GraphvizLayoutEngine always returns a float rotation; NoneLayoutEngine returns
    # None, in which case we fall back to the standalone compute_orientations call.
    _need_fallback_orientations = any(pos[2] is None for pos in raw_layout.values())
    if _need_fallback_orientations:
        tiers: dict[str, int] = assign_tiers(ir)
        orientations: dict[str, int] = compute_orientations(ir, layout, tiers)
    else:
        orientations = {ref: int(pos[2]) for ref, pos in raw_layout.items() if pos[2] is not None}

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

        # Compute pin endpoint positions in schematic space.
        # Pin (at px py angle) in library space is transformed by rotation θ
        # via _transform_pin_at; when θ=0 this is a pure translation.
        rotation = orientations.get(component.ref, 0)
        lib_name, sym_name = component.symbol.split(":", 1)
        for directory in symbol_index.directories:
            pin_at = read_lib_symbol_pin_at(lib_name, sym_name, symbols_dir=directory)
            if pin_at:
                transformed = _transform_pin_at(pin_at, x, y, rotation)
                for pin_num, endpoint in transformed.items():
                    pin_endpoints[(component.ref, pin_num)] = endpoint
                break  # use first directory that has the symbol

        stats["symbols"] += 1

    return symbol_positions, pin_endpoints, symbol_defs_missing, raw_layout


def _embed_symbol_if_found(*, doc: SchematicDoc, symbol: str, symbol_index: SymbolIndex) -> bool:
    lib_name, sym_name = symbol.split(":", 1)
    for directory in symbol_index.directories:
        sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=directory)
        if sym_def is not None:
            doc.embed_lib_symbol(sym_def)
            return True
    return False


# ---------------------------------------------------------------------------
# Mode resolution
# ---------------------------------------------------------------------------


def _resolve_mode(mode_name: str | None, *, default: ValidationMode) -> ValidationMode:
    """Map a mode/validate flag string to a :class:`~kicad_pcb.pipeline.ValidationMode`.

    Accepted values
    ---------------
    ``none``                 → :attr:`~ValidationMode.NONE`
    ``syntax``               → :attr:`~ValidationMode.SYNTAX`
    ``lint`` / ``internal``  → :attr:`~ValidationMode.LINT`  (``internal`` is legacy)
    ``kicad``                → :attr:`~ValidationMode.KICAD`
    ``full``                 → :attr:`~ValidationMode.FULL`
    """
    if mode_name is None:
        return default
    raw = mode_name.strip().lower()
    if raw == "none":
        return ValidationMode.NONE
    if raw == "syntax":
        return ValidationMode.SYNTAX
    if raw in {"internal", "syntax_lint", "lint"}:
        return ValidationMode.LINT
    if raw == "kicad":
        return ValidationMode.KICAD
    if raw == "full":
        return ValidationMode.FULL
    raise UserError(
        f"Unknown mode '{mode_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["none", "syntax", "lint", "kicad", "full"]},
    )


def _resolve_layout(
    layout_name: str | None,
    *,
    cache_path: Path | None = None,
) -> LayoutEngine:
    """Return the layout engine requested by *layout_name*.

    Accepted values
    ---------------
    ``None`` / ``graphviz`` — :func:`make_layout_engine`; raises if ``dot`` is absent.
    """
    name = (layout_name or "graphviz").strip().lower()
    if name == "graphviz":
        return make_layout_engine(cache_path=cache_path)
    raise UserError(
        f"Unknown layout engine '{layout_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["graphviz"]},
    )


def _resolve_routing(routing_name: str | None) -> bool:
    """Return ``use_bus`` bool from *routing_name*.

    Accepted values
    ---------------
    ``bus`` / ``hub`` / ``None``  — spine/hub routing (``use_bus=True``, default).
    ``labels``                    — label-stub routing (``use_bus=False``).
    """
    name = (routing_name or "bus").strip().lower()
    if name in {"bus", "hub"}:
        return True
    if name == "labels":
        return False
    raise UserError(
        f"Unknown routing style '{routing_name}'",
        code=ErrorCode.USER_ERROR,
        details={"allowed": ["bus", "hub", "labels"]},
    )


# ---------------------------------------------------------------------------
# Managed-sheet lifecycle helpers
# ---------------------------------------------------------------------------


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
        minimal_schematic_text(),
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
