"""Preflight integration tests — patterns with full circuit fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.patterns import (
    pattern_connector_breakout,
    pattern_decoupling_cap,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from kicad_pcb.preflight import (
    collect_existing_refs,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""


@pytest.fixture(autouse=True)
def _no_sym_library(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect _DEFAULT_SYMBOLS_DIR to /nonexistent for all tests.

    Pattern functions receive ``symbols_dir=None`` (the default), so
    ``check_symbol_accessible`` is a no-op in these tests.
    ``read_lib_symbol_pins`` falls back to the ``["1","2"]`` path via the
    patched ``_DEFAULT_SYMBOLS_DIR``.
    """
    monkeypatch.setattr(
        "kicad_pcb._lib_symbol_primitives._DEFAULT_SYMBOLS_DIR", Path("/nonexistent")
    )


def _make_doc(src: str = MINIMAL_SCH) -> SchematicDoc:
    return SchematicDoc(parse(src))


def _doc_with_symbol(ref: str = "R1") -> SchematicDoc:
    """Return a SchematicDoc that already has one placed symbol with *ref*."""
    doc = _make_doc()
    pattern_resistor_divider(doc, 50.8, 76.2, r1_ref=ref, r2_ref="R99")
    return doc


def _ref_count(doc: SchematicDoc) -> int:
    """Count placed symbol nodes in the schematic root."""
    return sum(1 for item in doc.root.items if hasattr(item, "key") and item.key == "symbol")


# ---------------------------------------------------------------------------
# collect_existing_refs
# ---------------------------------------------------------------------------


class TestPreflightIntegrationResistorDivider:
    def test_same_ref_twice_raises_before_mutation(self) -> None:
        doc = _make_doc()
        initial_count = _ref_count(doc)
        with pytest.raises(UserError, match="(?i)duplicate"):
            pattern_resistor_divider(doc, 0, 0, r1_ref="R1", r2_ref="R1")
        # Doc must remain unchanged
        assert _ref_count(doc) == initial_count

    def test_existing_ref_raises_before_mutation(self) -> None:
        doc = _doc_with_symbol("R1")
        count_before = _ref_count(doc)
        with pytest.raises(UserError, match="R1"):
            pattern_resistor_divider(doc, 0, 0, r1_ref="R1", r2_ref="R2")
        assert _ref_count(doc) == count_before

    def test_empty_net_name_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)empty"):
            pattern_resistor_divider(doc, 0, 0, vin_net="")

    def test_whitespace_net_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)forbidden"):
            pattern_resistor_divider(doc, 0, 0, vin_net="VIN NET")

    def test_forbidden_char_net_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)forbidden"):
            pattern_resistor_divider(doc, 0, 0, gnd_net="GND NET")

    def test_require_footprints_missing_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)footprint"):
            pattern_resistor_divider(doc, 0, 0, footprint="", require_footprints=True)

    def test_require_footprints_present_passes(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(
            doc,
            0,
            0,
            footprint="Resistor_SMD:R_0402_1005Metric",
            require_footprints=True,
        )
        assert len(outcome.components) == 2

    def test_valid_call_succeeds(self) -> None:
        doc = _make_doc()
        outcome = pattern_resistor_divider(doc, 50.8, 76.2, r1_ref="R1", r2_ref="R2")
        assert len(outcome.components) == 2
        refs = collect_existing_refs(doc)
        assert {"R1", "R2"}.issubset(refs)


class TestPreflightIntegrationLedResistor:
    def test_same_ref_twice_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)duplicate"):
            pattern_led_resistor(doc, 0, 0, r_ref="R1", d_ref="R1")

    def test_existing_led_ref_raises(self) -> None:
        doc = _make_doc()
        pattern_led_resistor(doc, 50.8, 76.2, r_ref="R1", d_ref="D1")
        with pytest.raises(UserError, match="D1"):
            pattern_led_resistor(doc, 76.2, 76.2, r_ref="R2", d_ref="D1")

    def test_invalid_vcc_net_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError):
            pattern_led_resistor(doc, 0, 0, vcc_net="")

    def test_require_footprints_partial_missing_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)footprint"):
            pattern_led_resistor(
                doc,
                0,
                0,
                r_footprint="Resistor_SMD:R_0402",
                led_footprint="",
                require_footprints=True,
            )

    def test_valid_call_succeeds(self) -> None:
        doc = _make_doc()
        outcome = pattern_led_resistor(doc, 50.8, 76.2)
        assert len(outcome.components) == 2


