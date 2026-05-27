"""KiCad symbol library reader helpers.

This module handles all I/O against ``.kicad_sym`` library files: parsing,
caching, extends-chain resolution, pin enumeration, and symbol-definition
extraction.  It has no dependency on :class:`~kicad_pcb.sch_doc.SchematicDoc`
or the AST emitters in :mod:`kicad_pcb.sch_nodes`.

Public API (also re-exported from :mod:`kicad_pcb.sch_doc` for backward
compatibility):

:func:`read_lib_symbol_def`       — extract a single symbol (no extends chain).
:func:`read_lib_symbol_def_chain` — load a symbol plus its full extends chain.
:func:`read_lib_symbol_def_flat`  — load a fully-merged self-contained symbol.
:func:`read_lib_symbol_pins`      — extract pin numbers from a library symbol.
:func:`read_lib_symbol_pin_at`    — extract pin connection-point coordinates.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .config import SYMBOLS_CANDIDATES
from .errors import ErrorCode, ParseError, UserError
from .sexpr.builder import string
from .sexpr.nodes import NO_POS, AtomNode, ListNode, Node, StringNode
from .sexpr.parser import parse_file
from .sexpr.utils import find_first, walk

__all__ = [
    "read_lib_symbol_def",
    "read_lib_symbol_def_chain",
    "read_lib_symbol_def_flat",
    "read_lib_symbol_pin_at",
    "read_lib_symbol_pin_electrical_types",
    "read_lib_symbol_pins",
    "read_lib_symbol_power_unit",
    "read_lib_symbol_unit_pins",
    "read_lib_symbol_unit_pin_at",
]

# Default system KiCad symbols directory.  Callers can override via the
# ``symbols_dir`` parameter on the public functions to point at a custom
# location.
_DEFAULT_SYMBOLS_DIR: Path = Path("/usr/share/kicad/symbols")
_REPO_LOCAL_SYMBOLS_DIR: Path = Path(__file__).resolve().parent / "resources" / "symbols"


def _iter_symbol_dirs(symbols_dir: Path | None) -> tuple[Path, ...]:
    """Return symbol-library search directories in precedence order."""
    if symbols_dir is not None:
        return (symbols_dir,)

    ordered: list[Path] = []
    if _REPO_LOCAL_SYMBOLS_DIR.is_dir():
        ordered.append(_REPO_LOCAL_SYMBOLS_DIR)

    for candidate in (_DEFAULT_SYMBOLS_DIR, *SYMBOLS_CANDIDATES):
        if candidate.is_dir():
            ordered.append(candidate)

    deduped: list[Path] = []
    seen: set[Path] = set()
    for path in ordered:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            deduped.append(resolved)
    return tuple(deduped)


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


# ---------------------------------------------------------------------------
# Symbol lookup primitives
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


def _collect_pin_electrical_types(sym_node: ListNode) -> list[str]:
    """Walk *sym_node* and return pin electrical types in encounter order."""
    result: list[str] = []
    for node in walk(sym_node):
        if not (isinstance(node, ListNode) and node.key == "pin"):
            continue
        if len(node.items) >= 2 and isinstance(node.items[1], AtomNode):
            result.append(node.items[1].value)
    return result


def _collect_pin_electrical_types_by_number(sym_node: ListNode) -> dict[str, str]:
    """Return ``{pin_number: electrical_type}`` for all pins in *sym_node*."""
    result: dict[str, str] = {}
    for node in walk(sym_node):
        if not (isinstance(node, ListNode) and node.key == "pin"):
            continue
        electrical_type: str | None = None
        if len(node.items) >= 2 and isinstance(node.items[1], AtomNode):
            electrical_type = node.items[1].value
        if electrical_type is None:
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
        result[pin_num] = electrical_type
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


# ---------------------------------------------------------------------------
# Extends-chain resolution
# ---------------------------------------------------------------------------


def _resolve_sym_chain(
    lib_name: str,
    sym_name: str,
    symbols_dir: Path | None,
) -> tuple[ListNode, list[str], bool] | None:
    """Load a ``.kicad_sym`` file and walk the ``extends`` chain for *sym_name*.

    Returns ``(lib_root, chain, complete)`` or ``None`` if the library file is
    not found.

    * ``lib_root``  — parsed root of the ``.kicad_sym`` file.
    * ``chain``     — symbol names in derived-first order (``sym_name`` first).
      Empty when *sym_name* itself is absent from the library.
    * ``complete``  — ``True`` when the walk ended at a root base symbol with
      no further ``extends``; ``False`` when a missing node caused early
      termination.  Callers that require the full chain should reject
      ``complete=False`` results.
    """
    fallback_result: tuple[ListNode, list[str], bool] | None = None
    for candidate_dir in _iter_symbol_dirs(symbols_dir):
        candidate_file = candidate_dir / f"{lib_name}.kicad_sym"
        if not candidate_file.exists():
            continue
        try:
            lib_root = _parse_lib_file(candidate_file)
        except ParseError as exc:
            raise ParseError(f"Failed to parse symbol library '{candidate_file}': {exc}") from exc
        except OSError as exc:
            raise UserError(
                f"Failed to read symbol library '{candidate_file}': {exc}",
                code=ErrorCode.IO_ERROR,
                details={"path": str(candidate_file)},
            ) from exc
        chain: list[str] = []
        visited: set[str] = set()
        current: str | None = sym_name
        complete = True
        while current is not None and current not in visited:
            node = _find_lib_symbol(lib_root, current)
            if node is None:
                complete = False
                break
            visited.add(current)
            chain.append(current)
            current = _get_extends_name(node)
        if chain and complete:
            return lib_root, chain, complete
        if fallback_result is None:
            fallback_result = (lib_root, chain, complete)
    return fallback_result


# ---------------------------------------------------------------------------
# Public library-reader functions
# ---------------------------------------------------------------------------


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

    .. note::
        This function does **not** follow ``(extends …)`` chains.  For symbols
        that inherit from a base, use :func:`read_lib_symbol_def_chain` or
        :func:`read_lib_symbol_def_flat` so the full inheritance tree is
        present in the schematic's ``lib_symbols`` section.

    Returns ``None`` when the library file or symbol is not found.
    Raises on library parse/read failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.  Defaults to the
                 system KiCad symbols directory if ``None``.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return None
    lib_root, _, _complete = resolved
    sym_node = _find_lib_symbol(lib_root, sym_name)
    if sym_node is None:
        return None
    # Rename root symbol: "R" → "Device:R".  Sub-symbol children keep their
    # short names ("R_0_1", "R_1_1") which KiCad requires.
    new_items: list[Node] = list(sym_node.items)
    new_items[1] = string(f"{lib_name}:{sym_name}")
    # Strip (id N) items recursively — not used in KiCad 9 schematics.
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
    callers can embed them with :meth:`~kicad_pcb.sch_doc.SchematicDoc.embed_lib_symbol`
    in order.  Each node has its id qualified to ``lib_name:name`` and
    ``(id N)`` children stripped.  ``(extends "BaseName")`` attributes in
    derived nodes are updated to the fully-qualified
    ``"lib_name:BaseName"`` form required by KiCad schematics.

    Returns an empty list when the library file or root symbol cannot be
    found or the extends chain is broken. Raises on library parse/read
    failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Amplifier_Operational"``).
    sym_name:    Symbol name within the library (e.g. ``"NE5532"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return []
    lib_root, chain, complete = resolved
    if not chain or not complete:
        return []  # symbol not found or extends chain is broken

    chain.reverse()  # base-first so KiCad can resolve references in order

    result: list[ListNode] = []
    for name in chain:
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            return []  # shouldn't happen — chain was verified complete

        # Qualify root-level id: "NE5532" → "Amplifier_Operational:NE5532".
        new_items_chain: list[Node] = list(node.items)
        new_items_chain[1] = string(f"{lib_name}:{name}")
        renamed = ListNode(tuple(new_items_chain), NO_POS)

        # Qualify extends reference if present: "LM2904" → "lib:LM2904".
        renamed = _qualify_extends(renamed, lib_name)

        result.append(_strip_id_nodes(renamed))

    return result


def _collect_subsymbols(sym_node: ListNode) -> list[ListNode]:
    """Return all direct ``(symbol …)`` children of *sym_node*."""
    return [item for item in sym_node.items if isinstance(item, ListNode) and item.key == "symbol"]


_UNIT_SUBSYMBOL_RE = re.compile(r"^.+_(\d+)_(\d+)$")


def _rename_subsymbol(sub: ListNode, old_base: str, new_base: str) -> ListNode:
    """Return *sub* with its name changed from ``old_base_N_M`` → ``new_base_N_M``."""
    if len(sub.items) < 2 or not isinstance(sub.items[1], StringNode):
        return sub
    old_name: str = sub.items[1].value
    if old_name.startswith(old_base + "_"):
        new_name = new_base + old_name[len(old_base) :]
        new_items = list(sub.items)
        new_items[1] = string(new_name)
        return ListNode(tuple(new_items), sub.pos)
    return sub


def read_lib_symbol_def_flat(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> ListNode | None:
    """Load and fully flatten a symbol definition for schematic embedding.

    Unlike :func:`read_lib_symbol_def_chain`, this returns a **single**
    ``ListNode`` that is completely self-contained — if the symbol has an
    ``(extends …)`` ancestor chain, the ancestor's geometry (sub-symbol
    drawing units + pins) is merged into the returned node and the
    ``(extends …)`` attribute is removed.  This avoids ``(extends …)``
    references in ``lib_symbols``, which some KiCad versions fail to
    resolve when opening a schematic file.

    Returns ``None`` when the library or symbol cannot be found, or when
    the extends chain is broken. Raises on library parse/read failures.
    """
    chain = read_lib_symbol_def_chain(lib_name, sym_name, symbols_dir=symbols_dir)
    if not chain:
        return None
    if len(chain) == 1:
        return chain[0]  # no extends — already self-contained

    # chain is [root_base, ..., direct_parent, derived_leaf] (base-first).
    root = chain[0]  # has all geometry sub-symbols
    leaf = chain[-1]  # has property overrides + (extends …)

    # Root base name (unqualified, e.g., "LM2904").
    root_full_id = _symbol_id(root) or ""
    root_base = root_full_id.split(":")[-1]

    # Leaf symbol name (unqualified, e.g., "NE5532").
    leaf_full_id = _symbol_id(leaf) or ""
    leaf_base = leaf_full_id.split(":")[-1]

    # Collect sub-symbols from the root (these carry all geometry).
    root_subsymbols = _collect_subsymbols(root)
    renamed_subsymbols: list[Node] = [
        _rename_subsymbol(sub, root_base, leaf_base) for sub in root_subsymbols
    ]

    # Build new leaf items: keep everything except (extends …), then append geometry.
    leaf_items_no_extends: list[Node] = [
        item for item in leaf.items if not (isinstance(item, ListNode) and item.key == "extends")
    ]
    merged_items = leaf_items_no_extends + renamed_subsymbols
    return ListNode(tuple(merged_items), leaf.pos)


def read_lib_symbol_pins(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> list[str]:
    """Return deduplicated pin number strings for a library symbol.

    Returns an empty list when the library file, symbol, or pin data is not
    available. Raises on library parse/read failures. The search is performed
    on the full AST subtree of the symbol, so sub-unit pins are included
    without any character-window heuristics.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return []
    lib_root, chain, _complete = resolved

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
    Raises on library parse/read failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return {}
    lib_root, chain, _complete = resolved

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


