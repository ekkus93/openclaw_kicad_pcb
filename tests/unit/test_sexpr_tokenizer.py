"""Tests for the KiCad S-expression tokenizer (kicad_pcb.sexpr.tokenizer).

Phase 3.1 coverage:
- parentheses  (LPAREN / RPAREN tokens)
- atoms        (bare identifiers, numbers, keywords)
- quoted strings with escape sequences
- whitespace and newline handling
- ; line comments
- position tracking (line, col — 1-based)
- error cases: unterminated strings
"""

from __future__ import annotations

import pytest

from kicad_pcb.errors import ParseError
from kicad_pcb.sexpr.tokenizer import tokenize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def kinds(src: str) -> list[str]:
    """Return only token kinds for *src*."""
    return [t.kind for t in tokenize(src)]


def values(src: str) -> list[str]:
    """Return only token values for *src*."""
    return [t.value for t in tokenize(src)]


# ---------------------------------------------------------------------------
# Parentheses
# ---------------------------------------------------------------------------


class TestParentheses:
    def test_single_open(self) -> None:
        toks = tokenize("(")
        assert len(toks) == 1
        assert toks[0].kind == "lparen"
        assert toks[0].value == "("

    def test_single_close(self) -> None:
        toks = tokenize(")")
        assert toks[0].kind == "rparen"
        assert toks[0].value == ")"

    def test_open_and_close(self) -> None:
        assert kinds("()") == ["lparen", "rparen"]

    def test_nested(self) -> None:
        assert kinds("(())") == ["lparen", "lparen", "rparen", "rparen"]

    def test_multiple_parens(self) -> None:
        assert kinds("((()))") == ["lparen"] * 3 + ["rparen"] * 3


# ---------------------------------------------------------------------------
# Atoms
# ---------------------------------------------------------------------------


class TestAtoms:
    def test_simple_keyword(self) -> None:
        toks = tokenize("kicad_sch")
        assert toks[0].kind == "atom"
        assert toks[0].value == "kicad_sch"

    def test_integer(self) -> None:
        assert values("42") == ["42"]

    def test_float(self) -> None:
        assert values("3.14") == ["3.14"]

    def test_negative_number(self) -> None:
        assert values("-5.0") == ["-5.0"]

    def test_atom_with_slash(self) -> None:
        assert values("Device:R") == ["Device:R"]

    def test_atom_with_dots(self) -> None:
        assert values("F.Cu") == ["F.Cu"]

    def test_multiple_atoms(self) -> None:
        assert values("a b c") == ["a", "b", "c"]

    def test_atom_surrounded_by_parens(self) -> None:
        toks = tokenize("(version 20230121)")
        assert [t.kind for t in toks] == ["lparen", "atom", "atom", "rparen"]
        assert toks[1].value == "version"
        assert toks[2].value == "20230121"

    def test_uuid_style_atom(self) -> None:
        # UUIDs appear as atoms when not quoted
        uid = "12345678-1234-1234-1234-1234567890ab"
        assert values(uid) == [uid]

    def test_boolean_yes_no(self) -> None:
        assert values("yes no") == ["yes", "no"]


# ---------------------------------------------------------------------------
# Quoted strings
# ---------------------------------------------------------------------------


class TestStrings:
    def test_empty_string(self) -> None:
        toks = tokenize('""')
        assert toks[0].kind == "string"
        assert toks[0].value == ""

    def test_simple_string(self) -> None:
        assert values('"hello"') == ["hello"]

    def test_string_with_spaces(self) -> None:
        assert values('"hello world"') == ["hello world"]

    def test_escaped_quote(self) -> None:
        assert values(r'"say \"hi\""') == ['say "hi"']

    def test_escaped_backslash(self) -> None:
        assert values(r'"C:\\Users"') == ["C:\\Users"]

    def test_escape_newline(self) -> None:
        assert values(r'"\n"') == ["\n"]

    def test_escape_tab(self) -> None:
        assert values(r'"\t"') == ["\t"]

    def test_escape_carriage_return(self) -> None:
        assert values(r'"\r"') == ["\r"]

    def test_unknown_escape_preserved(self) -> None:
        # unknown \x → preserved literally as \x
        assert values(r'"\x"') == ["\\x"]

    def test_string_with_parens_inside(self) -> None:
        # Parens inside a string are not tokenized
        toks = tokenize('"(not a paren)"')
        assert len(toks) == 1
        assert toks[0].kind == "string"
        assert toks[0].value == "(not a paren)"

    def test_string_is_not_atom(self) -> None:
        toks = tokenize('"word"')
        assert toks[0].kind == "string"

    def test_two_strings(self) -> None:
        toks = tokenize('"a" "b"')
        assert [t.kind for t in toks] == ["string", "string"]
        assert [t.value for t in toks] == ["a", "b"]


