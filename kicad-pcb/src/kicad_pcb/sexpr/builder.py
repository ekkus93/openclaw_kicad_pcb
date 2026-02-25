"""Convenience constructors for building S-expression AST nodes programmatically.

These thin wrappers allow command/document code to be written as structured data
rather than string templates::

    node = L(atom("at"), fnum(50.8, 2), fnum(76.2, 2), atom("0"))
    # → ListNode((AtomNode("at"), AtomNode("50.80"), AtomNode("76.20"), AtomNode("0")))

All nodes are created with :data:`~.nodes.NO_POS` (sentinel for synthesised nodes).

Public API
----------
:func:`atom`   — bare keyword / identifier / number atom
:func:`string` — quoted string node
:func:`L`      — list node from positional children
:func:`fnum`   — decimal-number atom with fixed precision
"""
from __future__ import annotations

from .nodes import NO_POS, AtomNode, ListNode, Node, StringNode

__all__ = ["L", "atom", "fnum", "string"]


def atom(value: str) -> AtomNode:
    """Return an :class:`~.nodes.AtomNode` with *value* and ``NO_POS``."""
    return AtomNode(value, NO_POS)


def string(value: str) -> StringNode:
    """Return a :class:`~.nodes.StringNode` with the given *unescaped* value."""
    return StringNode(value, NO_POS)


def L(*items: Node) -> ListNode:  # noqa: N802
    """Return a :class:`~.nodes.ListNode` built from positional *items*."""
    return ListNode(tuple(items), NO_POS)


def fnum(f: float, decimals: int = 3) -> AtomNode:
    """Return an :class:`~.nodes.AtomNode` formatted as a fixed-precision decimal.

    Examples::

        fnum(50.8, 2)  # → AtomNode("50.80")
        fnum(0.05, 3)  # → AtomNode("0.050")
    """
    return AtomNode(f"{f:.{decimals}f}", NO_POS)
