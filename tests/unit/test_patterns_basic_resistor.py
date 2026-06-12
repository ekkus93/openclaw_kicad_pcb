"""Unit tests for kicad_pcb.patterns (Phase 9.2).

Tests verify every pattern function and the CLI command layer using
in-memory schematic fixtures.  No KiCad installation or symbol library
files are required — the tests rely on the graceful fallback in
``_place_component`` when ``read_lib_symbol_pins`` returns ``[]``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.patterns import (
    PATTERNS,
    V_SPACING,
    PatternOutcome,
    PlacedComponent,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from kicad_pcb.results import ApplyPatternResult
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import find_all, parse
from kicad_pcb.sexpr.nodes import ListNode, StringNode

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_sym_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect _DEFAULT_SYMBOLS_DIR to a non-existent path.

    All pattern tests use the ``["1","2"]`` fallback from ``_place_component``
    because no KiCad symbol library is required.  Parsing large ``.kicad_sym``
    files on every test call would make the suite prohibitively slow.
    """
    monkeypatch.setattr(
        "kicad_pcb._lib_symbol_primitives._DEFAULT_SYMBOLS_DIR", Path("/nonexistent")
    )


# ---------------------------------------------------------------------------
# Minimal schematic helper
# ---------------------------------------------------------------------------

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""


def _make_doc(src: str = MINIMAL_SCH) -> SchematicDoc:
    return SchematicDoc(parse(src))


def _refs_in_doc(doc: SchematicDoc) -> list[str]:
    """Return all Reference property values from placed symbols."""
    refs = []
    for sym in find_all(doc.root, "symbol"):
        for prop in sym.items:
            if (
                isinstance(prop, ListNode)
                and prop.key == "property"
                and len(prop.items) >= 2
                and isinstance(prop.items[1], StringNode)
                and prop.items[1].value == "Reference"
                and len(prop.items) >= 3
                and isinstance(prop.items[2], StringNode)
            ):
                refs.append(prop.items[2].value)
    return refs


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


class TestPatternsRegistry:
    def test_all_four_patterns_registered(self) -> None:
        assert "resistor-divider" in PATTERNS
        assert "led-resistor" in PATTERNS
        assert "connector-breakout" in PATTERNS
        assert "decoupling-cap" in PATTERNS

    def test_registry_values_are_callable(self) -> None:
        for name, fn in PATTERNS.items():
            assert callable(fn), f"PATTERNS[{name!r}] is not callable"


# ===========================================================================
# PlacedComponent / PatternOutcome dataclasses
# ===========================================================================


class TestResultDataclasses:
    def test_placed_component_is_frozen(self) -> None:
        pc = PlacedComponent(
            ref="R1", lib_sym="Device:R", value="10k", x=50.8, y=76.2, pins=("1", "2")
        )
        with pytest.raises((AttributeError, TypeError)):
            pc.ref = "R99"  # type: ignore[misc]

    def test_pattern_outcome_is_frozen(self) -> None:
        pc = PlacedComponent(
            ref="C1", lib_sym="Device:C", value="100nF", x=0.0, y=0.0, pins=("1", "2")
        )
        outcome = PatternOutcome(components=(pc,), nets=("VCC", "GND"))
        with pytest.raises((AttributeError, TypeError)):
            outcome.nets = ()  # type: ignore[misc]

    def test_apply_pattern_result_is_frozen(self) -> None:
        r = ApplyPatternResult(
            pattern="decoupling-cap",
            components=("C1",),
            nets=("VCC", "GND"),
        )
        with pytest.raises((AttributeError, TypeError)):
            r.pattern = "x"  # type: ignore[misc]

    def test_apply_pattern_result_dry_run_default(self) -> None:
        r = ApplyPatternResult(pattern="decoupling-cap", components=("C1",), nets=("VCC",))
        assert r.dry_run is False


# ===========================================================================
# pattern_resistor_divider
# ===========================================================================


