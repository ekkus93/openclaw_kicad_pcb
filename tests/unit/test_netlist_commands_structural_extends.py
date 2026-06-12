"""Netlist commands: extends-chain symbol embedding, pin correctness, and net binding."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

# ---------------------------------------------------------------------------
# Extends chain — lib_symbols embedding, pin correctness, net binding
#
# These tests guard against the NE5532 regression where (extends "BaseName")
# caused three failures:
#   1. Only the derived node was embedded — base absent → KiCad blank box.
#   2. read_lib_symbol_pins returned [] → fallback to ["1","2"] → wrong wiring.
#   3. validate_ir_symbols raised SYMBOL_NOT_FOUND for all derived symbols.
# ---------------------------------------------------------------------------


def _get_lib_symbol_ids(doc: SchematicDoc) -> list[str]:
    """Return sorted list of symbol IDs embedded in (lib_symbols)."""
    lib_syms = find_first(doc.root, "lib_symbols")
    if lib_syms is None:
        return []
    ids: list[str] = []
    for item in lib_syms.items:
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            ids.append(item.items[1].value)
    return sorted(ids)


def _get_instance_pin_numbers(doc: SchematicDoc, ref: str) -> list[str]:
    """Return sorted pin numbers declared on the placed symbol instance for *ref*."""
    pins: list[str] = []
    for item in doc.root.items:
        if not (isinstance(item, ListNode) and item.key == "symbol"):
            continue
        # Match instance by Reference property value.
        instance_ref: str | None = None
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "property"
                and len(child.items) >= 3
                and isinstance(child.items[1], StringNode)
                and child.items[1].value == "Reference"
                and isinstance(child.items[2], StringNode)
            ):
                instance_ref = child.items[2].value
        if instance_ref != ref:
            continue
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "pin"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                pins.append(child.items[1].value)
    return sorted(pins)


def test_extends_symbol_embeds_flat_derived_in_lib_symbols(tmp_path: Path) -> None:
    """Extends chain: only the derived symbol is embedded, as a fully flat node.

    The flattening fix (read_lib_symbol_def_flat) merges the parent's geometry
    sub-symbols into the derived node, renames them, and removes the
    (extends ...) attribute.  Only the derived node is written to lib_symbols —
    KiCad can render it without needing the base symbol separately.

    Regression guard for the NE5532 bug: the old code embedded only the derived
    node WITHOUT parent geometry → KiCad renders a blank box with no pins.
    The current code merges parent geometry in so KiCad sees a complete symbol
    with no unresolved extends reference.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsEmbed",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    embedded_ids = _get_lib_symbol_ids(managed_doc)

    # Only the derived symbol is embedded — the base was merged in, not kept separately.
    assert "TestLib:DerivedOpAmp" in embedded_ids, (
        f"Derived 'TestLib:DerivedOpAmp' missing from lib_symbols; got: {embedded_ids}"
    )
    assert "TestLib:OpAmp" not in embedded_ids, (
        f"Base 'TestLib:OpAmp' should not be embedded separately (geometry was merged "
        f"into DerivedOpAmp by read_lib_symbol_def_flat); got: {embedded_ids}"
    )

    # The embedded derived symbol must be flat — no (extends ...) attribute.
    lib_syms = find_first(managed_doc.root, "lib_symbols")
    assert lib_syms is not None
    derived_node = next(
        (
            item
            for item in lib_syms.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "TestLib:DerivedOpAmp"
        ),
        None,
    )
    assert derived_node is not None
    extends_found = any(isinstance(c, ListNode) and c.key == "extends" for c in derived_node.items)
    assert not extends_found, (
        "Flattened derived symbol still contains (extends ...) — "
        "read_lib_symbol_def_flat should have removed it"
    )


