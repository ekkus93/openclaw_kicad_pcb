"""AST utility helpers for navigating and transforming KiCad S-expression trees.

All helpers treat the AST as **immutable** — mutation operations return new
tree instances rather than modifying in-place.

Public API
----------
:func:`walk`             — depth-first iterator over every node in a subtree
:func:`find_first`       — first direct child :class:`~.nodes.ListNode` by keyword
:func:`find_all`         — all direct child :class:`~.nodes.ListNode` by keyword
:func:`replace_section`  — replace (or append) a direct child section by keyword
:func:`append_to_section`— append a node into a direct child section by keyword
:func:`node_path`        — dot-notation path string for error/diagnostic messages

The terms *direct child* and *section* refer to a ``ListNode`` that is a
direct element of another ``ListNode``'s ``items`` tuple and whose first item
is an ``AtomNode`` with a specific ``value`` (the *keyword*).
"""

from __future__ import annotations

from collections.abc import Iterator

from .nodes import ListNode, Node

# ---------------------------------------------------------------------------
# Traversal
# ---------------------------------------------------------------------------


def walk(node: Node) -> Iterator[Node]:
    """Yield *node* and every descendant in depth-first pre-order.

    Example::

        for n in walk(root):
            if isinstance(n, AtomNode) and n.value == "uuid":
                print("found uuid atom")
    """
    yield node
    if isinstance(node, ListNode):
        for child in node.items:
            yield from walk(child)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def find_first(root: ListNode, key: str) -> ListNode | None:
    """Return the first *direct child* ``ListNode`` whose head atom equals *key*.

    Only the immediate children of *root* are searched (non-recursive).
    Returns ``None`` if no matching child is found.
    """
    for item in root.items:
        if isinstance(item, ListNode) and item.key == key:
            return item
    return None


def find_all(root: ListNode, key: str) -> list[ListNode]:
    """Return all *direct child* ``ListNode``\\s whose head atom equals *key*.

    Only the immediate children of *root* are searched (non-recursive).
    Returns an empty list if none match.
    """
    return [item for item in root.items if isinstance(item, ListNode) and item.key == key]


# ---------------------------------------------------------------------------
# Structural modification (returns new trees)
# ---------------------------------------------------------------------------


def replace_section(root: ListNode, key: str, new_section: ListNode) -> ListNode:
    """Return a new ``ListNode`` with the first matching direct child replaced.

    If no direct child with *key* exists, *new_section* is **appended** to the
    end of *root*'s items.  The original *root* is never mutated.

    Parameters
    ----------
    root:
        The parent ``ListNode`` to search.
    key:
        Head-atom keyword that identifies the target section.
    new_section:
        Replacement ``ListNode`` (must already have *key* as its head if you
        want the result to be consistent, though this is not enforced).
    """
    new_items: list[Node] = []
    replaced = False
    for item in root.items:
        if not replaced and isinstance(item, ListNode) and item.key == key:
            new_items.append(new_section)
            replaced = True
        else:
            new_items.append(item)
    if not replaced:
        new_items.append(new_section)
    return ListNode(items=tuple(new_items), pos=root.pos)


def append_to_section(root: ListNode, key: str, item: Node) -> ListNode:
    """Return a new ``ListNode`` with *item* appended inside the matching section.

    The *first* direct child of *root* whose head atom equals *key* gets
    *item* appended to its ``items`` tuple.

    Raises :exc:`KeyError` if no matching direct child section is found.
    """
    new_root_items: list[Node] = []
    appended = False
    for child in root.items:
        if not appended and isinstance(child, ListNode) and child.key == key:
            new_root_items.append(ListNode(items=(*child.items, item), pos=child.pos))
            appended = True
        else:
            new_root_items.append(child)
    if not appended:
        raise KeyError(f"section {key!r} not found in list")
    return ListNode(items=tuple(new_root_items), pos=root.pos)


# ---------------------------------------------------------------------------
# Diagnostic helpers
# ---------------------------------------------------------------------------


def node_path(root: ListNode, *keys: str) -> str:
    """Build a dot-notation path string for use in error/lint messages.

    Constructs a string like ``"kicad_sch.lib_symbols.symbol"`` from the root
    node's key plus any additional *keys* supplied.  Useful for pinpointing
    the location of a linting error inside a deeply-nested structure.

    Example::

        path = node_path(root, "lib_symbols", "symbol")
        # → "kicad_sch.lib_symbols.symbol"
    """
    parts: list[str] = [root.key or "?"]
    parts.extend(keys)
    return ".".join(parts)
