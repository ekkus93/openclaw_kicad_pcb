from __future__ import annotations

import copy
import hashlib
import json
import shutil
from argparse import Namespace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.electrical_equivalence import ElectricalTerminal
from kicad_pcb.refinement.electrical import (
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.operations import execute_layout_operations
from kicad_pcb.refinement.schematic_semantics import (
    extract_schematic_semantics_from_doc,
    resolve_component_pin_positions,
)
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom, fnum, string
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode


def _divider_payload() -> dict[str, object]:
    return {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "20k"},
            {"ref": "R3", "symbol": "TestLib:R", "value": "30k"},
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
            {
                "name": "GND",
                "pins": [
                    {"ref": "R2", "pin": "2"},
                    {"ref": "R3", "pin": "2"},
                ],
            },
            {"name": "AUX", "pins": [{"ref": "R3", "pin": "1"}]},
        ],
    }


def _generate_schematic(
    root: Path,
    *,
    name: str,
    payload: dict[str, object],
) -> tuple[CircuitIR, Path]:
    ir_path = root / f"{name}_ir.json"
    ir_path.write_text(json.dumps(payload), encoding="utf-8")
    symbols_dir = Path(__file__).parents[1] / "fixtures" / "symbols"
    generated = cmd_new_from_netlist(
        Namespace(
            name=name,
            out_dir=str(root),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(symbols_dir),
            mode="internal",
            layout="graphviz",
        )
    )
    return CircuitIR.load(ir_path), generated.managed_schematic_path


def _adapter() -> KicadCliAdapter:
    return KicadCliAdapter(kicad_cli=find_kicad_cli())


def _verify(
    *,
    authoritative: CircuitIR,
    accepted: Path,
    candidate: Path,
    tmp_path: Path,
    case: str,
) -> SchematicElectricalVerificationReport:
    baseline = build_schematic_electrical_baseline(authoritative, accepted)
    return verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=baseline,
        candidate_schematic=candidate,
        adapter=_adapter(),
        work_dir=tmp_path / f"verification_{case}",
    )


def _source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _apply_single_operation(
    candidate: Path,
    *,
    authoritative: CircuitIR,
    operation_type: str,
    arguments: dict[str, object],
) -> None:
    source_hash = _source_hash(candidate)
    execute_layout_operations(
        candidate,
        [
            {
                "schema_version": "1.0",
                "operation_id": f"a9-{operation_type}",
                "source_schematic_hash": source_hash,
                "operation_type": operation_type,
                "arguments": arguments,
            }
        ],
        expected_source_hash=source_hash,
        authoritative_ir=authoritative,
    )


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = next(
        (child for child in node.items if isinstance(child, ListNode) and child.key == "pts"),
        None,
    )
    assert pts is not None
    points: list[tuple[float, float]] = []
    for item in pts.items:
        if not (isinstance(item, ListNode) and item.key == "xy" and len(item.items) >= 3):
            continue
        x_node, y_node = item.items[1], item.items[2]
        assert isinstance(x_node, AtomNode)
        assert isinstance(y_node, AtomNode)
        points.append((float(x_node.value), float(y_node.value)))
    return points


def _wire_uuid(node: ListNode) -> str:
    uuid_node = next(
        (child for child in node.items if isinstance(child, ListNode) and child.key == "uuid"),
        None,
    )
    assert uuid_node is not None
    assert len(uuid_node.items) >= 2
    value = uuid_node.items[1]
    assert isinstance(value, StringNode)
    return value.value


def _replace_wire_segment(
    node: ListNode,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    wire_uuid: str,
) -> ListNode:
    points = L(
        atom("pts"),
        L(atom("xy"), fnum(start[0], 2), fnum(start[1], 2)),
        L(atom("xy"), fnum(end[0], 2), fnum(end[1], 2)),
    )
    uuid_node = L(atom("uuid"), string(wire_uuid))
    items = tuple(
        points
        if isinstance(child, ListNode) and child.key == "pts"
        else uuid_node
        if isinstance(child, ListNode) and child.key == "uuid"
        else child
        for child in node.items
    )
    return ListNode(items, node.pos)


def _add_mid_wire_dogleg(candidate: Path) -> None:
    doc = SchematicDoc.load(candidate)
    semantic = extract_schematic_semantics_from_doc(doc)
    components = {component.ref: component for component in semantic.components}
    r1_positions = resolve_component_pin_positions(doc, components["R1"])
    start = r1_positions[ElectricalTerminal(ref="R1", pin="2", unit="1")]

    target: ListNode | None = None
    target_points: list[tuple[float, float]] | None = None
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "wire":
            continue
        points = _wire_points(node)
        if start in {points[0], points[-1]}:
            target = node
            target_points = points
            break
    assert target is not None
    assert target_points is not None

    first, last = target_points[0], target_points[-1]
    if first[0] == last[0]:
        offset_x = first[0] - 2.54
        replacement_points = [first, (offset_x, first[1]), (offset_x, last[1]), last]
    else:
        offset_y = first[1] - 2.54
        replacement_points = [first, (first[0], offset_y), (last[0], offset_y), last]

    original_uuid = _wire_uuid(target)
    replacement_segments = [
        _replace_wire_segment(
            target,
            segment_start,
            segment_end,
            wire_uuid=str(uuid5(NAMESPACE_URL, f"{original_uuid}:a9:{index}")),
        )
        for index, (segment_start, segment_end) in enumerate(
            zip(replacement_points, replacement_points[1:])
        )
    ]
    items: list[Node] = []
    for item in doc.root.items:
        if item is target:
            items.extend(replacement_segments)
        else:
            items.append(item)
    doc.root = ListNode(tuple(items), doc.root.pos)
    doc.save(candidate)


