from __future__ import annotations

import hashlib
import json
import math
import shutil
import struct
import xml.etree.ElementTree as ET
import zlib
from argparse import Namespace
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from kicad_pcb.adapters import KicadCliAdapter
from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.commands.netlist import cmd_new_from_netlist
from kicad_pcb.electrical_equivalence import ElectricalTerminal
from kicad_pcb.errors import UserError
from kicad_pcb.refinement import rendering
from kicad_pcb.refinement.electrical import (
    build_schematic_electrical_baseline,
    verify_schematic_electrical_invariance,
)
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.schematic_semantics import (
    extract_schematic_semantics_from_doc,
    resolve_component_pin_positions,
)
from kicad_pcb.runner import find_kicad_cli
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sch_doc.nodes import make_wire_node
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest
from kicad_pcb_web.services.schematic_refinement import (
    RefinementIterationLimits,
    RefinementRuntime,
    apply_once_schematic_refinement,
)
from tests.conftest import requires_kicad


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
) -> None:
    doc = SchematicDoc.load(schematic)
    target_uuid = _wire_uuid(target)
    replacements = [
        make_wire_node(
            start[0],
            start[1],
            end[0],
            end[1],
            str(uuid5(NAMESPACE_URL, f"{target_uuid}:phase-k-detour:{index}")),
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


def _add_detour(schematic: Path) -> tuple[str, tuple[tuple[float, float], ...]]:
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
    _replace_segment(schematic, nodes[index], replacement)
    detour_nodes, detour_points = _mid_chain(schematic)
    return _wire_uuid(detour_nodes[0]), tuple(detour_points)


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(
        abs(start[0] - end[0]) + abs(start[1] - end[1])
        for start, end in zip(points, points[1:])
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _png_bytes(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    row = b"\x00" + (b"\xff" * width)
    pixels = zlib.compress(row * height, level=9)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", pixels)
        + _png_chunk(b"IEND", b"")
    )


def _deterministic_rasterize(svg_path: Path, png_path: Path) -> None:
    root = ET.parse(svg_path).getroot()
    raw_width = root.attrib.get("width", "")
    raw_height = root.attrib.get("height", "")
    if raw_width.endswith("px") and raw_height.endswith("px"):
        width = max(1, math.ceil(float(raw_width[:-2])))
        height = max(1, math.ceil(float(raw_height[:-2])))
    else:
        viewbox = tuple(float(value) for value in root.attrib["viewBox"].split())
        width = max(1, math.ceil(viewbox[2] * rendering.REVIEW_PIXELS_PER_MM))
        height = max(1, math.ceil(viewbox[3] * rendering.REVIEW_PIXELS_PER_MM))
    png_path.write_bytes(_png_bytes(width, height))


class _WireRepairModel:
    def __init__(
        self,
        *,
        wire_uuid: str,
        expected_points_mm: tuple[tuple[float, float], ...],
        stale_plan: bool = False,
    ) -> None:
        self.wire_uuid = wire_uuid
        self.expected_points_mm = expected_points_mm
        self.stale_plan = stale_plan
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        user_content = next(
            message.content for message in request.messages if message.role == "user"
        )
        if request.images:
            marker = "Bound refinement context:\n"
            context_payload = json.loads(user_content.split(marker, 1)[1])
            context = context_payload["vision_object_map"]
            response = {
                "schema_version": "1.0",
                "source_schematic_hash": context["source_schematic_hash"],
                "render_png_hash": context["render_png_hash"],
                "issues": [
                    {
                        "issue_id": "phase-k-wire-detour",
                        "category": "wire_length_bends",
                        "severity": "warning",
                        "confidence": 0.99,
                        "affected_object_ids": [f"wire:{self.wire_uuid}"],
                        "observation": "The MID net contains an avoidable orthogonal detour.",
                        "desired_outcome": "Shorten the MID path while preserving its endpoints.",
                        "evidence": "The rendered route uses extra Manhattan length and bends.",
                        "constraints": ["Preserve all electrical connectivity."],
                    }
                ],
            }
        else:
            marker = "Create a bounded RepairPlanResponse for this exact iteration.\n\n"
            planner_context = json.loads(user_content.split(marker, 1)[1])
            points = [list(point) for point in self.expected_points_mm]
            if self.stale_plan:
                points[1][0] += 1.27
            response = {
                "schema_version": "1.0",
                "iteration_id": planner_context["iteration_id"],
                "source_schematic_hash": planner_context["source_schematic_hash"],
                "operations": [
                    {
                        "operation_id": "phase-k-shorten-mid",
                        "issue_ids": ["phase-k-wire-detour"],
                        "expected_visual_benefit": "Remove the unnecessary MID detour.",
                        "operation_type": "shorten_wire_path",
                        "arguments": {
                            "wire_uuid": self.wire_uuid,
                            "net_name": "MID",
                            "expected_points_mm": points,
                        },
                    }
                ],
            }
        return LlmCompletion(
            provider="phase-k-fake",
            model="phase-k-fake",
            content=json.dumps(response),
        )


def _runtime(
    root: Path,
    *,
    authoritative: CircuitIR,
    adapter: KicadCliAdapter,
    model: _WireRepairModel,
) -> RefinementRuntime:
    return RefinementRuntime(
        authoritative_ir=authoritative,
        adapter=adapter,
        llm_client=model,
        work_dir=root / "refinement-work",
        evidence_root=root / "refinement-evidence",
    )


def _limits() -> RefinementIterationLimits:
    return RefinementIterationLimits(max_critic_repairs=0, max_planner_repairs=0, max_operations=1)


@requires_kicad
def test_apply_once_model_directed_real_kicad_round_improves_fixture(
    monkeypatch: pytest.MonkeyPatch,
    home_tmp: Path,
) -> None:
    authoritative, pristine = _generate(home_tmp, name="PhaseKApplyOnce")
    accepted = home_tmp / "phase_k_accepted.kicad_sch"
    shutil.copy2(pristine, accepted)
    wire_uuid, expected_points = _add_detour(accepted)
    before_bytes = accepted.read_bytes()
    before_metrics = compute_refinement_metrics(accepted)
    pristine_metrics = compute_refinement_metrics(pristine)
    assert (
        before_metrics.total_wire_manhattan_length_mm
        > pristine_metrics.total_wire_manhattan_length_mm
    )
    assert before_metrics.bend_count > pristine_metrics.bend_count

    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())
    precheck = verify_schematic_electrical_invariance(
        authoritative_ir=authoritative,
        baseline=build_schematic_electrical_baseline(authoritative, pristine),
        candidate_schematic=accepted,
        adapter=adapter,
        work_dir=home_tmp / "phase-k-precheck",
    )
    assert precheck.passed, precheck.mismatches

    monkeypatch.setattr(
        rendering.RsvgConvertRasterizer,
        "rasterize",
        staticmethod(_deterministic_rasterize),
    )
    model = _WireRepairModel(wire_uuid=wire_uuid, expected_points_mm=expected_points)
    result = apply_once_schematic_refinement(
        accepted_path=accepted,
        runtime=_runtime(home_tmp, authoritative=authoritative, adapter=adapter, model=model),
        iteration_id="phase-k-real-001",
        limits=_limits(),
    )

    assert result.status == "accepted"
    assert result.code == "REFINEMENT_ACCEPTED"
    assert result.electrical is not None and result.electrical.passed
    assert result.structural is not None and result.structural.passed
    assert result.quality is not None and result.quality.accepted
    assert set(result.quality.improved_metrics) >= {"bend_count", "total_wire_manhattan_length_mm"}
    assert result.operations is not None
    assert result.operations.results[0].operation_type == "shorten_wire_path"
    assert accepted.read_bytes() != before_bytes
    assert _hash(accepted) == result.accepted_hash_after == result.candidate_hash
    after_metrics = compute_refinement_metrics(accepted)
    assert (
        after_metrics.total_wire_manhattan_length_mm
        < before_metrics.total_wire_manhattan_length_mm
    )
    assert after_metrics.bend_count < before_metrics.bend_count
    assert result.evidence_dir is not None
    assert (result.evidence_dir / "manifest.json").is_file()
    assert len(model.requests) == 2
    assert model.requests[0].images
    assert not model.requests[1].images


@requires_kicad
def test_apply_once_stale_model_wire_plan_preserves_real_fixture_bytes(
    monkeypatch: pytest.MonkeyPatch,
    home_tmp: Path,
) -> None:
    authoritative, pristine = _generate(home_tmp, name="PhaseKStalePlan")
    accepted = home_tmp / "phase_k_stale_accepted.kicad_sch"
    shutil.copy2(pristine, accepted)
    wire_uuid, expected_points = _add_detour(accepted)
    before = accepted.read_bytes()
    adapter = KicadCliAdapter(kicad_cli=find_kicad_cli())

    monkeypatch.setattr(
        rendering.RsvgConvertRasterizer,
        "rasterize",
        staticmethod(_deterministic_rasterize),
    )
    model = _WireRepairModel(
        wire_uuid=wire_uuid,
        expected_points_mm=expected_points,
        stale_plan=True,
    )
    with pytest.raises(UserError) as exc_info:
        apply_once_schematic_refinement(
            accepted_path=accepted,
            runtime=_runtime(home_tmp, authoritative=authoritative, adapter=adapter, model=model),
            iteration_id="phase-k-stale-001",
            limits=_limits(),
        )

    assert exc_info.value.code in {"REFINEMENT_STALE", "REFINEMENT_AMBIGUOUS_TARGET"}
    assert accepted.read_bytes() == before
    assert len(model.requests) == 2
