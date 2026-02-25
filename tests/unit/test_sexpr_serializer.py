"""Tests for the S-expression serializer (kicad_pcb.sexpr.serializer).

Phase 3.3 coverage:
- AtomNode, StringNode, ListNode serialization
- string escape sequences (backslash, quote, newline, tab, CR)
- inline vs. block-indented formatting decisions
- round-trip: parse → serialize → parse gives equivalent AST
- deterministic output (same AST → same string)
- serialize_file writes correct content
"""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.serializer import _escape_string, serialize, serialize_file

# ---------------------------------------------------------------------------
# Leaf nodes
# ---------------------------------------------------------------------------


class TestSerializeAtom:
    def test_simple_keyword(self) -> None:
        assert serialize(AtomNode("kicad_sch")) == "kicad_sch"

    def test_number(self) -> None:
        assert serialize(AtomNode("42")) == "42"

    def test_float(self) -> None:
        assert serialize(AtomNode("3.14")) == "3.14"

    def test_negative(self) -> None:
        assert serialize(AtomNode("-5.0")) == "-5.0"

    def test_boolean(self) -> None:
        assert serialize(AtomNode("yes")) == "yes"


class TestSerializeString:
    def test_empty_string(self) -> None:
        assert serialize(StringNode("")) == '""'

    def test_simple_string(self) -> None:
        assert serialize(StringNode("hello")) == '"hello"'

    def test_string_with_spaces(self) -> None:
        assert serialize(StringNode("hello world")) == '"hello world"'

    def test_escaped_quote(self) -> None:
        assert serialize(StringNode('say "hi"')) == r'"say \"hi\""'

    def test_escaped_backslash(self) -> None:
        assert serialize(StringNode("C:\\Users")) == r'"C:\\Users"'

    def test_escaped_newline(self) -> None:
        assert serialize(StringNode("a\nb")) == r'"a\nb"'

    def test_escaped_tab(self) -> None:
        assert serialize(StringNode("a\tb")) == r'"a\tb"'

    def test_escaped_cr(self) -> None:
        assert serialize(StringNode("a\rb")) == r'"a\rb"'


class TestEscapeString:
    def test_no_special_chars(self) -> None:
        assert _escape_string("hello") == "hello"

    def test_double_quote(self) -> None:
        assert _escape_string('"') == '\\"'

    def test_backslash(self) -> None:
        assert _escape_string("\\") == "\\\\"

    def test_newline(self) -> None:
        assert _escape_string("\n") == "\\n"


# ---------------------------------------------------------------------------
# List nodes — inline
# ---------------------------------------------------------------------------


class TestSerializeListInline:
    def test_empty_list(self) -> None:
        assert serialize(ListNode(())) == "()"

    def test_single_atom(self) -> None:
        n = ListNode((AtomNode("foo"),))
        assert serialize(n) == "(foo)"

    def test_keyword_and_value(self) -> None:
        n = ListNode((AtomNode("version"), AtomNode("20230121")))
        assert serialize(n) == "(version 20230121)"

    def test_keyword_and_string(self) -> None:
        n = ListNode((AtomNode("generator"), StringNode("eeschema")))
        assert serialize(n) == '(generator "eeschema")'

    def test_three_atoms(self) -> None:
        n = ListNode((AtomNode("at"), AtomNode("100.0"), AtomNode("50.0")))
        assert serialize(n) == "(at 100.0 50.0)"

    def test_short_nested_list_stays_inline(self) -> None:
        inner = ListNode((AtomNode("at"), AtomNode("10"), AtomNode("20")))
        outer = ListNode((AtomNode("pin"), inner))
        result = serialize(outer)
        assert "\n" not in result
        assert "at 10 20" in result


# ---------------------------------------------------------------------------
# List nodes — block indented
# ---------------------------------------------------------------------------