def _semantic_corruption_payload(case: str) -> dict[str, object]:
    payload = copy.deepcopy(_divider_payload())
    components = payload["components"]
    nets = payload["nets"]
    assert isinstance(components, list)
    assert isinstance(nets, list)

    if case == "missing_component":
        payload["components"] = [item for item in components if item["ref"] != "R3"]
        retained_nets = []
        for net in nets:
            pins = [pin for pin in net["pins"] if pin["ref"] != "R3"]
            if pins:
                net["pins"] = pins
                retained_nets.append(net)
        payload["nets"] = retained_nets
    elif case == "extra_component":
        components.append({"ref": "R4", "symbol": "TestLib:R", "value": "40k"})
        nets.extend(
            [
                {"name": "EXTRA_A", "pins": [{"ref": "R4", "pin": "1"}]},
                {"name": "EXTRA_B", "pins": [{"ref": "R4", "pin": "2"}]},
            ]
        )
    elif case == "value_change":
        next(item for item in components if item["ref"] == "R2")["value"] = "22k"
    elif case == "pin_moved_net":
        mid = next(item for item in nets if item["name"] == "MID")
        vcc = next(item for item in nets if item["name"] == "VCC")
        mid["pins"] = [{"ref": "R1", "pin": "2"}]
        vcc["pins"].append({"ref": "R2", "pin": "1"})
    elif case == "net_merge":
        mid = next(item for item in nets if item["name"] == "MID")
        vcc = next(item for item in nets if item["name"] == "VCC")
        vcc["pins"].extend(mid["pins"])
        payload["nets"] = [item for item in nets if item["name"] != "MID"]
    elif case == "net_split":
        payload["nets"] = [item for item in nets if item["name"] != "MID"] + [
            {"name": "MID_A", "pins": [{"ref": "R1", "pin": "2"}]},
            {"name": "MID_B", "pins": [{"ref": "R2", "pin": "1"}]},
        ]
    else:
        raise AssertionError(case)
    return payload


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_real_kicad_export_preserves_generated_divider_electrically(tmp_path: Path) -> None:
    authoritative, schematic = _generate_schematic(
        tmp_path,
        name="RefinementElectricalDivider",
        payload=_divider_payload(),
    )

    report = _verify(
        authoritative=authoritative,
        accepted=schematic,
        candidate=schematic,
        tmp_path=tmp_path,
        case="baseline",
    )

    assert report.passed, report.mismatches


@pytest.mark.integration
@pytest.mark.requires_kicad
@pytest.mark.parametrize(
    ("operation_type", "arguments"),
    [
        (
            "move_component",
            {"target": {"ref": "R1", "unit": "1"}, "dx_mm": 1.27, "dy_mm": 0.0},
        ),
        (
            "rotate_component",
            {"target": {"ref": "R1", "unit": "1"}, "angle_deg": 90},
        ),
    ],
)
def test_real_kicad_geometry_only_component_edits_preserve_electrical_invariance(
    tmp_path: Path,
    operation_type: str,
    arguments: dict[str, object],
) -> None:
    authoritative, accepted = _generate_schematic(
        tmp_path,
        name=f"A9Geometry{operation_type}",
        payload=_divider_payload(),
    )
    candidate = tmp_path / f"candidate_{operation_type}.kicad_sch"
    shutil.copy2(accepted, candidate)
    _apply_single_operation(
        candidate,
        authoritative=authoritative,
        operation_type=operation_type,
        arguments=arguments,
    )

    report = _verify(
        authoritative=authoritative,
        accepted=accepted,
        candidate=candidate,
        tmp_path=tmp_path,
        case=operation_type,
    )

    assert report.passed, report.mismatches


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_real_kicad_wire_bend_only_change_preserves_electrical_invariance(tmp_path: Path) -> None:
    authoritative, accepted = _generate_schematic(
        tmp_path,
        name="A9WireBend",
        payload=_divider_payload(),
    )
    candidate = tmp_path / "candidate_wire_bend.kicad_sch"
    shutil.copy2(accepted, candidate)
    _add_mid_wire_dogleg(candidate)

    report = _verify(
        authoritative=authoritative,
        accepted=accepted,
        candidate=candidate,
        tmp_path=tmp_path,
        case="wire_bend",
    )

    assert report.passed, report.mismatches


@pytest.mark.integration
@pytest.mark.requires_kicad
@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("missing_component", "COMPONENT_REFS_MISMATCH"),
        ("extra_component", "COMPONENT_REFS_MISMATCH"),
        ("value_change", "COMPONENT_VALUE_MISMATCH"),
        ("pin_moved_net", "NET_TERMINALS_MISMATCH"),
        ("net_merge", "NET_NAMES_MISMATCH"),
        ("net_split", "NET_NAMES_MISMATCH"),
    ],
)
def test_real_kicad_semantic_corruptions_fail_closed(
    tmp_path: Path,
    case: str,
    expected_code: str,
) -> None:
    authoritative, accepted = _generate_schematic(
        tmp_path,
        name=f"A9Accepted{case}",
        payload=_divider_payload(),
    )
    _, candidate = _generate_schematic(
        tmp_path,
        name=f"A9Candidate{case}",
        payload=_semantic_corruption_payload(case),
    )

    report = _verify(
        authoritative=authoritative,
        accepted=accepted,
        candidate=candidate,
        tmp_path=tmp_path,
        case=case,
    )

    assert not report.passed
    assert expected_code in {mismatch.code for mismatch in report.mismatches}
