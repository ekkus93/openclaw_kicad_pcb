"""Unit tests for kicad_pcb.patterns (Phase 9.2).

Tests verify every pattern function and the CLI command layer using
in-memory schematic fixtures.  No KiCad installation or symbol library
files are required — the tests rely on the graceful fallback in
``_place_component`` when ``read_lib_symbol_pins`` returns ``[]``.
"""
from __future__ import annotations

import argparse
import datetime
import json
import types
from pathlib import Path
from textwrap import dedent

import pytest
from kicad_pcb import sch_doc as _sch_doc
from kicad_pcb.cli import _build_parser
from kicad_pcb.commands import patterns as _cmd_patterns
from kicad_pcb.commands.patterns import cmd_apply_pattern
from kicad_pcb.config import set_current_project
from kicad_pcb.errors import UserError
from kicad_pcb.formatting import format_result, format_result_json
from kicad_pcb.models import ProjectRef
from kicad_pcb.patterns import (
    PATTERNS,
    PIN_OFFSET,
    V_SPACING,
    PatternOutcome,
    PlacedComponent,
    pattern_connector_breakout,
    pattern_decoupling_cap,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from kicad_pcb.results import ApplyPatternResult
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import find_all, find_first, parse
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
    monkeypatch.setattr(_sch_doc, "_DEFAULT_SYMBOLS_DIR", Path("/nonexistent"))


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
        pc = PlacedComponent(ref="R1", lib_sym="Device:R", value="10k",
                             x=50.8, y=76.2, pins=("1", "2"))
        with pytest.raises((AttributeError, TypeError)):
            pc.ref = "R99"  # type: ignore[misc]

    def test_pattern_outcome_is_frozen(self) -> None:
        pc = PlacedComponent(ref="C1", lib_sym="Device:C", value="100nF",
                             x=0.0, y=0.0, pins=("1", "2"))
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
        outcome = pattern_resistor_divider(doc, 0.0, 0.0,
                                           vin_net="PWR", vout_net="TAP", gnd_net="AGND")
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
        outcome = pattern_led_resistor(doc, 0.0, 0.0,
                                       r_value="470", led_value="LED_RED")
        assert outcome.components[0].value == "470"
        assert outcome.components[1].value == "LED_RED"


# ===========================================================================
# pattern_connector_breakout
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
            item for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "VCC"
        ]
        assert vcc_labels, "VCC label not found in schematic"
        at_node = find_first(vcc_labels[0], "at")
        assert at_node is not None
        y_val = float(at_node.items[2].value)  # type: ignore[attr-defined]
        assert y_val < 76.2, "VCC label should be above the capacitor centre"

    def test_gnd_label_placed_below_center(self) -> None:
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2)
        gnd_labels = [
            item for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "GND"
        ]
        assert gnd_labels, "GND label not found"
        at_node = find_first(gnd_labels[0], "at")
        assert at_node is not None
        y_val = float(at_node.items[2].value)  # type: ignore[attr-defined]
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
            item for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "label"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "GND"
        ]
        at_node = find_first(gnd_labels[0], "at")
        gnd_y = float(at_node.items[2].value)  # type: ignore[attr-defined]
        assert abs(gnd_y - (comp_y + PIN_OFFSET)) < 1e-9


# ===========================================================================
# ApplyPatternResult
# ===========================================================================

class TestApplyPatternResult:
    def test_fields_present(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
            dry_run=True,
        )
        assert r.pattern == "resistor-divider"
        assert r.components == ("R1", "R2")
        assert r.nets == ("VIN", "VOUT", "GND")
        assert r.dry_run is True

    def test_dry_run_false_by_default(self) -> None:
        r = ApplyPatternResult(pattern="decoupling-cap", components=("C1",), nets=("VCC",))
        assert r.dry_run is False


# ===========================================================================
# cmd_apply_pattern — integration via pipeline with temp file
# ===========================================================================

class TestCmdApplyPattern:
    """Tests that exercise the full command path using a real temp schematic."""

    @pytest.fixture(autouse=True)  # noqa: F811
    def _no_sym_library(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[override]
        """Override: cmd tests use the real KiCad library via discover_symbols_dir.

        The real library is needed so that placed symbols can be embedded in
        ``lib_symbols`` and pass the SCH009 lint check during validation.
        """
        # Intentionally no-op: do NOT redirect _DEFAULT_SYMBOLS_DIR.
        # discover_symbols_dir will find the real /usr/share/kicad/symbols.

    _MINIMAL_SCH = dedent("""\
        (kicad_sch (version 20230121) (generator test)
          (lib_symbols)
          (sheet_instances (path "/" (page "1")))
        )
    """)

    def _make_args(self, **kwargs) -> types.SimpleNamespace:
        """Build a minimal argparse-like namespace for cmd_apply_pattern."""
        defaults = dict(
            pattern="decoupling-cap",
            r1="R1", r2="R2", r1_value="10k", r2_value="10k",
            vin_net="VIN", vout_net="VOUT",
            r="R1", d="D1", r_value="330", d_value="LED",
            vcc_net="VCC", gnd_net="GND",
            conn="J1", n_pins=4, net_prefix="IO",
            c="C1", c_value="100nF",
            symbols_dir=None,
            dry_run=True,  # always dry-run in unit tests
        )
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_cmd_apply_pattern_decoupling_cap_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="decoupling-cap", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "decoupling-cap"
        assert "C1" in result.components
        assert "VCC" in result.nets
        assert "GND" in result.nets
        assert result.dry_run is True
        # dry-run must not modify the schematic file
        assert "Device:C" not in sch.read_text(encoding="utf-8")

    def test_cmd_apply_pattern_resistor_divider_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="resistor-divider", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "resistor-divider"
        assert "R1" in result.components
        assert "R2" in result.components

    def test_cmd_apply_pattern_led_resistor_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        ProjectRef(
            name="myproject", path=tmp_path,
            created=datetime.datetime.now().isoformat(), description="",
        )
        # re-register project
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject", path=tmp_path,
            created=datetime.datetime.now().isoformat(), description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="led-resistor", r="R1", d="D1", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "led-resistor"
        assert "R1" in result.components
        assert "D1" in result.components

    def test_cmd_apply_pattern_connector_breakout_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject", path=tmp_path,
            created=datetime.datetime.now().isoformat(), description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="connector-breakout", conn="J2", n_pins=3,
                               net_prefix="A", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "connector-breakout"
        assert "J2" in result.components
        assert len(result.nets) == 3

    def test_unknown_pattern_raises_user_error(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject", path=tmp_path,
            created=datetime.datetime.now().isoformat(), description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="banana-circuit", dry_run=True)
        with pytest.raises(UserError, match="Unknown pattern"):
            cmd_apply_pattern(args)

    def test_no_project_raises_user_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_cmd_patterns, "get_current_project", lambda: None)
        args = self._make_args(pattern="decoupling-cap", dry_run=True)
        with pytest.raises(UserError, match="[Nn]o project"):
            cmd_apply_pattern(args)


