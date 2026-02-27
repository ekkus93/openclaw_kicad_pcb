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
import json
from dataclasses import dataclass
from functools import lru_cache
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


@lru_cache(maxsize=64)
def _parse_lib_file(path: Path) -> ListNode:
    """Parse a ``.kicad_sym`` library file, caching the result for the lifetime
    of the process.

    KiCad system symbol libraries can exceed 94 k lines
    (``Connector.kicad_sym``).  Without caching, every symbol look-up in the
    same library re-parses the entire file, causing the tool to hang.

    Only ``.kicad_sym`` files are passed here; schematic (``.kicad_sch``) files
    are always read fresh via :func:`parse_file` so in-process edits and test
    round-trips are never shadowed by a stale cache entry.
    """
    return parse_file(path)


__all__ = [
    "SchematicDoc",
    "make_label_node",
    "make_managed_sheet_node",
    "make_text_node",
    "make_symbol_node",
    "make_wire_node",
    "read_lib_symbol_def",
    "read_lib_symbol_def_chain",
    "read_lib_symbol_pin_at",
    "read_lib_symbol_pins",
]

# ---------------------------------------------------------------------------
# AST emitters (IR → ListNode)  [Phase 4.3]
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
        L(atom("at"), fnum(x, 2), fnum(y, 2), atom(str(angle))),
        L(atom("fields_autoplaced"), atom("yes")),
        effects,
        L(atom("uuid"), string(label_uuid)),
        intersheet_prop,
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
        if isinstance(item, ListNode) and item.key == "symbol" and _symbol_id(item) == sym_name:
            return item
    return None


