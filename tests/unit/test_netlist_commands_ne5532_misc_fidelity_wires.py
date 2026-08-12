"""Circuit fidelity and search / debug / path / layout tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands._sch_apply import _transform_pin_at
from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.sch_doc import SchematicDoc, read_lib_symbol_pin_at
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first, walk

_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)


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


def _check_circuit_fidelity(ir_data: dict, managed_doc: SchematicDoc) -> None:
    """Assert that *managed_doc* faithfully represents *ir_data*.

    Raises ``AssertionError`` with a descriptive message on the first mismatch.

    Parameters
    ----------
    ir_data:
        Parsed Circuit IR dict (``version``, ``components``, ``nets`` keys).
    managed_doc:
        The generated managed schematic loaded as a :class:`SchematicDoc`.
    """
    # --- component placement check ---
    placed_refs = {str(s["ref"]) for s in managed_doc.list_symbols()}
    for component in ir_data["components"]:
        ref = component["ref"]
        assert ref in placed_refs, (
            f"Component {ref!r} (symbol {component['symbol']!r}) "
            f"is missing from the generated schematic. "
            f"Placed refs: {sorted(placed_refs)}"
        )

    # --- net binding check ---
    # Build a lookup: (ref, pin) → net_name from the generated binding markers.
    binding_index: dict[tuple[str, str], str] = {
        (b["ref"], b["pin"]): b["net_name"] for b in managed_doc.extract_pin_label_bindings()
    }
    for net in ir_data["nets"]:
        net_name = net["name"]
        for pin_ref in net["pins"]:
            key = (pin_ref["ref"], pin_ref["pin"])
            assert key in binding_index, (
                f"No OpenClaw:bind= marker found for {pin_ref['ref']} pin {pin_ref['pin']!r} "
                f"(expected net {net_name!r}). "
                f"Bindings present: {sorted(binding_index.keys())}"
            )
            actual_net = binding_index[key]
            assert actual_net == net_name, (
                f"{pin_ref['ref']} pin {pin_ref['pin']!r}: "
                f"expected net {net_name!r} but schematic records {actual_net!r}"
            )


def test_circuit_fidelity_multi_component_testlib(tmp_path: Path) -> None:
    """Circuit fidelity: a 3-component, 4-net circuit with TestLib symbols.

    Uses R, OpAmp (flat), and DerivedOpAmp (extends OpAmp) together.
    Verifies that every component is placed and every net/pin binding is
    recorded correctly — including pins inherited by DerivedOpAmp.

    This test runs without system KiCad libraries and is always executed in CI.
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "22k"},
            {"ref": "U1", "symbol": "TestLib:DerivedOpAmp", "value": "DerivedOpAmp"},
        ],
        "nets": [
            # IN+ (pin 1 of DerivedOpAmp, inherited from OpAmp) through R1
            {"name": "IN_P", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
            # IN- (pin 2, inherited) through R2
            {"name": "IN_N", "pins": [{"ref": "U1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            # Feedback: OUT (pin 6, inherited) back to IN- via R2
            {"name": "OUT", "pins": [{"ref": "U1", "pin": "6"}, {"ref": "R2", "pin": "2"}]},
            # Input bias
            {"name": "GND", "pins": [{"ref": "R1", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="FidelityTestLib",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    assert result.symbols_added == 3
    assert result.nets_applied == 4

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    _check_circuit_fidelity(ir_data, managed_doc)


def test_wires_connect_at_pin_endpoints(tmp_path: Path) -> None:  # noqa: PLR0912
    """P1: wires in the managed schematic start at the actual library pin endpoints.

    Before the P1 fix, _write_nets used arbitrary symbol-relative offsets
    (sym_x + 5.08, sym_y + 2.54*index) regardless of which pin was being
    wired.  After the fix, each wire must start at the exact (x, y) derived
    from the pin's ``(at X Y angle)`` in the library, translated by the
    symbol placement position.

    Circuit: R1 and R2 in series (VCC→R1→MID→R2→GND).
    TestLib:R pin positions:
      pin 1 at (at 0 0 0)   → endpoint at symbol_origin + (0, 0)
      pin 2 at (at 5.08 0 180) → endpoint at symbol_origin + (5.08, 0)
    """
    ir_data = {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
        ],
        "nets": [
            {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
            {"name": "MID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
            {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }
    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir_data), encoding="utf-8")

    result = cmd_new_from_netlist(
        Namespace(
            name="WireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all wire endpoints from the AST. Segment orientation is not
    # semantically meaningful after simplification, so either endpoint is valid.
    wire_endpoints: set[tuple[float, float]] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        for xy in pts.items[1:3]:
            if isinstance(xy, ListNode) and xy.key == "xy" and len(xy.items) >= 3:
                try:
                    x = round(float(xy.items[1].value), 2)  # type: ignore[union-attr]
                    y = round(float(xy.items[2].value), 2)  # type: ignore[union-attr]
                    wire_endpoints.add((x, y))
                except (ValueError, AttributeError):
                    pass

    # Compute expected pin endpoints from the ACTUAL symbol positions in the
    # generated schematic using the same helper as generation.
    pin_at = read_lib_symbol_pin_at("TestLib", "R", symbols_dir=fixtures_dir)
    assert pin_at, "TestLib:R pin positions not found in fixture library"

    expected_endpoints: dict[tuple[str, str], tuple[float, float]] = {}
    for sym in managed_doc.list_symbols():
        ref = str(sym["ref"])
        sx, sy = cast(float, sym["x"]), cast(float, sym["y"])
        rotation = int(cast(float, sym.get("rotation", 0.0)))
        transformed_pin_at = _transform_pin_at(pin_at, sx, sy, rotation)
        for pin_num, (px, py, _pa) in transformed_pin_at.items():
            expected_endpoints[(ref, pin_num)] = (round(px, 2), round(py, 2))

    # Verify every expected pin endpoint is touched by a wire segment.
    # Skip power symbols (#PWR* refs) — they are placed at stub ends and do
    # not need outgoing wires of their own.
    missing: list[str] = []
    for (ref, pin), (ex, ey) in sorted(expected_endpoints.items()):
        if ref.startswith("#"):
            continue  # power symbol — no outgoing wire expected
        if (ex, ey) not in wire_endpoints:
            missing.append(f"{ref} pin {pin}: expected wire endpoint at ({ex}, {ey})")

    assert not missing, (
        "Wire(s) do not touch pin endpoints — wiring is disconnected:\n"
        + "\n".join(f"  {m}" for m in missing)
        + f"\nActual wire endpoints: {sorted(wire_endpoints)}"
    )


def _point_on_wire_segment(
    point: tuple[float, float],
    segment: tuple[float, float, float, float],
    *,
    tolerance: float = 0.01,
) -> bool:
    """Return whether *point* lies on an orthogonal schematic wire segment."""
    x, y = point
    x1, y1, x2, y2 = segment
    if abs(x1 - x2) <= tolerance:
        return abs(x - x1) <= tolerance and min(y1, y2) - tolerance <= y <= max(y1, y2) + tolerance
    if abs(y1 - y2) <= tolerance:
        return abs(y - y1) <= tolerance and min(x1, x2) - tolerance <= x <= max(x1, x2) + tolerance
    return False


def test_direct_wiring_not_all_label_only(tmp_path: Path) -> None:  # noqa: PLR0915
    """Router: an in-range 2-pin net must be physically wired, not label-only.

    A two-resistor voltage-divider (VCC→R1→MID→R2→GND) has three nets. The
    final grid snap may align the two MID pins onto one x-coordinate, allowing
    simplification to collapse the old stub + L-bridge sequence into one
    straight wire. The contract is physical continuity, not segment count.
    """
    ir_path = tmp_path / "divider.json"
    ir_path.write_text(
        json.dumps(
            {
                "version": "1",
                "components": [
                    {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
                    {"ref": "R2", "symbol": "TestLib:R", "value": "4.7k"},
                ],
                "nets": [
                    {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
                    {
                        "name": "MID",
                        "pins": [
                            {"ref": "R1", "pin": "2"},
                            {"ref": "R2", "pin": "1"},
                        ],
                    },
                    {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
                ],
            }
        ),
        encoding="utf-8",
    )

    fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
    result = cmd_new_from_netlist(
        Namespace(
            name="DirectWireTest",
            out_dir=str(tmp_path),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(fixtures_dir),
            mode="internal",
        )
    )
    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    # Collect all net-label text values from the schematic AST.
    label_names: set[str] = set()
    for node in walk(managed_doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
        ):
            label_names.add(node.items[1].value)

    # MID must NOT appear as a label — it must be directly wired.
    assert "MID" not in label_names, (
        f"Net 'MID' found as a schematic label; expected direct wire routing. "
        f"All labels present: {sorted(label_names)}"
    )

    all_wire_segments: list[tuple[float, float, float, float]] = []
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "wire"):
            continue
        pts = find_first(node, "pts")
        if pts is None or len(pts.items) < 3:  # noqa: PLR2004
            continue
        xy1, xy2 = pts.items[1], pts.items[2]
        if isinstance(xy1, ListNode) and isinstance(xy2, ListNode):
            try:
                x1 = float(xy1.items[1].value)  # type: ignore[union-attr]
                y1 = float(xy1.items[2].value)  # type: ignore[union-attr]
                x2 = float(xy2.items[1].value)  # type: ignore[union-attr]
                y2 = float(xy2.items[2].value)  # type: ignore[union-attr]
                all_wire_segments.append((x1, y1, x2, y2))
            except (ValueError, AttributeError, IndexError):
                pass

    # Verify electrical continuity between the two MID pins directly. This is
    # robust to collinear wire simplification and catches disconnected stubs.
    pin_at = read_lib_symbol_pin_at("TestLib", "R", symbols_dir=fixtures_dir)
    assert pin_at, "TestLib:R pin positions not found in fixture library"
    midpoint_pins: dict[tuple[str, str], tuple[float, float]] = {}
    for sym in managed_doc.list_symbols():
        ref = str(sym["ref"])
        if ref not in {"R1", "R2"}:
            continue
        sx, sy = cast(float, sym["x"]), cast(float, sym["y"])
        rotation = int(cast(float, sym.get("rotation", 0.0)))
        transformed = _transform_pin_at(pin_at, sx, sy, rotation)
        for pin_num, (x, y, _angle) in transformed.items():
            midpoint_pins[(ref, pin_num)] = (x, y)

    r1_mid = midpoint_pins[("R1", "2")]
    r2_mid = midpoint_pins[("R2", "1")]
    connecting_segments = [
        segment
        for segment in all_wire_segments
        if _point_on_wire_segment(r1_mid, segment) and _point_on_wire_segment(r2_mid, segment)
    ]
    assert connecting_segments, (
        "Expected direct physical continuity from R1.2 to R2.1; "
        f"R1.2={r1_mid}, R2.1={r2_mid}, wires={all_wire_segments}"
    )

    # Single-pin power nets must get power symbol nodes, not labels.
    power_lib_ids: set[str] = set()
    for node in walk(managed_doc.root):
        if not (isinstance(node, ListNode) and node.key == "symbol"):
            continue
        lib_id_node = find_first(node, "lib_id")
        if (
            lib_id_node is not None
            and len(lib_id_node.items) >= 2  # noqa: PLR2004
            and isinstance(lib_id_node.items[1], StringNode)
        ):
            power_lib_ids.add(lib_id_node.items[1].value)
    assert "power:VCC" in power_lib_ids, (
        f"Expected power:VCC symbol for power net 'VCC'; lib_ids found: {sorted(power_lib_ids)}"
    )
    assert "power:GND" in power_lib_ids, (
        f"Expected power:GND symbol for power net 'GND'; lib_ids found: {sorted(power_lib_ids)}"
    )