# ===========================================================================
# CLI arg parsing for apply-pattern
# ===========================================================================

class TestCliArgParsing:
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        return _build_parser().parse_args(argv)

    def test_apply_pattern_subcommand_registered(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.command == "apply-pattern"

    def test_default_pattern_args_for_decoupling_cap(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.pattern == "decoupling-cap"
        assert ns.c == "C1"
        assert ns.c_value == "100nF"
        assert ns.vcc_net == "VCC"
        assert ns.gnd_net == "GND"

    def test_resistor_divider_custom_args(self) -> None:
        ns = self._parse([
            "apply-pattern", "--pattern", "resistor-divider",
            "--r1", "RA1", "--r2", "RA2",
            "--r1-value", "47k", "--r2-value", "22k",
            "--vin-net", "PWR5V", "--vout-net", "TAP",
        ])
        assert ns.pattern == "resistor-divider"
        assert ns.r1 == "RA1"
        assert ns.r2 == "RA2"
        assert ns.r1_value == "47k"
        assert ns.r2_value == "22k"
        assert ns.vin_net == "PWR5V"
        assert ns.vout_net == "TAP"

    def test_led_resistor_custom_args(self) -> None:
        ns = self._parse([
            "apply-pattern", "--pattern", "led-resistor",
            "--r", "R10", "--d", "D10",
            "--r-value", "470", "--d-value", "LED_GREEN",
            "--vcc-net", "3V3", "--gnd-net", "DGND",
        ])
        assert ns.r == "R10"
        assert ns.d == "D10"
        assert ns.r_value == "470"
        assert ns.d_value == "LED_GREEN"
        assert ns.vcc_net == "3V3"

    def test_connector_breakout_custom_args(self) -> None:
        ns = self._parse([
            "apply-pattern", "--pattern", "connector-breakout",
            "--conn", "P2", "--n-pins", "8", "--net-prefix", "GPIO",
        ])
        assert ns.conn == "P2"
        assert ns.n_pins == 8
        assert ns.net_prefix == "GPIO"

    def test_dry_run_flag(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap", "--dry-run"])
        assert ns.dry_run is True

    def test_symbols_dir_flag(self) -> None:
        ns = self._parse([
            "apply-pattern", "--pattern", "decoupling-cap",
            "--symbols-dir", "/custom/kicad/symbols",
        ])
        assert ns.symbols_dir == "/custom/kicad/symbols"

    def test_invalid_pattern_name_rejected(self) -> None:
        with pytest.raises(SystemExit):
            self._parse(["apply-pattern", "--pattern", "banana-circuit"])

    def test_func_is_cmd_apply_pattern(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.func is cmd_apply_pattern


# ===========================================================================
# Formatter
# ===========================================================================

class TestApplyPatternFormatter:
    def test_success_output_mentions_pattern_name(self) -> None:
        r = ApplyPatternResult(
            pattern="decoupling-cap",
            components=("C1",),
            nets=("VCC", "GND"),
        )
        lines = format_result(r)
        combined = " ".join(lines)
        assert "decoupling-cap" in combined

    def test_success_output_lists_components(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
        )
        lines = format_result(r)
        combined = " ".join(lines)
        assert "R1" in combined
        assert "R2" in combined

    def test_dry_run_prefix_present(self) -> None:
        r = ApplyPatternResult(
            pattern="led-resistor",
            components=("R1", "D1"),
            nets=("VCC", "GND"),
            dry_run=True,
        )
        assert any("DRY RUN" in line for line in format_result(r))

    def test_no_dry_run_prefix_when_not_dry(self) -> None:
        r = ApplyPatternResult(
            pattern="led-resistor",
            components=("R1", "D1"),
            nets=("VCC", "GND"),
            dry_run=False,
        )
        assert not any("DRY RUN" in line for line in format_result(r))

    def test_net_names_listed(self) -> None:
        r = ApplyPatternResult(
            pattern="decoupling-cap",
            components=("C1",),
            nets=("3V3", "AGND"),
        )
        combined = " ".join(format_result(r))
        assert "3V3" in combined
        assert "AGND" in combined

    def test_json_serialisable(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
        )
        text = format_result_json(r)
        obj = json.loads(text)
        assert obj["pattern"] == "resistor-divider"
        assert obj["components"] == ["R1", "R2"]
