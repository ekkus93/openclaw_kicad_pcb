"""NE5532 fixture placement quality tests."""

from __future__ import annotations

import json
import math
from argparse import Namespace
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.layout import GRID_COL_MM
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.utils import find_all
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

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


def _symbol_positions(doc: SchematicDoc) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for symbol in doc.list_symbols():
        ref = symbol["ref"]
        x = symbol["x"]
        y = symbol["y"]
        if isinstance(ref, str) and isinstance(x, float) and isinstance(y, float):
            positions[ref] = (x, y)
    return positions


def _symbol_positions_by_id(doc: SchematicDoc, symbol_id: str) -> dict[str, tuple[float, float]]:
    positions: dict[str, tuple[float, float]] = {}
    for symbol in doc.list_symbols():
        ref = symbol["ref"]
        current_symbol_id = symbol["symbol_id"]
        x = symbol["x"]
        y = symbol["y"]
        if (
            isinstance(ref, str)
            and isinstance(current_symbol_id, str)
            and current_symbol_id == symbol_id
            and isinstance(x, float)
            and isinstance(y, float)
        ):
            positions[ref] = (x, y)
    return positions


def _symbol_angles(doc: SchematicDoc) -> dict[str, int]:
    angles: dict[str, int] = {}
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue

        ref: str | None = None
        angle: int | None = None
        for child in node.items:
            if not isinstance(child, ListNode):
                continue
            if child.key == "property" and len(child.items) >= 3:
                name_node = child.items[1]
                value_node = child.items[2]
                if (
                    isinstance(name_node, StringNode)
                    and name_node.value == "Reference"
                    and isinstance(value_node, StringNode)
                ):
                    ref = value_node.value
            elif (
                child.key == "at" and len(child.items) >= 4 and isinstance(child.items[3], AtomNode)
            ):
                angle = int(float(child.items[3].value))

        if ref is not None and angle is not None:
            angles[ref] = angle

    return angles


