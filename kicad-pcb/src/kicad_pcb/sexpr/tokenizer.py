"""S-expression tokenizer for KiCad file formats.

Produces a flat list of :class:`Token` objects from a source string.
Whitespace is consumed silently (not emitted).  Semicolon line-comments are
emitted with kind ``"comment"`` so callers can choose to preserve or discard them.

Supported token kinds
---------------------
``lparen``  — ``(``
``rparen``  — ``)``
``atom``    — unquoted run of non-whitespace, non-parenthesis, non-quote chars
``string``  — ``"…"`` double-quoted literal; ``value`` is the *unescaped* content
``comment`` — ``;`` to end-of-line; ``value`` includes the leading semicolon

Escape sequences inside strings
--------------------------------
``\\"`` → ``"``     ``\\\\`` → ``\\``
``\\n``  → newline  ``\\r`` → carriage-return  ``\\t`` → tab
Any other ``\\x`` is preserved literally as ``\\x`` (permissive, matching
KiCad's own loose escaping behaviour).

Raises :class:`~kicad_pcb.errors.ParseError` on unterminated strings.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..errors import ParseError

TokenKind = Literal["lparen", "rparen", "atom", "string", "comment"]


@dataclass(frozen=True)
class Token:
    """A single lexical token with source position."""

    kind: TokenKind
    value: str  # for "string": unescaped content; for others: raw text
    line: int   # 1-based
    col: int    # 1-based (start of token)

    def __repr__(self) -> str:
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"


def tokenize(src: str) -> list[Token]:  # noqa: PLR0912, PLR0915
    """Tokenize *src* and return all tokens (including ``"comment"`` tokens).

    Raises :class:`~kicad_pcb.errors.ParseError` if a string literal is
    unterminated or ends with an incomplete escape sequence.
    """
    tokens: list[Token] = []
    i = 0
    n = len(src)
    line = 1
    col = 1

    def _step() -> None:
        """Advance one character, tracking line/col."""
        nonlocal i, line, col
        if i < n:
            if src[i] == "\n":
                line += 1
                col = 1
            else:
                col += 1
            i += 1

    while i < n:
        c = src[i]
        tok_line, tok_col = line, col

        # --- whitespace ---
        if c in " \t\r\n":
            _step()
            continue

        # --- line comment ---
        if c == ";":
            start = i
            while i < n and src[i] != "\n":
                _step()
            tokens.append(Token("comment", src[start:i], tok_line, tok_col))
            continue

        # --- parentheses ---
        if c == "(":
            tokens.append(Token("lparen", "(", tok_line, tok_col))
            _step()
            continue

        if c == ")":
            tokens.append(Token("rparen", ")", tok_line, tok_col))
            _step()
            continue

        # --- quoted string ---
        if c == '"':
            _step()  # consume opening quote
            chars: list[str] = []
            while i < n and src[i] != '"':
                ch = src[i]
                if ch == "\\":
                    _step()  # consume backslash
                    if i >= n:
                        raise ParseError(
                            f"{tok_line}:{tok_col}: unterminated string (ends with backslash)"
                        )
                    esc = src[i]
                    _step()  # consume escape char
                    if esc == '"':
                        chars.append('"')
                    elif esc == "\\":
                        chars.append("\\")
                    elif esc == "n":
                        chars.append("\n")
                    elif esc == "r":
                        chars.append("\r")
                    elif esc == "t":
                        chars.append("\t")
                    else:
                        # permissive: preserve unknown escapes literally
                        chars.append("\\")
                        chars.append(esc)
                elif ch == "\n":
                    # newline inside string — illegal but permissive
                    chars.append(ch)
                    _step()
                else:
                    chars.append(ch)
                    _step()
            if i >= n:
                raise ParseError(
                    f"{tok_line}:{tok_col}: unterminated string literal"
                )
            _step()  # consume closing quote
            tokens.append(Token("string", "".join(chars), tok_line, tok_col))
            continue

        # --- atom ---
        # Consumes any non-whitespace, non-paren, non-quote run of characters.
        atom_chars: list[str] = []
        while i < n and src[i] not in ' \t\r\n()"':
            atom_chars.append(src[i])
            _step()
        if atom_chars:
            tokens.append(Token("atom", "".join(atom_chars), tok_line, tok_col))
            continue

        # Should be unreachable given the branches above, but guard explicitly.
        raise ParseError(  # pragma: no cover
            f"{tok_line}:{tok_col}: unexpected character {c!r}"
        )

    return tokens
