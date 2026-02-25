"""KiCad PCB document wrapper — AST-based editing for ``.kicad_pcb`` files.

This module replaces the regex-based editing helpers in ``commands/pcb.py``
with a proper document-object model backed by the S-expression AST from
:mod:`kicad_pcb.sexpr`.

Main class
----------
:class:`PcbDoc` — load, mutate, save a ``.kicad_pcb`` document.

AST emitters (IR → ListNode)
-----------------------------
:func:`make_gr_line_node` — a single ``(gr_line …)`` Edge.Cuts segment.
"""

from __future__ import annotations

from pathlib import Path

from .errors import ParseError
from .fs import _atomic_write, _new_uuid
from .models import BoardOutlineRect
from .sexpr.builder import L, atom, fnum, string
from .sexpr.nodes import NO_POS, ListNode, Node, StringNode
from .sexpr.parser import parse_file
from .sexpr.serializer import serialize
from .sexpr.utils import find_first

__all__ = [
    "PcbDoc",
    "make_gr_line_node",
]


# ---------------------------------------------------------------------------
# AST emitters (IR → ListNode)  [Phase 4.3]
# ---------------------------------------------------------------------------


def make_gr_line_node(  # noqa: PLR0913
    sx: float,
    sy: float,
    ex: float,
    ey: float,
    line_uuid: str,
    *,
    layer: str = "Edge.Cuts",
    width: float = 0.05,
) -> ListNode:
    """Build a ``(gr_line …)`` node for a PCB layout line.

    Parameters
    ----------
    sx, sy:     Start coordinate (mm).
    ex, ey:     End coordinate (mm).
    line_uuid:  UUID string for the line instance.
    layer:      KiCad layer name (default ``"Edge.Cuts"``).
    width:      Stroke width in mm (default ``0.05``).
    """
    return L(
        atom("gr_line"),
        L(atom("start"), fnum(sx, 3), fnum(sy, 3)),
        L(atom("end"), fnum(ex, 3), fnum(ey, 3)),
        L(
            atom("stroke"),
            L(atom("width"), fnum(width, 2)),
            L(atom("type"), atom("solid")),
        ),
        L(atom("layer"), string(layer)),
        L(atom("uuid"), string(line_uuid)),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _footprint_ref(fp: ListNode) -> str | None:
    """Return the ``Reference`` property value of a footprint, or ``None``."""
    for child in fp.items:
        if (
            isinstance(child, ListNode)
            and child.key == "property"
            and len(child.items) >= 3
            and isinstance(child.items[1], StringNode)
            and child.items[1].value == "Reference"
            and isinstance(child.items[2], StringNode)
        ):
            return child.items[2].value
    return None


def _is_edge_cuts_gr_line(item: ListNode) -> bool:
    """Return ``True`` if *item* is a ``(gr_line …)`` on layer ``Edge.Cuts``."""
    if item.key != "gr_line":
        return False
    layer_node = find_first(item, "layer")
    return (
        layer_node is not None
        and len(layer_node.items) >= 2
        and isinstance(layer_node.items[1], StringNode)
        and layer_node.items[1].value == "Edge.Cuts"
    )


def _update_footprint_at(fp: ListNode, new_x: float, new_y: float) -> ListNode:
    """Return a copy of *fp* with its ``(at …)`` coordinates updated.

    Any rotation / angle already present in the ``at`` node is preserved.
    """
    new_fp_items: list[Node] = []
    for child in fp.items:
        if isinstance(child, ListNode) and child.key == "at":
            # Preserve any rotation (items after "at", x, y).
            extra = child.items[3:]
            new_at = ListNode((atom("at"), fnum(new_x, 3), fnum(new_y, 3)) + extra, NO_POS)
            new_fp_items.append(new_at)
        else:
            new_fp_items.append(child)
    return ListNode(tuple(new_fp_items), fp.pos)


# ---------------------------------------------------------------------------
# PcbDoc
# ---------------------------------------------------------------------------


class PcbDoc:
    """Mutable wrapper around a parsed KiCad PCB layout (``.kicad_pcb``) AST.

    All mutation methods update ``self.root`` in-place by constructing
    replacement :class:`~kicad_pcb.sexpr.nodes.ListNode` instances (the
    underlying AST nodes remain immutable).

    Typical usage::

        doc = PcbDoc.load(path)
        doc.set_rect_outline(50.0, 30.0)
        doc.save(path)
    """

    def __init__(self, root: ListNode) -> None:
        if root.key != "kicad_pcb":
            raise ParseError(f"Expected kicad_pcb root node, got {root.key!r}")
        self.root = root

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> PcbDoc:
        """Parse *path* and return a :class:`PcbDoc`.

        Raises :class:`~kicad_pcb.errors.ParseError` if the file is malformed
        or the root node is not ``kicad_pcb``.
        """
        return cls(parse_file(path))

    def save(self, path: Path, *, backup: bool = False) -> None:
        """Serialize ``self.root`` and write atomically to *path*.

        *backup* — if ``True`` and *path* exists, copy original to
        ``<path>.bak`` before overwriting.
        """
        text = serialize(self.root)
        _atomic_write(path, text, "kicad_pcb", backup=backup, operation="save-pcb")

    # ------------------------------------------------------------------
    # Outline mutations
    # ------------------------------------------------------------------

    def clear_generated_outline(self) -> None:
        """Remove all ``(gr_line …)`` items on the ``Edge.Cuts`` layer."""
        new_items = [
            item
            for item in self.root.items
            if not (isinstance(item, ListNode) and _is_edge_cuts_gr_line(item))
        ]
        self.root = ListNode(tuple(new_items), self.root.pos)

    def set_rect_outline(self, width: float, height: float) -> None:
        """Set a rectangular board outline on ``Edge.Cuts``.

        Clears any existing ``Edge.Cuts`` ``gr_line`` entries, then appends
        four new segments forming a closed rectangle starting at the origin.

        Parameters
        ----------
        width, height: Board dimensions in mm.
        """
        outline = BoardOutlineRect(width=width, height=height)
        self.clear_generated_outline()
        new_lines = [
            make_gr_line_node(s[0], s[1], e[0], e[1], _new_uuid()) for s, e in outline.corners
        ]
        self.root = ListNode(self.root.items + tuple(new_lines), self.root.pos)

    # ------------------------------------------------------------------
    # Footprint queries / mutations
    # ------------------------------------------------------------------

    def find_footprint_by_ref(self, ref: str) -> ListNode | None:
        """Return the first footprint ``ListNode`` whose ``Reference`` equals *ref*.

        Only direct children of the PCB root are searched.  Returns ``None``
        if no matching footprint is found.
        """
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "footprint"
                and _footprint_ref(item) == ref
            ):
                return item
        return None

    def all_footprints(self) -> list[tuple[str, ListNode]]:
        """Return ``[(ref_or_lib_name, node), …]`` for all footprints.

        The first tuple element is the ``Reference`` property value when
        available, falling back to the footprint library name (second item)
        when no ``Reference`` property is found.
        """
        result: list[tuple[str, ListNode]] = []
        for item in self.root.items:
            if isinstance(item, ListNode) and item.key == "footprint":
                ref = _footprint_ref(item)
                if ref is None:
                    # Fall back to the footprint lib:name string.
                    ref = (
                        item.items[1].value
                        if len(item.items) >= 2 and isinstance(item.items[1], StringNode)
                        else "?"
                    )
                result.append((ref, item))
        return result

    def move_footprint(self, ref: str, x: float, y: float) -> bool:
        """Update the placement coordinates of the footprint identified by *ref*.

        The reference must match the footprint's ``Reference`` property value.
        Rotation / angle in the ``(at …)`` node is preserved.

        Returns
        -------
        bool
            ``True`` if the footprint was found and updated, ``False`` otherwise.
        """
        new_items: list[Node] = []
        found = False
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "footprint"
                and _footprint_ref(item) == ref
            ):
                new_items.append(_update_footprint_at(item, x, y))
                found = True
            else:
                new_items.append(item)
        if found:
            self.root = ListNode(tuple(new_items), self.root.pos)
        return found
