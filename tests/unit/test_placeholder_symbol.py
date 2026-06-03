"""Unit tests for kicad_pcb.placeholder_symbol."""

from __future__ import annotations

import pytest

from kicad_pcb.placeholder_symbol import (
    PlaceholderSymbol,
    _split_pins,
    build,
)
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_symbol_id(node: ListNode) -> str:
    assert node.key == "symbol"
    id_node = node.items[1]
    assert isinstance(id_node, StringNode)
    return id_node.value


def _get_property(node: ListNode, name: str) -> str | None:
    for item in node.items:
        if not isinstance(item, ListNode) or item.key != "property":
            continue
        name_node = item.items[1] if len(item.items) >= 3 else None
        if not isinstance(name_node, StringNode) or name_node.value != name:
            continue
        val = item.items[2]
        if isinstance(val, StringNode):
            return val.value
    return None


def _get_sub_symbol(defn: ListNode) -> ListNode:
    for item in defn.items:
        if isinstance(item, ListNode) and item.key == "symbol":
            return item
    raise AssertionError("No sub-symbol found in definition")


def _get_pins(sub_sym: ListNode) -> list[ListNode]:
    return [item for item in sub_sym.items if isinstance(item, ListNode) and item.key == "pin"]


def _get_rectangle(sub_sym: ListNode) -> ListNode:
    for item in sub_sym.items:
        if isinstance(item, ListNode) and item.key == "rectangle":
            return item
    raise AssertionError("No rectangle in sub-symbol")


def _pin_number(pin_node: ListNode) -> str:
    for item in pin_node.items:
        if isinstance(item, ListNode) and item.key == "number":
            num = item.items[1]
            assert isinstance(num, StringNode)
            return num.value
    raise AssertionError("No number node in pin")


def _pin_at(pin_node: ListNode) -> tuple[float, float, float]:
    for item in pin_node.items:
        if isinstance(item, ListNode) and item.key == "at":
            x = float(item.items[1].value)  # type: ignore[union-attr]
            y = float(item.items[2].value)  # type: ignore[union-attr]
            angle = float(item.items[3].value)  # type: ignore[union-attr]
            return (x, y, angle)
    raise AssertionError("No at node in pin")


# ---------------------------------------------------------------------------
# _split_pins
# ---------------------------------------------------------------------------


def test_split_pins_single() -> None:
    left, right = _split_pins(["1"])
    assert left == ["1"]
    assert right == []


def test_split_pins_two() -> None:
    left, right = _split_pins(["1", "2"])
    assert left == ["1"]
    assert right == ["2"]


def test_split_pins_odd() -> None:
    left, right = _split_pins(["1", "2", "3", "4", "5"])
    assert len(left) == 3
    assert len(right) == 2
    assert left + right == ["1", "2", "3", "4", "5"]


def test_split_pins_even() -> None:
    left, right = _split_pins(["1", "2", "3", "4", "5", "6"])
    assert len(left) == 3
    assert len(right) == 3


# ---------------------------------------------------------------------------
# build: basic structure
# ---------------------------------------------------------------------------


def test_build_returns_placeholder_symbol() -> None:
    ph = build("TestLib:TestPart", frozenset({"1", "2"}))
    assert isinstance(ph, PlaceholderSymbol)
    assert ph.symbol_id == "TestLib:TestPart"
    assert ph.pin_numbers == frozenset({"1", "2"})


def test_build_raises_on_empty_pins() -> None:
    with pytest.raises(ValueError, match="no pins"):
        build("Lib:Part", frozenset())


def test_build_symbol_id_preserved_in_definition() -> None:
    ph = build("74xx:74HC4017", frozenset({"1", "3", "7", "14"}))
    assert _get_symbol_id(ph.definition) == "74xx:74HC4017"


def test_build_value_contains_placeholder_marker() -> None:
    ph = build("MyLib:MyPart", frozenset({"1", "2", "3"}))
    value = _get_property(ph.definition, "Value")
    assert value is not None
    assert "[PLACEHOLDER]" in value


def test_build_reference_property_set() -> None:
    ph = build("74xx:74HC4017", frozenset({"1"}))
    ref = _get_property(ph.definition, "Reference")
    assert ref is not None
    assert len(ref) >= 1


def test_build_single_pin_left_only() -> None:
    ph = build("Lib:Part", frozenset({"5"}))
    sub = _get_sub_symbol(ph.definition)
    pins = _get_pins(sub)
    assert len(pins) == 1
    _x, _y, angle = _pin_at(pins[0])
    assert angle == 0.0  # left-side pin


def test_build_two_pins_one_each_side() -> None:
    ph = build("Lib:Part", frozenset({"1", "2"}))
    sub = _get_sub_symbol(ph.definition)
    pins = _get_pins(sub)
    assert len(pins) == 2
    angles = {_pin_at(p)[2] for p in pins}
    assert 0.0 in angles  # left
    assert 180.0 in angles  # right


def test_build_even_pins_split_equally() -> None:
    ph = build("Lib:Part", frozenset({"1", "2", "3", "4", "5", "6"}))
    sub = _get_sub_symbol(ph.definition)
    pins = _get_pins(sub)
    assert len(pins) == 6
    left = [p for p in pins if _pin_at(p)[2] == 0.0]
    right = [p for p in pins if _pin_at(p)[2] == 180.0]
    assert len(left) == 3
    assert len(right) == 3


def test_build_odd_pins_extra_on_left() -> None:
    ph = build("Lib:Part", frozenset({"1", "2", "3", "4", "5"}))
    sub = _get_sub_symbol(ph.definition)
    pins = _get_pins(sub)
    left = [p for p in pins if _pin_at(p)[2] == 0.0]
    right = [p for p in pins if _pin_at(p)[2] == 180.0]
    assert len(left) == 3
    assert len(right) == 2


# ---------------------------------------------------------------------------
# build: dashed border
# ---------------------------------------------------------------------------


def test_build_dashed_border() -> None:
    ph = build("Lib:Part", frozenset({"1", "2"}))
    sub = _get_sub_symbol(ph.definition)
    rect = _get_rectangle(sub)
    stroke = next(
        item for item in rect.items if isinstance(item, ListNode) and item.key == "stroke"
    )
    stroke_type = next(
        item for item in stroke.items if isinstance(item, ListNode) and item.key == "type"
    )
    assert isinstance(stroke_type.items[1], AtomNode)
    assert stroke_type.items[1].value == "dash"


# ---------------------------------------------------------------------------
# build: pin_at matches definition
# ---------------------------------------------------------------------------


def test_pin_at_matches_definition_coordinates() -> None:
    ph = build("Lib:Part", frozenset({"1", "2", "3", "4"}))
    sub = _get_sub_symbol(ph.definition)
    defn_coords: dict[str, tuple[float, float, float]] = {}
    for p in _get_pins(sub):
        defn_coords[_pin_number(p)] = _pin_at(p)

    for pin_num, (x, y, angle) in ph.pin_at["1"].items():
        dx, dy, da = defn_coords[pin_num]
        assert abs(x - dx) < 0.001
        assert abs(y - dy) < 0.001
        assert abs(angle - da) < 0.001


def test_pin_at_contains_all_pins() -> None:
    pins = frozenset({"A", "B", "C", "D", "E"})
    ph = build("Lib:Part", pins)
    assert set(ph.pin_at["1"].keys()) == pins


def test_pin_at_single_unit_key() -> None:
    ph = build("Lib:Part", frozenset({"1", "2"}))
    assert list(ph.pin_at.keys()) == ["1"]
