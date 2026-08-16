from __future__ import annotations

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
from kicad_pcb.sch_doc.nodes import make_wire_node
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode


def _payload() -> dict[str, object]:
    return {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "20k"},
        ],
        "nets": [
            {"name": "LEFT", "pins": [{"ref": "R1", "pin": "1"}]},
            {
                "name": "MID",
                "pins": [
                    {"ref": "R1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {"name": "RIGHT", "pins": [{"ref": "R2", "pin": "2"}]},
        ],
    }


def _generate(root: Path, *, name: str) -> tuple[CircuitIR, Path]:
    ir_path = root / f"{name}_ir.json"
    ir_path.write_text(json.dumps(_payload()), encoding="utf-8")
    generated = cmd_new_from_netlist(
        Namespace(
            name=name,
            out_dir=str(root),
            description="",
            netlist=str(ir_path),
            symbols_dir=str(Path(__file__).parents[1] / "fixtures" / "symbols"),
            mode="internal",
            layout="graphviz",
        )
    )
    return CircuitIR.load(ir_path), generated.managed_schematic_path


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _list_child(node: ListNode, key: str) -> ListNode:
    for child in node.items:
        if isinstance(child, ListNode) and child.key == key:
            return child
    raise AssertionError(f"missing {key} child")


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = _list_child(node, "pts")
    points: list[tuple[float, float]] = []
    for item in pts.items:
        if not (isinstance(item, ListNode) and item.key == "xy"):
            continue
        x_node, y_node = item.items[1], item.items[2]
        assert isinstance(x_node, AtomNode)
        assert isinstance(y_node, AtomNode)
        points.append((float(x_node.value), float(y_node.value)))
    return points


def _wire_uuid(node: ListNode) -> str:
    uuid_node = _list_child(node, "uuid")
    value = uuid_node.items[1]
    assert isinstance(value, StringNode)
    return value.value


def _wire_nodes(doc: SchematicDoc) -> list[ListNode]:
    return [node for node in doc.root.items if isinstance(node, ListNode) and node.key == "wire"]


def _mid_endpoints(doc: SchematicDoc) -> tuple[tuple[float, float], tuple[float, float]]:
    semantic = extract_schematic_semantics_from_doc(doc)
    components = {component.ref: component for component in semantic.components}
    r1 = resolve_component_pin_positions(doc, components["R1"])
    r2 = resolve_component_pin_positions(doc, components["R2"])
    return (
        r1[ElectricalTerminal(ref="R1", pin="2", unit="1")],
        r2[ElectricalTerminal(ref="R2", pin="1", unit="1")],
    )


def _mid_chain(schematic: Path) -> tuple[list[ListNode], list[tuple[float, float]]]:
    doc = SchematicDoc.load(schematic)
    start, end = _mid_endpoints(doc)
    wires = _wire_nodes(doc)
    nodes: list[ListNode] = []
    points = [start]
    current = start
    previous: ListNode | None = None
    for _ in range(len(wires) + 1):
        if current == end:
            break
        matches: list[tuple[ListNode, tuple[float, float]]] = []
        for node in wires:
            if node is previous:
                continue
            segment = _wire_points(node)
            assert len(segment) == 2
            if segment[0] == current:
                matches.append((node, segment[1]))
            elif segment[1] == current:
                matches.append((node, segment[0]))
        assert len(matches) == 1, (current, matches)
        node, current = matches[0]
        nodes.append(node)
        points.append(current)
        previous = node
    assert current == end
    return nodes, points


def _replace_segment(
    schematic: Path,
    target: ListNode,
    replacement_points: list[tuple[float, float]],
    *,
    seed: str,
) -> None:
    doc = SchematicDoc.load(schematic)
    target_uuid = _wire_uuid(target)
    replacements = [
        make_wire_node(
            start[0],
            start[1],
            end[0],
            end[1],
            str(uuid5(NAMESPACE_URL, f"{target_uuid}:{seed}:{index}")),
        )
        for index, (start, end) in enumerate(zip(replacement_points, replacement_points[1:]))
    ]
    items: list[Node] = []
    replaced = False
    for item in doc.root.items:
        if isinstance(item, ListNode) and item.key == "wire" and _wire_uuid(item) == target_uuid:
            items.extend(replacements)
            replaced = True
        else:
            items.append(item)
    assert replaced
    doc.root = ListNode(tuple(items), doc.root.pos)
    doc.save(schematic)


def _add_redundant_split(schematic: Path) -> None:
    nodes, points = _mid_chain(schematic)
    index = max(
        range(len(nodes)),
        key=lambda item: _path_length([points[item], points[item + 1]]),
    )
    start, end = points[index], points[index + 1]
    if start[0] == end[0]:
        step = 1.27 if end[1] > start[1] else -1.27
        midpoint = (start[0], start[1] + step)
    else:
        step = 1.27 if end[0] > start[0] else -1.27
        midpoint = (start[0] + step, start[1])
    _replace_segment(
        schematic,
        nodes[index],
        [start, midpoint, end],
        seed="phase-d-redundant",
    )


def _add_detour(schematic: Path) -> None:
    nodes, points = _mid_chain(schematic)
    index = max(
        range(len(nodes)),
        key=lambda item: _path_length([points[item], points[item + 1]]),
    )
    start, end = points[index], points[index + 1]
    if start[0] == end[0]:
        offset = start[0] - 5.08
        replacement = [start, (offset, start[1]), (offset, end[1]), end]
    else:
        offset = start[1] - 5.08
        replacement = [start, (start[0], offset), (end[0], offset), end]
    _replace_segment(
        schematic,
        nodes[index],
        replacement,
        seed="phase-d-detour",
    )


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(
        abs(start[0] - end[0]) + abs(start[1] - end[1])
        for start, end in zip(points, points[1:])
    )


def _apply(
    schematic: Path,
    authoritative: CircuitIR,
    operation_type: str,
    arguments: dict[str, object],
) -> None:
    source_hash = _hash(schematic)
    result = execute_layout_operations(
        schematic,
        [
            {
                "schema_version": "1.0",
                "operation_id": f"phase-d-{operation_type}",
                "source_schematic_hash": source_hash,
                "operation_type": operation_type,
                "arguments": arguments,
            }
        ],
        expected_source_hash=source_hash,
        authoritative_ir=authoritative,
    )
    assert result.candidate_hash != source_hash


def _verify(
    authoritative: CircuitIR,
    accepted: Path,
    candidate: Path,
    tmp_path: Path,
    case: str,
) -> None:
    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=build_schematic_electrical_baseline(authoritative, accepted),
        candidate_schematic=candidate,
        adapter=KicadCliAdapter(kicad_cli=find_kicad_cli()),
        work_dir=tmp_path / f"verification_phase_d_{case}",
    )
    assert report.passed, report.mismatches


def _assert_legal_wire_segments(schematic: Path) -> None:
    doc = SchematicDoc.load(schematic)
    assert all(len(_wire_points(node)) == 2 for node in _wire_nodes(doc))


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_remove_redundant_wire_bend_preserves_real_kicad_electrical_invariance(
    tmp_path: Path,
) -> None:
    authoritative, accepted = _generate(tmp_path, name="PhaseDRemoveBend")
    candidate = tmp_path / "phase_d_remove_bend.kicad_sch"
    shutil.copy2(accepted, candidate)
    _add_redundant_split(candidate)
    nodes, before_points = _mid_chain(candidate)

    _apply(
        candidate,
        authoritative,
        "remove_redundant_wire_bend",
        {
            "wire_uuid": _wire_uuid(nodes[0]),
            "expected_points_mm": before_points,
        },
    )

    after_nodes, _ = _mid_chain(candidate)
    assert len(after_nodes) == len(nodes) - 1
    _assert_legal_wire_segments(candidate)
    _verify(authoritative, accepted, candidate, tmp_path, "remove_bend")


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_shorten_wire_path_preserves_real_kicad_electrical_invariance(tmp_path: Path) -> None:
    authoritative, accepted = _generate(tmp_path, name="PhaseDShorten")
    candidate = tmp_path / "phase_d_shorten.kicad_sch"
    shutil.copy2(accepted, candidate)
    _add_detour(candidate)
    nodes, before_points = _mid_chain(candidate)
    before_length = _path_length(before_points)

    _apply(
        candidate,
        authoritative,
        "shorten_wire_path",
        {
            "wire_uuid": _wire_uuid(nodes[0]),
            "net_name": "MID",
            "expected_points_mm": before_points,
        },
    )

    _, after_points = _mid_chain(candidate)
    assert _path_length(after_points) < before_length
    _assert_legal_wire_segments(candidate)
    _verify(authoritative, accepted, candidate, tmp_path, "shorten")


@pytest.mark.integration
@pytest.mark.requires_kicad
def test_reroute_existing_net_preserves_real_kicad_electrical_invariance(tmp_path: Path) -> None:
    authoritative, accepted = _generate(tmp_path, name="PhaseDReroute")
    candidate = tmp_path / "phase_d_reroute.kicad_sch"
    shutil.copy2(accepted, candidate)
    nodes, before_points = _mid_chain(candidate)
    xs = [point[0] for point in before_points]
    ys = [point[1] for point in before_points]

    _apply(
        candidate,
        authoritative,
        "reroute_existing_net_orthogonal",
        {
            "wire_uuid": _wire_uuid(nodes[0]),
            "net_name": "MID",
            "expected_points_mm": before_points,
            "region_min_x_mm": min(xs) - 1.27,
            "region_min_y_mm": min(ys) - 1.27,
            "region_max_x_mm": max(xs) + 1.27,
            "region_max_y_mm": max(ys) + 1.27,
        },
    )

    _, after_points = _mid_chain(candidate)
    assert after_points != before_points
    _assert_legal_wire_segments(candidate)
    _verify(authoritative, accepted, candidate, tmp_path, "reroute")
