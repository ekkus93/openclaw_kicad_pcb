"""Low-level KiCad symbol library I/O and AST helpers."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from .config import SYMBOLS_CANDIDATES
from .errors import ErrorCode, ParseError, UserError
from .sexpr.builder import string
from .sexpr.nodes import AtomNode, ListNode, Node, StringNode
from .sexpr.parser import parse_file
from .sexpr.utils import find_first, walk

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
