"""AST node types for KiCad S-expressions.

All nodes are immutable frozen dataclasses.  Position information uses 1-based
line and column numbers; programmatically-constructed nodes use the sentinel
``NO_POS = Position(0, 0)``.

The ``Node`` union type covers every node kind:

* ``AtomNode``   — unquoted token  (keyword, number, identifier, boolean)
* ``StringNode`` — double-quoted string  (value is already unescaped)
* ``ListNode``   — parenthesised list  ``(item …)``
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Position:
    """Source location using 1-based line and column numbers.

    ``Position(0, 0)`` is the sentinel for synthesised / position-less nodes.
    """

    line: int
    col: int

    def __str__(self) -> str:
        return f"{self.line}:{self.col}"


#: Sentinel position for nodes that were built programmatically.
NO_POS: Position = Position(0, 0)


@dataclass(frozen=True)
class AtomNode:
    """Unquoted atom — a keyword, number, boolean, or bare identifier."""

    value: str
    pos: Position = field(default=NO_POS)

    def __repr__(self) -> str:
        return f"AtomNode({self.value!r})"


@dataclass(frozen=True)
class StringNode:
    """Quoted string; ``value`` holds the *unescaped* content."""

    value: str
    pos: Position = field(default=NO_POS)

    def __repr__(self) -> str:
        return f"StringNode({self.value!r})"


@dataclass(frozen=True)
class ListNode:
    """Parenthesised list: ``(item item …)``."""

    items: tuple[Node, ...]
    pos: Position = field(default=NO_POS)

    @property
    def head(self) -> AtomNode | None:
        """First item if it is an ``AtomNode`` (the section keyword), else ``None``."""
        if self.items and isinstance(self.items[0], AtomNode):
            return self.items[0]
        return None

    @property
    def key(self) -> str | None:
        """Value of the head atom, or ``None`` if the list has no atom head."""
        h = self.head
        return h.value if h is not None else None

    def __repr__(self) -> str:
        return f"ListNode({self.key!r}, {len(self.items)} items)"


#: Union of all concrete node types.
Node = AtomNode | StringNode | ListNode
