"""Unit tests for kicad_pcb.preflight (Phase 9.3).

Tests verify every preflight check function in isolation, plus integration
tests confirming that pattern functions raise ``UserError`` before making any
document mutations when invalid arguments are supplied.

No KiCad installation or symbol library files are required for the pure
unit tests because ``check_symbol_accessible`` is tested with
``symbols_dir=None`` (skip) or ``symbols_dir=Path("/nonexistent")`` (fail-fast).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb import lib_symbol as _lib_symbol
from kicad_pcb.errors import UserError
from kicad_pcb.patterns import (
    pattern_connector_breakout,
    pattern_decoupling_cap,
    pattern_led_resistor,
    pattern_resistor_divider,
)
from kicad_pcb.preflight import (
    check_footprints_assigned,
    check_net_names_valid,
    check_no_duplicate_refs,
    check_refs_unique_in_request,
    check_symbol_accessible,
    collect_existing_net_names,
    collect_existing_refs,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

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
    monkeypatch.setattr(_lib_symbol, "_DEFAULT_SYMBOLS_DIR", Path("/nonexistent"))


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


class TestCollectExistingRefs:
    def test_empty_doc_returns_empty_set(self) -> None:
        doc = _make_doc()
        assert collect_existing_refs(doc) == frozenset()

    def test_one_symbol(self) -> None:
        doc = _doc_with_symbol("R1")
        refs = collect_existing_refs(doc)
        assert "R1" in refs

    def test_two_symbols(self) -> None:
        doc = _make_doc()
        pattern_resistor_divider(doc, 50.8, 76.2, r1_ref="R1", r2_ref="R2")
        refs = collect_existing_refs(doc)
        assert {"R1", "R2"}.issubset(refs)

    def test_returns_frozenset(self) -> None:
        doc = _make_doc()
        assert isinstance(collect_existing_refs(doc), frozenset)

    def test_non_symbol_nodes_ignored(self) -> None:
        # A minimal doc has lib_symbols and sheet_instances but no placed symbols
        doc = _make_doc()
        assert collect_existing_refs(doc) == frozenset()


# ---------------------------------------------------------------------------
# collect_existing_net_names
# ---------------------------------------------------------------------------


class TestCollectExistingNetNames:
    def test_empty_doc_returns_empty_set(self) -> None:
        doc = _make_doc()
        assert collect_existing_net_names(doc) == frozenset()

    def test_labels_collected_after_pattern(self) -> None:
        doc = _make_doc()
        pattern_resistor_divider(
            doc,
            50.8,
            76.2,
            r1_ref="R1",
            r2_ref="R2",
            vin_net="VIN",
            vout_net="VOUT",
            gnd_net="GND",
        )
        names = collect_existing_net_names(doc)
        assert {"VIN", "VOUT", "GND"}.issubset(names)

    def test_deduplicates_repeated_label(self) -> None:
        doc = _make_doc()
        # Place two decoupling caps: same VCC net → label appears twice in doc
        pattern_decoupling_cap(doc, 50.8, 76.2, c_ref="C1", vcc_net="VCC", gnd_net="GND")
        pattern_decoupling_cap(doc, 76.2, 76.2, c_ref="C2", vcc_net="VCC", gnd_net="GND")
        names = collect_existing_net_names(doc)
        assert "VCC" in names
        # frozenset deduplicates
        assert isinstance(names, frozenset)

    def test_returns_frozenset(self) -> None:
        doc = _make_doc()
        assert isinstance(collect_existing_net_names(doc), frozenset)

    def test_non_label_nodes_ignored(self) -> None:
        # After placing a pattern, symbols are present but labels are the only nodes
        # captured; we verify symbols (key="symbol") are NOT returned as net names.
        doc = _make_doc()
        pattern_decoupling_cap(doc, 50.8, 76.2, c_ref="C1")
        names = collect_existing_net_names(doc)
        # Symbol refs should not appear in net names
        assert "C1" not in names


# ---------------------------------------------------------------------------
# check_no_duplicate_refs
# ---------------------------------------------------------------------------


class TestCheckNoDuplicateRefs:
    def test_no_conflict_passes(self) -> None:
        check_no_duplicate_refs(["R3", "R4"], frozenset({"R1", "R2"}))

    def test_empty_requested_passes(self) -> None:
        check_no_duplicate_refs([], frozenset({"R1"}))

    def test_empty_existing_passes(self) -> None:
        check_no_duplicate_refs(["R1", "R2"], frozenset())

    def test_single_conflict_raises(self) -> None:
        with pytest.raises(UserError, match="R1"):
            check_no_duplicate_refs(["R1", "R2"], frozenset({"R1"}))

    def test_multiple_conflicts_raises_all_names(self) -> None:
        with pytest.raises(UserError) as exc_info:
            check_no_duplicate_refs(["R1", "R2", "C1"], frozenset({"R1", "R2"}))
        msg = str(exc_info.value)
        assert "R1" in msg
        assert "R2" in msg

    def test_error_message_hints_at_fix(self) -> None:
        with pytest.raises(UserError, match="(?i)designator"):
            check_no_duplicate_refs(["R1"], frozenset({"R1"}))


# ---------------------------------------------------------------------------
# check_refs_unique_in_request
# ---------------------------------------------------------------------------


class TestCheckRefsUniqueInRequest:
    def test_all_unique_passes(self) -> None:
        check_refs_unique_in_request(["R1", "R2", "C1"])

    def test_empty_list_passes(self) -> None:
        check_refs_unique_in_request([])

    def test_single_element_passes(self) -> None:
        check_refs_unique_in_request(["R1"])

    def test_duplicate_pair_raises(self) -> None:
        with pytest.raises(UserError, match="R1"):
            check_refs_unique_in_request(["R1", "R1"])

    def test_only_dupe_reported_once(self) -> None:
        # "R1" appears three times but should only be listed once in the error
        with pytest.raises(UserError) as exc_info:
            check_refs_unique_in_request(["R1", "R1", "R1"])
        assert exc_info.value.args[0].count("R1") == 1

    def test_multiple_different_dupes(self) -> None:
        with pytest.raises(UserError) as exc_info:
            check_refs_unique_in_request(["R1", "R2", "R1", "R2"])
        msg = str(exc_info.value)
        assert "R1" in msg
        assert "R2" in msg

    def test_error_message_mentions_unique(self) -> None:
        with pytest.raises(UserError, match="(?i)unique"):
            check_refs_unique_in_request(["R1", "R1"])


# ---------------------------------------------------------------------------
# check_net_names_valid
# ---------------------------------------------------------------------------


class TestCheckNetNamesValid:
    # --- passing cases ---

    def test_simple_names_pass(self) -> None:
        check_net_names_valid(["VCC", "GND", "VIN", "VOUT"])

    def test_empty_list_passes(self) -> None:
        check_net_names_valid([])

    def test_underscore_passes(self) -> None:
        check_net_names_valid(["NET_A", "_SIGNALS", "SPI_MISO"])

    def test_hyphen_passes(self) -> None:
        check_net_names_valid(["VCC-3V3", "NET-1"])

    def test_slash_passes(self) -> None:
        check_net_names_valid(["SYS/VCC"])

    def test_letter_start_passes(self) -> None:
        check_net_names_valid(["A1", "B2", "IO3"])

    # --- failing cases ---

    def test_empty_string_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)empty"):
            check_net_names_valid([""])

    def test_digit_start_passes(self) -> None:
        # KiCad allows net names like 3V3, +5V, 1V8 \u2014 no digit-start restriction
        check_net_names_valid(["1VCC", "3V3", "5V", "12V", "1V8"])

    def test_whitespace_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(["VCC GND"])

    def test_comma_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(["VCC,GND"])

    def test_semicolon_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(["VCC;GND"])

    def test_double_quote_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(['"VCC"'])

    def test_single_quote_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(["'VCC'"])

    def test_open_paren_raises(self) -> None:
        with pytest.raises(UserError, match="(?i)forbidden"):
            check_net_names_valid(["VCC(3V3)"])

    def test_digit_start_allowed(self) -> None:
        # Common KiCad net names like 3V3, +5V are valid
        check_net_names_valid(["3V3", "5V", "1V8", "12V"])

    def test_multiple_errors_all_reported(self) -> None:
        with pytest.raises(UserError) as exc_info:
            check_net_names_valid(["", "VCC GND", "net;two"])
        msg = str(exc_info.value)
        assert "empty" in msg.lower()
        assert "VCC GND" in msg
        assert "net;two" in msg

    def test_mixed_valid_invalid_raises(self) -> None:
        with pytest.raises(UserError):
            check_net_names_valid(["GND", "VCC", "bad name"])


# ---------------------------------------------------------------------------
# check_symbol_accessible
# ---------------------------------------------------------------------------


class TestCheckSymbolAccessible:
    def test_none_symbols_dir_skips_check(self) -> None:
        # Must not raise even with a completely bogus symbol name
        check_symbol_accessible("Completely:Bogus", symbols_dir=None)

    def test_nonexistent_dir_raises_user_error(self) -> None:
        with pytest.raises(UserError, match="not found"):
            check_symbol_accessible("Device:R", symbols_dir=Path("/nonexistent"))

    def test_missing_symbol_in_real_lib(self) -> None:
        symbols_dir = Path("/usr/share/kicad/symbols")
        if not symbols_dir.exists():
            pytest.skip("KiCad symbol library not installed")
        with pytest.raises(UserError, match="not found"):
            check_symbol_accessible("Device:ThisSymbolDoesNotExistXYZ123", symbols_dir=symbols_dir)

    def test_real_symbol_accessible(self) -> None:
        symbols_dir = Path("/usr/share/kicad/symbols")
        if not symbols_dir.exists():
            pytest.skip("KiCad symbol library not installed")
        # Should not raise
        check_symbol_accessible("Device:R", symbols_dir=symbols_dir)

    def test_missing_colon_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="LibName:SymName"):
            check_symbol_accessible("NoColon", symbols_dir=Path("/usr/share/kicad/symbols"))


# ---------------------------------------------------------------------------
# check_footprints_assigned
# ---------------------------------------------------------------------------


class TestCheckFootprintsAssigned:
    def test_require_false_never_raises(self) -> None:
        # Even with empty footprints, require=False is a no-op
        check_footprints_assigned([("R1", ""), ("R2", "")], require=False)

    def test_require_false_is_default(self) -> None:
        check_footprints_assigned([("R1", ""), ("R2", "")])

    def test_require_true_all_present_passes(self) -> None:
        check_footprints_assigned(
            [("R1", "Resistor_SMD:R_0402"), ("R2", "Resistor_SMD:R_0402")],
            require=True,
        )

    def test_require_true_one_missing_raises(self) -> None:
        with pytest.raises(UserError, match="R1"):
            check_footprints_assigned([("R1", ""), ("R2", "Resistor_SMD:R_0402")], require=True)

    def test_require_true_multiple_missing_all_named(self) -> None:
        with pytest.raises(UserError) as exc_info:
            check_footprints_assigned(
                [("R1", ""), ("R2", ""), ("C1", "Cap_SMD:C_0402")], require=True
            )
        msg = str(exc_info.value)
        assert "R1" in msg
        assert "R2" in msg

    def test_whitespace_only_footprint_counts_as_missing(self) -> None:
        with pytest.raises(UserError, match="R1"):
            check_footprints_assigned([("R1", "   ")], require=True)

    def test_empty_list_require_true_passes(self) -> None:
        check_footprints_assigned([], require=True)

    def test_error_mentions_pcb_layout(self) -> None:
        with pytest.raises(UserError, match="(?i)pcb"):
            check_footprints_assigned([("C1", "")], require=True)


# ---------------------------------------------------------------------------
# Integration: preflight fires from within pattern functions
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
