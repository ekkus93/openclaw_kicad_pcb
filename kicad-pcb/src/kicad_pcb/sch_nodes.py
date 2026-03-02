"""AST emitters for KiCad schematic elements.

This module contains the pure-function builders that convert IR-level data
into the ``ListNode`` trees required by the KiCad S-expression format.  It
has no dependencies on :class:`~kicad_pcb.sch_doc.SchematicDoc` or any library
I/O; the only dependency is the S-expression builder layer.

Public API (also re-exported from :mod:`kicad_pcb.sch_doc` for backward
compatibility):

:func:`make_symbol_node`        — placed symbol instance.
:func:`make_wire_node`          — wire segment.
:func:`make_label_node`         — net label.
:func:`make_global_label_node`  — global / power label.
:func:`make_junction_node`      — junction dot.
:func:`make_text_node`          — text annotation.
:func:`make_managed_sheet_node` — OpenClaw managed sub-sheet.
:class:`ManagedSheetSpec`       — spec dataclass for :func:`make_managed_sheet_node`.
"""

from __future__ import annotations

from dataclasses import dataclass

from .sexpr.builder import L, atom, fnum, string
from .sexpr.nodes import NO_POS, ListNode, Node

__all__ = [
    "ManagedSheetSpec",
    "make_global_label_node",
    "make_junction_node",
    "make_label_node",
    "make_managed_sheet_node",
    "make_symbol_node",
    "make_text_node",
    "make_wire_node",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _effects_font() -> ListNode:
    """Return ``(font (size 1.27 1.27))``."""
    return L(atom("font"), L(atom("size"), fnum(1.27, 2), fnum(1.27, 2)))


def _make_property(name: str, value: str, x: float, y: float, *, hide: bool = False) -> ListNode:
    """Return a KiCad ``(property NAME VALUE (at X Y 0) (effects ...))`` node."""
    at = L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0"))
    effects_items: list[Node] = [atom("effects"), _effects_font()]
    if hide:
        effects_items.append(atom("hide"))
    effects = ListNode(tuple(effects_items), NO_POS)
    return L(atom("property"), string(name), string(value), at, effects)


def _make_intersheet_prop() -> ListNode:
    """Return the standard KiCad ``(property "Intersheet References" …)`` node.

    Both ``(label …)`` and ``(global_label …)`` nodes require this property
    for cross-sheet reference rendering.  Extracted to avoid duplication.
    """
    return L(
        atom("property"),
        string("Intersheet References"),
        string("${INTERSHEET_REFS}"),
        L(atom("at"), atom("0"), atom("0"), atom("0")),
        L(atom("effects"), _effects_font(), L(atom("hide"), atom("yes"))),
    )


# ---------------------------------------------------------------------------
# Public AST emitters
# ---------------------------------------------------------------------------


def make_symbol_node(  # noqa: PLR0913
    lib_sym: str,
    ref: str,
    value: str,
    footprint: str,
    x: float,
    y: float,
    sym_uuid: str,
    pin_nums: list[str],
    pin_uuids: list[str],
    project_name: str,
    rotation: int = 0,
) -> ListNode:
    """Build a placed symbol instance ``(symbol …)`` node for a schematic.

    Parameters
    ----------
    lib_sym:      Full ``Lib:Symbol`` id (e.g. ``"Device:R"``).
    ref:          Reference designator (e.g. ``"R1"``).
    value:        Value string (e.g. ``"10k"``).
    footprint:    Footprint assignment (may be empty string).
    x, y:         Placement coordinates in mm.
    sym_uuid:     UUID string for the symbol instance.
    pin_nums:     Ordered list of pin number strings.
    pin_uuids:    UUID string for each pin (parallel to *pin_nums*).
    project_name: KiCad project name (used in ``(instances …)``).
    rotation:     Symbol rotation in degrees (CCW, KiCad convention).
                  0 = default orientation, 90 = rotated 90° CCW.
    """
    items: list[Node] = [
        atom("symbol"),
        L(atom("lib_id"), string(lib_sym)),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom(str(rotation))),
        L(atom("unit"), atom("1")),
        L(atom("exclude_from_sim"), atom("yes")),
        L(atom("in_bom"), atom("yes")),
        L(atom("on_board"), atom("yes")),
        L(atom("uuid"), string(sym_uuid)),
        _make_property("Reference", ref, x + 1.27, y - 1.27),
        _make_property("Value", value, x + 1.27, y + 1.27),
        _make_property("Footprint", footprint, x, y, hide=True),
        _make_property("Datasheet", "~", x, y, hide=True),
    ]
    for p_num, p_uuid in zip(pin_nums, pin_uuids):
        items.append(L(atom("pin"), string(p_num), L(atom("uuid"), string(p_uuid))))
    items.append(
        L(
            atom("instances"),
            L(
                atom("project"),
                string(project_name),
                L(
                    atom("path"),
                    string("/"),
                    L(atom("reference"), string(ref)),
                    L(atom("unit"), atom("1")),
                ),
            ),
        )
    )
    return ListNode(tuple(items), NO_POS)