def _get_extends_name(sym_node: ListNode) -> str | None:
    """Return the bare base name from ``(extends "BaseName")`` if present."""
    for item in sym_node.items:
        if (
            isinstance(item, ListNode)
            and item.key == "extends"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            return item.items[1].value
    return None


def _qualify_extends(sym_node: ListNode, lib_name: str) -> ListNode:
    """Qualify ``(extends "BaseName")`` to ``(extends "lib_name:BaseName")``.

    KiCad schematics require fully-qualified symbol ids in ``extends``
    references.  Does nothing when the value already contains ``":"``.
    Returns a modified copy; the original node is unchanged.
    """
    new_items: list[Node] = []
    for item in sym_node.items:
        out_item: Node = item
        if (
            isinstance(item, ListNode)
            and item.key == "extends"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            base_name = item.items[1].value
            if ":" not in base_name:
                new_sub = list(item.items)
                new_sub[1] = string(f"{lib_name}:{base_name}")
                out_item = ListNode(tuple(new_sub), item.pos)
        new_items.append(out_item)
    return ListNode(tuple(new_items), sym_node.pos)


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


def _collect_pin_at(
    sym_node: ListNode,
) -> dict[str, tuple[float, float, float]]:
    """Walk *sym_node* and return ``{pin_number: (x, y, angle)}`` for all pins.

    *(x, y)* is the pin connection endpoint in library-local coordinates.
    *angle* is in degrees using KiCad's convention (0=right, 90=down,
    180=left, 270=up).

    Only the first occurrence of each pin number is kept (base-symbol wins
    when results from multiple levels in an extends chain are merged by the
    caller).
    """
    result: dict[str, tuple[float, float, float]] = {}
    for node in walk(sym_node):
        if not (isinstance(node, ListNode) and node.key == "pin"):
            continue
        pin_num: str | None = None
        for child in node.items:
            if (
                isinstance(child, ListNode)
                and child.key == "number"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                pin_num = child.items[1].value
                break
        if pin_num is None or pin_num in result:
            continue
        at_node = find_first(node, "at")
        if at_node is None or len(at_node.items) < 4:
            continue
        try:
            x = float(at_node.items[1].value)  # type: ignore[union-attr]
            y = float(at_node.items[2].value)  # type: ignore[union-attr]
            a = float(at_node.items[3].value)  # type: ignore[union-attr]
        except (ValueError, AttributeError):
            continue
        result[pin_num] = (x, y, a)
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
        lib_root = _parse_lib_file(lib_file)
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


def read_lib_symbol_def_chain(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> list[ListNode]:
    """Load a symbol and its full ``extends`` ancestor chain for embedding.

    KiCad symbols that use ``(extends "BaseName")`` carry no graphics or
    pins of their own — those are inherited from the base symbol.  A
    schematic's ``lib_symbols`` section must contain *all* nodes in the
    inheritance chain for KiCad to render the symbol correctly.

    Returns nodes in dependency order (**base first**, derived last) so
    callers can embed them with :meth:`~SchematicDoc.embed_lib_symbol` in
    order.  Each node has its id qualified to ``lib_name:name`` and
    ``(id N)`` children stripped.  ``(extends "BaseName")`` attributes in
    derived nodes are updated to the fully-qualified
    ``"lib_name:BaseName"`` form required by KiCad schematics.

    Returns an empty list when the library file or root symbol cannot be
    found or the extends chain is broken.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Amplifier_Operational"``).
    sym_name:    Symbol name within the library (e.g. ``"NE5532"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    if symbols_dir is None:
        symbols_dir = _DEFAULT_SYMBOLS_DIR

    lib_file = symbols_dir / f"{lib_name}.kicad_sym"
    if not lib_file.exists():
        return []
    try:
        lib_root = _parse_lib_file(lib_file)
    except (ParseError, OSError):
        return []

    # Walk extends chain: collect sym_names in derived-first order.
    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = sym_name
    while current is not None and current not in visited:
        node = _find_lib_symbol(lib_root, current)
        if node is None:
            return []  # broken chain — refuse to emit a partial result
        visited.add(current)
        chain.append(current)
        current = _get_extends_name(node)

    chain.reverse()  # base-first so KiCad can resolve references in order

    result: list[ListNode] = []
    for name in chain:
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            return []  # shouldn't happen — already verified above

        # Qualify root-level id: "NE5532" → "Amplifier_Operational:NE5532".
        new_items_chain: list[Node] = list(node.items)
        new_items_chain[1] = string(f"{lib_name}:{name}")
        renamed = ListNode(tuple(new_items_chain), NO_POS)

        # Qualify extends reference if present: "LM2904" → "lib:LM2904".
        renamed = _qualify_extends(renamed, lib_name)

        result.append(_strip_id_nodes(renamed))

    return result


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
        lib_root = _parse_lib_file(lib_file)
    except (ParseError, OSError):
        return []

    # Build extends chain (derived first) so we can collect pins from all
    # ancestor symbols.  For non-extends symbols the chain has one entry.
    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = sym_name
    while current is not None and current not in visited:
        node = _find_lib_symbol(lib_root, current)
        if node is None:
            break
        visited.add(current)
        chain.append(current)
        current = _get_extends_name(node)

    # Collect pins base-first.  Derived symbols may redefine pins from the
    # base; ``seen`` deduplicates by pin number so each appears only once.
    seen: set[str] = set()
    result: list[str] = []
    for name in reversed(chain):  # reversed = base first
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            continue
        for pin_num in _collect_pin_numbers(node):
            if pin_num not in seen:
                seen.add(pin_num)
                result.append(pin_num)
    return result


def read_lib_symbol_pin_at(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, tuple[float, float, float]]:
    """Return pin connection-point coordinates for *lib_name:sym_name*.

    Follows ``(extends ...)`` chains so inherited pin positions are included.
    Base-symbol positions take priority when a derived symbol redefines a pin.

    Returns a dict of ``{pin_number: (x, y, angle)}`` where:

    * *(x, y)* — pin endpoint in library-local coordinates (mm).
    * *angle* — KiCad pin direction in degrees: 0=right, 90=down, 180=left,
      270=up.  This is the direction **from** the endpoint **toward** the
      symbol body; wire extensions should point in the **opposite** direction.

    Returns an empty dict when the library file or symbol cannot be found.

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
        return {}
    try:
        lib_root = _parse_lib_file(lib_file)
    except (ParseError, OSError):
        return {}

    # Build extends chain (derived first) so we can collect from base symbols.
    chain: list[str] = []
    visited: set[str] = set()
    current: str | None = sym_name
    while current is not None and current not in visited:
        node = _find_lib_symbol(lib_root, current)
        if node is None:
            break
        visited.add(current)
        chain.append(current)
        current = _get_extends_name(node)

    # Collect pin positions base-first; first occurrence wins (same as pins).
    result: dict[str, tuple[float, float, float]] = {}
    for name in reversed(chain):  # reversed = base first
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            continue
        for pin_num, coords in _collect_pin_at(node).items():
            if pin_num not in result:
                result[pin_num] = coords
    return result


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
        for sym_def in read_lib_symbol_def_chain("Device", "R"):
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
            if isinstance(item, ListNode) and item.key == "symbol" and _symbol_id(item) == full_id:
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
            lib_sym,
            ref,
            value,
            footprint,
            x,
            y,
            sym_uuid,
            pin_nums,
            pin_uuids,
            project_name,
        )
        self._insert_before_sheet_instances(node)

    def add_wire(self, x1: float, y1: float, x2: float, y2: float, wire_uuid: str) -> None:
        """Append a wire segment to the schematic."""
        self._insert_before_sheet_instances(make_wire_node(x1, y1, x2, y2, wire_uuid))

    def add_label(self, name: str, x: float, y: float, label_uuid: str, *, angle: int = 0) -> None:
        """Append a net label to the schematic.

        Parameters
        ----------
        angle:
            Label orientation in degrees (0=right, 90=down, 180=left, 270=up).
            Pass the outward direction of the pin the label will attach to so
            the label visually extends away from the symbol body.
        """
        self._insert_before_sheet_instances(make_label_node(name, x, y, label_uuid, angle=angle))

    def add_text(self, text: str, x: float, y: float, *, hidden: bool = False) -> None:
        """Append a text node to the schematic."""
        self._insert_before_sheet_instances(make_text_node(text, x, y, hidden=hidden))

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
    # Introspection helpers (CODE_REVIEW3)
    # ------------------------------------------------------------------

    def has_openclaw_marker(self) -> bool:
        """Return ``True`` when an OpenClaw ownership marker text exists."""
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
                and item.items[1].value.startswith("OpenClaw:generated=")
            ):
                return True
        return False

    def ensure_openclaw_marker(self) -> bool:
        """Ensure off-canvas OpenClaw marker text nodes are present.

        Returns ``True`` when at least one marker is inserted, ``False`` when
        markers already exist.
        """
        has_generated = False
        has_region = False
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
            ):
                value = item.items[1].value
                if value == "OpenClaw:generated=v1":
                    has_generated = True
                elif value == "OpenClaw:region=managed":
                    has_region = True

        inserted = False
        if not has_generated:
            self._insert_before_sheet_instances(
                make_text_node("OpenClaw:generated=v1", -1000.0, -1000.0, hidden=True)
            )
            inserted = True
        if not has_region:
            self._insert_before_sheet_instances(
                make_text_node("OpenClaw:region=managed", -1000.0, -1010.0, hidden=True)
            )
            inserted = True
        return inserted

    def list_symbols(self) -> list[dict[str, object]]:
        """Return a deterministic list of placed symbol metadata."""
        symbols: list[dict[str, object]] = []
        for item in self.root.items:
            if not (isinstance(item, ListNode) and item.key == "symbol"):
                continue
            symbols.append(_symbol_metadata(item))

        return sorted(symbols, key=lambda entry: str(entry.get("ref", "")))

    def count_nodes(self, key: str) -> int:
        """Count direct children of root with the given S-expression key.

        Useful for post-mutation invariant checks querying the live AST
        (e.g. ``count_nodes("symbol")`` or ``count_nodes("label")``).
        """
        return sum(1 for item in self.root.items if isinstance(item, ListNode) and item.key == key)

    def extract_pin_label_bindings(self) -> list[dict[str, str]]:
        """Return pin→net bindings for generated schematics.

        The writer records deterministic hidden marker text nodes with the
        prefix ``OpenClaw:bind=`` and a JSON payload containing
        ``{"ref": ..., "pin": ..., "net_name": ...}``.
        """
        bindings: list[dict[str, str]] = []
        for item in self.root.items:
            if not (
                isinstance(item, ListNode)
                and item.key == "text"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
            ):
                continue

            marker = item.items[1].value
            if not marker.startswith("OpenClaw:bind="):
                continue

            parsed = _parse_binding_marker(marker)
            if parsed is not None:
                bindings.append(parsed)

        return sorted(bindings, key=lambda entry: (entry["ref"], entry["pin"], entry["net_name"]))

    def has_managed_sheet(self, *, sheet_name: str = "OpenClaw_Managed") -> bool:
        """Return ``True`` if a sheet with ``Sheetname=<sheet_name>`` exists."""
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "sheet"
                and _sheet_property_value(item, "Sheetname") == sheet_name
            ):
                return True
        return False

    def ensure_managed_sheet(
        self,
        *,
        sheet_name: str = "OpenClaw_Managed",
        sheet_file: str = "OpenClaw_Managed.kicad_sch",
        sheet_uuid: str,
    ) -> str:
        """Ensure a top-level managed sheet exists; return its UUID.

        When the sheet already exists, returns its actual UUID from the AST.
        When a new sheet is inserted, returns *sheet_uuid*.
        """
        for item in self.root.items:
            if (
                isinstance(item, ListNode)
                and item.key == "sheet"
                and _sheet_property_value(item, "Sheetname") == sheet_name
            ):
                existing = _get_sheet_uuid(item)
                return existing if existing is not None else sheet_uuid
        self._insert_before_sheet_instances(
            make_managed_sheet_node(
                ManagedSheetSpec(
                    sheet_name=sheet_name,
                    sheet_file=sheet_file,
                    sheet_uuid=sheet_uuid,
                )
            )
        )
        return sheet_uuid

    def update_managed_path(self, sheet_uuid: str) -> None:
        """Qualify all bare ``(path "/" …)`` entries to ``(path "/{uuid}/" …)``.

        KiCad requires sub-schematic ``(sheet_instances ...)`` and symbol
        ``(instances ...)`` paths to reference the parent sheet's UUID so that
        KiCad can resolve the hierarchy and assign correct reference
        annotations.  This method walks the entire AST and replaces every
        remaining unqualified root path ``"/"`` with ``"/{sheet_uuid}/"``.

        Call this on the managed ``SchematicDoc`` after writing all symbols and
        nets, before saving.
        """

        def _fix(node: Node) -> Node:  # noqa: PLR0911 — recursive walk helper
            if not isinstance(node, ListNode):
                return node
            if (
                node.key == "path"
                and len(node.items) >= 2
                and isinstance(node.items[1], StringNode)
                and node.items[1].value == "/"
            ):
                new_items = list(node.items)
                new_items[1] = string(f"/{sheet_uuid}/")
                return ListNode(tuple(new_items), node.pos)
            new_children = tuple(_fix(child) for child in node.items)
            if new_children == node.items:
                return node
            return ListNode(new_children, node.pos)

        result = _fix(self.root)
        assert isinstance(result, ListNode)
        self.root = result

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


