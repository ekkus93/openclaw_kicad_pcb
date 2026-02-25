"""Schematic editing commands: add-component, add-net, connect.

All structural modifications to ``.kicad_sch`` files are performed through
:class:`~kicad_pcb.sch_doc.SchematicDoc`, which operates on the S-expression
AST rather than regex/string splicing.
"""
from __future__ import annotations

from pathlib import Path

from ..config import get_current_project
from ..errors import UserError
from ..fs import _new_uuid
from ..models import ComponentSpec, NetLabelSpec, WireSegment
from ..results import AddComponentResult, AddNetResult, ConnectResult
from ..sch_doc import SchematicDoc, read_lib_symbol_def, read_lib_symbol_pins

# KiCad symbol library path (system default; override via KICAD_SYMBOLS_DIR env
# if needed, or pass ``symbols_dir=`` to read_lib_symbol_def / read_lib_symbol_pins).
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
    pin_nums = read_lib_symbol_pins(
        spec.lib_name, spec.sym_name, symbols_dir=KICAD_SYMBOLS_DIR
    )
    if not pin_nums:
        print(f"Warning: Symbol '{spec.lib_sym}' not found in {KICAD_SYMBOLS_DIR}")
        print("   Using default pins [1, 2]. Edit footprint assignment in KiCad.")
        pin_nums = ["1", "2"]

    doc = SchematicDoc.load(sch_file)
    x, y = doc.next_component_position()
    sym_uuid = _new_uuid()
    pin_uuids = [_new_uuid() for _ in pin_nums]

    doc.add_symbol(
        spec.lib_sym, spec.ref, spec.value, spec.footprint,
        x, y, sym_uuid, pin_nums, pin_uuids, project.name,
    )

    # Embed the symbol definition so kicad-cli can generate library part info
    # for netlist/BOM export.  read_lib_symbol_def uses AST-based extraction,
    # stripping (id N) and using short sub-symbol names (e.g. "R_0_1", not
    # "Device:R_0_1") for KiCad compatibility.
    sym_def = read_lib_symbol_def(
        spec.lib_name, spec.sym_name, symbols_dir=KICAD_SYMBOLS_DIR
    )
    if sym_def is not None:
        doc.embed_lib_symbol(sym_def)

    doc.save(sch_file)

    return AddComponentResult(
        ref=spec.ref,
        lib_sym=spec.lib_sym,
        value=spec.value,
        x=x,
        y=y,
        pins=tuple(pin_nums),
        has_footprint=bool(spec.footprint),
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
    doc = SchematicDoc.load(sch_file)
    doc.add_label(label.name, label.x, label.y, _new_uuid())
    doc.save(sch_file)
    return AddNetResult(name=label.name, x=label.x, y=label.y)


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
    doc = SchematicDoc.load(sch_file)
    doc.add_wire(wire.x1, wire.y1, wire.x2, wire.y2, _new_uuid())
    doc.save(sch_file)
    return ConnectResult(x1=wire.x1, y1=wire.y1, x2=wire.x2, y2=wire.y2)
