"""Unit tests for sch_doc library symbol readers — pins, def chain, and extends."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import ErrorCode, ParseError, UserError
from kicad_pcb.sch_doc import (
    SchematicDoc,
    read_lib_symbol_def_chain,
    read_lib_symbol_pins,
    read_lib_symbol_power_unit,
    read_lib_symbol_unit_pin_at,
    read_lib_symbol_unit_pins,
)
from kicad_pcb.sexpr import find_all, find_first, parse
from kicad_pcb.sexpr.nodes import StringNode

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

        monkeypatch.setattr("kicad_pcb._lib_symbol_primitives._parse_lib_file", _raise_oserror)

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


class TestReadLibSymbolUnitPins:
    def test_extracts_unit_pin_groups_from_fixture_dual_op_amp(self) -> None:
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "symbols"

        assert read_lib_symbol_unit_pins("TestLib", "DualOpAmp", symbols_dir=fixture_dir) == {
            "1": ["1", "2", "3"],
            "2": ["5", "6", "7"],
            "3": ["4", "8"],
        }


class TestReadLibSymbolUnitPinAt:
    def test_extracts_unit_local_pin_geometry_from_fixture_dual_op_amp(self) -> None:
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "symbols"

        assert read_lib_symbol_unit_pin_at("TestLib", "DualOpAmp", symbols_dir=fixture_dir) == {
            "1": {
                "1": (0.0, 0.0, 0.0),
                "2": (0.0, -2.54, 0.0),
                "3": (5.08, -1.27, 180.0),
            },
            "2": {
                "5": (0.0, 0.0, 0.0),
                "6": (0.0, -2.54, 0.0),
                "7": (5.08, -1.27, 180.0),
            },
            "3": {
                "4": (2.54, 2.54, 270.0),
                "8": (2.54, -5.08, 90.0),
            },
        }


class TestReadLibSymbolPowerUnit:
    def test_detects_dedicated_power_unit_from_fixture_dual_op_amp(self) -> None:
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "symbols"

        assert read_lib_symbol_power_unit("TestLib", "DualOpAmp", symbols_dir=fixture_dir) == "3"

    def test_returns_none_when_symbol_has_no_dedicated_power_unit(self) -> None:
        fixture_dir = Path(__file__).parent.parent / "fixtures" / "symbols"

        assert read_lib_symbol_power_unit("TestLib", "SingleOpAmp", symbols_dir=fixture_dir) is None
