"""S-expression parser — converts a token stream into an AST.

Public API
----------
:func:`parse`      — parse a source string into a :class:`~.nodes.ListNode`
:func:`parse_file` — read a file and parse it

Both raise :class:`~kicad_pcb.errors.ParseError` for malformed input,
including unmatched parentheses, unexpected tokens, or extra content after
the top-level expression.

Comment tokens (``kind == "comment"``) are silently discarded; they carry
no semantic weight in KiCad files.
"""

from __future__ import annotations

from pathlib import Path

from ..errors import ParseError
from .nodes import AtomNode, ListNode, Node, Position, StringNode
from .tokenizer import Token, tokenize

# ---------------------------------------------------------------------------
# Internal recursive parser
# ---------------------------------------------------------------------------


def _parse_one(tokens: list[Token], idx: int) -> tuple[Node, int]:
    """Parse a single node starting at ``tokens[idx]``.

    Returns ``(node, next_idx)`` where *next_idx* is the index of the first
    token that was *not* consumed.

    Raises :class:`ParseError` on malformed input.
    """
    if idx >= len(tokens):
        raise ParseError("unexpected end of input while parsing")

    tok = tokens[idx]

    if tok.kind == "rparen":
        raise ParseError(f"{tok.line}:{tok.col}: unexpected ')'")

    if tok.kind == "atom":
        return AtomNode(tok.value, Position(tok.line, tok.col)), idx + 1

    if tok.kind == "string":
        return StringNode(tok.value, Position(tok.line, tok.col)), idx + 1

    if tok.kind == "lparen":
        list_pos = Position(tok.line, tok.col)
        idx += 1  # consume '('
        items: list[Node] = []
        while True:
            if idx >= len(tokens):
                raise ParseError(f"{list_pos.line}:{list_pos.col}: unmatched '(' — missing ')'")
            if tokens[idx].kind == "rparen":
                idx += 1  # consume ')'
                return ListNode(tuple(items), list_pos), idx
            node, idx = _parse_one(tokens, idx)
            items.append(node)

    # "comment" tokens must be pre-filtered; guard against programming errors.
    raise ParseError(  # pragma: no cover
        f"{tok.line}:{tok.col}: unexpected token kind {tok.kind!r}"
    )


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------


def parse(src: str) -> ListNode:
    """Parse a KiCad S-expression *src* string.

    Expects exactly **one** top-level list (i.e. a single ``(…)``
    expression).  Returns the root :class:`~.nodes.ListNode`.

    Raises :class:`~kicad_pcb.errors.ParseError` if the input is empty,
    does not start with ``(``, is structurally malformed, or contains
    extra content after the top-level list.
    """
    all_tokens = tokenize(src)
    # Discard comments — they carry no semantic content.
    tokens = [t for t in all_tokens if t.kind != "comment"]

    if not tokens:
        raise ParseError("empty input — no S-expression found")

    if tokens[0].kind != "lparen":
        raise ParseError(
            f"{tokens[0].line}:{tokens[0].col}: "
            f"expected '(' at start of expression, got {tokens[0].value!r}"
        )

    root, next_idx = _parse_one(tokens, 0)

    # Any remaining meaningful tokens indicate unexpected trailing content.
    trailing = tokens[next_idx:]
    if trailing:
        t = trailing[0]
        raise ParseError(
            f"{t.line}:{t.col}: unexpected content after top-level expression: {t.value!r}"
        )

    if not isinstance(root, ListNode):  # pragma: no cover — always a ListNode here
        raise ParseError("top-level expression is not a list")

    return root


def parse_file(path: Path) -> ListNode:
    """Read *path* and parse it as a KiCad S-expression file.

    Raises :class:`~kicad_pcb.errors.ParseError` on read errors or
    malformed content.
    """
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ParseError(f"cannot read {path}: {exc}") from exc
    return parse(src)