def read_lib_symbol_pin_electrical_types(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, str]:
    """Return ``{pin_number: electrical_type}`` for *lib_name:sym_name*."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}
    return _collect_pin_electrical_types_by_number(sym_def)


def read_lib_symbol_unit_pins(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, list[str]]:
    """Return ``{unit_number: [pin_numbers...]}`` for a library symbol."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}

    unit_pins: dict[str, list[str]] = {}
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        subsymbol_pins = _collect_pin_numbers(subsymbol)
        if not subsymbol_pins:
            continue
        pins = unit_pins.setdefault(unit, [])
        for pin_num in subsymbol_pins:
            if pin_num not in pins:
                pins.append(pin_num)
    return unit_pins


def read_lib_symbol_unit_pin_at(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> dict[str, dict[str, tuple[float, float, float]]]:
    """Return ``{unit_number: {pin_number: (x, y, angle)}}`` for a library symbol."""
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return {}

    unit_pin_at: dict[str, dict[str, tuple[float, float, float]]] = {}
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        subsymbol_pin_at = _collect_pin_at(subsymbol)
        if not subsymbol_pin_at:
            continue
        pins = unit_pin_at.setdefault(unit, {})
        for pin_num, coords in subsymbol_pin_at.items():
            pins.setdefault(pin_num, coords)
    return unit_pin_at


def read_lib_symbol_power_unit(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> str | None:
    """Return the unit number for a dedicated power-only sub-unit, if present.

    A symbol is considered to have a separate power unit when exactly one
    numbered sub-symbol contains pins and every pin in that unit uses a
    ``power_*`` electrical type, while at least one other numbered sub-symbol
    contains a non-power pin type.
    """
    sym_def = read_lib_symbol_def_flat(lib_name, sym_name, symbols_dir=symbols_dir)
    if sym_def is None:
        return None

    power_only_units: list[str] = []
    non_power_units = 0
    for subsymbol in _collect_subsymbols(sym_def):
        subsymbol_id = _symbol_id(subsymbol)
        if subsymbol_id is None:
            continue
        match = _UNIT_SUBSYMBOL_RE.match(subsymbol_id)
        if match is None:
            continue
        unit = match.group(1)
        pin_types = _collect_pin_electrical_types(subsymbol)
        if not pin_types:
            continue
        if all(pin_type.startswith("power_") for pin_type in pin_types):
            power_only_units.append(unit)
        else:
            non_power_units += 1

    if len(power_only_units) == 1 and non_power_units >= 1:
        return power_only_units[0]
    return None