def test_extends_symbol_instance_carries_all_inherited_pins(tmp_path: Path) -> None:
    """Extends chain: the placed instance must declare all pins inherited from the base.

    With the old code, read_lib_symbol_pins returned [] for a derived symbol,
    causing the placed instance to record no pins (or a wrong two-pin fallback).
    The result was a schematic where U1 had no net connections and was
    electrically wrong even though it opened without errors in KiCad.
    """
    # DerivedOpAmp inherits pins 1, 2, 3, 6 from OpAmp in TestLib.kicad_sym.
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [{"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsPins",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    pin_numbers = _get_instance_pin_numbers(managed_doc, "U1")

    # Old broken code produced [] or ["1", "2"]; correct code gives all four.
    assert pin_numbers == ["1", "2", "3", "6"], (
        f"Expected inherited pins ['1','2','3','6']; got {pin_numbers}"
    )


def test_extends_symbol_nets_on_inherited_pins_validate_and_bind(tmp_path: Path) -> None:
    """Extends chain: nets on inherited pins must pass validation and produce bindings.

    Pin '6' exists only on the base OpAmp — not declared on DerivedOpAmp directly.
    The old code raised SYMBOL_NOT_FOUND before writing anything because pin
    resolution returned [] for the derived symbol.  The fixed code must:
    1) accept pin '6' as valid for DerivedOpAmp during IR validation,
    2) record an OpenClaw:bind= marker for each net connection.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
                ],
                "nets": [
                    {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}]},
                    {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}]},
                    {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}]},  # only on base OpAmp
                ],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

    result = cmd_new_from_netlist(
        Namespace(
            name="ExtendsNets",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.nets_applied == 3

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    bindings = managed_doc.extract_pin_label_bindings()
    bound_pins = {b["pin"] for b in bindings if b["ref"] == "U1"}
    net_by_pin = {b["pin"]: b["net_name"] for b in bindings if b["ref"] == "U1"}

    # All three nets must be bound; inherited pin '6' is the critical one.
    assert "1" in bound_pins, f"Pin '1' not bound; bindings: {bindings}"
    assert "2" in bound_pins, f"Pin '2' not bound; bindings: {bindings}"
    assert "6" in bound_pins, (
        f"Pin '6' (inherited from base OpAmp) not bound; bound_pins: {bound_pins}"
    )
    assert net_by_pin["6"] == "OUT", f"Pin '6' bound to wrong net: {net_by_pin}"


def test_broken_extends_chain_raises_symbol_has_no_pins(tmp_path: Path) -> None:
    """Broken extends chain must abort with SYMBOL_HAS_NO_PINS before writing anything.

    A symbol that IS declared in the library file but whose extends base does
    not exist resolves to 0 pins.  The error must be SYMBOL_HAS_NO_PINS (not
    SYMBOL_NOT_FOUND) to distinguish "symbol present but broken" from "symbol
    absent entirely".
    """
    # Library with a derived symbol whose base is intentionally absent.
    broken_lib = tmp_path / "BrokenLib.kicad_sym"
    broken_lib.write_text(
        """\
(kicad_symbol_lib (version 20230121) (generator test)
  (symbol "Orphan" (extends "NonExistentBase")
    (property "Reference" "U" (at 0 5.08 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "Orphan" (at 0 -5.08 0)
      (effects (font (size 1.27 1.27)))
    )
  )
)
""",
        encoding="utf-8",
    )
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "U1", "symbol": "BrokenLib:Orphan", "value": "Orphan"}],
                "nets": [{"name": "N1", "pins": [{"ref": "U1", "pin": "1"}]}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="BrokenChainProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(tmp_path),
                mode="internal",
            )
        )

    # Broken chain → symbol in file but 0 pins → SYMBOL_HAS_NO_PINS.
    assert exc_info.value.code == ErrorCode.SYMBOL_HAS_NO_PINS
    # Error details must identify the offending symbol.
    assert "BrokenLib:Orphan" in str(exc_info.value)


def test_apply_netlist_aborts_on_invalid_pin_ref(tmp_path: Path) -> None:
    """Invalid pin reference must abort with PIN_INVALID before writing anything.

    `TestLib:R` only has pins '1' and '2'.  Referencing non-existent pin '99'
    must raise at `validate_ir_symbols` time — before the managed schematic is
    created or mutated — so partial / corrupt output is never written to disk.
    """
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [{"ref": "R1", "symbol": "TestLib:R", "value": "10k"}],
                "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "99"}]}],
            }
        ),
        encoding="utf-8",
    )
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    managed_sch = tmp_path / "InvalidPinProj" / "OpenClaw_Managed.kicad_sch"

    with pytest.raises(UserError) as exc_info:
        cmd_new_from_netlist(
            Namespace(
                name="InvalidPinProj",
                out_dir=str(tmp_path),
                description="",
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
                mode="internal",
            )
        )

    assert exc_info.value.code == ErrorCode.PIN_INVALID
    assert "99" in str(exc_info.value)
    assert "TestLib:R" in str(exc_info.value)
    # Confirm the managed schematic was NOT written (preflight fired pre-write).
    assert not managed_sch.exists(), (
        "Managed schematic must not be created when preflight validation fails"
    )


# ---------------------------------------------------------------------------
# Circuit fidelity — general helper + tests
#
# These tests answer the core question: "Given a Circuit IR, does the
# generated schematic actually represent that circuit?"
#
# Checks performed by _check_circuit_fidelity:
#   1. Every component ref in the IR is present as a placed symbol instance.
#   2. Every (ref, pin, net_name) triple in the IR has a matching
#      OpenClaw:bind= marker in the managed schematic — meaning the tool
#      recorded the net connection for that specific pin.
#
# This is stricter than the EMPTY_GENERATION guard: a schematic could have
# the right number of symbols but wire them to wrong nets, or omit a pin.
# ---------------------------------------------------------------------------

# Path to the system KiCad symbol libraries (installed by kicad package).
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_SYMBOLS = SYMBOLS_FIXTURE_DIR
_REAL_NE5532_REVIEW_NETLIST = NE5532_HEADPHONE_REVIEW_FIXTURE.netlist_path

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)

_skip_no_real_ne5532_fixture_symbols = pytest.mark.skipif(
    not all(
        (
            _REAL_NE5532_SYMBOLS / "Amplifier_Operational.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Device.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector_Generic.kicad_sym",
        )
    ),
    reason="Portable NE5532 regression symbol fixtures are missing",
)
