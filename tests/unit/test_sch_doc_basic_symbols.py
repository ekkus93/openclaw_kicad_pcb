"""Unit tests for kicad_pcb.sch_doc — SchematicDoc and library helpers.

Tests use in-memory string fixtures and temporary directories; no system
KiCad installation is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import ParseError
from kicad_pcb.sch_doc import (
    SchematicDoc,
)
from kicad_pcb.sexpr import find_all, find_first, parse, serialize

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""

SCH_WITH_SYMBOL = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50.80 76.20 0) (unit 1)
    (exclude_from_sim yes) (in_bom yes) (on_board yes)
    (uuid "sym-uuid-1")
    (property "Reference" "R1" (at 52.07 74.93 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "10k" (at 52.07 77.47 0)
      (effects (font (size 1.27 1.27)))
    )
    (pin "1" (uuid "pin-uuid-1"))
    (pin "2" (uuid "pin-uuid-2"))
  )
  (sheet_instances (path "/" (page "1")))
)
"""

SCH_NO_LIB_SYMBOLS = """\
(kicad_sch (version 20230121) (generator test)
  (sheet_instances (path "/" (page "1")))
)
"""

SCH_WRONG_ROOT = """\
(kicad_pcb (version 20230121) (generator test)
)
"""

MINIMAL_LIB_SYM = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "R"
    (symbol "R_1_1"
      (pin passive passive (at 0 2.54 270) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
      (pin passive passive (at 0 -2.54 90) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "2" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""

MINIMAL_LIB_SYM_WITH_ID = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "R"
    (id 0)
    (property "Reference" "R" (id 0)
      (at 2.032 0 90)
      (effects (font (size 1.27 1.27)))
    )
    (symbol "R_1_1"
      (pin passive passive (at 0 2.54 270) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""


def _doc_from(content: str) -> SchematicDoc:
    return SchematicDoc(parse(content))


# ---------------------------------------------------------------------------
# SchematicDoc — load / construction
# ---------------------------------------------------------------------------


class TestSchematicDocLoad:
    def test_construct_valid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        assert doc.root.key == "kicad_sch"

    def test_construct_wrong_root_raises(self) -> None:
        with pytest.raises(ParseError, match="kicad_sch"):
            _doc_from(SCH_WRONG_ROOT)

    def test_load_fixture_file(self) -> None:
        fixture = Path(__file__).parent.parent / "fixtures" / "working" / "SmokeTest_R1.kicad_sch"
        doc = SchematicDoc.load(fixture)
        assert doc.root.key == "kicad_sch"

    def test_load_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ParseError, OSError, FileNotFoundError)):
            SchematicDoc.load(tmp_path / "nonexistent.kicad_sch")

    def test_load_malformed_file_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.kicad_sch"
        p.write_text("(unclosed")
        with pytest.raises(ParseError):
            SchematicDoc.load(p)


# ---------------------------------------------------------------------------
# ensure_lib_symbols_section
# ---------------------------------------------------------------------------


class TestEnsureLibSymbolsSection:
    def test_adds_section_when_absent(self) -> None:
        doc = _doc_from(SCH_NO_LIB_SYMBOLS)
        assert find_first(doc.root, "lib_symbols") is None
        doc.ensure_lib_symbols_section()
        assert find_first(doc.root, "lib_symbols") is not None

    def test_does_not_duplicate_when_present(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.ensure_lib_symbols_section()
        doc.ensure_lib_symbols_section()  # second call should be idempotent
        count = sum(
            1 for item in doc.root.items if hasattr(item, "key") and item.key == "lib_symbols"
        )
        assert count == 1


# ---------------------------------------------------------------------------
# embed_lib_symbol
# ---------------------------------------------------------------------------


class TestEmbedLibSymbol:
    def test_embeds_symbol(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        sym_def = parse('(symbol "Device:R" (pin_numbers hide) (in_bom yes))')
        doc.embed_lib_symbol(sym_def)
        ls = find_first(doc.root, "lib_symbols")
        assert ls is not None
        syms = find_all(ls, "symbol")
        assert len(syms) == 1
        assert syms[0].items[1].value == "Device:R"  # type: ignore[union-attr]

    def test_embed_is_idempotent(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        sym_def = parse('(symbol "Device:R" (in_bom yes))')
        doc.embed_lib_symbol(sym_def)
        doc.embed_lib_symbol(sym_def)  # second embed should be a no-op
        ls = find_first(doc.root, "lib_symbols")
        assert ls is not None
        syms = find_all(ls, "symbol")
        assert len(syms) == 1

    def test_returns_true_on_success(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        sym_def = parse('(symbol "Device:C" (in_bom yes))')
        assert doc.embed_lib_symbol(sym_def) is True

    def test_returns_true_when_already_embedded(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        sym_def = parse('(symbol "Device:R" (in_bom yes))')
        doc.embed_lib_symbol(sym_def)
        assert doc.embed_lib_symbol(sym_def) is True

    def test_returns_false_for_missing_id(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        # A ListNode without a StringNode as second item
        bad_node = parse("(symbol 123)")
        assert doc.embed_lib_symbol(bad_node) is False

    def test_creates_lib_symbols_if_missing(self) -> None:
        doc = _doc_from(SCH_NO_LIB_SYMBOLS)
        sym_def = parse('(symbol "Device:R" (in_bom yes))')
        result = doc.embed_lib_symbol(sym_def)
        assert result is True
        assert find_first(doc.root, "lib_symbols") is not None


# ---------------------------------------------------------------------------
# next_component_position
# ---------------------------------------------------------------------------


class TestNextComponentPosition:
    def test_default_when_empty(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        x, y = doc.next_component_position()
        assert x == pytest.approx(50.8)
        assert y == pytest.approx(76.2)

    def test_steps_right_of_existing_symbol(self) -> None:
        doc = _doc_from(SCH_WITH_SYMBOL)
        x, y = doc.next_component_position()
        # Existing symbol is at x=50.80, so next should be 50.80 + 25.4
        assert x == pytest.approx(50.8 + 25.4)
        assert y == pytest.approx(76.2)

    def test_uses_max_x_of_multiple_symbols(self) -> None:
        sch = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 50.80 76.20 0) (unit 1) (uuid "s1"))
  (symbol (lib_id "Device:C") (at 100.00 76.20 0) (unit 1) (uuid "s2"))
  (sheet_instances (path "/" (page "1")))
)
"""
        doc = _doc_from(sch)
        x, _ = doc.next_component_position()
        assert x == pytest.approx(100.0 + 25.4)

    def test_ignores_non_zero_rotation(self) -> None:
        # (at X Y 90) — angle is not 0, should be skipped
        sch = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (symbol (lib_id "Device:R") (at 80.00 76.20 90) (unit 1) (uuid "s1"))
  (sheet_instances (path "/" (page "1")))
)
"""
        doc = _doc_from(sch)
        x, _ = doc.next_component_position()
        # Rotated symbol excluded → falls back to default
        assert x == pytest.approx(50.8)


# ---------------------------------------------------------------------------
# list_symbols
# ---------------------------------------------------------------------------


class TestListSymbols:
    def test_returns_symbol_metadata(self) -> None:
        doc = _doc_from(SCH_WITH_SYMBOL)

        symbols = doc.list_symbols()

        assert len(symbols) == 1
        assert symbols[0]["ref"] == "R1"
        assert symbols[0]["x"] == pytest.approx(50.8)
        assert symbols[0]["y"] == pytest.approx(76.2)

    def test_raises_parse_error_for_malformed_symbol_at_coordinate(self) -> None:
        malformed = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (symbol (lib_id "Device:R") (at not-a-number 76.20 0) (unit 1)
    (uuid "sym-uuid-1")
    (property "Reference" "R1" (at 52.07 74.93 0))
    (property "Value" "10k" (at 52.07 77.47 0))
  )
  (sheet_instances (path "/" (page "1")))
)
"""
        doc = _doc_from(malformed)

        with pytest.raises(ParseError, match="Malformed symbol"):
            doc.list_symbols()


# ---------------------------------------------------------------------------
# add_symbol
# ---------------------------------------------------------------------------


class TestAddSymbol:
    def test_symbol_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "sym-uuid",
            ["1", "2"],
            ["p1", "p2"],
            "test_proj",
        )
        syms = find_all(doc.root, "symbol")
        assert len(syms) == 1
        assert syms[0].key == "symbol"

    def test_symbol_inserted_before_sheet_instances(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "sym-uuid",
            ["1", "2"],
            ["p1", "p2"],
            "proj",
        )
        items = doc.root.items
        sym_idx = next(i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "symbol")
        si_idx = next(
            i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "sheet_instances"
        )
        assert sym_idx < si_idx

    def test_symbol_has_correct_lib_id(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol(
            "Device:C",
            "C1",
            "100n",
            "Capacitor_SMD:C_0402",
            30.0,
            50.0,
            "u1",
            ["1", "2"],
            ["p1", "p2"],
            "p",
        )
        syms = find_all(doc.root, "symbol")
        lib_id = find_first(syms[0], "lib_id")
        assert lib_id is not None
        assert lib_id.items[1].value == "Device:C"  # type: ignore[union-attr]

    def test_symbol_has_reference_property(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol("Device:R", "R42", "1k", "", 50.8, 76.2, "u1", ["1", "2"], ["p1", "p2"], "p")
        syms = find_all(doc.root, "symbol")
        props = find_all(syms[0], "property")
        refs = [p for p in props if p.items[1].value == "Reference"]  # type: ignore[union-attr]
        assert refs and refs[0].items[2].value == "R42"  # type: ignore[union-attr]

    def test_symbol_has_pins(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol("Device:R", "R1", "10k", "", 50.8, 76.2, "u1", ["1", "2"], ["p1", "p2"], "p")
        syms = find_all(doc.root, "symbol")
        pins = find_all(syms[0], "pin")
        assert len(pins) == 2

    def test_symbol_serializable(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_symbol(
            "Device:R", "R1", "10k", "", 50.8, 76.2, "u1", ["1", "2"], ["p1", "p2"], "proj"
        )
        text = serialize(doc.root)
        # Round-trip
        doc2 = SchematicDoc(parse(text))
        assert len(find_all(doc2.root, "symbol")) == 1


# ---------------------------------------------------------------------------
# add_wire
# ---------------------------------------------------------------------------
