"""S-expression serializer — converts an AST back to a string.

Design goals
------------
* **Round-trip stability**: ``parse(serialize(node))`` produces a structurally
  equivalent AST (same values/nesting; positions will differ from the original
  because formatting may change indentation).
* **Deterministic output**: same AST always serializes to the same string.
* **Readable formatting**: short / leaf-only lists are kept inline; lists that
  are too long or contain nested lists are block-indented with 2-space steps.

Public API
----------
:func:`serialize`       — serialize a node to a string (pretty-printed)
:func:`serialize_file`  — serialize and write to a file (UTF-8)
"""

from __future__ import annotations

from pathlib import Path

from .nodes import AtomNode, ListNode, Node, StringNode

# ---------------------------------------------------------------------------
# String escaping
# ---------------------------------------------------------------------------


def _escape_string(s: str) -> str:
    """Return the escaped form of *s* suitable for embedding inside ``"…"``."""
    return (
        s.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )


# ---------------------------------------------------------------------------
# Inline (compact) serializer — used internally for length probing
# ---------------------------------------------------------------------------


def _inline(node: Node) -> str:
    """Serialize *node* to a fully-inline (single-line) string.

    This is used to probe whether a list node fits within the line limit before
    deciding to fall back to block-indented formatting.
    """
    if isinstance(node, AtomNode):
        return node.lexeme if node.lexeme is not None else node.value
    if isinstance(node, StringNode):
        return '"' + _escape_string(node.value) + '"'
    # ListNode
    return "(" + " ".join(_inline(item) for item in node.items) + ")"


# ---------------------------------------------------------------------------
# Pretty-printer
# ---------------------------------------------------------------------------

#: Column budget used to decide inline vs. block formatting.
_MAX_INLINE = 80


def serialize(node: Node, indent: int = 0) -> str:
    """Serialize *node* to a pretty-printed string.

    Formatting rules:

    * ``AtomNode`` → its bare value (unquoted).
    * ``StringNode`` → ``"…"`` with escape sequences applied.
    * ``ListNode`` — inline if ``len(inline_form) + indent <= 80`` **and**
      the inline form contains no embedded newlines; otherwise block-indented
      with the closing ``)`` on its own line at the original indentation.

    *indent* is the current column for the opening ``(``, used purely for
    deciding the line-length budget.  Normally you call ``serialize(root)``
    and the indentation is handled recursively.
    """
    if isinstance(node, AtomNode):
        return node.lexeme if node.lexeme is not None else node.value
    if isinstance(node, StringNode):
        return '"' + _escape_string(node.value) + '"'

    # --- ListNode ---
    assert isinstance(node, ListNode)

    # Try the fully-inline form first.
    candidate = _inline(node)
    if len(candidate) + indent <= _MAX_INLINE and "\n" not in candidate:
        return candidate

    # Fall back to block-indented form.
    if not node.items:
        return "()"

    child_indent = indent + 2
    prefix = " " * child_indent

    # Serialize each child at the child indentation level.
    child_strs = [serialize(item, child_indent) for item in node.items]

    head = child_strs[0]
    if len(child_strs) == 1:
        return "(" + head + ")"

    rest = ("\n" + prefix).join(child_strs[1:])
    return "(" + head + "\n" + prefix + rest + "\n" + " " * indent + ")"


def serialize_file(path: Path, node: Node) -> None:
    """Serialize *node* and write the result to *path* as UTF-8 text.

    The parent directory of *path* must already exist.  Raises ``OSError`` on
    write failure (not wrapped — let it propagate so callers can decide).
    """
    path.write_text(serialize(node), encoding="utf-8")
