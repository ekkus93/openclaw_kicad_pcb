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

from ..errors import DocSyntaxError, ParseError, SExprParseError
from .nodes import AtomNode, ListNode, Node, Position, StringNode
from .tokenizer import Token, tokenize

# ---------------------------------------------------------------------------
# Iterative parser (avoids Python's ~1 000-frame recursion limit on large
# KiCad library files such as Device.kicad_sym / Connector.kicad_sym which
# can exceed 90 000 lines of deeply-nested s-expressions).
# ---------------------------------------------------------------------------


def _parse_iterative(tokens: list[Token]) -> ListNode:  # noqa: PLR0912 — iterative dispatch over token kinds; branches count is fixed by the grammar
    """Parse a pre-filtered token list into a single root :class:`ListNode`.

    Uses an explicit stack instead of call-stack recursion so that arbitrarily
    deep KiCad s-expression files parse without hitting Python's recursion
    limit.

    *tokens* must have ``comment`` tokens already removed.  The function
    expects the first token to be ``lparen`` (caller is responsible for that
    check) and raises :class:`SExprParseError` on any structural error.
    """
    # stack entries: (list_position, child_items_so_far)
    stack: list[tuple[Position, list[Node]]] = []
    root: ListNode | None = None

    for i, tok in enumerate(tokens):
        if tok.kind == "lparen":
            stack.append((Position(tok.line, tok.col), []))

        elif tok.kind == "rparen":
            if not stack:
                raise SExprParseError(
                    f"{tok.line}:{tok.col}: unexpected ')'",
                    line=tok.line,
                    col=tok.col,
                )
            pos, items = stack.pop()
            node: ListNode = ListNode(tuple(items), pos)
            if stack:
                stack[-1][1].append(node)
            else:
                # Top-level list closed — check for trailing tokens.
                remaining = tokens[i + 1 :]
                if remaining:
                    t = remaining[0]
                    raise SExprParseError(
                        f"{t.line}:{t.col}: unexpected content after "
                        f"top-level expression: {t.value!r}",
                        line=t.line,
                        col=t.col,
                    )
                root = node
                break

        elif tok.kind == "atom":
            leaf: Node = AtomNode(tok.value, Position(tok.line, tok.col), lexeme=tok.value)
            if not stack:
                raise SExprParseError(
                    f"{tok.line}:{tok.col}: unexpected atom at top level: {tok.value!r}",
                    line=tok.line,
                    col=tok.col,
                )
            stack[-1][1].append(leaf)

        elif tok.kind == "string":
            str_leaf: Node = StringNode(tok.value, Position(tok.line, tok.col))
            if not stack:
                raise SExprParseError(
                    f"{tok.line}:{tok.col}: unexpected string at top level: {tok.value!r}",
                    line=tok.line,
                    col=tok.col,
                )
            stack[-1][1].append(str_leaf)

        # "comment" tokens must be pre-filtered — guard against bugs.
        # pragma: no cover — tokenizer never emits unknown kinds
        else:
            raise ParseError(
                f"{tok.line}:{tok.col}: unexpected token kind {tok.kind!r}"
            )  # pragma: no cover

    if stack:
        pos = stack[0][0]
        raise SExprParseError(
            f"{pos.line}:{pos.col}: unmatched '(' \u2014 missing ')'",
            line=pos.line,
            col=pos.col,
        )

    if root is None:  # pragma: no cover — caught by empty-input check in parse()
        raise SExprParseError("internal error: no root node produced")

    return root


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
        raise SExprParseError("empty input \u2014 no S-expression found")

    if tokens[0].kind != "lparen":
        raise SExprParseError(
            f"{tokens[0].line}:{tokens[0].col}: "
            f"expected '(' at start of expression, got {tokens[0].value!r}",
            line=tokens[0].line,
            col=tokens[0].col,
        )

    return _parse_iterative(tokens)


def parse_file(path: Path) -> ListNode:
    """Read *path* and parse it as a KiCad S-expression file.

    Raises :class:`~kicad_pcb.errors.ParseError` on read errors or
    malformed content.
    """
    try:
        src = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocSyntaxError(f"cannot read {path}: {exc}", path=path) from exc
    return parse(src)
