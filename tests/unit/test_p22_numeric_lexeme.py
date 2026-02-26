"""P2.2 tests — numeric lexeme preservation.

Verifies the three-layer contract introduced by P2.2:

1. ``AtomNode.lexeme`` carries the original source text; the field is
   transparent to ``==`` / ``hash`` so existing code is unaffected.
2. The parser populates ``lexeme`` for every atom token it creates.
3. The serializer emits ``lexeme`` (when present) so precision is preserved
   through a parse / serialize round-trip without any normalisation.
4. ``fnum_or_keep`` returns the *original* ``AtomNode`` unchanged when the
   supplied float value already matches the atom's lexeme.
5. ``PcbDoc.move_footprint`` no longer produces diff noise when the new
   coordinates are identical to the existing ones.
"""

from __future__ import annotations

import pytest
from kicad_pcb.pcb_doc import PcbDoc
from kicad_pcb.sexpr.builder import atom, fnum, fnum_or_keep
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Position
from kicad_pcb.sexpr.parser import parse
from kicad_pcb.sexpr.serializer import _inline, serialize

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Minimal PCB containing a single footprint whose ``at`` uses 4-decimal coords.
_MINIMAL_PCB_WITH_FOOTPRINT = """\
(kicad_pcb
  (version 20240101)
  (generator pcbnew)
  (footprint "TestLib:R" (layer "F.Cu")
    (at 50.0000 75.0000)
    (property "Reference" "R1" (at 0 0) (layer "F.SilkS") (uuid "ref-uuid"))
    (pad "1" thru_hole circle (at -1.524 0) (size 1.4986 1.4986) (drill 0.8128)
      (layers "*.Cu" "*.Mask") (uuid "pad1-uuid"))
  )
)"""


# ===========================================================================
# 1. AtomNode.lexeme field contract
# ===========================================================================


class TestAtomNodeLexemeField:
    """The ``lexeme`` field is metadata: invisible to equality and hashing."""

    def test_default_lexeme_is_none(self) -> None:
        node = AtomNode("foo")
        assert node.lexeme is None

    def test_explicit_lexeme_stored(self) -> None:
        node = AtomNode("10.0", lexeme="10.0000")
        assert node.lexeme == "10.0000"

    def test_equality_ignores_lexeme(self) -> None:
        a = AtomNode("foo")
        b = AtomNode("foo", lexeme="foo")
        assert a == b, "lexeme must not affect equality"

    def test_hash_ignores_lexeme(self) -> None:
        a = AtomNode("foo")
        b = AtomNode("foo", lexeme="foo")
        assert hash(a) == hash(b), "lexeme must not affect hash"

    def test_equality_still_considers_value(self) -> None:
        assert AtomNode("foo") != AtomNode("bar")

    def test_equality_still_considers_pos(self) -> None:
        assert AtomNode("foo", pos=Position(1, 1)) != AtomNode("foo", pos=Position(2, 2))

    def test_repr_shows_value_not_lexeme(self) -> None:
        node = AtomNode("10.0", lexeme="10.0000")
        assert repr(node) == "AtomNode('10.0')"


# ===========================================================================
# 2. Parser populates lexeme
# ===========================================================================


class TestParserSetsLexeme:
    """The parser sets ``lexeme`` on every ``AtomNode`` it creates."""

    def test_keyword_atom_has_lexeme(self) -> None:
        root = parse("(kicad_sch)")
        head = root.items[0]
        assert isinstance(head, AtomNode)
        assert head.lexeme == "kicad_sch"

    def test_numeric_atom_preserves_exact_text(self) -> None:
        root = parse("(at 10.0000 200)")
        x_atom = root.items[1]
        y_atom = root.items[2]
        assert isinstance(x_atom, AtomNode)
        assert isinstance(y_atom, AtomNode)
        assert x_atom.lexeme == "10.0000"
        assert y_atom.lexeme == "200"

    def test_trailing_zeros_preserved_in_lexeme(self) -> None:
        root = parse("(foo 3.14000)")
        atom_node = root.items[1]
        assert isinstance(atom_node, AtomNode)
        assert atom_node.lexeme == "3.14000"
        # value also equals the raw text (AtomNode.value stores verbatim token)
        assert atom_node.value == "3.14000"

    def test_lexeme_set_for_all_parsed_atoms(self) -> None:
        """Every atom in a nested expression carries a lexeme."""
        root = parse("(version 20230121)")
        for item in root.items:
            assert isinstance(item, AtomNode)
            assert item.lexeme is not None

    def test_nested_atoms_carry_lexeme(self) -> None:
        root = parse("(outer (inner 42.0000))")
        inner = root.items[1]
        assert isinstance(inner, ListNode)
        num = inner.items[1]
        assert isinstance(num, AtomNode)
        assert num.lexeme == "42.0000"


# ===========================================================================
# 3. Serializer emits lexeme when present
# ===========================================================================


class TestSerializerEmitsLexeme:
    """Serializer prefers ``lexeme`` over ``value`` for ``AtomNode``."""

    def test_inline_uses_lexeme_when_present(self) -> None:
        node = AtomNode("10.0", lexeme="10.0000")
        assert _inline(node) == "10.0000"

    def test_inline_falls_back_to_value_without_lexeme(self) -> None:
        node = AtomNode("10.0")
        assert _inline(node) == "10.0"

    def test_serialize_atom_uses_lexeme(self) -> None:
        node = AtomNode("3.14", lexeme="3.14000")
        assert serialize(node) == "3.14000"

    def test_fnum_atom_no_lexeme_serializes_formatted_value(self) -> None:
        node = fnum(10.0, 3)
        assert node.lexeme is None
        assert serialize(node) == "10.000"

    def test_atom_builder_no_lexeme(self) -> None:
        node = atom("kicad_sch")
        assert node.lexeme is None
        assert serialize(node) == "kicad_sch"