def make_wire_node(x1: float, y1: float, x2: float, y2: float, wire_uuid: str) -> ListNode:
    """Build a ``(wire …)`` segment node for a schematic."""
    return L(
        atom("wire"),
        L(
            atom("pts"),
            L(atom("xy"), fnum(x1, 2), fnum(y1, 2)),
            L(atom("xy"), fnum(x2, 2), fnum(y2, 2)),
        ),
        L(atom("stroke"), L(atom("width"), atom("0")), L(atom("type"), atom("default"))),
        L(atom("uuid"), string(wire_uuid)),
    )


def make_label_node(name: str, x: float, y: float, label_uuid: str, *, angle: int = 0) -> ListNode:
    """Build a ``(label …)`` net-label node for a schematic.

    Parameters
    ----------
    angle:
        Label orientation in degrees (KiCad convention: 0=right, 90=down,
        180=left, 270=up).  Controls which direction the label's connection
        stub points — use the outward direction of the pin the label attaches
        to.  Defaults to 0 (right).
    """
    effects = L(
        atom("effects"),
        _effects_font(),
        L(atom("justify"), atom("left"), atom("bottom")),
    )
    return L(
        atom("label"),
        string(name),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom(str(angle))),
        L(atom("fields_autoplaced"), atom("yes")),
        effects,
        L(atom("uuid"), string(label_uuid)),
        _make_intersheet_prop(),
    )


def make_text_node(text: str, x: float, y: float, *, hidden: bool = False) -> ListNode:
    """Build a schematic ``(text ...)`` node.

    This is used for lightweight metadata markers (for example, OpenClaw
    ownership markers) and optional helper annotations.
    """
    effects_items: list[Node] = [atom("effects"), _effects_font(), L(atom("justify"), atom("left"))]
    if hidden:
        effects_items.append(atom("hide"))
    effects = ListNode(tuple(effects_items), NO_POS)
    return L(
        atom("text"),
        string(text),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0")),
        effects,
    )


def make_junction_node(x: float, y: float, junction_uuid: str) -> ListNode:
    """Build a KiCad ``(junction ...)`` node at *(x, y)*.

    Junctions are placed where wire segments meet at a T- or X-intersection
    to make the electrical connection explicit in the schematic.
    """
    return L(
        atom("junction"),
        L(atom("at"), fnum(x, 2), fnum(y, 2)),
        L(atom("diameter"), atom("0")),
        L(atom("color"), atom("0"), atom("0"), atom("0"), atom("0")),
        L(atom("uuid"), string(junction_uuid)),
    )


def make_global_label_node(  # noqa: PLR0913
    name: str,
    x: float,
    y: float,
    label_uuid: str,
    *,
    angle: int = 0,
    shape: str = "input",
) -> ListNode:
    """Build a KiCad ``(global_label ...)`` node.

    Global labels (power flags, supply rails) are used in place of ordinary
    net labels for power nets (GND, VCC, etc.) and for nets that cross
    sheet boundaries, to avoid spaghetti label repetition.

    Parameters
    ----------
    shape:
        KiCad shape keyword: ``"input"``, ``"output"``, ``"bidirectional"``,
        ``"tri_state"``, or ``"passive"``.  Defaults to ``"input"``.
    """
    effects = L(
        atom("effects"),
        _effects_font(),
        L(atom("justify"), atom("left")),
    )
    return L(
        atom("global_label"),
        string(name),
        L(atom("shape"), atom(shape)),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom(str(angle))),
        L(atom("fields_autoplaced"), atom("yes")),
        effects,
        L(atom("uuid"), string(label_uuid)),
        _make_intersheet_prop(),
    )


@dataclass(frozen=True)
class ManagedSheetSpec:
    sheet_name: str
    sheet_file: str
    sheet_uuid: str
    x: float = 20.0
    y: float = 20.0
    w: float = 80.0
    h: float = 60.0


def make_managed_sheet_node(
    spec: ManagedSheetSpec,
) -> ListNode:
    """Build a top-level ``(sheet ...)`` node for the OpenClaw managed sheet."""
    return L(
        atom("sheet"),
        L(atom("at"), fnum(spec.x, 2), fnum(spec.y, 2)),
        L(atom("size"), fnum(spec.w, 2), fnum(spec.h, 2)),
        L(atom("stroke"), L(atom("width"), atom("0")), L(atom("type"), atom("default"))),
        L(atom("fill"), L(atom("color"), atom("0"), atom("0"), atom("0"), atom("0"))),
        L(atom("uuid"), string(spec.sheet_uuid)),
        _make_property("Sheetname", spec.sheet_name, spec.x + 1.0, spec.y - 1.5),
        _make_property("Sheetfile", spec.sheet_file, spec.x + 1.0, spec.y + spec.h + 1.5),
    )