def _get_sheet_uuid(sheet_node: ListNode) -> str | None:
    """Extract ``(uuid "value")`` from a ``(sheet ...)`` node."""
    for item in sheet_node.items:
        if (
            isinstance(item, ListNode)
            and item.key == "uuid"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            return item.items[1].value
    return None


def _sheet_property_value(sheet_node: ListNode, prop_name: str) -> str | None:
    """Return property value for a schematic ``(sheet ...)`` property name."""
    for child in sheet_node.items:
        if not isinstance(child, ListNode) or child.key != "property":
            continue
        if len(child.items) < 3:
            continue
        name_node = child.items[1]
        value_node = child.items[2]
        if (
            isinstance(name_node, StringNode)
            and isinstance(value_node, StringNode)
            and name_node.value == prop_name
        ):
            return value_node.value
    return None


def _symbol_metadata(symbol_node: ListNode) -> dict[str, object]:
    symbol_id = ""
    ref = ""
    value = ""
    sym_uuid = ""
    unit = ""
    x = 0.0
    y = 0.0

    for child in symbol_node.items:
        if not isinstance(child, ListNode):
            continue
        if (
            child.key == "lib_id"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            symbol_id = child.items[1].value
        elif (
            child.key == "uuid" and len(child.items) >= 2 and isinstance(child.items[1], StringNode)
        ):
            sym_uuid = child.items[1].value
        elif child.key == "unit" and len(child.items) >= 2 and isinstance(child.items[1], AtomNode):
            unit = child.items[1].value
        elif child.key == "at" and len(child.items) >= 3:
            x = _parse_float_atom(child.items[1], default=x)
            y = _parse_float_atom(child.items[2], default=y)
        elif child.key == "property" and len(child.items) >= 3:
            name_node = child.items[1]
            value_node = child.items[2]
            if isinstance(name_node, StringNode) and isinstance(value_node, StringNode):
                if name_node.value == "Reference":
                    ref = value_node.value
                elif name_node.value == "Value":
                    value = value_node.value

    return {
        "ref": ref,
        "symbol_id": symbol_id,
        "value": value,
        "uuid": sym_uuid,
        "x": x,
        "y": y,
        "unit": unit,
    }


def _parse_float_atom(node: Node, *, default: float) -> float:
    if not isinstance(node, AtomNode):
        return default
    with contextlib.suppress(ValueError):
        return float(node.value)
    return default


def _parse_binding_marker(marker: str) -> dict[str, str] | None:
    payload = marker.removeprefix("OpenClaw:bind=")
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    if not isinstance(data, dict):
        return None

    ref = data.get("ref")
    pin = data.get("pin")
    net_name = data.get("net_name")
    # Check each field individually so mypy can narrow the types to `str`.
    if not isinstance(ref, str) or not ref:
        return None
    if not isinstance(pin, str) or not pin:
        return None
    if not isinstance(net_name, str) or not net_name:
        return None

    return {
        "ref": ref,
        "pin": pin,
        "net_name": net_name,
    }
