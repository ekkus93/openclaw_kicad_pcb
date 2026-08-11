"""Deterministic, fail-closed geometry metrics for schematic refinement."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from kicad_pcb.errors import UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node

from .page_geometry import schematic_page_bounds
from .schematic_semantics import extract_schematic_semantics_from_doc

METRIC_SCHEMA_VERSION = "1.0"
_GRID_MM = 1.27
_SYMBOL_HALF_MM = 5.08
_LONG_WIRE_MM = 25.4
_SHORT_WIRE_MM = 2.54


@dataclass(frozen=True)
class RefinementMetricReport:
    schema_version: str
    schematic_hash: str
    component_count: int
    component_overlap_count: int
    component_overlap_area_mm2: float
    component_text_collision_count: int
    wire_text_collision_count: int
    wire_component_body_intersection_count: int
    out_of_page_count: int
    off_grid_geometry_count: int
    non_junction_wire_crossing_count: int
    total_wire_manhattan_length_mm: float
    wire_segment_count: int
    bend_count: int
    excessive_bend_wire_count: int
    long_wire_count: int
    short_wire_segment_count: int
    wire_stub_ratio: float
    average_symbol_spacing_mm: float
    minimum_symbol_spacing_mm: float
    maximum_local_density: int
    occupied_bbox_area_mm2: float
    occupied_page_fraction: float
    distinct_x_columns: int
    alignment_residual_mean_mm: float
    power_symbol_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RefinementMetricComparison:
    before_hash: str
    after_hash: str
    deltas: dict[str, float]


@dataclass(frozen=True)
class _WireSegment:
    a: tuple[float, float]
    b: tuple[float, float]

    @property
    def length(self) -> float:
        return abs(self.a[0] - self.b[0]) + abs(self.a[1] - self.b[1])


def compute_refinement_metrics(path: Path) -> RefinementMetricReport:
    payload = path.read_bytes()
    doc = SchematicDoc.load(path)
    page = schematic_page_bounds(doc)
    semantic = extract_schematic_semantics_from_doc(doc)
    components = [c for c in semantic.components if c.ref not in semantic.helper_refs]
    wires = _wires(doc)
    junctions = set(_point_nodes(doc, "junction"))
    labels = _label_points(doc)

    bboxes = [
        (
            c.x - _SYMBOL_HALF_MM,
            c.y - _SYMBOL_HALF_MM,
            c.x + _SYMBOL_HALF_MM,
            c.y + _SYMBOL_HALF_MM,
        )
        for c in components
    ]
    overlap_count = 0
    overlap_area = 0.0
    for i, first in enumerate(bboxes):
        for second in bboxes[i + 1 :]:
            ix = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
            iy = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
            if ix > 0 and iy > 0:
                overlap_count += 1
                overlap_area += ix * iy

    wire_component = sum(
        1
        for segment in wires
        for bbox in bboxes
        if _segment_crosses_bbox_interior(segment, bbox)
    )
    crossings = _wire_crossings(wires, junctions)
    graph_bends, excessive = _graph_bend_metrics(wires)
    lengths = [wire.length for wire in wires]
    total_length = sum(lengths)
    long_count = sum(length > _LONG_WIRE_MM for length in lengths)
    short_count = sum(0 < length < _SHORT_WIRE_MM for length in lengths)
    stub_ratio = short_count / len(wires) if wires else 0.0

    positions = [(c.x, c.y) for c in components]
    spacings = [
        math.dist(first, second)
        for i, first in enumerate(positions)
        for second in positions[i + 1 :]
    ]
    avg_spacing = sum(spacings) / len(spacings) if spacings else 0.0
    min_spacing = min(spacings) if spacings else 0.0
    density = max(
        (sum(math.dist(point, other) <= 25.4 for other in positions) for point in positions),
        default=0,
    )

    all_points = positions + [p for wire in wires for p in (wire.a, wire.b)] + labels
    if all_points:
        min_x = min(p[0] for p in all_points)
        max_x = max(p[0] for p in all_points)
        min_y = min(p[1] for p in all_points)
        max_y = max(p[1] for p in all_points)
        bbox_area = max(0.0, max_x - min_x) * max(0.0, max_y - min_y)
    else:
        bbox_area = 0.0
    page_area = page.width_mm * page.height_mm

    out_of_page = sum(
        not (0 <= x <= page.width_mm and 0 <= y <= page.height_mm) for x, y in all_points
    )
    off_grid = sum(not _on_grid(v) for point in all_points for v in point)
    distinct_columns = len({round(c.x / _GRID_MM) for c in components})
    residuals = [
        min(abs(c.x / _GRID_MM - round(c.x / _GRID_MM)), 0.5) * _GRID_MM
        for c in components
    ]
    alignment = sum(residuals) / len(residuals) if residuals else 0.0

    component_text = sum(_point_in_bbox(label, bbox) for label in labels for bbox in bboxes)
    wire_text = sum(_point_on_segment(label, wire) for label in labels for wire in wires)
    power_count = sum(c.ref.startswith("#PWR") for c in semantic.components)

    return RefinementMetricReport(
        schema_version=METRIC_SCHEMA_VERSION,
        schematic_hash=hashlib.sha256(payload).hexdigest(),
        component_count=len(components),
        component_overlap_count=overlap_count,
        component_overlap_area_mm2=round(overlap_area, 4),
        component_text_collision_count=component_text,
        wire_text_collision_count=wire_text,
        wire_component_body_intersection_count=wire_component,
        out_of_page_count=out_of_page,
        off_grid_geometry_count=off_grid,
        non_junction_wire_crossing_count=crossings,
        total_wire_manhattan_length_mm=round(total_length, 4),
        wire_segment_count=len(wires),
        bend_count=graph_bends,
        excessive_bend_wire_count=excessive,
        long_wire_count=long_count,
        short_wire_segment_count=short_count,
        wire_stub_ratio=round(stub_ratio, 6),
        average_symbol_spacing_mm=round(avg_spacing, 4),
        minimum_symbol_spacing_mm=round(min_spacing, 4),
        maximum_local_density=density,
        occupied_bbox_area_mm2=round(bbox_area, 4),
        occupied_page_fraction=round(bbox_area / page_area if page_area else 0.0, 6),
        distinct_x_columns=distinct_columns,
        alignment_residual_mean_mm=round(alignment, 6),
        power_symbol_count=power_count,
    )


def compare_refinement_metrics(
    before: RefinementMetricReport,
    after: RefinementMetricReport,
) -> RefinementMetricComparison:
    deltas: dict[str, float] = {}
    for name, before_value in before.to_dict().items():
        if name in {"schema_version", "schematic_hash"}:
            continue
        after_value = getattr(after, name)
        if isinstance(before_value, (int, float)) and isinstance(after_value, (int, float)):
            deltas[name] = float(after_value) - float(before_value)
    return RefinementMetricComparison(before.schematic_hash, after.schematic_hash, deltas)


def _wires(doc: SchematicDoc) -> list[_WireSegment]:
    result: list[_WireSegment] = []
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "wire":
            continue
        pts = next((c for c in node.items if isinstance(c, ListNode) and c.key == "pts"), None)
        if pts is None:
            raise UserError(
                "Wire is missing required pts geometry.",
                code="REFINEMENT_METRIC_INVALID_GEOMETRY",
            )
        points = [
            _xy(child)
            for child in pts.items
            if isinstance(child, ListNode) and child.key == "xy"
        ]
        if len(points) < 2:
            raise UserError(
                "Wire requires at least two points.",
                code="REFINEMENT_METRIC_INVALID_GEOMETRY",
            )
        for first, second in zip(points, points[1:]):
            if first == second:
                continue
            if first[0] != second[0] and first[1] != second[1]:
                raise UserError(
                    "Refinement metrics require orthogonal wire geometry.",
                    code="REFINEMENT_METRIC_INVALID_GEOMETRY",
                )
            result.append(_WireSegment(first, second))
    return result


def _point_nodes(doc: SchematicDoc, key: str) -> list[tuple[float, float]]:
    return [
        _node_at(node)
        for node in doc.root.items
        if isinstance(node, ListNode) and node.key == key
    ]


def _label_points(doc: SchematicDoc) -> list[tuple[float, float]]:
    return [
        _node_at(node)
        for node in doc.root.items
        if isinstance(node, ListNode)
        and node.key in {"label", "global_label", "hierarchical_label"}
    ]


def _node_at(node: ListNode) -> tuple[float, float]:
    at = next((c for c in node.items if isinstance(c, ListNode) and c.key == "at"), None)
    if at is None or len(at.items) < 3:
        raise UserError(
            f"{node.key} is missing coordinates.",
            code="REFINEMENT_METRIC_INVALID_GEOMETRY",
        )
    return (_num(at.items[1]), _num(at.items[2]))


def _xy(node: ListNode) -> tuple[float, float]:
    if len(node.items) < 3:
        raise UserError("xy node is incomplete.", code="REFINEMENT_METRIC_INVALID_GEOMETRY")
    return (_num(node.items[1]), _num(node.items[2]))


def _num(node: Node) -> float:
    if not isinstance(node, AtomNode):
        raise UserError("Expected numeric atom.", code="REFINEMENT_METRIC_INVALID_GEOMETRY")
    try:
        value = float(node.value)
    except ValueError as exc:
        raise UserError(
            "Invalid numeric geometry.",
            code="REFINEMENT_METRIC_INVALID_GEOMETRY",
        ) from exc
    if not math.isfinite(value):
        raise UserError(
            "Non-finite geometry is invalid.",
            code="REFINEMENT_METRIC_INVALID_GEOMETRY",
        )
    return value


def _on_grid(value: float) -> bool:
    return abs(value / _GRID_MM - round(value / _GRID_MM)) <= 1e-6


def _point_in_bbox(point: tuple[float, float], bbox: tuple[float, float, float, float]) -> bool:
    return bbox[0] < point[0] < bbox[2] and bbox[1] < point[1] < bbox[3]


def _segment_crosses_bbox_interior(
    seg: _WireSegment,
    bbox: tuple[float, float, float, float],
) -> bool:
    if seg.a[0] == seg.b[0]:
        x = seg.a[0]
        lo, hi = sorted((seg.a[1], seg.b[1]))
        return bbox[0] < x < bbox[2] and max(lo, bbox[1]) < min(hi, bbox[3])
    y = seg.a[1]
    lo, hi = sorted((seg.a[0], seg.b[0]))
    return bbox[1] < y < bbox[3] and max(lo, bbox[0]) < min(hi, bbox[2])


def _point_on_segment(point: tuple[float, float], seg: _WireSegment) -> bool:
    if seg.a[0] == seg.b[0] == point[0]:
        return min(seg.a[1], seg.b[1]) <= point[1] <= max(seg.a[1], seg.b[1])
    if seg.a[1] == seg.b[1] == point[1]:
        return min(seg.a[0], seg.b[0]) <= point[0] <= max(seg.a[0], seg.b[0])
    return False


def _wire_crossings(wires: list[_WireSegment], junctions: set[tuple[float, float]]) -> int:
    count = 0
    for i, first in enumerate(wires):
        for second in wires[i + 1 :]:
            if first.a[0] == first.b[0] and second.a[1] == second.b[1]:
                vertical, horizontal = first, second
            elif second.a[0] == second.b[0] and first.a[1] == first.b[1]:
                vertical, horizontal = second, first
            else:
                continue
            point = (vertical.a[0], horizontal.a[1])
            if not (_point_on_segment(point, vertical) and _point_on_segment(point, horizontal)):
                continue
            if point in {vertical.a, vertical.b, horizontal.a, horizontal.b} or point in junctions:
                continue
            count += 1
    return count


def _graph_bend_metrics(wires: list[_WireSegment]) -> tuple[int, int]:
    adjacency: dict[tuple[float, float], list[_WireSegment]] = {}
    for wire in wires:
        adjacency.setdefault(wire.a, []).append(wire)
        adjacency.setdefault(wire.b, []).append(wire)
    bends = 0
    for segments in adjacency.values():
        if len(segments) == 2:
            orientations = {segment.a[0] == segment.b[0] for segment in segments}
            bends += len(orientations) == 2

    # Count connected wire components whose local bend count exceeds three.
    visited: set[int] = set()
    excessive = 0
    for start in range(len(wires)):
        if start in visited:
            continue
        stack = [start]
        component: set[int] = set()
        while stack:
            idx = stack.pop()
            if idx in component:
                continue
            component.add(idx)
            for point in (wires[idx].a, wires[idx].b):
                for other in adjacency.get(point, []):
                    other_idx = wires.index(other)
                    if other_idx not in component:
                        stack.append(other_idx)
        visited |= component
        local_points = {point for idx in component for point in (wires[idx].a, wires[idx].b)}
        local_bends = 0
        for point in local_points:
            segments = [wire for wire in adjacency[point] if wires.index(wire) in component]
            if len(segments) == 2 and len({s.a[0] == s.b[0] for s in segments}) == 2:
                local_bends += 1
        excessive += local_bends > 3
    return bends, excessive
