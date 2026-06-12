from __future__ import annotations

import pytest

from kicad_pcb.patterns import (
    PIN_OFFSET,
    pattern_connector_breakout,
    pattern_decoupling_cap,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import find_all, find_first, parse
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode

pytestmark = pytest.mark.unit


MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""


def _make_doc(src: str = MINIMAL_SCH) -> SchematicDoc:
    return SchematicDoc(parse(src))


def _label_names_in_doc(doc: SchematicDoc) -> list[str]:
    """Return the net name strings from all ``(label …)`` nodes in the document."""
    names = []
    for item in doc.root.items:
        if (
            isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            names.append(item.items[1].value)
    return names


# ===========================================================================
# PATTERNS registry
# ===========================================================================


class TestConnectorBreakoutPattern:
    def test_returns_one_component(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 50.8, 76.2)
        assert len(outcome.components) == 1

    def test_component_lib_sym_is_conn(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=4)
        assert outcome.components[0].lib_sym == "Connector_Generic:Conn_01x04"

    def test_net_count_equals_n_pins(self) -> None:
        for n in (2, 4, 6):
            doc = _make_doc()
            outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=n)
            assert len(outcome.nets) == n

    def test_nets_use_prefix(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=3, net_prefix="SPI")
        assert outcome.nets == ("SPI1", "SPI2", "SPI3")

    def test_default_prefix_is_io(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=2)
        assert outcome.nets == ("IO1", "IO2")

    def test_default_ref_j1(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0)
        assert outcome.components[0].ref == "J1"

    def test_custom_ref(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, conn_ref="P1")
        assert outcome.components[0].ref == "P1"

    def test_labels_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_connector_breakout(doc, 50.8, 76.2, n_pins=3, net_prefix="TX")
        labels = _label_names_in_doc(doc)
        assert "TX1" in labels
        assert "TX2" in labels
        assert "TX3" in labels

    def test_n_pins_one_is_allowed(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=1)
        assert len(outcome.nets) == 1

    def test_n_pins_zero_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(ValueError, match="n_pins"):
            pattern_connector_breakout(doc, 0.0, 0.0, n_pins=0)

    def test_n_pins_negative_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(ValueError, match="n_pins"):
            pattern_connector_breakout(doc, 0.0, 0.0, n_pins=-1)

    def test_lib_sym_zero_padded_two_digits(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0.0, 0.0, n_pins=2)
        assert "Conn_01x02" in outcome.components[0].lib_sym


# ===========================================================================
# pattern_decoupling_cap
# ===========================================================================


class TestDecouplingCapPattern:
    def test_returns_one_component(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 50.8, 76.2)
        assert len(outcome.components) == 1

    def test_component_lib_sym_is_capacitor(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0)
        assert outcome.components[0].lib_sym == "Device:C"

    def test_default_ref_c1(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0)
        assert outcome.components[0].ref == "C1"

    def test_custom_ref(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0, c_ref="C10")
        assert outcome.components[0].ref == "C10"

    def test_default_value(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0)
        assert outcome.components[0].value == "100nF"

    def test_custom_value(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0, c_value="10uF")
        assert outcome.components[0].value == "10uF"

    def test_two_nets_vcc_gnd(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0)
        assert set(outcome.nets) == {"VCC", "GND"}

    def test_custom_nets(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0.0, 0.0, vcc_net="3V3", gnd_net="AGND")
        assert set(outcome.nets) == {"3V3", "AGND"}

    def test_vcc_label_placed_above_center(self) -> None:
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2)
        # VCC label should be placed above the component centre
        vcc_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "VCC"
        ]
        assert vcc_labels, "VCC label not found in schematic"
        at_node = find_first(vcc_labels[0], "at")
        assert at_node is not None
        y_item = at_node.items[2]
        assert isinstance(y_item, (AtomNode, StringNode))
        y_val = float(y_item.value)
        assert y_val < 76.2, "VCC label should be above the capacitor centre"

    def test_gnd_label_placed_below_center(self) -> None:
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2)
        gnd_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "GND"
        ]
        assert gnd_labels, "GND label not found"
        at_node = find_first(gnd_labels[0], "at")
        assert at_node is not None
        y_item = at_node.items[2]
        assert isinstance(y_item, (AtomNode, StringNode))
        y_val = float(y_item.value)
        assert y_val > 76.2, "GND label should be below the capacitor centre"

    def test_symbol_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2)
        assert len(find_all(doc.root, "symbol")) == 1

    def test_pin_offset_matches_constant(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 50.8, 80.0)
        comp_y = outcome.components[0].y
        gnd_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "GND"
        ]
        at_node = find_first(gnd_labels[0], "at")
        assert at_node is not None
        gnd_item = at_node.items[2]
        assert isinstance(gnd_item, (AtomNode, StringNode))
        gnd_y = float(gnd_item.value)
        assert abs(gnd_y - (comp_y + PIN_OFFSET)) < 1e-9


# ===========================================================================
# ApplyPatternResult
# ===========================================================================