# ===========================================================================
# 4. Round-trip preserves numeric precision
# ===========================================================================


class TestRoundTripPreservesLexeme:
    """parse(text) → serialize → text-identical for numeric strings."""

    @pytest.mark.parametrize(
        "src",
        [
            "(at 10.0000 200.0000)",
            "(foo 3.14159265 2.71828182)",
            "(version 20230121)",
            '(uuid "abc-def")',
            "(at 0 0 90)",
        ],
    )
    def test_numeric_token_survives_round_trip(self, src: str) -> None:
        assert serialize(parse(src)) == src

    def test_four_decimal_precision_preserved(self) -> None:
        src = "(at 50.0000 75.0000)"
        result = serialize(parse(src))
        assert "50.0000" in result
        assert "75.0000" in result
        assert result == src

    def test_whitespace_normalised_but_numbers_preserved(self) -> None:
        # Inline form is normalised (spaces between items), but numbers stay verbatim.
        src = "(at   10.0000   200.0000  )"
        result = serialize(parse(src))
        assert "10.0000" in result
        assert "200.0000" in result


# ===========================================================================
# 5. fnum_or_keep helper
# ===========================================================================


class TestFnumOrKeep:
    """``fnum_or_keep`` returns original atom when value is identical."""

    def test_returns_original_when_value_matches(self) -> None:
        original = AtomNode("10.0000", lexeme="10.0000")
        result = fnum_or_keep(10.0, original, 3)
        assert result is original, "should be the exact same object"

    def test_returns_new_atom_when_value_differs(self) -> None:
        original = AtomNode("10.0000", lexeme="10.0000")
        result = fnum_or_keep(20.0, original, 3)
        assert result is not original
        assert result.value == "20.000"
        assert result.lexeme is None

    def test_new_atom_precision_uses_decimals_arg(self) -> None:
        original = AtomNode("1.0", lexeme="1.0")
        result = fnum_or_keep(2.5, original, 4)
        assert result.value == "2.5000"

    def test_falls_back_to_fnum_when_no_lexeme(self) -> None:
        """Atoms without a lexeme (synthesised) don't benefit from preservation."""
        original = AtomNode("10.000")  # lexeme=None
        result = fnum_or_keep(10.0, original, 2)
        # No lexeme → always creates a new atom via fnum
        assert result.lexeme is None
        assert result.value == "10.00"

    def test_handles_non_numeric_lexeme_gracefully(self) -> None:
        """If lexeme is not a valid float, fall through to fnum."""
        original = AtomNode("solid", lexeme="solid")
        result = fnum_or_keep(1.5, original, 3)
        assert result is not original
        assert result.value == "1.500"

    def test_integer_preserved_as_integer(self) -> None:
        original = AtomNode("90", lexeme="90")
        result = fnum_or_keep(90.0, original, 3)
        assert result is original  # float("90") == 90.0

    def test_trailing_zeros_different_length_still_matches(self) -> None:
        """10.0000 and 10.000 represent the same float."""
        orig_4dec = AtomNode("10.0000", lexeme="10.0000")
        orig_3dec = AtomNode("10.000", lexeme="10.000")
        assert fnum_or_keep(10.0, orig_4dec) is orig_4dec
        assert fnum_or_keep(10.0, orig_3dec) is orig_3dec


# ===========================================================================
# 6. PcbDoc.move_footprint preserves coords when unchanged
# ===========================================================================


class TestMoveFootprintPreservesLexeme:
    """``move_footprint`` with identical coords produces zero diff."""

    def _parse_pcb(self, src: str) -> PcbDoc:
        root = parse(src)
        doc = PcbDoc.__new__(PcbDoc)
        doc.root = root
        return doc

    def test_same_coords_preserves_original_strings(self) -> None:
        doc = self._parse_pcb(_MINIMAL_PCB_WITH_FOOTPRINT)
        doc.move_footprint("R1", 50.0, 75.0)
        serialized = serialize(doc.root)
        assert "50.0000" in serialized, "x coord text should be unchanged"
        assert "75.0000" in serialized, "y coord text should be unchanged"

    def test_changed_x_creates_new_string(self) -> None:
        doc = self._parse_pcb(_MINIMAL_PCB_WITH_FOOTPRINT)
        doc.move_footprint("R1", 60.0, 75.0)
        serialized = serialize(doc.root)
        assert "60.000" in serialized, "new x should appear"
        assert "50.0000" not in serialized, "old x should be gone"
        assert "75.0000" in serialized, "unchanged y should be preserved"

    def test_changed_y_creates_new_string(self) -> None:
        doc = self._parse_pcb(_MINIMAL_PCB_WITH_FOOTPRINT)
        doc.move_footprint("R1", 50.0, 80.0)
        serialized = serialize(doc.root)
        assert "50.0000" in serialized, "unchanged x should be preserved"
        assert "75.0000" not in serialized, "old y should be gone"
        assert "80.000" in serialized, "new y should appear"

    def test_both_changed(self) -> None:
        doc = self._parse_pcb(_MINIMAL_PCB_WITH_FOOTPRINT)
        doc.move_footprint("R1", 60.0, 80.0)
        serialized = serialize(doc.root)
        assert "60.000" in serialized
        assert "80.000" in serialized
        assert "50.0000" not in serialized
        assert "75.0000" not in serialized