def _distance_mm(left: tuple[float, float], right: tuple[float, float]) -> float:
    return math.hypot(left[0] - right[0], left[1] - right[1])


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_marks_unused_trs_ring_pins(tmp_path: Path) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532ConnectorClarity",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    assert len(find_all(managed_doc.root, "no_connect")) == 2


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_authored_trs_connector_symbols(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532ConnectorPolicy",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}

    assert placed_symbols["J1"]["symbol_id"] == "Connector:AudioJack3"
    assert placed_symbols["J2"]["symbol_id"] == "Connector:AudioJack3"


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_splits_u1_into_explicit_units(tmp_path: Path) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532UnitSplit",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)

    placed_symbols = {str(sym["ref"]): sym for sym in managed_doc.list_symbols()}
    assert "U1" not in placed_symbols
    assert {"U1A", "U1B", "U1P"} <= set(placed_symbols)
    assert placed_symbols["U1A"]["unit"] == "1"
    assert placed_symbols["U1B"]["unit"] == "2"
    assert placed_symbols["U1P"]["unit"] == "3"

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    assert binding_index[("U1P", "8")] == "VPLUS15"
    assert binding_index[("U1P", "4")] == "VMINUS15"
    assert binding_index[("U1A", "3")] == "VOL_L_OUT"
    assert binding_index[("U1A", "2")] == "U1A_INV"
    assert binding_index[("U1A", "1")] == "OUT_L_STAGE1"
    assert binding_index[("U1B", "5")] == "BUF_L_IN"
    assert binding_index[("U1B", "6")] == "OUT_L_STAGE2_RAW"
    assert binding_index[("U1B", "7")] == "OUT_L_STAGE2_RAW"


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_managed_schematic_structure_is_stable(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532Structure",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    assert managed_doc.has_openclaw_marker() is True

    fixture = json.loads(_REAL_NE5532_REVIEW_NETLIST.read_text(encoding="utf-8"))
    source_component_refs = {
        str(component["ref"])
        for component in fixture["components"]
        if str(component["ref"]) != "U1"
    }

    symbols = managed_doc.list_symbols()
    non_power_symbols = [sym for sym in symbols if not str(sym["ref"]).startswith("#")]
    non_power_refs = {str(sym["ref"]) for sym in non_power_symbols}

    assert len(non_power_symbols) == len(source_component_refs) + 3
    assert non_power_refs == source_component_refs | {"U1A", "U1B", "U1P"}

    binding_index = {
        (binding["ref"], binding["pin"]): binding["net_name"]
        for binding in managed_doc.extract_pin_label_bindings()
    }
    expected_bindings = {
        ("J1", "T"): "LEFT_IN",
        ("C5", "1"): "LEFT_IN",
        ("C5", "2"): "IN_L_AC",
        ("RV1", "2"): "VOL_L_OUT",
        ("U1A", "3"): "VOL_L_OUT",
        ("U1A", "1"): "OUT_L_STAGE1",
        ("C6", "1"): "OUT_L_STAGE1",
        ("C6", "2"): "BUF_L_IN",
        ("U1B", "5"): "BUF_L_IN",
        ("U1B", "7"): "OUT_L_STAGE2_RAW",
        ("R6", "2"): "AFTER_R6",
        ("C7", "2"): "HP_L_OUT",
        ("J2", "T"): "HP_L_OUT",
    }
    for pin_ref, net_name in expected_bindings.items():
        assert binding_index[pin_ref] == net_name

    assert ("J1", "R") not in binding_index
    assert ("J2", "R") not in binding_index


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_decoupling_caps_in_opamp_region(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingRegion",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    opamp_region_refs = ("U1A", "U1B", "U1P")
    audio_connector_refs = ("J1", "J2")

    for ref in ("C1", "C2", "C3", "C4"):
        nearest_opamp_region = min(
            _distance_mm(positions[ref], positions[anchor_ref]) for anchor_ref in opamp_region_refs
        )
        nearest_audio_connector = min(
            _distance_mm(positions[ref], positions[anchor_ref])
            for anchor_ref in audio_connector_refs
        )
        assert nearest_opamp_region < nearest_audio_connector, (
            f"Decoupling cap {ref} should stay associated with the op-amp region: "
            f"nearest op-amp distance={nearest_opamp_region:.2f} mm, "
            f"nearest audio connector distance={nearest_audio_connector:.2f} mm"
        )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_debug_dump_keeps_decoupling_map_on_u1a(
    tmp_path: Path,
) -> None:
    debug_dump_path = tmp_path / "real-ne5532-decoupling-debug.json"
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingAnchor",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
            debug_dump=str(debug_dump_path),
        )
    )

    assert result.debug_dump_path == debug_dump_path

    debug_dump = json.loads(debug_dump_path.read_text(encoding="utf-8"))
    expected_decoupling_map = {
        "C1": "U1B",
        "C2": "U1B",
        "C3": "U1B",
        "C4": "U1B",
    }

    assert debug_dump["decoupling_map"] == expected_decoupling_map
    assert (
        cast(dict[str, object], debug_dump["placement_constraints"])["decoupling_map"]
        == expected_decoupling_map
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_separates_positive_and_negative_decouplers(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingPolarity",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)
    signal_band_y = positions["U1A"][1]

    for ref in ("C1", "C3"):
        assert positions[ref][1] < signal_band_y, (
            f"Positive-rail decoupler {ref} should sit above the op-amp signal band: {positions}"
        )
    for ref in ("C2", "C4"):
        assert positions[ref][1] > signal_band_y, (
            f"Negative-rail decoupler {ref} should sit below the op-amp signal band: {positions}"
        )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_decoupling_bank_compact_in_x(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingCompactBank",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    opamp_region_refs = ("U1A", "U1B", "U1P")
    decoupling_refs = ("C1", "C2", "C3", "C4")
    decoupling_xs = [positions[ref][0] for ref in decoupling_refs]
    bank_span_x = max(decoupling_xs) - min(decoupling_xs)

    assert bank_span_x <= 2.0 * GRID_COL_MM, (
        "Decoupling bank should stay within compact op-amp support lanes: "
        f"x-span={bank_span_x:.2f} mm, "
        f"threshold={2.0 * GRID_COL_MM:.2f} mm, positions={positions}"
    )

    for ref in decoupling_refs:
        nearest_opamp_x = min(
            abs(positions[ref][0] - positions[anchor_ref][0]) for anchor_ref in opamp_region_refs
        )
        assert nearest_opamp_x <= GRID_COL_MM, (
            f"Decoupling cap {ref} should remain within one op-amp support lane in x: "
            f"nearest op-amp x-distance={nearest_opamp_x:.2f} mm, threshold={GRID_COL_MM:.2f} mm"
        )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_centers_decoupling_bank_on_u1_family(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingFamilyCenter",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    family_center_x = (positions["U1A"][0] + positions["U1B"][0]) / 2.0
    decoupling_refs = ("C1", "C2", "C3", "C4")
    centered_caps = [
        ref
        for ref in decoupling_refs
        if math.isclose(positions[ref][0], family_center_x, abs_tol=0.01)
    ]

    assert centered_caps, (
        "Real NE5532 decoupling bank should use the split-unit family centerline "
        "as its primary x lane: "
        f"family_center_x={family_center_x:.2f}, positions={positions}"
    )
