"""KiCad schematic document wrapper — AST-based editing for ``.kicad_sch`` files.

This module replaces the regex/string-splice helpers in ``commands/sch.py`` with
a proper document-object model backed by the S-expression AST from
:mod:`kicad_pcb.sexpr`.

Main class
----------
:class:`SchematicDoc` — load, mutate, save a ``.kicad_sch`` document.

Library helpers (standalone)
-----------------------------
:func:`read_lib_symbol_def`  — extract and rename a symbol from a ``.kicad_sym`` file.
:func:`read_lib_symbol_pins` — extract pin numbers from a library symbol.

AST emitters (IR → ListNode)
-----------------------------
These create the ``ListNode`` trees for schematic elements:

:func:`make_symbol_node`  — placed symbol instance.
:func:`make_wire_node`    — wire segment.
:func:`make_label_node`   — net label.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from .errors import ParseError
from .fs import _atomic_write
from .sexpr.builder import L, atom, fnum, string
from .sexpr.nodes import NO_POS, AtomNode, ListNode, Node, StringNode
from .sexpr.parser import parse_file
from .sexpr.serializer import serialize
from .sexpr.utils import find_first, replace_section, walk

# Default system KiCad symbols directory.  Callers can override via the
# ``symbols_dir`` parameter on :func:`read_lib_symbol_def` /
# :func:`read_lib_symbol_pins` to point at a custom location.
_DEFAULT_SYMBOLS_DIR: Path = Path("/usr/share/kicad/symbols")

__all__ = [
    "SchematicDoc",
    "make_label_node",
    "make_symbol_node",
    "make_wire_node",
    "read_lib_symbol_def",
    "read_lib_symbol_pins",
]

# ---------------------------------------------------------------------------
# AST emitters (IR → ListNode)  [Phase 4.3]
# ---------------------------------------------------------------------------


def _effects_font() -> ListNode:
    """Return ``(font (size 1.27 1.27))``."""
    return L(atom("font"), L(atom("size"), fnum(1.27, 2), fnum(1.27, 2)))


def _make_property(
    name: str, value: str, x: float, y: float, *, hide: bool = False
) -> ListNode:
    """Return a KiCad ``(property NAME VALUE (at X Y 0) (effects ...))`` node."""
    at = L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0"))
    effects_items: list[Node] = [atom("effects"), _effects_font()]
    if hide:
        effects_items.append(atom("hide"))
    effects = ListNode(tuple(effects_items), NO_POS)
    return L(atom("property"), string(name), string(value), at, effects)


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
    """
    items: list[Node] = [
        atom("symbol"),
        L(atom("lib_id"), string(lib_sym)),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0")),
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


def make_label_node(name: str, x: float, y: float, label_uuid: str) -> ListNode:
    """Build a ``(label …)`` net-label node for a schematic."""
    effects = L(
        atom("effects"),
        _effects_font(),
        L(atom("justify"), atom("left"), atom("bottom")),
    )
    intersheet_effects = L(
        atom("effects"),
        _effects_font(),
        L(atom("hide"), atom("yes")),
    )
    intersheet_prop = L(
        atom("property"),
        string("Intersheet References"),
        string("${INTERSHEET_REFS}"),
        L(atom("at"), atom("0"), atom("0"), atom("0")),
        intersheet_effects,
    )
    return L(
        atom("label"),
        string(name),
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom("0")),
        L(atom("fields_autoplaced"), atom("yes")),
        effects,
        L(atom("uuid"), string(label_uuid)),
        intersheet_prop,
    )


# ---------------------------------------------------------------------------
# Library symbol helpers
# ---------------------------------------------------------------------------


def _symbol_id(node: ListNode) -> str | None:
    """Return the string id from the second item of a ``(symbol "id" …)`` node."""
    if len(node.items) >= 2 and isinstance(node.items[1], StringNode):
        return node.items[1].value
    return None


def _find_lib_symbol(lib_root: ListNode, sym_name: str) -> ListNode | None:
    """Find a direct-child ``(symbol "sym_name" …)`` node in *lib_root*."""
    for item in lib_root.items:
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and _symbol_id(item) == sym_name
        ):
            return item
    return None


