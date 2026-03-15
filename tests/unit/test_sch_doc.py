"""Unit tests for kicad_pcb.sch_doc — SchematicDoc and library helpers.

Tests use in-memory string fixtures and temporary directories; no system
KiCad installation is required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from kicad_pcb.errors import ErrorCode, ParseError, UserError
from kicad_pcb.sch_doc import (
    SchematicDoc,
    make_label_node,
    make_symbol_node,
    make_wire_node,
    read_lib_symbol_def,
    read_lib_symbol_def_chain,
    read_lib_symbol_pins,
)
from kicad_pcb.sexpr import find_all, find_first, parse, serialize
from kicad_pcb.sexpr.nodes import ListNode, StringNode

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


class TestAddWire:
    def test_wire_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        wires = find_all(doc.root, "wire")
        assert len(wires) == 1

    def test_wire_inserted_before_sheet_instances(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        items = doc.root.items
        w_idx = next(i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "wire")
        si_idx = next(
            i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "sheet_instances"
        )
        assert w_idx < si_idx

    def test_wire_has_pts(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(10.0, 20.0, 30.0, 40.0, "w1")
        wires = find_all(doc.root, "wire")
        pts = find_first(wires[0], "pts")
        assert pts is not None
        xys = find_all(pts, "xy")
        assert len(xys) == 2

    def test_wire_has_uuid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(0.0, 0.0, 10.0, 0.0, "my-uuid")
        wires = find_all(doc.root, "wire")
        uuid_node = find_first(wires[0], "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "my-uuid"  # type: ignore[union-attr]

    def test_multiple_wires(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(0.0, 0.0, 10.0, 0.0, "w1")
        doc.add_wire(10.0, 0.0, 20.0, 0.0, "w2")
        assert len(find_all(doc.root, "wire")) == 2


# ---------------------------------------------------------------------------
# add_label
# ---------------------------------------------------------------------------


class TestAddLabel:
    def test_label_added_to_root(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        assert len(labels) == 1

    def test_label_inserted_before_sheet_instances(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("GND", 60.0, 50.0, "lbl1")
        items = doc.root.items
        l_idx = next(i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "label")
        si_idx = next(
            i for i, it in enumerate(items) if hasattr(it, "key") and it.key == "sheet_instances"
        )
        assert l_idx < si_idx

    def test_label_has_correct_name(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        assert isinstance(labels[0].items[1], StringNode)
        assert labels[0].items[1].value == "VCC"

    def test_label_has_uuid(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("NET1", 0.0, 0.0, "lbl-uuid-99")
        labels = find_all(doc.root, "label")
        uuid_node = find_first(labels[0], "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "lbl-uuid-99"  # type: ignore[union-attr]

    def test_label_has_intersheet_property(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_label("VCC", 60.0, 50.0, "lbl1")
        labels = find_all(doc.root, "label")
        props = find_all(labels[0], "property")
        assert any(p.items[1].value == "Intersheet References" for p in props)  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# extract_pin_label_bindings
# ---------------------------------------------------------------------------


class TestExtractPinLabelBindings:
    def test_returns_bindings_from_deterministic_markers(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_text(
            'OpenClaw:bind={"ref":"R1","pin":"1","net_name":"N1"}',
            -1200.0,
            -1500.0,
            hidden=True,
        )
        doc.add_text(
            'OpenClaw:bind={"ref":"R2","pin":"2","net_name":"N2"}',
            -1200.0,
            -1510.0,
            hidden=True,
        )

        bindings = doc.extract_pin_label_bindings()

        assert bindings == [
            {"ref": "R1", "pin": "1", "net_name": "N1"},
            {"ref": "R2", "pin": "2", "net_name": "N2"},
        ]

    def test_ignores_malformed_markers(self) -> None:
        doc = _doc_from(MINIMAL_SCH)
        doc.add_text("OpenClaw:bind=not-json", -1200.0, -1500.0, hidden=True)
        doc.add_text('OpenClaw:bind={"ref":"R1"}', -1200.0, -1510.0, hidden=True)

        assert doc.extract_pin_label_bindings() == []


# ---------------------------------------------------------------------------
# save / round-trip
# ---------------------------------------------------------------------------


class TestSchematicDocSave:
    def test_save_and_reload(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        doc = _doc_from(MINIMAL_SCH)
        doc.add_wire(50.8, 76.2, 76.2, 76.2, "w1")
        doc.save(out)
        assert out.exists()
        doc2 = SchematicDoc.load(out)
        assert len(find_all(doc2.root, "wire")) == 1

    def test_save_creates_valid_sexp(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out)
        text = out.read_text()
        assert text.startswith("(kicad_sch")

    def test_save_backup_creates_bak(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        out.write_text(MINIMAL_SCH)
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out, backup=True)
        assert (tmp_path / "test.kicad_sch.bak").exists()

    def test_save_no_backup_by_default(self, tmp_path: Path) -> None:
        out = tmp_path / "test.kicad_sch"
        out.write_text(MINIMAL_SCH)
        doc = _doc_from(MINIMAL_SCH)
        doc.save(out)
        assert not (tmp_path / "test.kicad_sch.bak").exists()


# ---------------------------------------------------------------------------
# make_symbol_node / make_wire_node / make_label_node (standalone)
# ---------------------------------------------------------------------------


class TestMakeSymbolNode:
    def test_key_is_symbol(self) -> None:
        node = make_symbol_node(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "uid",
            ["1", "2"],
            ["p1", "p2"],
            "proj",
        )
        assert node.key == "symbol"

    def test_contains_lib_id(self) -> None:
        node = make_symbol_node(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "uid",
            ["1", "2"],
            ["p1", "p2"],
            "proj",
        )
        lib_id = find_first(node, "lib_id")
        assert lib_id is not None
        assert lib_id.items[1].value == "Device:R"  # type: ignore[union-attr]

    def test_contains_uuid(self) -> None:
        node = make_symbol_node(
            "Device:R",
            "R1",
            "10k",
            "",
            0.0,
            0.0,
            "my-uuid-1",
            ["1"],
            ["pu1"],
            "p",
        )
        uuid_node = find_first(node, "uuid")
        assert uuid_node is not None
        assert uuid_node.items[1].value == "my-uuid-1"  # type: ignore[union-attr]

    def test_contains_instances_section(self) -> None:
        node = make_symbol_node(
            "Device:C",
            "C1",
            "100n",
            "",
            0.0,
            0.0,
            "u",
            [],
            [],
            "myproject",
        )
        inst = find_first(node, "instances")
        assert inst is not None
        proj = find_first(inst, "project")
        assert proj is not None
        assert proj.items[1].value == "myproject"  # type: ignore[union-attr]

    def test_places_reference_and_value_with_vertical_clearance_at_zero_rotation(self) -> None:
        node = make_symbol_node(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "uid",
            ["1", "2"],
            ["p1", "p2"],
            "proj",
            rotation=0,
        )

        properties = [
            item for item in node.items if isinstance(item, ListNode) and item.key == "property"
        ]
        reference = next(prop for prop in properties if prop.items[1].value == "Reference")  # type: ignore[union-attr]
        value = next(prop for prop in properties if prop.items[1].value == "Value")  # type: ignore[union-attr]
        reference_at = find_first(reference, "at")
        value_at = find_first(value, "at")

        assert reference_at is not None
        assert value_at is not None
        assert reference_at.items[1].value == "50.80"  # type: ignore[union-attr]
        assert reference_at.items[2].value == "69.85"  # type: ignore[union-attr]
        assert value_at.items[1].value == "50.80"  # type: ignore[union-attr]
        assert value_at.items[2].value == "82.55"  # type: ignore[union-attr]

    def test_places_reference_and_value_with_horizontal_clearance_at_ninety_rotation(self) -> None:
        node = make_symbol_node(
            "Device:R",
            "R1",
            "10k",
            "",
            50.8,
            76.2,
            "uid",
            ["1", "2"],
            ["p1", "p2"],
            "proj",
            rotation=90,
        )

        properties = [
            item for item in node.items if isinstance(item, ListNode) and item.key == "property"
        ]
        reference = next(prop for prop in properties if prop.items[1].value == "Reference")  # type: ignore[union-attr]
        value = next(prop for prop in properties if prop.items[1].value == "Value")  # type: ignore[union-attr]
        reference_at = find_first(reference, "at")
        value_at = find_first(value, "at")

        assert reference_at is not None
        assert value_at is not None
        assert reference_at.items[1].value == "44.45"  # type: ignore[union-attr]
        assert reference_at.items[2].value == "76.20"  # type: ignore[union-attr]
        assert value_at.items[1].value == "57.15"  # type: ignore[union-attr]
        assert value_at.items[2].value == "76.20"  # type: ignore[union-attr]


class TestMakeWireNode:
    def test_key_is_wire(self) -> None:
        assert make_wire_node(0.0, 0.0, 10.0, 0.0, "u").key == "wire"

    def test_coords_in_pts(self) -> None:
        node = make_wire_node(1.0, 2.0, 3.0, 4.0, "u")
        pts = find_first(node, "pts")
        assert pts is not None
        xys = find_all(pts, "xy")
        assert len(xys) == 2
        assert xys[0].items[1].value == "1.00"  # type: ignore[union-attr]
        assert xys[0].items[2].value == "2.00"  # type: ignore[union-attr]


class TestMakeLabelNode:
    def test_key_is_label(self) -> None:
        assert make_label_node("VCC", 0.0, 0.0, "u").key == "label"

    def test_name_is_second_item(self) -> None:
        node = make_label_node("GND", 0.0, 0.0, "u")
        assert isinstance(node.items[1], StringNode)
        assert node.items[1].value == "GND"


# ---------------------------------------------------------------------------
# read_lib_symbol_def
# ---------------------------------------------------------------------------


class TestReadLibSymbolDef:
    def test_returns_none_for_missing_dir(self, tmp_path: Path) -> None:
        result = read_lib_symbol_def("Device", "R", symbols_dir=tmp_path)
        assert result is None

    def test_returns_none_for_missing_symbol(self, tmp_path: Path) -> None:
        lib_file = tmp_path / "Device.kicad_sym"
        lib_file.write_text(MINIMAL_LIB_SYM)
        result = read_lib_symbol_def("Device", "Nonexistent", symbols_dir=tmp_path)
        assert result is None

    def test_returns_list_node_for_found_symbol(self, tmp_path: Path) -> None:
        lib_file = tmp_path / "Device.kicad_sym"
        lib_file.write_text(MINIMAL_LIB_SYM)
        result = read_lib_symbol_def("Device", "R", symbols_dir=tmp_path)
        assert result is not None
        assert result.key == "symbol"

    def test_renames_root_symbol_to_full_id(self, tmp_path: Path) -> None:
        lib_file = tmp_path / "Device.kicad_sym"
        lib_file.write_text(MINIMAL_LIB_SYM)
        result = read_lib_symbol_def("Device", "R", symbols_dir=tmp_path)
        assert result is not None
        assert isinstance(result.items[1], StringNode)
        assert result.items[1].value == "Device:R"

    def test_strips_id_children(self, tmp_path: Path) -> None:
        lib_file = tmp_path / "Device.kicad_sym"
        lib_file.write_text(MINIMAL_LIB_SYM_WITH_ID)
        result = read_lib_symbol_def("Device", "R", symbols_dir=tmp_path)
        assert result is not None
        # No (id N) child should remain
        id_nodes = find_all(result, "id")
        assert len(id_nodes) == 0

    def test_sub_symbols_keep_short_names(self, tmp_path: Path) -> None:
        lib_file = tmp_path / "Device.kicad_sym"
        lib_file.write_text(MINIMAL_LIB_SYM)
        result = read_lib_symbol_def("Device", "R", symbols_dir=tmp_path)
        assert result is not None
        sub_syms = find_all(result, "symbol")
        # Sub-symbol "R_1_1" must keep short name, not be renamed to "Device:R_1_1"
        for ss in sub_syms:
            assert isinstance(ss.items[1], StringNode)
            assert not ss.items[1].value.startswith("Device:")


# ---------------------------------------------------------------------------
# read_lib_symbol_pins
# ---------------------------------------------------------------------------


class TestReadLibSymbolPins:
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        result = read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)
        assert result == []

    def test_returns_empty_for_missing_symbol(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        assert read_lib_symbol_pins("Device", "X", symbols_dir=tmp_path) == []

    def test_returns_pin_numbers(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        pins = read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)
        assert pins == ["1", "2"]

    def test_pin_numbers_are_deduplicated(self, tmp_path: Path) -> None:
        # Library file with duplicate pin "1"
        dup_lib = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "R"
    (symbol "R_1_1"
      (pin passive passive (at 0 2.54 270) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
    (symbol "R_1_2"
      (pin passive passive (at 0 -2.54 90) (length 1.27)
        (name "~" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))
      )
    )
  )
)
"""
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(dup_lib)
        pins = read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)
        assert pins == ["1"]

    def test_pins_from_sub_symbols(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        pins = read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)
        # Pins are inside (symbol "R_1_1") sub-symbol
        assert "1" in pins
        assert "2" in pins

    def test_raises_parse_error_for_malformed_library(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text("(kicad_symbol_lib (version 20230121)")

        with pytest.raises(ParseError, match="Failed to parse symbol library"):
            read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)

    def test_raises_user_error_for_library_read_failure(self, tmp_path: Path, monkeypatch) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)

        def _raise_oserror(path: Path):
            raise OSError(f"permission denied: {path}")

        monkeypatch.setattr("kicad_pcb.lib_symbol._parse_lib_file", _raise_oserror)

        with pytest.raises(UserError) as exc_info:
            read_lib_symbol_pins("Device", "R", symbols_dir=tmp_path)

        assert exc_info.value.code == ErrorCode.IO_ERROR


