"""Schematic editing commands: add-component, add-net, connect.

All structural modifications to ``.kicad_sch`` files go through
:func:`~kicad_pcb.pipeline.mutate_and_validate_sch`, which:

1. Loads the file into an AST-backed :class:`~kicad_pcb.sch_doc.SchematicDoc`.
2. Applies the mutation via a closure.
3. Round-trip serialises and re-parses the result (syntax check).
4. Runs structural lint rules (SCH001–SCH009).
5. Commits atomically only when all checks pass.
"""

from __future__ import annotations

from pathlib import Path

from ..config import SymbolsDir, discover_symbols_dir, get_current_project
from ..errors import UserError
from ..fs import _new_uuid
from ..models import ComponentSpec, NetLabelSpec, WireSegment
from ..pipeline import mutate_and_validate_sch
from ..results import AddComponentResult, AddNetResult, ConnectResult
from ..sch_doc import SchematicDoc, read_lib_symbol_def, read_lib_symbol_pins

# Default fallback symbol library path kept for backward compatibility.
# Callers should use :func:`~kicad_pcb.config.discover_symbols_dir` to obtain
# the correct path for the current environment.
KICAD_SYMBOLS_DIR = Path("/usr/share/kicad/symbols")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_add_component(args) -> AddComponentResult:
    """Add a component symbol to the schematic.

    Usage: add-component <LIB:SYM> <REF> [--value V] [--footprint FP]
    Example: add-component Device:R R1 --value 10k --footprint Resistor_SMD:R_0402
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    spec = ComponentSpec.from_args(args)

    # Resolve symbol library directory: explicit flag > env var > config > platform.
    explicit_path = Path(args.symbols_dir) if getattr(args, "symbols_dir", None) else None
    sym_dir_result: SymbolsDir | None = discover_symbols_dir(explicit=explicit_path)
    sym_dir = sym_dir_result.path if sym_dir_result is not None else KICAD_SYMBOLS_DIR

    pin_nums = read_lib_symbol_pins(spec.lib_name, spec.sym_name, symbols_dir=sym_dir)
    if not pin_nums:
        print(f"Warning: Symbol '{spec.lib_sym}' not found in {sym_dir}")
        print("   Using default pins [1, 2]. Edit footprint assignment in KiCad.")
        print("   Hint: set KICAD_SYMBOLS_DIR or --symbols-dir to point to your library.")
        pin_nums = ["1", "2"]

    # Load the symbol definition before the pipeline so we can embed it.
    # read_lib_symbol_def uses AST-based extraction, stripping (id N) and
    # using short sub-symbol names (e.g. "R_0_1") for KiCad compatibility.
    sym_def = read_lib_symbol_def(spec.lib_name, spec.sym_name, symbols_dir=sym_dir)

    # Capture placement coordinates from inside the closure.
    _placed: dict[str, object] = {}

    def _mutate(doc: SchematicDoc) -> None:
        x, y = doc.next_component_position()
        sym_uuid = _new_uuid()
        pin_uuids = [_new_uuid() for _ in pin_nums]
        doc.add_symbol(
            spec.lib_sym,
            spec.ref,
            spec.value,
            spec.footprint,
            x,
            y,
            sym_uuid,
            pin_nums,
            pin_uuids,
            project.name,
        )
        if sym_def is not None:
            doc.embed_lib_symbol(sym_def)
        _placed["x"] = x
        _placed["y"] = y

    mutate_and_validate_sch(
        sch_file, _mutate, operation="add-component", dry_run=getattr(args, "dry_run", False)
    )

    return AddComponentResult(
        ref=spec.ref,
        lib_sym=spec.lib_sym,
        value=spec.value,
        x=float(_placed["x"]),  # type: ignore[arg-type]
        y=float(_placed["y"]),  # type: ignore[arg-type]
        pins=tuple(pin_nums),
        has_footprint=bool(spec.footprint),
        dry_run=getattr(args, "dry_run", False),
    )


def cmd_add_net(args) -> AddNetResult:
    """Add a named net label to the schematic.

    Usage: add-net <NAME> [--x X] [--y Y]   (coordinates in mm)
    Example: add-net VCC --x 60 --y 50
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    label = NetLabelSpec.from_args(args)
    uuid = _new_uuid()

    def _mutate(doc: SchematicDoc) -> None:
        doc.add_label(label.name, label.x, label.y, uuid)

    mutate_and_validate_sch(
        sch_file, _mutate, operation="add-net", dry_run=getattr(args, "dry_run", False)
    )
    return AddNetResult(
        name=label.name, x=label.x, y=label.y, dry_run=getattr(args, "dry_run", False)
    )


def cmd_connect(args) -> ConnectResult:
    """Add a wire segment between two coordinates in the schematic.

    Usage: connect --from X1,Y1 --to X2,Y2   (coordinates in mm)
    Example: connect --from 50.8,76.2 --to 76.2,76.2

    Tip: use ``preview-schematic`` to read pin coordinates after placing components.
    """
    project = get_current_project()
    if not project:
        raise UserError("No project selected")

    sch_file = project.sch_file
    if not sch_file.exists():
        raise UserError(f"Schematic not found: {sch_file}")

    wire = WireSegment.from_args(args)
    uuid = _new_uuid()

    def _mutate(doc: SchematicDoc) -> None:
        doc.add_wire(wire.x1, wire.y1, wire.x2, wire.y2, uuid)

    mutate_and_validate_sch(
        sch_file, _mutate, operation="connect", dry_run=getattr(args, "dry_run", False)
    )
    return ConnectResult(
        x1=wire.x1, y1=wire.y1, x2=wire.x2, y2=wire.y2, dry_run=getattr(args, "dry_run", False)
    )
