"""NE5532 fixture power, feedback, and multi-unit placement quality tests."""

from __future__ import annotations

import math
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import (
    cmd_new_from_netlist,
)
from kicad_pcb.graphviz_layout.snap import ORIGIN_X
from kicad_pcb.layout import GRID_COL_MM
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
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
def test_new_from_real_ne5532_fixture_keeps_power_gnd_local_to_decoupling_bank(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532DecouplingGroundLocality",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)
    gnd_symbol_positions = _symbol_positions_by_id(managed_doc, "power:GND")

    assert gnd_symbol_positions, "Expected the managed schematic to contain power:GND symbols"

    max_local_decoupling_gnd_distance = 1.5 * GRID_COL_MM
    for ref in ("C1", "C2", "C3", "C4"):
        nearest_gnd_distance = min(
            _distance_mm(positions[ref], gnd_position)
            for gnd_position in gnd_symbol_positions.values()
        )
        assert nearest_gnd_distance <= max_local_decoupling_gnd_distance, (
            f"Decoupling cap {ref} should have a local power:GND symbol near the bank: "
            f"nearest power:GND distance={nearest_gnd_distance:.2f} mm, "
            f"threshold={max_local_decoupling_gnd_distance:.2f} mm"
        )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_feedback_parts_local_to_u1a(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532FeedbackLocality",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)
    stage1_pos = positions["U1A"]
    stage2_pos = positions["U1B"]
    output_pos = positions["J2"]

    for ref in ("R2", "R3"):
        distance_to_stage1 = _distance_mm(positions[ref], stage1_pos)
        distance_to_stage2 = _distance_mm(positions[ref], stage2_pos)
        distance_to_output = _distance_mm(positions[ref], output_pos)

        assert distance_to_stage1 < distance_to_stage2, (
            f"Feedback part {ref} should remain closer to U1A than U1B: "
            f"U1A={distance_to_stage1:.2f} mm, U1B={distance_to_stage2:.2f} mm"
        )
        assert distance_to_stage1 < distance_to_output, (
            f"Feedback part {ref} should remain local to U1A, not the output tail: "
            f"U1A={distance_to_stage1:.2f} mm, J2={distance_to_output:.2f} mm"
        )
        assert positions[ref][0] < stage1_pos[0], (
            f"Feedback part {ref} should stay on the input/feedback side of U1A: "
            f"{positions[ref][0]:.2f} !< {stage1_pos[0]:.2f}"
        )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_shapes_u1a_feedback_node_like_gain_stage(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532FeedbackNodeShape",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    u1a_x, u1a_y = positions["U1A"]
    r2_x, r2_y = positions["R2"]
    r3_x, r3_y = positions["R3"]

    assert r2_x == pytest.approx(r3_x), (
        "Feedback bridge and shunt should share one vertical node column: "
        f"R2.x={r2_x:.2f}, R3.x={r3_x:.2f}"
    )
    assert r2_x < u1a_x, (
        f"Feedback node should remain on U1A's input side: R2.x={r2_x:.2f}, U1A.x={u1a_x:.2f}"
    )
    assert u1a_x - r2_x <= 30.48, (
        "The feedback bridge should stay close to U1A instead of stretching across the stage: "
        f"U1A.x={u1a_x:.2f}, R2.x={r2_x:.2f}"
    )
    assert r2_y == pytest.approx(u1a_y), (
        f"Feedback bridge should sit on U1A's stage row: R2.y={r2_y:.2f}, U1A.y={u1a_y:.2f}"
    )
    assert r3_y == pytest.approx(u1a_y + 7.62), (
        "Gain-to-ground shunt should hang one row below the inverting node: "
        f"R3.y={r3_y:.2f}, expected={u1a_y + 7.62:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_shapes_u1a_non_inverting_input_like_gain_stage(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532NonInvertingInputNode",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    u1a_x, u1a_y = positions["U1A"]
    rv1_x, rv1_y = positions["RV1"]
    r4_x, r4_y = positions["R4"]

    assert rv1_x == pytest.approx(r4_x), (
        "The U1A non-inverting bridge and shunt should share one input-node column: "
        f"RV1={positions['RV1']}, R4={positions['R4']}, U1A={positions['U1A']}"
    )
    assert u1a_x - rv1_x == pytest.approx(GRID_COL_MM), (
        "The U1A non-inverting input node should sit one grid lane left of the gain stage: "
        f"RV1.x={rv1_x:.2f}, U1A.x={u1a_x:.2f}"
    )
    assert rv1_y == pytest.approx(u1a_y), (
        "The non-inverting bridge should stay on the U1A stage row: "
        f"RV1.y={rv1_y:.2f}, U1A.y={u1a_y:.2f}"
    )
    assert r4_y == pytest.approx(u1a_y + 7.62), (
        "The local shunt on the non-inverting input should hang one row below the U1A node: "
        f"R4.y={r4_y:.2f}, expected={u1a_y + 7.62:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_u1a_upstream_input_bundle_as_left_column(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532NonInvertingInputBundle",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    u1a_x, u1a_y = positions["U1A"]
    c5_x, c5_y = positions["C5"]
    rv1_x, rv1_y = positions["RV1"]
    r2_x, r2_y = positions["R2"]
    r3_x, r3_y = positions["R3"]

    assert ORIGIN_X < c5_x < rv1_x, (
        "The upstream AC-coupling handoff should sit just right of the input connector margin "
        "while remaining left of the non-inverting input node: "
        f"C5.x={c5_x:.2f}, RV1.x={rv1_x:.2f}, ORIGIN_X={ORIGIN_X:.2f}"
    )
    assert rv1_x - c5_x >= GRID_COL_MM / 2.0, (
        "The incoming AC-coupling handoff should still leave visible space before "
        "the non-inverting "
        "input node: "
        f"C5.x={c5_x:.2f}, RV1.x={rv1_x:.2f}"
    )
    assert r2_x - rv1_x == pytest.approx(GRID_COL_MM / 2.0), (
        "The feedback node should stay between the non-inverting input node and U1A: "
        f"RV1.x={rv1_x:.2f}, R2.x={r2_x:.2f}, U1A.x={u1a_x:.2f}"
    )
    assert u1a_x - r2_x == pytest.approx(GRID_COL_MM / 2.0), (
        "U1A should complete a readable three-column gain-stage chain: "
        f"R2.x={r2_x:.2f}, U1A.x={u1a_x:.2f}"
    )
    assert c5_y == pytest.approx(u1a_y), (
        "The coupling capacitor handoff should stay on the U1A input row after removing the "
        "parallel bypass resistor: "
        f"C5.y={c5_y:.2f}, U1A.y={u1a_y:.2f}"
    )
    assert rv1_y == pytest.approx(u1a_y), (
        "The non-inverting bridge should still sit on the U1A row after upstream bundling: "
        f"RV1.y={rv1_y:.2f}, U1A.y={u1a_y:.2f}"
    )
    assert r2_y == pytest.approx(u1a_y), (
        "The feedback bridge should remain on the U1A row after upstream bundling: "
        f"R2.y={r2_y:.2f}, U1A.y={u1a_y:.2f}"
    )
    assert r3_y == pytest.approx(u1a_y + 7.62), (
        "The gain-to-ground shunt should remain one row below the feedback node: "
        f"R3.y={r3_y:.2f}, expected={u1a_y + 7.62:.2f}"
    )
