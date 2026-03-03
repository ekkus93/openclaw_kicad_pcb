"""Shared private AST helpers used by both ``lint_sch`` and ``lint_pcb``.

All functions in this module are private (``_``-prefixed) and are re-imported
into ``lint.py`` only for backwards-compat access.  New code should import
from the sub-modules directly.

Functions
---------
_is_numeric_atom        Test whether an AST node is a numeric atom.
_float_from_atom        Extract a float from an atom node safely.
_collect_uuids          Walk a tree and collect all UUID strings.
_get_property_value     Read a ``(property "name" "value")`` child.
_symbol_lib_id          Read the ``(lib_id "…")`` child of a symbol node.
_check_duplicate_uuids  Return ERROR issues for every repeated UUID.
_collect_wire_segments  Extract ``(x1, y1, x2, y2)`` tuples from wire nodes.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..sexpr.nodes import AtomNode, ListNode, StringNode
from ..sexpr.utils import find_first, walk
from .defs import _ERR, LintIssue


def _is_numeric_atom(node: object) -> bool:
    """Return ``True`` when *node* is an :class:`AtomNode` holding a valid float."""
    if not isinstance(node, AtomNode):
        return False
    try:
        float(node.value)
        return True
    except ValueError:
        return False


def _float_from_atom(node: object) -> float | None:
    """Return the float value of *node* if it is a numeric :class:`AtomNode`."""
    if not isinstance(node, AtomNode):
        return None
    try:
        return float(node.value)
    except ValueError:
        return None


def _collect_uuids(root: ListNode) -> list[str]:
    """Walk *root* and return every ``(uuid "value")`` string found."""
    uuids: list[str] = []
    for node in walk(root):
        if (
            isinstance(node, ListNode)
            and node.key == "uuid"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            uuids.append(node.items[1].value)
    return uuids


def _get_property_value(sym: ListNode, prop_name: str) -> str | None:
    """Return the value of a ``(property "prop_name" "value" …)`` direct child."""
    for child in sym.items:
        if (
            isinstance(child, ListNode)
            and child.key == "property"
            and len(child.items) >= 3
            and isinstance(child.items[1], StringNode)
            and child.items[1].value == prop_name
            and isinstance(child.items[2], StringNode)
        ):
            return child.items[2].value
    return None


def _symbol_lib_id(sym: ListNode) -> str | None:
    """Return the ``(lib_id "…")`` value from a placed symbol node."""
    lib_id_node = find_first(sym, "lib_id")
    if lib_id_node is not None and len(lib_id_node.items) >= 2:
        item = lib_id_node.items[1]
        if isinstance(item, StringNode):
            return item.value
    return None


def _check_duplicate_uuids(uuids: list[str], code: str) -> list[LintIssue]:
    """Return ERROR issues for every UUID that appears more than once.

    Used by both the SCH002 and PCB002 rules to avoid duplicating the
    detection loop.
    """
    issues: list[LintIssue] = []
    seen: set[str] = set()
    for u in uuids:
        if u in seen:
            issues.append(LintIssue(_ERR, code, f"Duplicate UUID '{u}'"))
        seen.add(u)
    return issues


def _collect_wire_segments(
    items: Sequence[object],
) -> list[tuple[float, float, float, float]]:
    """Return ``(x1, y1, x2, y2)`` for every well-formed wire node in *items*.

    Items that are malformed (missing ``pts``, non-numeric coordinates, etc.)
    are silently skipped — the structural rules (SCH007) handle those cases.
    Used by both LAY002 and LAY005 to avoid duplicating the extraction loop.
    """
    segs: list[tuple[float, float, float, float]] = []
    for node in items:
        if not isinstance(node, ListNode) or node.key != "wire":
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        xy_nodes = [n for n in pts.items[1:] if isinstance(n, ListNode) and n.key == "xy"]
        if len(xy_nodes) < 2:
            continue
        try:
            x1 = float(xy_nodes[0].items[1].value)  # type: ignore[union-attr]
            y1 = float(xy_nodes[0].items[2].value)  # type: ignore[union-attr]
            x2 = float(xy_nodes[1].items[1].value)  # type: ignore[union-attr]
            y2 = float(xy_nodes[1].items[2].value)  # type: ignore[union-attr]
            segs.append((x1, y1, x2, y2))
        except (AttributeError, ValueError, IndexError):
            pass
    return segs
