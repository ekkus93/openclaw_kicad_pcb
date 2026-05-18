"""Convenience constructors for building S-expression AST nodes programmatically.

These thin wrappers allow command/document code to be written as structured data
rather than string templates::

    node = L(atom("at"), fnum(50.8, 2), fnum(76.2, 2), atom("0"))
    # → ListNode((AtomNode("at"), AtomNode("50.80"), AtomNode("76.20"), AtomNode("0")))

All nodes are created with :data:`~.nodes.NO_POS` (sentinel for synthesised nodes).

Public API
----------
:func:`atom`         — bare keyword / identifier / number atom
:func:`string`       — quoted string node
:func:`L`            — list node from positional children
:func:`fnum`         — decimal-number atom with fixed precision
:func:`fnum_or_keep` — preserve original atom text when the float value is unchanged
"""

from __future__ import annotations

from .nodes import NO_POS, AtomNode, ListNode, Node, StringNode

__all__ = ["L", "atom", "fnum", "fnum_or_keep", "string"]


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

    The resulting atom has no ``lexeme`` (it is a synthesised node), so the
    serializer will always emit the formatted ``value``.

    Examples::

        fnum(50.8, 2)  # → AtomNode("50.80")
        fnum(0.05, 3)  # → AtomNode("0.050")
    """
    return AtomNode(f"{f:.{decimals}f}", NO_POS)


def fnum_or_keep(f: float, original: AtomNode, decimals: int = 3) -> AtomNode:
    """Return *original* when it already represents *f*; otherwise :func:`fnum`.

    This preserves the source lexeme (e.g. ``"10.0000"``) when a mutation
    operation is passed a coordinate equal to the one already stored in the
    AST, avoiding spurious diff noise from precision normalisation.

    Parameters
    ----------
    f:
        The new float value to store.
    original:
        The existing :class:`~.nodes.AtomNode` from the parsed AST.  Carries a
        ``lexeme`` when it came from a real file.
    decimals:
        Precision used when creating a *new* atom (i.e. when ``f`` differs from
        the original value).  Defaults to 3.

    Examples::

        at_x = parse("(at 10.0000 0)").items[1]   # AtomNode, lexeme="10.0000"
        fnum_or_keep(10.0, at_x)                  # → same AtomNode (no diff)
        fnum_or_keep(20.0, at_x)                  # → AtomNode("20.000")
    """
    if original.lexeme is not None:
        try:
            if float(original.lexeme) == f:
                return original
        except (ValueError, OverflowError):
            pass
    return fnum(f, decimals)
