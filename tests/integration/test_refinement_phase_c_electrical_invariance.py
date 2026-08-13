from __future__ import annotations

import hashlib
import json
import shutil
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.operations import execute_layout_operations
from kicad_pcb.refinement.schematic_semantics import extract_schematic_semantics_from_doc
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import fnum
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode


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


def _isolated_payload() -> dict[str, object]:
    nets: list[dict[str, object]] = []
    for index in range(1, 4):
        nets.extend(
            [
                {"name": f"N{index}A", "pins": [{"ref": f"R{index}", "pin": "1"}]},
                {"name": f"N{index}B", "pins": [{"ref": f"R{index}", "pin": "2"}]},
            ]
        )
    return {
        "version": "1",
        "components": [
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "R2", "symbol": "TestLib:R", "value": "20k"},
            {"ref": "R3", "symbol": "TestLib:R", "value": "30k"},
        ],
        "nets": nets,
    }


def _generate(root: Path, *, name: str, payload: dict[str, object]) -> tuple[CircuitIR, Path]:
    ir_path = root / f"{name}_ir.json"
    ir_path.write_text(json.dumps(payload), encoding="utf-8")
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


def _apply(
    path: Path,
    authoritative: CircuitIR,
    operation_type: str,
    arguments: dict[str, object],
) -> None:
    source_hash = _hash(path)
    execute_layout_operations(
        path,
        [
            {
                "schema_version": "1.0",
                "operation_id": f"phase-c-{operation_type}",
                "source_schematic_hash": source_hash,
                "operation_type": operation_type,
                "arguments": arguments,
            }
        ],
        expected_source_hash=source_hash,
        authoritative_ir=authoritative,
    )


def _verify(
    authoritative: CircuitIR,
    accepted: Path,
    candidate: Path,
    tmp_path: Path,
    operation_type: str,
) -> None:
    report = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=build_schematic_electrical_baseline(authoritative, accepted),
        candidate_schematic=candidate,
        adapter=KicadCliAdapter(kicad_cli=find_kicad_cli()),
        work_dir=tmp_path / f"verification_phase_c_{operation_type}",
    )
    assert report.passed, report.mismatches


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = next(child for child in node.items if isinstance(child, ListNode) and child.key == "pts")
    points: list[tuple[float, float]] = []
    for item in pts.items:
        if not (isinstance(item, ListNode) and item.key == "xy"):
            continue
        x_node, y_node = item.items[1], item.items[2]
        assert isinstance(x_node, AtomNode)
        assert isinstance(y_node, AtomNode)
        points.append((float(x_node.value), float(y_node.value)))
    return points


def _node_xy(node: ListNode) -> tuple[float, float]:
    at = next(child for child in node.items if isinstance(child, ListNode) and child.key == "at")
    x_node, y_node = at.items[1], at.items[2]
    assert isinstance(x_node, AtomNode)
    assert isinstance(y_node, AtomNode)
    return float(x_node.value), float(y_node.value)


def _replace_xy(node: ListNode, point: tuple[float, float]) -> ListNode:
    at = next(child for child in node.items if isinstance(child, ListNode) and child.key == "at")
    replacement_at = ListNode(
        (at.items[0], fnum(point[0], 2), fnum(point[1], 2), *at.items[3:]),
        at.pos,
    )
    return ListNode(
        tuple(replacement_at if child is at else child for child in node.items),
        node.pos,
    )


def _uuid(node: ListNode) -> str:
    uuid_node = next(
        child for child in node.items if isinstance(child, ListNode) and child.key == "uuid"
    )
    value = uuid_node.items[1]
    assert isinstance(value, StringNode)
    return value.value


def _collapse_label_stubs(schematic: Path) -> None:
    doc = SchematicDoc.load(schematic)
    wires = [node for node in doc.root.items if isinstance(node, ListNode) and node.key == "wire"]
    replacements: dict[int, ListNode] = {}
    removed: set[int] = set()
    for label in (
        node for node in doc.root.items if isinstance(node, ListNode) and node.key == "label"
    ):
        label_point = _node_xy(label)
        matches = [
            (wire, points[1] if points[0] == label_point else points[0])
            for wire in wires
            if len(points := _wire_points(wire)) == 2 and label_point in points
        ]
        assert len(matches) == 1
        wire, pin_point = matches[0]
        replacements[id(label)] = _replace_xy(label, pin_point)
        removed.add(id(wire))
    assert len(replacements) == len(removed) == 6
    doc.root = ListNode(
        tuple(
            replacements.get(id(item), item) for item in doc.root.items if id(item) not in removed
        ),
        doc.root.pos,
    )
    doc.save(schematic)


