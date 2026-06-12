from __future__ import annotations

import math
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.graphviz_layout.snap import ORIGIN_X
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

pytestmark = pytest.mark.unit


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
def test_new_from_real_ne5532_fixture_keeps_j1_attached_to_incoming_signal_row(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532InputConnectorAttachment",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)

    j1_x, j1_y = positions["J1"]
    c5_x, c5_y = positions["C5"]
    r4_x, r4_y = positions["R4"]
    rv1_x, _rv1_y = positions["RV1"]

    assert j1_y == pytest.approx(c5_y), (
        "The input connector should align with the incoming AC-coupling handoff row: "
        f"J1={positions['J1']}, C5={positions['C5']}, RV1={positions['RV1']}"
    )
    assert j1_y != pytest.approx(r4_y), (
        "The input connector should not sit on the grounded shunt row: "
        f"J1={positions['J1']}, R4={positions['R4']}"
    )
    assert j1_x == pytest.approx(ORIGIN_X), (
        f"The input connector should remain clamped to the left page margin: J1.x={j1_x:.2f}"
    )
    assert j1_x <= c5_x < rv1_x, (
        "The input connector should stay left of the handoff chain into the gain stage: "
        f"J1.x={j1_x:.2f}, C5.x={c5_x:.2f}, RV1.x={rv1_x:.2f}, R4.x={r4_x:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_connectors_attached_and_facing_inward(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532ConnectorGeometry",
            out_dir=str(tmp_path),
            description="",
            netlist=str(_REAL_NE5532_REVIEW_NETLIST),
            symbols_dir=str(_REAL_NE5532_SYMBOLS),
            mode="internal",
        )
    )

    managed_doc = SchematicDoc.load(result.managed_schematic_path)
    positions = _symbol_positions(managed_doc)
    angles = _symbol_angles(managed_doc)

    assert angles["J1"] == 0, (
        f"Input connector should face into the circuit: J1 angle={angles['J1']}"
    )
    assert angles["J2"] == 180, (
        "Output connector should face back toward the circuit from the right edge: "
        f"J2 angle={angles['J2']}"
    )
    assert positions["J2"][1] == pytest.approx(positions["C7"][1]), (
        "The output connector should stay on the final output-tail row with the coupling cap: "
        f"J2={positions['J2']}, C7={positions['C7']}, R7={positions['R7']}"
    )
    assert positions["J2"][1] == pytest.approx(positions["R7"][1]), (
        "The output connector should remain attached to the resistor/capacitor tail row: "
        f"J2={positions['J2']}, C7={positions['C7']}, R7={positions['R7']}"
    )
    assert positions["R7"][0] < positions["J2"][0], (
        "The output connector should remain the outermost element on the final output row: "
        f"R7.x={positions['R7'][0]:.2f}, J2.x={positions['J2'][0]:.2f}"
    )


@_skip_no_real_ne5532_fixture_symbols
def test_new_from_real_ne5532_fixture_keeps_multi_unit_placement_cohesive(
    tmp_path: Path,
) -> None:
    result = cmd_new_from_netlist(
        Namespace(
            name="RealNe5532UnitPlacement",
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
    power_pos = positions["U1P"]
    input_pos = positions["J1"]
    output_pos = positions["J2"]

    assert stage1_pos[0] < stage2_pos[0], (
        f"Signal units should preserve left-to-right stage flow: "
        f"U1A.x={stage1_pos[0]:.2f}, U1B.x={stage2_pos[0]:.2f}"
    )
    assert abs(stage2_pos[0] - stage1_pos[0]) <= 35.0, (
        f"Signal units should stay in nearby x-columns: "
        f"|U1B.x-U1A.x|={abs(stage2_pos[0] - stage1_pos[0]):.2f} mm"
    )
    assert _distance_mm(stage1_pos, stage2_pos) <= 70.0, (
        f"Signal units should remain visually cohesive, not drift apart: "
        f"distance(U1A,U1B)={_distance_mm(stage1_pos, stage2_pos):.2f} mm"
    )

    signal_unit_band_min_x = min(stage1_pos[0], stage2_pos[0]) - 10.0
    signal_unit_band_max_x = max(stage1_pos[0], stage2_pos[0]) + 10.0
    assert signal_unit_band_min_x <= power_pos[0] <= signal_unit_band_max_x, (
        f"Power unit should stay laterally tied to the signal units: "
        "U1P.x="
        f"{power_pos[0]:.2f}, allowed=[{signal_unit_band_min_x:.2f}, "
        f"{signal_unit_band_max_x:.2f}]"
    )

    nearest_signal_unit = min(
        _distance_mm(power_pos, stage_pos) for stage_pos in (stage1_pos, stage2_pos)
    )
    nearest_connector = min(
        _distance_mm(power_pos, connector_pos) for connector_pos in (input_pos, output_pos)
    )
    assert nearest_signal_unit < nearest_connector, (
        f"Power unit should stay associated with the op-amp neighborhood, not the connectors: "
        f"nearest signal unit={nearest_signal_unit:.2f} mm, "
        f"nearest connector={nearest_connector:.2f} mm"
    )
