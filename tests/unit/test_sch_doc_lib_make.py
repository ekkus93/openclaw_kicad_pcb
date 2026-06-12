"""Unit tests for sch_doc node factories and library symbol readers."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.sch_doc import (
    make_label_node,
    make_power_symbol_node,
    make_symbol_node,
    make_wire_node,
    read_lib_symbol_def,
)
from kicad_pcb.sexpr import find_all, find_first
from kicad_pcb.sexpr.nodes import ListNode, StringNode

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

    def test_writes_explicit_unit_to_symbol_and_instance_path(self) -> None:
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
            unit=2,
        )

        unit_node = find_first(node, "unit")
        assert unit_node is not None
        assert unit_node.items[1].value == "2"  # type: ignore[union-attr]

        instances = find_first(node, "instances")
        assert instances is not None
        project_node = find_first(instances, "project")
        assert project_node is not None
        path_node = find_first(project_node, "path")
        assert path_node is not None
        nested_unit = next(
            child
            for child in path_node.items
            if isinstance(child, ListNode) and child.key == "unit"
        )
        assert nested_unit.items[1].value == "2"  # type: ignore[union-attr]

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


class TestMakePowerSymbolNode:
    def test_places_value_along_upward_facing_angle(self) -> None:
        node = make_power_symbol_node(
            "power:GND",
            "GND",
            "#PWR01",
            50.8,
            76.2,
            "sym-uuid",
            "pin-uuid",
            "proj",
            angle=270,
        )

        properties = [
            item for item in node.items if isinstance(item, ListNode) and item.key == "property"
        ]
        value = next(prop for prop in properties if prop.items[1].value == "Value")  # type: ignore[union-attr]
        value_at = find_first(value, "at")

        assert value_at is not None
        assert value_at.items[1].value == "50.80"  # type: ignore[union-attr]
        assert value_at.items[2].value == "69.85"  # type: ignore[union-attr]

    def test_places_value_along_left_facing_angle(self) -> None:
        node = make_power_symbol_node(
            "power:GND",
            "GND",
            "#PWR02",
            50.8,
            76.2,
            "sym-uuid",
            "pin-uuid",
            "proj",
            angle=180,
        )

        properties = [
            item for item in node.items if isinstance(item, ListNode) and item.key == "property"
        ]
        value = next(prop for prop in properties if prop.items[1].value == "Value")  # type: ignore[union-attr]
        value_at = find_first(value, "at")

        assert value_at is not None
        assert value_at.items[1].value == "44.45"  # type: ignore[union-attr]
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