def _prepare_isolated_layout(root: Path, *, name: str) -> tuple[CircuitIR, Path]:
    authoritative, schematic = _generate(root, name=name, payload=_isolated_payload())
    _collapse_label_stubs(schematic)
    for ref, dx_mm in (("R1", -20.32), ("R3", 20.32)):
        _apply(
            schematic,
            authoritative,
            "move_component",
            {"target": {"ref": ref, "unit": "1"}, "dx_mm": dx_mm, "dy_mm": 0.0},
        )
    return authoritative, schematic


def _divider_arguments(schematic: Path, operation_type: str) -> dict[str, object]:
    doc = SchematicDoc.load(schematic)
    semantic = extract_schematic_semantics_from_doc(doc)
    if operation_type == "move_component":
        return {"target": {"ref": "R1", "unit": "1"}, "dx_mm": 1.27, "dy_mm": 0.0}
    if operation_type == "rotate_component":
        r1 = next(component for component in semantic.components if component.ref == "R1")
        return {"target": {"ref": "R1", "unit": "1"}, "angle_deg": (r1.rotation + 90) % 360}
    if operation_type == "move_power_symbol":
        power = next(
            component for component in semantic.components if component.symbol_id == "power:VCC"
        )
        return {
            "target": {"ref": power.ref, "unit": power.unit},
            "dx_mm": 0.0,
            "dy_mm": 1.27,
        }
    if operation_type == "move_component_group":
        return {
            "targets": [{"ref": "R1", "unit": "1"}, {"ref": "R2", "unit": "1"}],
            "dx_mm": 1.27,
            "dy_mm": 1.27,
        }
    if operation_type == "move_label":
        label = next(
            node
            for node in doc.root.items
            if isinstance(node, ListNode)
            and node.key == "label"
            and isinstance(node.items[1], StringNode)
            and node.items[1].value == "AUX"
        )
        label_point = _node_xy(label)
        wire = next(
            node
            for node in doc.root.items
            if isinstance(node, ListNode)
            and node.key == "wire"
            and label_point in _wire_points(node)
        )
        points = _wire_points(wire)
        other = points[1] if points[0] == label_point else points[0]
        if label_point[0] == other[0]:
            point = (
                label_point[0],
                label_point[1] + (2.54 if other[1] > label_point[1] else -2.54),
            )
        else:
            point = (
                label_point[0] + (2.54 if other[0] > label_point[0] else -2.54),
                label_point[1],
            )
        return {"label_uuid": _uuid(label), "x_mm": point[0], "y_mm": point[1]}
    raise AssertionError(operation_type)


@pytest.mark.integration
@pytest.mark.requires_kicad
@pytest.mark.parametrize(
    "operation_type",
    [
        "move_component",
        "rotate_component",
        "move_label",
        "move_power_symbol",
        "move_component_group",
    ],
)
def test_divider_operations_preserve_real_kicad_electrical_invariance(
    tmp_path: Path,
    operation_type: str,
) -> None:
    authoritative, accepted = _generate(
        tmp_path,
        name=f"PhaseC{operation_type}",
        payload=_divider_payload(),
    )
    candidate = tmp_path / f"phase_c_{operation_type}.kicad_sch"
    shutil.copy2(accepted, candidate)
    source_hash = _hash(candidate)
    _apply(candidate, authoritative, operation_type, _divider_arguments(candidate, operation_type))
    assert _hash(candidate) != source_hash
    _verify(authoritative, accepted, candidate, tmp_path, operation_type)


@pytest.mark.integration
@pytest.mark.requires_kicad
@pytest.mark.parametrize("operation_type", ["align_components", "distribute_components"])
def test_multi_component_operations_preserve_real_kicad_electrical_invariance(
    tmp_path: Path,
    operation_type: str,
) -> None:
    authoritative, accepted = _prepare_isolated_layout(
        tmp_path,
        name=f"PhaseC{operation_type}",
    )
    candidate = tmp_path / f"phase_c_{operation_type}.kicad_sch"
    shutil.copy2(accepted, candidate)
    semantic = extract_schematic_semantics_from_doc(SchematicDoc.load(candidate))
    components = {component.ref: component for component in semantic.components}
    targets = [{"ref": ref, "unit": components[ref].unit} for ref in ("R1", "R2", "R3")]
    if operation_type == "align_components":
        arguments: dict[str, object] = {
            "targets": targets,
            "axis": "y",
            "coordinate_mm": components["R2"].y + 5.08,
        }
    else:
        arguments = {"targets": targets, "axis": "y"}
    source_hash = _hash(candidate)
    _apply(candidate, authoritative, operation_type, arguments)
    assert _hash(candidate) != source_hash
    _verify(authoritative, accepted, candidate, tmp_path, operation_type)