# ---------------------------------------------------------------------------
# Extends-chain fixtures
# ---------------------------------------------------------------------------

LIB_WITH_EXTENDS = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "BaseChip"
    (pin power_in line
      (at 0 2.54 270) (length 1.27)
      (name "VCC" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27))))
    )
    (pin passive line
      (at 0 -2.54 90) (length 1.27)
      (name "GND" (effects (font (size 1.27 1.27))))
      (number "2" (effects (font (size 1.27 1.27))))
    )
    (pin output line
      (at 5.08 0 180) (length 1.27)
      (name "OUT" (effects (font (size 1.27 1.27))))
      (number "3" (effects (font (size 1.27 1.27))))
    )
  )
  (symbol "DerivedChip" (extends "BaseChip")
    (property "Reference" "U" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "DerivedChip" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
"""


# ---------------------------------------------------------------------------
# read_lib_symbol_def_chain
# ---------------------------------------------------------------------------


class TestReadLibSymbolDefChain:
    def test_returns_empty_for_missing_dir(self, tmp_path: Path) -> None:
        result = read_lib_symbol_def_chain("Lib", "R", symbols_dir=tmp_path)
        assert result == []

    def test_returns_empty_for_missing_symbol(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        assert read_lib_symbol_def_chain("Device", "Nonexistent", symbols_dir=tmp_path) == []

    def test_non_extends_symbol_returns_single_node(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        chain = read_lib_symbol_def_chain("Device", "R", symbols_dir=tmp_path)
        assert len(chain) == 1
        assert chain[0].key == "symbol"

    def test_non_extends_node_has_qualified_id(self, tmp_path: Path) -> None:
        lib = tmp_path / "Device.kicad_sym"
        lib.write_text(MINIMAL_LIB_SYM)
        chain = read_lib_symbol_def_chain("Device", "R", symbols_dir=tmp_path)

        assert isinstance(chain[0].items[1], StringNode)
        assert chain[0].items[1].value == "Device:R"

    def test_extends_symbol_returns_two_nodes(self, tmp_path: Path) -> None:
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        chain = read_lib_symbol_def_chain("MyLib", "DerivedChip", symbols_dir=tmp_path)
        assert len(chain) == 2

    def test_extends_chain_is_base_first(self, tmp_path: Path) -> None:
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        chain = read_lib_symbol_def_chain("MyLib", "DerivedChip", symbols_dir=tmp_path)

        assert isinstance(chain[0].items[1], StringNode)
        assert isinstance(chain[1].items[1], StringNode)
        assert chain[0].items[1].value == "MyLib:BaseChip"
        assert chain[1].items[1].value == "MyLib:DerivedChip"

    def test_extends_reference_is_qualified_in_derived_node(self, tmp_path: Path) -> None:
        """Derived node must use 'Lib:BaseName' in (extends ...) for KiCad."""
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        chain = read_lib_symbol_def_chain("MyLib", "DerivedChip", symbols_dir=tmp_path)
        derived = chain[1]

        extends_node = find_first(derived, "extends")
        assert extends_node is not None
        assert isinstance(extends_node.items[1], StringNode)
        assert extends_node.items[1].value == "MyLib:BaseChip"

    def test_broken_extends_chain_returns_empty(self, tmp_path: Path) -> None:
        """extends pointing to a non-existent base must return empty, not partial."""
        broken_lib = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "BrokenChild" (extends "MissingBase")
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
  )
)
"""
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(broken_lib)
        result = read_lib_symbol_def_chain("MyLib", "BrokenChild", symbols_dir=tmp_path)
        assert result == []

    def test_all_nodes_strip_id_children(self, tmp_path: Path) -> None:
        lib_with_id = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Base"
    (id 0)
    (pin passive line (at 0 0 0) (length 1.27)
      (name "A" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27))))
    )
  )
  (symbol "Child" (extends "Base")
    (id 1)
    (property "Reference" "U" (at 0 0 0) (effects (font (size 1.27 1.27))))
  )
)
"""
        lib = tmp_path / "Lib.kicad_sym"
        lib.write_text(lib_with_id)
        chain = read_lib_symbol_def_chain("Lib", "Child", symbols_dir=tmp_path)
        assert len(chain) == 2
        for node in chain:
            id_nodes = find_all(node, "id")
            assert id_nodes == [], f"(id N) node not stripped from {node.items[1]}"

    def test_embed_chain_embeds_base_and_derived(self, tmp_path: Path) -> None:
        """Embedding the full chain means lib_symbols contains both nodes."""
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        doc = SchematicDoc(
            parse(
                "(kicad_sch (version 20230121) (generator test)"
                " (lib_symbols)"
                ' (sheet_instances (path "/" (page "1"))))'
            )
        )
        chain = read_lib_symbol_def_chain("MyLib", "DerivedChip", symbols_dir=tmp_path)
        for sym_def in chain:
            doc.embed_lib_symbol(sym_def)

        lib_symbols = find_first(doc.root, "lib_symbols")
        assert lib_symbols is not None
        ids = [
            item.items[1].value  # type: ignore[union-attr]
            for item in lib_symbols.items
            if hasattr(item, "key") and item.key == "symbol"  # type: ignore[union-attr]
        ]
        assert "MyLib:BaseChip" in ids
        assert "MyLib:DerivedChip" in ids


# ---------------------------------------------------------------------------
# read_lib_symbol_pins — extends chain
# ---------------------------------------------------------------------------


class TestReadLibSymbolPinsExtendsChain:
    def test_extends_symbol_inherits_pins_from_base(self, tmp_path: Path) -> None:
        """DerivedChip has no pins of its own; all pins live on BaseChip."""
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        pins = read_lib_symbol_pins("MyLib", "DerivedChip", symbols_dir=tmp_path)
        assert sorted(pins) == ["1", "2", "3"]

    def test_non_extends_symbol_pins_unchanged(self, tmp_path: Path) -> None:
        lib = tmp_path / "MyLib.kicad_sym"
        lib.write_text(LIB_WITH_EXTENDS)
        pins = read_lib_symbol_pins("MyLib", "BaseChip", symbols_dir=tmp_path)
        assert sorted(pins) == ["1", "2", "3"]

    def test_extends_deduplicates_pins(self, tmp_path: Path) -> None:
        """If derived redefines a pin from base, it must appear only once."""
        lib_text = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Base"
    (pin passive line (at 0 0 0) (length 1.27)
      (name "A" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27))))
    )
  )
  (symbol "Child" (extends "Base")
    (pin passive line (at 0 -2.54 0) (length 1.27)
      (name "A_extra" (effects (font (size 1.27 1.27))))
      (number "1" (effects (font (size 1.27 1.27))))
    )
  )
)
"""
        lib = tmp_path / "Lib.kicad_sym"
        lib.write_text(lib_text)
        pins = read_lib_symbol_pins("Lib", "Child", symbols_dir=tmp_path)
        # Pin "1" appears in both base and derived — must be deduplicated.
        assert pins.count("1") == 1

    def test_broken_extends_returns_partial_pins(self, tmp_path: Path) -> None:
        """When base is missing, pins from derived node itself are still returned."""
        lib_text = """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Child" (extends "MissingBase")
    (pin passive line (at 0 0 0) (length 1.27)
      (name "A" (effects (font (size 1.27 1.27))))
      (number "5" (effects (font (size 1.27 1.27))))
    )
  )
)
"""
        lib = tmp_path / "Lib.kicad_sym"
        lib.write_text(lib_text)
        # Chain walk stops when MissingBase not found, but "Child" is in chain.
        pins = read_lib_symbol_pins("Lib", "Child", symbols_dir=tmp_path)
        assert "5" in pins

    def test_fixture_file_derived_opamp(self) -> None:
        """DerivedOpAmp in TestLib fixture inherits pins 1, 2, 3, 6 from OpAmp."""
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "symbols"
        pins = read_lib_symbol_pins("TestLib", "DerivedOpAmp", symbols_dir=fixture_dir)
        assert sorted(pins) == ["1", "2", "3", "6"]