class TestResistorDividerPattern:
    def test_returns_two_components(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        assert len(outcome.components) == 2

    def test_component_lib_syms_are_resistors(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        for comp in outcome.components:
            assert comp.lib_sym == "Device:R"

    def test_default_refs_are_r1_r2(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        assert outcome.components[0].ref == "R1"
        assert outcome.components[1].ref == "R2"

    def test_custom_refs(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 0.0, 0.0, r1_ref="RA1", r2_ref="RA2")
        assert outcome.components[0].ref == "RA1"
        assert outcome.components[1].ref == "RA2"

    def test_default_values_applied(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 0.0, 0.0)
        assert outcome.components[0].value == "10k"
        assert outcome.components[1].value == "10k"

    def test_custom_values(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 0.0, 0.0, r1_value="47k", r2_value="22k")
        assert outcome.components[0].value == "47k"
        assert outcome.components[1].value == "22k"

    def test_three_nets_returned(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 0.0, 0.0)
        assert set(outcome.nets) == {"VIN", "VOUT", "GND"}

    def test_custom_nets(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(
            doc, 0.0, 0.0, vin_net="PWR", vout_net="TAP", gnd_net="AGND"
        )
        assert set(outcome.nets) == {"PWR", "TAP", "AGND"}

    def test_symbols_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_resistor_divider(doc, 50.8, 76.2)
        symbols = find_all(doc.root, "symbol")
        assert len(symbols) == 2

    def test_labels_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_resistor_divider(doc, 50.8, 76.2)
        labels = _label_names_in_doc(doc)
        assert "VIN" in labels
        assert "VOUT" in labels
        assert "GND" in labels

    def test_r2_placed_below_r1(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        r1_y = outcome.components[0].y
        r2_y = outcome.components[1].y
        assert r2_y > r1_y, "R2 must be placed below R1"

    def test_r1_r2_x_coordinates_equal(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        assert outcome.components[0].x == outcome.components[1].x

    def test_r1_r2_vertical_spacing_equals_v_spacing(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2)
        delta = outcome.components[1].y - outcome.components[0].y
        assert abs(delta - V_SPACING) < 1e-9

    def test_fallback_pins_when_no_library(self) -> None:
        """Pattern uses fallback ["1","2"] when symbols_dir is None (offline mode).

        The ``_no_sym_library`` autouse fixture patches ``_DEFAULT_SYMBOLS_DIR``
        to ``/nonexistent``, so ``_place_component(symbols_dir=None)`` falls back
        to ``["1", "2"]``.  Explicitly providing an invalid path would raise
        ``UserError`` instead (see Phase 9.3 preflight checks).
        """
        doc = _make_doc()
        # symbols_dir defaults to None → preflight check is skipped → fallback
        outcome = pattern_resistor_divider(doc, 0.0, 0.0)
        for comp in outcome.components:
            assert comp.pins == ("1", "2")

    def test_each_component_has_at_least_two_pins(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 0.0, 0.0)
        for comp in outcome.components:
            assert len(comp.pins) >= 2


# ===========================================================================
# pattern_led_resistor
# ===========================================================================


class TestLedResistorPattern:
    def test_returns_two_components(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 50.8, 76.2)
        assert len(outcome.components) == 2

    def test_first_component_is_resistor(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        assert outcome.components[0].lib_sym == "Device:R"

    def test_second_component_is_led(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        assert outcome.components[1].lib_sym == "Device:LED"

    def test_default_refs(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        assert outcome.components[0].ref == "R1"
        assert outcome.components[1].ref == "D1"

    def test_custom_refs(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0, r_ref="R10", d_ref="D10")
        assert outcome.components[0].ref == "R10"
        assert outcome.components[1].ref == "D10"

    def test_two_nets_vcc_gnd(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        assert set(outcome.nets) == {"VCC", "GND"}

    def test_custom_nets(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0, vcc_net="3V3", gnd_net="DGND")
        assert set(outcome.nets) == {"3V3", "DGND"}

    def test_symbols_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_led_resistor(doc, 50.8, 76.2)
        assert len(find_all(doc.root, "symbol")) == 2

    def test_labels_added_to_schematic(self) -> None:
        doc = _make_doc()
        pattern_led_resistor(doc, 50.8, 76.2)
        labels = _label_names_in_doc(doc)
        assert "VCC" in labels
        assert "GND" in labels

    def test_led_placed_below_resistor(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        assert outcome.components[1].y > outcome.components[0].y

    def test_led_r_vertical_spacing(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0)
        delta = outcome.components[1].y - outcome.components[0].y
        assert abs(delta - V_SPACING) < 1e-9

    def test_custom_values(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 0.0, 0.0, r_value="470", led_value="LED_RED")
        assert outcome.components[0].value == "470"
        assert outcome.components[1].value == "LED_RED"


# ===========================================================================
# pattern_connector_breakout
# ===========================================================================