# ---------------------------------------------------------------------------
# Whitespace handling
# ---------------------------------------------------------------------------


class TestWhitespace:
    def test_spaces_between_tokens(self) -> None:
        assert values("( a b )") == ["(", "a", "b", ")"]

    def test_tabs_stripped(self) -> None:
        assert values("(\ta\tb)") == ["(", "a", "b", ")"]

    def test_newlines_stripped(self) -> None:
        assert values("(\na\nb)") == ["(", "a", "b", ")"]

    def test_empty_string_input(self) -> None:
        assert tokenize("") == []

    def test_only_whitespace(self) -> None:
        assert tokenize("   \n\t  ") == []

    def test_multiline_input(self) -> None:
        src = """
(kicad_sch
  (version 20230121)
  (generator eeschema))
"""
        toks = [t for t in tokenize(src) if t.kind != "comment"]
        kinds_ = [t.kind for t in toks]
        assert kinds_[0] == "lparen"
        assert toks[1].value == "kicad_sch"


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------


class TestComments:
    def test_inline_comment(self) -> None:
        toks = tokenize("; this is a comment")
        assert len(toks) == 1
        assert toks[0].kind == "comment"
        assert "; this is a comment" in toks[0].value

    def test_comment_after_tokens(self) -> None:
        toks = tokenize("(a b) ; trailing comment")
        comment_toks = [t for t in toks if t.kind == "comment"]
        assert len(comment_toks) == 1

    def test_comment_does_not_consume_next_line(self) -> None:
        toks = tokenize("; comment\n(a)")
        non_comment = [t for t in toks if t.kind != "comment"]
        assert [t.kind for t in non_comment] == ["lparen", "atom", "rparen"]

    def test_no_comment_inside_string(self) -> None:
        toks = tokenize('"not ; a comment"')
        assert len(toks) == 1
        assert toks[0].kind == "string"
        assert toks[0].value == "not ; a comment"


# ---------------------------------------------------------------------------
# Position tracking
# ---------------------------------------------------------------------------


class TestPositions:
    def test_first_token_at_1_1(self) -> None:
        toks = tokenize("(")
        assert toks[0].line == 1
        assert toks[0].col == 1

    def test_atom_col_after_space(self) -> None:
        toks = tokenize("( abc")
        atom = next(t for t in toks if t.kind == "atom")
        assert atom.col == 3

    def test_second_line_position(self) -> None:
        toks = tokenize("(\nabc")
        atom = next(t for t in toks if t.kind == "atom")
        assert atom.line == 2
        assert atom.col == 1

    def test_rparen_col(self) -> None:
        toks = tokenize("(a)")
        rparen = toks[-1]
        assert rparen.kind == "rparen"
        assert rparen.col == 3

    def test_string_position(self) -> None:
        toks = tokenize('  "hello"')
        assert toks[0].kind == "string"
        assert toks[0].col == 3

    def test_multiple_lines_col_reset(self) -> None:
        toks = tokenize("abc\nxyz")
        assert toks[0].line == 1 and toks[0].col == 1
        assert toks[1].line == 2 and toks[1].col == 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestTokenizerErrors:
    def test_unterminated_string(self) -> None:
        with pytest.raises(ParseError, match="unterminated"):
            tokenize('"hello')

    def test_unterminated_string_with_escape_at_end(self) -> None:
        with pytest.raises(ParseError, match="unterminated"):
            tokenize('"hello\\')

    def test_error_includes_position(self) -> None:
        with pytest.raises(ParseError, match=r"1:1"):
            tokenize('"unterminated')

    def test_error_position_second_line(self) -> None:
        with pytest.raises(ParseError, match=r"2:"):
            tokenize('(good)\n"bad')