class TestSerializeListBlock:
    def test_long_list_goes_block(self) -> None:
        # "(key " + 76 x + ")" = 82 chars → exceeds 80-char inline budget
        long_value = "x" * 76
        n = ListNode((AtomNode("key"), AtomNode(long_value)))
        result = serialize(n)
        assert "\n" in result

    def test_block_opens_with_paren_and_head(self) -> None:
        long_value = "x" * 76
        n = ListNode((AtomNode("key"), AtomNode(long_value)))
        result = serialize(n)
        assert result.startswith("(key\n")

    def test_block_closes_with_rparen(self) -> None:
        long_value = "x" * 76
        n = ListNode((AtomNode("key"), AtomNode(long_value)))
        result = serialize(n)
        assert result.endswith(")")

    def test_nested_block_indentation(self) -> None:
        # "(child1 " + 72 a + ")" = 81 chars → block at indent=0
        long1 = "a" * 72
        long2 = "b" * 72
        inner1 = ListNode((AtomNode("child1"), AtomNode(long1)))
        inner2 = ListNode((AtomNode("child2"), AtomNode(long2)))
        root = ListNode((AtomNode("root"), inner1, inner2))
        result = serialize(root)
        assert "(root" in result
        lines = result.splitlines()
        # inner children should be indented
        indented = [ln for ln in lines if ln.startswith("  ")]
        assert len(indented) >= 2

    def test_indent_parameter_shifts_budget(self) -> None:
        # A list that fits at indent=0 may go block at indent=60
        n = ListNode((AtomNode("version"), AtomNode("20230121")))
        at0 = serialize(n, indent=0)
        # "(version 20230121)" is 18 chars → still fits at indent=60 (60+18=78≤80)
        assert "\n" not in at0
        # at indent=70, 70+19=89 > 80 → goes block
        at70 = serialize(n, indent=70)
        assert "\n" in at70


# ---------------------------------------------------------------------------
# Round-trip stability
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def _assert_equivalent(self, src: str) -> None:
        """Parse → serialize → re-parse; check structural equivalence."""
        root1 = parse(src)
        serialized = serialize(root1)
        root2 = parse(serialized)
        # Compare via re-serialization (positions differ, values must not)
        assert serialize(root2) == serialize(root1)

    def test_simple_list(self) -> None:
        self._assert_equivalent("(version 20230121)")

    def test_nested_list(self) -> None:
        self._assert_equivalent("(kicad_sch (version 20230121) (generator eeschema))")

    def test_string_value(self) -> None:
        self._assert_equivalent('(generator "eeschema")')

    def test_quoted_string_with_escapes(self) -> None:
        self._assert_equivalent(r'(label "say \"hi\"")')

    def test_deeply_nested(self) -> None:
        self._assert_equivalent("(a (b (c (d 42))))")

    def test_real_kicad_snippet(self) -> None:
        src = """(kicad_sch
  (version 20230121)
  (generator eeschema)
  (uuid "fd37f8b1-0000-0000-0000-000000000000"))"""
        self._assert_equivalent(src)

    def test_numbers_preserved(self) -> None:
        self._assert_equivalent("(at 100.25 -50.0 90)")

    def test_empty_list_round_trip(self) -> None:
        self._assert_equivalent("()")


class TestDeterministic:
    def test_same_tree_same_output(self) -> None:
        n = ListNode(
            (
                AtomNode("kicad_sch"),
                ListNode((AtomNode("version"), AtomNode("1"))),
            )
        )
        assert serialize(n) == serialize(n)

    def test_parse_same_source_twice(self) -> None:
        src = "(kicad_sch (version 1))"
        assert serialize(parse(src)) == serialize(parse(src))


# ---------------------------------------------------------------------------
# serialize_file
# ---------------------------------------------------------------------------


class TestSerializeFile:
    def test_writes_file(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        n = ListNode((AtomNode("kicad_sch"), ListNode((AtomNode("version"), AtomNode("1")))))
        serialize_file(out, n)
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "kicad_sch" in content

    def test_content_parseable(self, tmp_path: Path) -> None:
        out = tmp_path / "rt.kicad_sch"
        n = ListNode((AtomNode("kicad_sch"), ListNode((AtomNode("version"), AtomNode("1")))))
        serialize_file(out, n)
        root = parse(out.read_text(encoding="utf-8"))
        assert root.key == "kicad_sch"

    def test_utf8_string(self, tmp_path: Path) -> None:
        out = tmp_path / "utf8.kicad_sch"
        n = ListNode((AtomNode("root"), StringNode("café")))
        serialize_file(out, n)
        root = parse(out.read_text(encoding="utf-8"))
        assert root.items[1].value == "café"  # type: ignore[union-attr]
