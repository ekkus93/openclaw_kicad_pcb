"""Tests for the KiCad S-expression parser (kicad_pcb.sexpr.parser).

Phase 3.2 coverage:
- valid nested lists → ListNode / AtomNode / StringNode
- malformed input  (unexpected EOF, unmatched parens, unexpected ')')
- precise error locations in ParseError messages
- parse_file I/O path
- comment tokens are silently discarded
- convenience assertions: root.key, root.head, node positions
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from kicad_pcb.errors import ParseError
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.parser import parse, parse_file

# ---------------------------------------------------------------------------
# Minimal / leaf cases
# ---------------------------------------------------------------------------


class TestParseLeaves:
    def test_single_atom_list(self) -> None:
        root = parse("(kicad_sch)")
        assert isinstance(root, ListNode)
        assert root.key == "kicad_sch"
        assert len(root.items) == 1

    def test_list_with_atom_value(self) -> None:
        root = parse("(version 20230121)")
        assert root.key == "version"
        assert isinstance(root.items[1], AtomNode)
        assert root.items[1].value == "20230121"

    def test_list_with_string_value(self) -> None:
        root = parse('(generator "eeschema")')
        assert isinstance(root.items[1], StringNode)
        assert root.items[1].value == "eeschema"

    def test_list_with_multiple_atoms(self) -> None:
        root = parse("(at 100.0 50.0 0)")
        assert len(root.items) == 4
        assert root.items[1].value == "100.0"  # type: ignore[union-attr]

    def test_empty_list(self) -> None:
        root = parse("()")
        assert isinstance(root, ListNode)
        assert root.items == ()

    def test_list_head_is_atom_node(self) -> None:
        root = parse("(foo)")
        assert isinstance(root.head, AtomNode)
        assert root.head.value == "foo"

    def test_list_without_atom_head(self) -> None:
        root = parse('("string-head")')
        assert root.head is None
        assert root.key is None


# ---------------------------------------------------------------------------
# Nested lists
# ---------------------------------------------------------------------------


class TestParseNested:
    def test_one_nested_list(self) -> None:
        root = parse("(kicad_sch (version 20230121))")
        assert root.key == "kicad_sch"
        assert len(root.items) == 2
        child = root.items[1]
        assert isinstance(child, ListNode)
        assert child.key == "version"

    def test_multiple_nested(self) -> None:
        root = parse("(kicad_sch (version 20230121) (generator eeschema))")
        assert len(root.items) == 3
        assert root.items[1].key == "version"  # type: ignore[union-attr]
        assert root.items[2].key == "generator"  # type: ignore[union-attr]

    def test_deeply_nested(self) -> None:
        root = parse("(a (b (c (d))))")
        b = root.items[1]
        assert isinstance(b, ListNode) and b.key == "b"
        c = b.items[1]
        assert isinstance(c, ListNode) and c.key == "c"
        d = c.items[1]
        assert isinstance(d, ListNode) and d.key == "d"

    def test_mixed_types_in_list(self) -> None:
        root = parse('(sym "Device:R" (at 10.0 20.0) 90)')
        assert isinstance(root.items[1], StringNode)
        assert isinstance(root.items[2], ListNode)
        assert isinstance(root.items[3], AtomNode)

    def test_sibling_lists(self) -> None:
        root = parse("(parent (a 1) (b 2) (c 3))")
        keys = [root.items[i].key for i in range(1, 4)]  # type: ignore[union-attr]
        assert keys == ["a", "b", "c"]

    def test_real_kicad_snippet(self) -> None:
        src = """(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "fd37f8b1-0000-0000-0000-000000000000"))"""
        root = parse(src)
        assert root.key == "kicad_sch"
        version_node = root.items[1]
        assert isinstance(version_node, ListNode)
        assert version_node.items[1].value == "20230121"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# String handling through parser
# ---------------------------------------------------------------------------


class TestParseStrings:
    def test_string_unescaped(self) -> None:
        root = parse(r'(val "say \"hi\"")')
        s = root.items[1]
        assert isinstance(s, StringNode)
        assert s.value == 'say "hi"'

    def test_backslash_unescaped(self) -> None:
        root = parse(r'(path "C:\\Users")')
        s = root.items[1]
        assert isinstance(s, StringNode)
        assert s.value == "C:\\Users"


# ---------------------------------------------------------------------------
# Comments are discarded
# ---------------------------------------------------------------------------


class TestParseComments:
    def test_comment_before_list(self) -> None:
        root = parse("; top comment\n(kicad_sch)")
        assert root.key == "kicad_sch"

    def test_comment_inside_list(self) -> None:
        root = parse("(kicad_sch ; comment here\n(version 1))")
        assert len(root.items) == 2  # only kicad_sch + version child

    def test_comment_after_list(self) -> None:
        root = parse("(kicad_sch) ; trailing comment")
        assert root.key == "kicad_sch"


# ---------------------------------------------------------------------------
# Position information
# ---------------------------------------------------------------------------


class TestParsePositions:
    def test_root_position_line_1(self) -> None:
        root = parse("(kicad_sch)")
        assert root.pos.line == 1
        assert root.pos.col == 1

    def test_nested_child_position(self) -> None:
        root = parse("(parent\n  (child))")
        child = root.items[1]
        assert isinstance(child, ListNode)
        assert child.pos.line == 2

    def test_atom_position(self) -> None:
        root = parse("(version 42)")
        atom = root.items[1]
        assert isinstance(atom, AtomNode)
        assert atom.pos.line == 1
        assert atom.pos.col > 1

    def test_string_position(self) -> None:
        root = parse('(gen "eeschema")')
        s = root.items[1]
        assert isinstance(s, StringNode)
        assert s.pos.col > 1


# ---------------------------------------------------------------------------
# Error cases
# ---------------------------------------------------------------------------


class TestParseErrors:
    def test_empty_input(self) -> None:
        with pytest.raises(ParseError, match="empty input"):
            parse("")

    def test_only_whitespace(self) -> None:
        with pytest.raises(ParseError, match="empty input"):
            parse("   \n")

    def test_only_comment(self) -> None:
        with pytest.raises(ParseError, match="empty input"):
            parse("; just a comment\n")

    def test_starts_with_atom_not_paren(self) -> None:
        with pytest.raises(ParseError, match="expected '\\('"):
            parse("kicad_sch")

    def test_starts_with_rparen(self) -> None:
        with pytest.raises(ParseError, match="expected '\\('"):
            parse(")")

    def test_unmatched_open_paren(self) -> None:
        with pytest.raises(ParseError, match="unmatched"):
            parse("(kicad_sch")

    def test_rparen_at_top_level(self) -> None:
        # A bare ')' at the top level is caught by the "expected '('" check
        with pytest.raises(ParseError):
            parse(")")

    def test_extra_close_paren(self) -> None:
        with pytest.raises(ParseError):
            parse("(kicad_sch))")

    def test_extra_atom_after_list(self) -> None:
        with pytest.raises(ParseError, match="unexpected content"):
            parse("(kicad_sch) extra")

    def test_extra_list_after_list(self) -> None:
        with pytest.raises(ParseError, match="unexpected content"):
            parse("(kicad_sch) (second)")

    def test_error_includes_line_col(self) -> None:
        with contextlib.suppress(ParseError):
            parse("(a\n)")
        # main check: unmatched lparen error includes position
        with pytest.raises(ParseError, match=r"\d+:\d+"):
            parse("(unclosed\n  (nested)")

    def test_nested_unmatched(self) -> None:
        with pytest.raises(ParseError, match="unmatched"):
            parse("(a (b (c)")


# ---------------------------------------------------------------------------
# parse_file
# ---------------------------------------------------------------------------


class TestParseFile:
    def test_reads_and_parses(self, tmp_path: Path) -> None:
        f = tmp_path / "test.kicad_sch"
        f.write_text("(kicad_sch (version 1))", encoding="utf-8")
        root = parse_file(f)
        assert root.key == "kicad_sch"

    def test_missing_file_raises_parse_error(self, tmp_path: Path) -> None:
        with pytest.raises(ParseError, match="cannot read"):
            parse_file(tmp_path / "nonexistent.kicad_sch")

    def test_malformed_file_raises_parse_error(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.kicad_sch"
        f.write_text("(unclosed", encoding="utf-8")
        with pytest.raises(ParseError):
            parse_file(f)

    def test_round_trip_via_file(self, tmp_path: Path) -> None:
        f = tmp_path / "rt.kicad_sch"
        f.write_text('(kicad_sch (generator "eeschema"))', encoding="utf-8")
        root = parse_file(f)
        assert root.items[1].key == "generator"  # type: ignore[union-attr]