def _collect_pin_numbers(sym_node: ListNode) -> list[str]:
    """Walk *sym_node* and return deduplicated pin number strings in order."""
    seen: set[str] = set()
    result: list[str] = []
    for node in walk(sym_node):
        if isinstance(node, ListNode) and node.key == "pin":
            for child in node.items:
                if (
                    isinstance(child, ListNode)
                    and child.key == "number"
                    and len(child.items) >= 2
                    and isinstance(child.items[1], StringNode)
                ):
                    p = child.items[1].value
                    if p not in seen:
                        seen.add(p)
                        result.append(p)
    return result


def read_lib_symbol_def(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> ListNode | None:
    """Load a symbol definition from a KiCad symbol library and prepare it for embedding.

    Modifications applied before returning:

    * Root symbol id renamed from ``"sym_name"`` → ``"lib_name:sym_name"``.
    * ``(id N)`` child items stripped (KiCad 9 schematics do not use them).

    Returns ``None`` when the library file or symbol is not found, or if the
    file cannot be parsed.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.  Defaults to the
                 system KiCad symbols directory if ``None``.
    """
    if symbols_dir is None:
        symbols_dir = _DEFAULT_SYMBOLS_DIR

    lib_file = symbols_dir / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return None
    try:
        lib_root = parse_file(lib_file)
    except (ParseError, OSError):
        return None

    sym_node = _find_lib_symbol(lib_root, sym_name)
    if sym_node is None:
        return None

    # Rename root symbol: "R" → "Device:R".  Sub-symbol children keep their
    # short names ("R_0_1", "R_1_1") which KiCad requires.
    full_id = f"{lib_name}:{sym_name}"
    new_items: list[Node] = list(sym_node.items)
    new_items[1] = string(full_id)

    # Strip (id N) items recursively — not used in KiCad 9 schematics.
    # In real KiCad library files (id N) appears both as a direct child of
    # the symbol node AND nested inside (property ...) sub-nodes.
    return _strip_id_nodes(ListNode(tuple(new_items), NO_POS))


def _strip_id_nodes(node: ListNode) -> ListNode:
    """Return a copy of *node* with every ``(id N)`` descendant removed."""
    new_children: list[Node] = []
    for item in node.items:
        if isinstance(item, ListNode) and item.key == "id":
            continue  # drop (id N) at any nesting level
        if isinstance(item, ListNode):
            new_children.append(_strip_id_nodes(item))
        else:
            new_children.append(item)
    return ListNode(tuple(new_children), node.pos)


def read_lib_symbol_pins(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> list[str]:
    """Return deduplicated pin number strings for a library symbol.

    Returns an empty list when the library file, symbol, or pin data is not
    available.  The search is performed on the full AST subtree of the symbol,
    so sub-unit pins are included without any character-window heuristics.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    if symbols_dir is None:
        symbols_dir = _DEFAULT_SYMBOLS_DIR

    lib_file = symbols_dir / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return []
    try:
        lib_root = parse_file(lib_file)
    except (ParseError, OSError):
        return []

    sym_node = _find_lib_symbol(lib_root, sym_name)
    if sym_node is None:
        return []
    return _collect_pin_numbers(sym_node)


# ---------------------------------------------------------------------------
# SchematicDoc
# ---------------------------------------------------------------------------


class SchematicDoc:
    """Mutable wrapper around a parsed KiCad schematic (``.kicad_sch``) AST.

    All mutation methods update ``self.root`` in-place by constructing
    replacement :class:`~kicad_pcb.sexpr.nodes.ListNode` instances (the
    underlying AST nodes remain immutable).

    Typical usage::

        doc = SchematicDoc.load(path)
        sym_def = read_lib_symbol_def("Device", "R")
        if sym_def:
            doc.embed_lib_symbol(sym_def)
        pins = read_lib_symbol_pins("Device", "R") or ["1", "2"]
        pin_uuids = [new_uuid() for _ in pins]
        doc.add_symbol("Device:R", "R1", "10k", "", *doc.next_component_position(),
                       new_uuid(), pins, pin_uuids, project.name)
        doc.save(path)
    """

    def __init__(self, root: ListNode) -> None:
        if root.key != "kicad_sch":
            raise ParseError(f"Expected kicad_sch root node, got {root.key!r}")
        self.root = root

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> SchematicDoc:
        """Parse *path* and return a :class:`SchematicDoc`.

        Raises :class:`~kicad_pcb.errors.ParseError` if the file is malformed
        or the root node is not ``kicad_sch``.
        """
        return cls(parse_file(path))

    def save(self, path: Path, *, backup: bool = False) -> None:
        """Serialize ``self.root`` and write atomically to *path*.

        *backup* — if ``True`` and *path* exists, copy original to
        ``<path>.bak`` before overwriting.
        """
        text = serialize(self.root)
        _atomic_write(path, text, "kicad_sch", backup=backup, operation="save-schematic")

    # ------------------------------------------------------------------
    # Library symbol embedding
    # ------------------------------------------------------------------

    def ensure_lib_symbols_section(self) -> None:
        """Add an empty ``(lib_symbols)`` section if none exists."""
        if find_first(self.root, "lib_symbols") is None:
            new_section = L(atom("lib_symbols"))
            self.root = ListNode(self.root.items + (new_section,), self.root.pos)

    def embed_lib_symbol(self, sym_def_node: ListNode) -> bool:
        """Embed *sym_def_node* into ``(lib_symbols)``, skipping duplicates.

        The symbol id is determined from the second item of *sym_def_node*
        (expected to be a :class:`~kicad_pcb.sexpr.nodes.StringNode`).

        Returns
        -------
        bool
            ``True``  — symbol added or was already present.
            ``False`` — symbol id could not be determined.
        """
        full_id = _symbol_id(sym_def_node)
        if full_id is None:
            return False

        self.ensure_lib_symbols_section()
        lib_symbols = find_first(self.root, "lib_symbols")
        if lib_symbols is None:
            return False  # shouldn't happen after ensure_lib_symbols_section

        # Check for existing embedding.
        for item in lib_symbols.items:
            if (
                isinstance(item, ListNode)
                and item.key == "symbol"
                and _symbol_id(item) == full_id
            ):
                return True  # Already embedded.

        new_lib_symbols = ListNode(lib_symbols.items + (sym_def_node,), lib_symbols.pos)
        self.root = replace_section(self.root, "lib_symbols", new_lib_symbols)
        return True

    # ------------------------------------------------------------------
    # Element additions
    # ------------------------------------------------------------------

    def add_symbol(  # noqa: PLR0913
        self,
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
    ) -> None:
        """Append a placed symbol instance to the schematic.

        Parameters mirror :func:`make_symbol_node`.
        """
        node = make_symbol_node(
            lib_sym, ref, value, footprint, x, y,
            sym_uuid, pin_nums, pin_uuids, project_name,
        )
        self._insert_before_sheet_instances(node)

    def add_wire(self, x1: float, y1: float, x2: float, y2: float, wire_uuid: str) -> None:
        """Append a wire segment to the schematic."""
        self._insert_before_sheet_instances(make_wire_node(x1, y1, x2, y2, wire_uuid))

    def add_label(self, name: str, x: float, y: float, label_uuid: str) -> None:
        """Append a net label to the schematic."""
        self._insert_before_sheet_instances(make_label_node(name, x, y, label_uuid))

    # ------------------------------------------------------------------
    # Layout query
    # ------------------------------------------------------------------

    def next_component_position(self) -> tuple[float, float]:
        """Return ``(x, y)`` coordinates for the next component placement.

        Steps 25.4 mm right of the rightmost existing symbol ``(at X Y 0)``
        on the root schematic level.  Defaults to ``(50.8, 76.2)`` for empty
        schematics.
        """
        xs: list[float] = []
        for item in self.root.items:
            if isinstance(item, ListNode) and item.key == "symbol":
                at_node = find_first(item, "at")
                if at_node is not None and len(at_node.items) >= 4:
                    x_node = at_node.items[1]
                    angle_node = at_node.items[3]
                    if (
                        isinstance(x_node, AtomNode)
                        and isinstance(angle_node, AtomNode)
                        and angle_node.value == "0"
                    ):
                        with contextlib.suppress(ValueError):
                            xs.append(float(x_node.value))
        base_x = (max(xs) + 25.4) if xs else 50.8
        return (base_x, 76.2)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_before_sheet_instances(self, node: ListNode) -> None:
        """Insert *node* in root items just before ``(sheet_instances …)``."""
        items = list(self.root.items)
        insertion_idx = len(items)
        for i, item in enumerate(items):
            if isinstance(item, ListNode) and item.key == "sheet_instances":
                insertion_idx = i
                break
        items.insert(insertion_idx, node)
        self.root = ListNode(tuple(items), self.root.pos)