class TestPreflightIntegrationConnectorBreakout:
    def test_existing_conn_ref_raises(self) -> None:
        doc = _make_doc()
        pattern_connector_breakout(doc, 50.8, 76.2, conn_ref="J1", n_pins=4)
        with pytest.raises(UserError, match="J1"):
            pattern_connector_breakout(doc, 76.2, 76.2, conn_ref="J1", n_pins=2)

    def test_digit_start_prefix_allowed(self) -> None:
        # "1IO" prefix generates "1IO1", "1IO2" — starts with digit but valid
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 0, 0, net_prefix="1IO", n_pins=2)
        assert len(outcome.nets) == 2

    def test_empty_prefix_raises(self) -> None:
        # Empty prefix is caught separately before net-name validation
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)prefix"):
            pattern_connector_breakout(doc, 0, 0, net_prefix="")

    def test_require_footprints_connector_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)footprint"):
            pattern_connector_breakout(doc, 0, 0, footprint="", require_footprints=True)

    def test_valid_call_succeeds(self) -> None:
        doc = _make_doc()
        outcome = pattern_connector_breakout(doc, 50.8, 76.2, conn_ref="J1", n_pins=3)
        assert len(outcome.components) == 1
        assert len(outcome.nets) == 3

    def test_forbidden_char_in_prefix_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)forbidden"):
            pattern_connector_breakout(doc, 0, 0, net_prefix="IO ")


class TestPreflightIntegrationDecouplingCap:
    def test_existing_cap_ref_raises(self) -> None:
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2, c_ref="C1")
        with pytest.raises(UserError, match="C1"):
            pattern_decoupling_cap(doc, 76.2, 76.2, c_ref="C1")

    def test_empty_gnd_net_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)empty"):
            pattern_decoupling_cap(doc, 0, 0, gnd_net="")

    def test_digit_start_vcc_allowed(self) -> None:
        # 3V3, +5V etc. are common KiCad net names and must be accepted
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 0, 0, c_ref="C1", vcc_net="3V3", gnd_net="GND")
        assert "3V3" in outcome.nets

    def test_require_footprints_raises(self) -> None:
        doc = _make_doc()
        with pytest.raises(UserError, match="(?i)footprint"):
            pattern_decoupling_cap(doc, 0, 0, footprint="", require_footprints=True)

    def test_valid_call_succeeds(self) -> None:
        doc = _make_doc()
        outcome = pattern_decoupling_cap(doc, 50.8, 76.2, c_ref="C1")
        assert len(outcome.components) == 1
        assert "VCC" in outcome.nets
        assert "GND" in outcome.nets


# ---------------------------------------------------------------------------
# Regression: preflight does not mutate doc on failure
# ---------------------------------------------------------------------------


class TestNoMutationOnPreflightFailure:
    """Verify the doc is pristine after a preflight check raises."""

    def test_resistor_divider_no_mutation_on_dup_ref(self) -> None:
        doc = _make_doc()
        # Place R1 legitimately
        pattern_resistor_divider(doc, 50.8, 76.2, r1_ref="R1", r2_ref="R2")
        symbols_before = list(
            item for item in doc.root.items if hasattr(item, "key") and item.key == "symbol"
        )
        # Attempt to place R1 again — preflight should catch it
        with pytest.raises(UserError, match="R1"):
            pattern_resistor_divider(doc, 76.2, 76.2, r1_ref="R1", r2_ref="R3")
        symbols_after = list(
            item for item in doc.root.items if hasattr(item, "key") and item.key == "symbol"
        )
        assert len(symbols_before) == len(symbols_after)

    def test_decoupling_cap_no_mutation_on_invalid_net(self) -> None:
        doc = _make_doc()
        symbols_before = _ref_count(doc)
        with pytest.raises(UserError):
            # whitespace in net name triggers forbidden-char check
            pattern_decoupling_cap(doc, 0, 0, vcc_net="VCC RAIL")
        assert _ref_count(doc) == symbols_before
