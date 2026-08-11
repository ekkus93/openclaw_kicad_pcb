"""Versioned, deterministic schematic layout operation registry."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Annotated, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.electrical_equivalence import ElectricalTerminal
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom, fnum
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode

from .page_geometry import schematic_page_bounds
from .schematic_semantics import (
    PlacedSchematicComponent,
    extract_schematic_semantics_from_doc,
    resolve_component_pin_position_candidates,
    resolve_component_pin_positions,
)

OPERATION_SCHEMA_VERSION = "1.0"
DEFAULT_GRID_MM = 1.27
DEFAULT_MAX_OPERATIONS = 32
MAX_COORDINATE_ABS_MM = 2000.0


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True, allow_inf_nan=False)


class ComponentTarget(_StrictModel):
    ref: str = Field(min_length=1, max_length=64)
    unit: str | None = Field(default=None, max_length=16)


class _MoveArgs(_StrictModel):
    target: ComponentTarget
    x_mm: float | None = Field(default=None, ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)
    y_mm: float | None = Field(default=None, ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)
    dx_mm: float | None = Field(default=None, ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)
    dy_mm: float | None = Field(default=None, ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)

    @model_validator(mode="after")
    def exact_move_form(self) -> _MoveArgs:
        absolute = self.x_mm is not None or self.y_mm is not None
        delta = self.dx_mm is not None or self.dy_mm is not None
        if absolute == delta:
            raise ValueError("provide exactly one of absolute x/y or delta dx/dy")
        if absolute and (self.x_mm is None or self.y_mm is None):
            raise ValueError("absolute move requires both x_mm and y_mm")
        if delta and (self.dx_mm is None or self.dy_mm is None):
            raise ValueError("delta move requires both dx_mm and dy_mm")
        return self


class MoveComponentArgs(_MoveArgs):
    pass


class MovePowerSymbolArgs(_MoveArgs):
    pass


class RotateComponentArgs(_StrictModel):
    target: ComponentTarget
    angle_deg: Literal[0, 90, 180, 270]


class MoveLabelArgs(_StrictModel):
    label_uuid: str = Field(min_length=1, max_length=128)
    x_mm: float = Field(ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)
    y_mm: float = Field(ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)


class AlignComponentsArgs(_StrictModel):
    targets: tuple[ComponentTarget, ...] = Field(min_length=2, max_length=16)
    axis: Literal["x", "y"]
    coordinate_mm: float | None = Field(default=None, ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)


class DistributeComponentsArgs(_StrictModel):
    targets: tuple[ComponentTarget, ...] = Field(min_length=3, max_length=16)
    axis: Literal["x", "y"]


class MoveComponentGroupArgs(_StrictModel):
    targets: tuple[ComponentTarget, ...] = Field(min_length=2, max_length=16)
    dx_mm: float = Field(ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)
    dy_mm: float = Field(ge=-MAX_COORDINATE_ABS_MM, le=MAX_COORDINATE_ABS_MM)


class RemoveRedundantWireBendArgs(_StrictModel):
    wire_uuid: str = Field(min_length=1, max_length=128)
    expected_points_mm: tuple[tuple[float, float], ...] = Field(min_length=3, max_length=32)


class ShortenWirePathArgs(_StrictModel):
    wire_uuid: str = Field(min_length=1, max_length=128)
    net_name: str = Field(min_length=1, max_length=256)
    expected_points_mm: tuple[tuple[float, float], ...] = Field(min_length=2, max_length=32)


class RerouteExistingNetOrthogonalArgs(_StrictModel):
    wire_uuid: str = Field(min_length=1, max_length=128)
    net_name: str = Field(min_length=1, max_length=256)
    expected_points_mm: tuple[tuple[float, float], ...] = Field(min_length=2, max_length=32)
    region_min_x_mm: float
    region_min_y_mm: float
    region_max_x_mm: float
    region_max_y_mm: float

    @model_validator(mode="after")
    def valid_region(self) -> RerouteExistingNetOrthogonalArgs:
        values = (
            self.region_min_x_mm,
            self.region_min_y_mm,
            self.region_max_x_mm,
            self.region_max_y_mm,
        )
        if not all(math.isfinite(v) and abs(v) <= MAX_COORDINATE_ABS_MM for v in values):
            raise ValueError("routing region must contain finite bounded coordinates")
        if self.region_min_x_mm >= self.region_max_x_mm or self.region_min_y_mm >= self.region_max_y_mm:
            raise ValueError("routing region min must be smaller than max")
        return self


OperationArgs = Annotated[
    MoveComponentArgs
    | MovePowerSymbolArgs
    | RotateComponentArgs
    | MoveLabelArgs
    | AlignComponentsArgs
    | DistributeComponentsArgs
    | MoveComponentGroupArgs
    | RemoveRedundantWireBendArgs
    | ShortenWirePathArgs
    | RerouteExistingNetOrthogonalArgs,
    Field(discriminator=None),
]

_OPERATION_MODELS: dict[str, type[_StrictModel]] = {
    "move_component": MoveComponentArgs,
    "rotate_component": RotateComponentArgs,
    "move_label": MoveLabelArgs,
    "move_power_symbol": MovePowerSymbolArgs,
    "align_components": AlignComponentsArgs,
    "distribute_components": DistributeComponentsArgs,
    "move_component_group": MoveComponentGroupArgs,
    "remove_redundant_wire_bend": RemoveRedundantWireBendArgs,
    "shorten_wire_path": ShortenWirePathArgs,
    "reroute_existing_net_orthogonal": RerouteExistingNetOrthogonalArgs,
}


class LayoutOperationEnvelope(_StrictModel):
    schema_version: Literal["1.0"] = OPERATION_SCHEMA_VERSION
    operation_id: str = Field(min_length=1, max_length=128)
    source_schematic_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operation_type: str = Field(min_length=1, max_length=64)
    arguments: dict[str, object]


@dataclass(frozen=True)
class LayoutOperationPolicy:
    grid_mm: float = DEFAULT_GRID_MM
    max_operations: int = DEFAULT_MAX_OPERATIONS
    max_component_move_mm: float = 100.0


@dataclass(frozen=True)
class LayoutOperationResult:
    operation_id: str
    operation_type: str
    status: str
    details: dict[str, object]


@dataclass(frozen=True)
class LayoutOperationBatchResult:
    source_hash: str
    candidate_hash: str
    results: tuple[LayoutOperationResult, ...]


def registered_operation_schemas() -> dict[str, dict[str, object]]:
    return {name: model.model_json_schema() for name, model in sorted(_OPERATION_MODELS.items())}


def validate_operation_envelopes(
    payloads: list[dict[str, object]] | tuple[dict[str, object], ...],
    *,
    expected_source_hash: str,
    max_operations: int = DEFAULT_MAX_OPERATIONS,
) -> tuple[tuple[LayoutOperationEnvelope, _StrictModel], ...]:
    if len(payloads) > max_operations:
        raise UserError(
            "Layout operation batch exceeds configured maximum.",
            code="REFINEMENT_OPERATION_BUDGET_EXCEEDED",
            details={"count": len(payloads), "max_operations": max_operations},
        )
    parsed: list[tuple[LayoutOperationEnvelope, _StrictModel]] = []
    ids: set[str] = set()
    for raw in payloads:
        envelope = LayoutOperationEnvelope.model_validate(raw)
        if envelope.operation_id in ids:
            raise UserError("Duplicate layout operation id.", code="REFINEMENT_DUPLICATE_OPERATION_ID")
        ids.add(envelope.operation_id)
        if envelope.source_schematic_hash != expected_source_hash:
            raise UserError("Layout operation is stale for current schematic.", code="REFINEMENT_STALE")
        model = _OPERATION_MODELS.get(envelope.operation_type)
        if model is None:
            raise UserError(
                f"Unsupported layout operation: {envelope.operation_type}",
                code="REFINEMENT_UNSUPPORTED_OPERATION",
            )
        parsed.append((envelope, model.model_validate(envelope.arguments)))
    return tuple(parsed)


def execute_layout_operations(
    candidate_path: Path,
    payloads: list[dict[str, object]] | tuple[dict[str, object], ...],
    *,
    expected_source_hash: str,
    authoritative_ir: CircuitIR | None = None,
    policy: LayoutOperationPolicy = LayoutOperationPolicy(),
) -> LayoutOperationBatchResult:
    """Apply a validated operation batch entirely in memory, then save once."""

    current_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    if current_hash != expected_source_hash:
        raise UserError("Candidate does not match operation source hash.", code="REFINEMENT_STALE")
    parsed = validate_operation_envelopes(
        payloads,
        expected_source_hash=expected_source_hash,
        max_operations=policy.max_operations,
    )
    doc = SchematicDoc.load(candidate_path)
    results: list[LayoutOperationResult] = []
    for envelope, args in parsed:
        details = _apply_operation(doc, envelope.operation_type, args, authoritative_ir, policy)
        results.append(LayoutOperationResult(envelope.operation_id, envelope.operation_type, "applied", details))
    doc.save(candidate_path)
    candidate_hash = hashlib.sha256(candidate_path.read_bytes()).hexdigest()
    return LayoutOperationBatchResult(current_hash, candidate_hash, tuple(results))


def _apply_operation(
    doc: SchematicDoc,
    operation_type: str,
    args: _StrictModel,
    authoritative_ir: CircuitIR | None,
    policy: LayoutOperationPolicy,
) -> dict[str, object]:
    if operation_type in {"move_component", "move_power_symbol"}:
        assert isinstance(args, _MoveArgs)
        component = _resolve_component(doc, args.target, power_only=operation_type == "move_power_symbol")
        x, y = _resolve_move(component, args, policy)
        _move_component(doc, component, x=x, y=y, rotation=component.rotation, policy=policy)
        return {"ref": component.ref, "unit": component.unit, "x_mm": x, "y_mm": y}
    if operation_type == "rotate_component":
        assert isinstance(args, RotateComponentArgs)
        component = _resolve_component(doc, args.target)
        _move_component(doc, component, x=component.x, y=component.y, rotation=args.angle_deg, policy=policy)
        return {"ref": component.ref, "unit": component.unit, "angle_deg": args.angle_deg}
    if operation_type == "move_label":
        assert isinstance(args, MoveLabelArgs)
        _validate_point(doc, args.x_mm, args.y_mm, policy)
        node = _find_top_level_uuid(doc, args.label_uuid, {"label", "global_label", "hierarchical_label"})
        _replace_top_level(doc, node, _replace_at(node, args.x_mm, args.y_mm))
        return {"label_uuid": args.label_uuid, "x_mm": args.x_mm, "y_mm": args.y_mm}
    if operation_type == "align_components":
        assert isinstance(args, AlignComponentsArgs)
        components = _resolve_targets(doc, args.targets)
        coordinate = args.coordinate_mm
        if coordinate is None:
            coordinate = sum(c.x if args.axis == "x" else c.y for c in components) / len(components)
        coordinate = _snap(coordinate, policy.grid_mm)
        for component in components:
            x = coordinate if args.axis == "x" else component.x
            y = coordinate if args.axis == "y" else component.y
            _move_component(doc, _resolve_component(doc, ComponentTarget(ref=component.ref, unit=component.unit)), x=x, y=y, rotation=component.rotation, policy=policy)
        return {"axis": args.axis, "coordinate_mm": coordinate, "refs": [c.ref for c in components]}
    if operation_type == "distribute_components":
        assert isinstance(args, DistributeComponentsArgs)
        components = _resolve_targets(doc, args.targets)
        ordered = sorted(components, key=lambda c: c.x if args.axis == "x" else c.y)
        first = ordered[0].x if args.axis == "x" else ordered[0].y
        last = ordered[-1].x if args.axis == "x" else ordered[-1].y
        step = (last - first) / (len(ordered) - 1)
        for index, original in enumerate(ordered[1:-1], 1):
            coordinate = _snap(first + step * index, policy.grid_mm)
            current = _resolve_component(doc, ComponentTarget(ref=original.ref, unit=original.unit))
            x = coordinate if args.axis == "x" else current.x
            y = coordinate if args.axis == "y" else current.y
            _move_component(doc, current, x=x, y=y, rotation=current.rotation, policy=policy)
        return {"axis": args.axis, "refs": [c.ref for c in ordered]}
    if operation_type == "move_component_group":
        assert isinstance(args, MoveComponentGroupArgs)
        components = _resolve_targets(doc, args.targets)
        for original in components:
            current = _resolve_component(doc, ComponentTarget(ref=original.ref, unit=original.unit))
            x = _snap(current.x + args.dx_mm, policy.grid_mm)
            y = _snap(current.y + args.dy_mm, policy.grid_mm)
            _move_component(doc, current, x=x, y=y, rotation=current.rotation, policy=policy)
        return {"refs": [c.ref for c in components], "dx_mm": args.dx_mm, "dy_mm": args.dy_mm}
    if operation_type == "remove_redundant_wire_bend":
        assert isinstance(args, RemoveRedundantWireBendArgs)
        node, points = _resolve_wire(doc, args.wire_uuid, args.expected_points_mm)
        simplified = _simplify_orthogonal(points)
        if len(simplified) >= len(points):
            raise UserError("Wire has no redundant bend to remove.", code="REFINEMENT_OPERATION_NO_EFFECT")
        _replace_top_level(doc, node, _replace_wire_points(node, simplified))
        return {"wire_uuid": args.wire_uuid, "before_points": len(points), "after_points": len(simplified)}
    if operation_type in {"shorten_wire_path", "reroute_existing_net_orthogonal"}:
        if authoritative_ir is None:
            raise UserError("Wire routing operation requires authoritative Circuit IR.", code="REFINEMENT_WIRE_CONTEXT_REQUIRED")
        if operation_type == "shorten_wire_path":
            assert isinstance(args, ShortenWirePathArgs)
            node, points = _resolve_wire(doc, args.wire_uuid, args.expected_points_mm)
            _validate_wire_net_context(doc, authoritative_ir, args.net_name, points)
            replacement = _shorter_manhattan(points)
            if replacement is None:
                raise UserError("No safe shorter orthogonal path exists.", code="REFINEMENT_OPERATION_NO_EFFECT")
        else:
            assert isinstance(args, RerouteExistingNetOrthogonalArgs)
            node, points = _resolve_wire(doc, args.wire_uuid, args.expected_points_mm)
            _validate_wire_net_context(doc, authoritative_ir, args.net_name, points)
            replacement = _bounded_reroute(args, points, policy)
        _validate_route_collision(doc, node, replacement)
        _replace_top_level(doc, node, _replace_wire_points(node, replacement))
        return {"wire_uuid": args.wire_uuid, "net_name": args.net_name, "points": [list(p) for p in replacement]}
    raise AssertionError(operation_type)


def _resolve_component(doc: SchematicDoc, target: ComponentTarget, *, power_only: bool = False) -> PlacedSchematicComponent:
    semantic = extract_schematic_semantics_from_doc(doc)
    matches = [c for c in semantic.components if c.ref == target.ref and (target.unit is None or c.unit == target.unit)]
    if power_only:
        matches = [c for c in matches if c.ref in semantic.helper_refs]
    elif any(c.ref in semantic.helper_refs for c in matches):
        raise UserError("Power helper requires move_power_symbol.", code="REFINEMENT_AMBIGUOUS_TARGET")
    if len(matches) != 1:
        raise UserError(
            "Component target did not resolve to exactly one placed unit.",
            code="REFINEMENT_AMBIGUOUS_TARGET",
            details={"ref": target.ref, "unit": target.unit, "match_count": len(matches)},
        )
    return matches[0]


def _resolve_targets(doc: SchematicDoc, targets: tuple[ComponentTarget, ...]) -> list[PlacedSchematicComponent]:
    keys = [(t.ref, t.unit) for t in targets]
    if len(keys) != len(set(keys)):
        raise UserError("Duplicate component targets are not allowed.", code="REFINEMENT_AMBIGUOUS_TARGET")
    return [_resolve_component(doc, target) for target in targets]


def _resolve_move(component: PlacedSchematicComponent, args: _MoveArgs, policy: LayoutOperationPolicy) -> tuple[float, float]:
    if args.x_mm is not None:
        x, y = args.x_mm, args.y_mm
        assert y is not None
    else:
        assert args.dx_mm is not None and args.dy_mm is not None
        if math.hypot(args.dx_mm, args.dy_mm) > policy.max_component_move_mm:
            raise UserError("Component move exceeds configured distance bound.", code="REFINEMENT_OPERATION_OUT_OF_BOUNDS")
        x, y = component.x + args.dx_mm, component.y + args.dy_mm
    return _snap(x, policy.grid_mm), _snap(y, policy.grid_mm)


def _move_component(doc: SchematicDoc, component: PlacedSchematicComponent, *, x: float, y: float, rotation: int, policy: LayoutOperationPolicy) -> None:
    _validate_point(doc, x, y, policy)
    node = _find_component_node(doc, component)
    old_positions = resolve_component_pin_positions(doc, component)
    moved = PlacedSchematicComponent(**{**asdict(component), "x": x, "y": y, "rotation": rotation})
    new_positions = resolve_component_pin_positions(doc, moved)
    mapping = {old_positions[t]: new_positions[t] for t in old_positions if t in new_positions}
    _validate_anchor_move_safety(doc, component, mapping)
    _replace_top_level(doc, node, _replace_at(node, x, y, rotation))
    _retarget_anchors(doc, mapping)


def _validate_anchor_move_safety(doc: SchematicDoc, component: PlacedSchematicComponent, mapping: Mapping[tuple[float, float], tuple[float, float]]) -> None:
    moving_old = set(mapping)
    moving_new = set(mapping.values())
    semantic = extract_schematic_semantics_from_doc(doc)
    stationary_positions: set[tuple[float, float]] = set()
    for other in semantic.components:
        if (other.ref, other.unit) == (component.ref, component.unit):
            continue
        for positions in resolve_component_pin_position_candidates(doc, other).values():
            stationary_positions.update(positions)
    if moving_new & stationary_positions:
        raise UserError("Component move would collide with a stationary pin.", code="REFINEMENT_AMBIGUOUS_TARGET")
    for wire in _wire_nodes(doc):
        points = _wire_points(wire)
        for old in moving_old:
            if _point_on_polyline_interior(old, points):
                raise UserError("Moving pin touches wire interior; mutation is ambiguous.", code="REFINEMENT_AMBIGUOUS_TARGET")
        for new in moving_new:
            if any(_point_on_segment(new, a, b) for a, b in zip(points, points[1:])) and new not in points:
                raise UserError("Moved pin would create an unintended wire contact.", code="REFINEMENT_AMBIGUOUS_TARGET")


def _retarget_anchors(doc: SchematicDoc, mapping: Mapping[tuple[float, float], tuple[float, float]]) -> None:
    items = list(doc.root.items)
    for i, node in enumerate(items):
        if not isinstance(node, ListNode):
            continue
        if node.key == "wire":
            points = _wire_points(node)
            changed = [mapping.get(point, point) if index in {0, len(points) - 1} else point for index, point in enumerate(points)]
            if changed != points:
                items[i] = _replace_wire_points(node, changed)
        elif node.key in {"label", "global_label", "hierarchical_label", "junction", "no_connect"}:
            point = _node_at(node)
            if point in mapping:
                items[i] = _replace_at(node, *mapping[point])
    doc.root = ListNode(tuple(items), doc.root.pos)


def _validate_point(doc: SchematicDoc, x: float, y: float, policy: LayoutOperationPolicy) -> None:
    if not (math.isfinite(x) and math.isfinite(y)):
        raise UserError("Non-finite layout coordinate.", code="REFINEMENT_OPERATION_OUT_OF_BOUNDS")
    if not (_on_grid(x, policy.grid_mm) and _on_grid(y, policy.grid_mm)):
        raise UserError("Layout coordinate is off the executor grid.", code="REFINEMENT_OPERATION_OFF_GRID")
    page = schematic_page_bounds(doc)
    if not (0 <= x <= page.width_mm and 0 <= y <= page.height_mm):
        raise UserError("Layout coordinate lies outside schematic page.", code="REFINEMENT_OPERATION_OUT_OF_BOUNDS")


def _resolve_wire(doc: SchematicDoc, wire_uuid: str, expected: tuple[tuple[float, float], ...]) -> tuple[ListNode, list[tuple[float, float]]]:
    node = _find_top_level_uuid(doc, wire_uuid, {"wire"})
    points = _wire_points(node)
    normalized_expected = [(round(x, 9), round(y, 9)) for x, y in expected]
    if points != normalized_expected:
        raise UserError("Wire endpoints/points changed since plan creation.", code="REFINEMENT_STALE")
    _validate_orthogonal(points)
    return node, points


def _validate_wire_net_context(doc: SchematicDoc, ir: CircuitIR, net_name: str, points: list[tuple[float, float]]) -> None:
    net = next((net for net in ir.nets if net.name == net_name), None)
    if net is None:
        raise UserError("Wire operation references unknown authoritative net.", code="REFINEMENT_WIRE_CONTEXT_REQUIRED")
    endpoints = {points[0], points[-1]}
    evidence: set[tuple[float, float]] = set()
    semantic = extract_schematic_semantics_from_doc(doc)
    components = {(c.ref, c.unit): c for c in semantic.components}
    for pin in net.pins:
        candidates = [c for (ref, _unit), c in components.items() if ref == pin.ref]
        for component in candidates:
            for terminal, positions in resolve_component_pin_position_candidates(doc, component).items():
                if terminal.pin == pin.pin and (pin.unit is None or terminal.unit == str(pin.unit)):
                    evidence.update(positions)
    for node in doc.root.items:
        if isinstance(node, ListNode) and node.key in {"label", "global_label", "hierarchical_label"}:
            name = _first_string(node)
            if name == net_name:
                evidence.add(_node_at(node))
    if not endpoints <= evidence:
        raise UserError(
            "Wire endpoints lack direct authoritative net evidence.",
            code="REFINEMENT_WIRE_CONTEXT_REQUIRED",
            details={"net_name": net_name, "endpoints": [list(p) for p in sorted(endpoints)]},
        )
    for point in endpoints:
        if not (_on_grid(point[0], DEFAULT_GRID_MM) and _on_grid(point[1], DEFAULT_GRID_MM)):
            raise UserError("Wire source endpoint is off grid.", code="REFINEMENT_OPERATION_OFF_GRID")


def _shorter_manhattan(points: list[tuple[float, float]]) -> list[tuple[float, float]] | None:
    start, end = points[0], points[-1]
    candidates = [[start, end]] if start[0] == end[0] or start[1] == end[1] else [
        [start, (start[0], end[1]), end],
        [start, (end[0], start[1]), end],
    ]
    before = _path_length(points)
    valid = [_simplify_orthogonal(candidate) for candidate in candidates if _path_length(candidate) < before - 1e-9]
    return min(valid, key=lambda p: (_path_length(p), p), default=None)


def _bounded_reroute(args: RerouteExistingNetOrthogonalArgs, points: list[tuple[float, float]], policy: LayoutOperationPolicy) -> list[tuple[float, float]]:
    start, end = points[0], points[-1]
    candidates: list[list[tuple[float, float]]] = []
    for x in {_snap(args.region_min_x_mm, policy.grid_mm), _snap(args.region_max_x_mm, policy.grid_mm), start[0], end[0]}:
        candidate = _simplify_orthogonal([start, (x, start[1]), (x, end[1]), end])
        candidates.append(candidate)
    for y in {_snap(args.region_min_y_mm, policy.grid_mm), _snap(args.region_max_y_mm, policy.grid_mm), start[1], end[1]}:
        candidate = _simplify_orthogonal([start, (start[0], y), (end[0], y), end])
        candidates.append(candidate)
    def inside(path: list[tuple[float, float]]) -> bool:
        return all(args.region_min_x_mm <= x <= args.region_max_x_mm and args.region_min_y_mm <= y <= args.region_max_y_mm for x, y in path)
    choices = [p for p in candidates if inside(p) and p != points and len(p) == len(set(p))]
    if not choices:
        raise UserError("No bounded reroute candidate exists.", code="REFINEMENT_OPERATION_NO_EFFECT")
    return min(choices, key=lambda p: (_path_length(p), len(p), p))


def _validate_route_collision(doc: SchematicDoc, source_node: ListNode, points: list[tuple[float, float]]) -> None:
    _validate_orthogonal(points)
    endpoints = {points[0], points[-1]}
    for wire in _wire_nodes(doc):
        if wire is source_node:
            continue
        other = _wire_points(wire)
        for a, b in zip(points, points[1:]):
            for c, d in zip(other, other[1:]):
                intersection = _orthogonal_intersection(a, b, c, d)
                if intersection is not None and intersection not in endpoints:
                    raise UserError("Reroute would collide with unrelated wire geometry.", code="REFINEMENT_ROUTE_COLLISION")
    semantic = extract_schematic_semantics_from_doc(doc)
    for component in semantic.components:
        bbox = (component.x - 5.08, component.y - 5.08, component.x + 5.08, component.y + 5.08)
        for a, b in zip(points, points[1:]):
            if _segment_crosses_bbox(a, b, bbox) and a not in endpoints and b not in endpoints:
                raise UserError("Reroute would cross a component body.", code="REFINEMENT_ROUTE_COLLISION")


def _simplify_orthogonal(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    result: list[tuple[float, float]] = []
    for point in points:
        if result and point == result[-1]:
            continue
        result.append(point)
        while len(result) >= 3:
            a, b, c = result[-3:]
            if (a[0] == b[0] == c[0]) or (a[1] == b[1] == c[1]):
                result.pop(-2)
            else:
                break
    _validate_orthogonal(result)
    return result


def _validate_orthogonal(points: list[tuple[float, float]]) -> None:
    if len(points) < 2:
        raise UserError("Wire requires at least two points.", code="REFINEMENT_AMBIGUOUS_TARGET")
    for a, b in zip(points, points[1:]):
        if a[0] != b[0] and a[1] != b[1]:
            raise UserError("Wire operation requires orthogonal geometry.", code="REFINEMENT_AMBIGUOUS_TARGET")


def _wire_nodes(doc: SchematicDoc) -> list[ListNode]:
    return [node for node in doc.root.items if isinstance(node, ListNode) and node.key == "wire"]


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = next((c for c in node.items if isinstance(c, ListNode) and c.key == "pts"), None)
    if pts is None:
        raise UserError("Wire missing pts.", code="REFINEMENT_AMBIGUOUS_TARGET")
    result = []
    for xy in pts.items:
        if isinstance(xy, ListNode) and xy.key == "xy" and len(xy.items) >= 3:
            result.append((round(_num(xy.items[1]), 9), round(_num(xy.items[2]), 9)))
    if len(result) < 2:
        raise UserError("Wire geometry is incomplete.", code="REFINEMENT_AMBIGUOUS_TARGET")
    return result


def _replace_wire_points(node: ListNode, points: list[tuple[float, float]]) -> ListNode:
    replacement = L(atom("pts"), *(L(atom("xy"), fnum(x, 2), fnum(y, 2)) for x, y in points))
    items = [replacement if isinstance(child, ListNode) and child.key == "pts" else child for child in node.items]
    return ListNode(tuple(items), node.pos)


def _find_component_node(doc: SchematicDoc, component: PlacedSchematicComponent) -> ListNode:
    for node in doc.root.items:
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue
        if _property(node, "Reference") == component.ref and (_atom_child(node, "unit") or "1") == component.unit:
            return node
    raise UserError("Component disappeared during operation batch.", code="REFINEMENT_STALE")


def _find_top_level_uuid(doc: SchematicDoc, uuid: str, allowed_keys: set[str]) -> ListNode:
    matches = [node for node in doc.root.items if isinstance(node, ListNode) and node.key in allowed_keys and _string_child(node, "uuid") == uuid]
    if len(matches) != 1:
        raise UserError("Exact geometry UUID did not resolve uniquely.", code="REFINEMENT_AMBIGUOUS_TARGET", details={"uuid": uuid, "match_count": len(matches)})
    return matches[0]


def _replace_top_level(doc: SchematicDoc, original: ListNode, replacement: ListNode) -> None:
    items = list(doc.root.items)
    index = next((i for i, item in enumerate(items) if item is original), None)
    if index is None:
        uuid = _string_child(original, "uuid")
        candidates = [i for i, item in enumerate(items) if isinstance(item, ListNode) and item.key == original.key and uuid and _string_child(item, "uuid") == uuid]
        if len(candidates) != 1:
            raise UserError("Geometry target became stale during batch.", code="REFINEMENT_STALE")
        index = candidates[0]
    items[index] = replacement
    doc.root = ListNode(tuple(items), doc.root.pos)


def _replace_at(node: ListNode, x: float, y: float, rotation: int | None = None) -> ListNode:
    found = False
    items: list[Node] = []
    for child in node.items:
        if isinstance(child, ListNode) and child.key == "at":
            angle = rotation
            if angle is None and len(child.items) >= 4:
                angle = int(_num(child.items[3]))
            at_items: list[Node] = [atom("at"), fnum(x, 2), fnum(y, 2)]
            if angle is not None:
                at_items.append(atom(str(angle)))
            items.append(ListNode(tuple(at_items), child.pos))
            found = True
        else:
            items.append(child)
    if not found:
        raise UserError(f"{node.key} target is missing at coordinates.", code="REFINEMENT_AMBIGUOUS_TARGET")
    return ListNode(tuple(items), node.pos)


def _node_at(node: ListNode) -> tuple[float, float]:
    at = next((c for c in node.items if isinstance(c, ListNode) and c.key == "at"), None)
    if at is None or len(at.items) < 3:
        raise UserError(f"{node.key} target lacks coordinates.", code="REFINEMENT_AMBIGUOUS_TARGET")
    return round(_num(at.items[1]), 9), round(_num(at.items[2]), 9)


def _property(node: ListNode, name: str) -> str:
    for child in node.items:
        if isinstance(child, ListNode) and child.key == "property" and len(child.items) >= 3 and isinstance(child.items[1], StringNode) and child.items[1].value == name and isinstance(child.items[2], StringNode):
            return child.items[2].value
    return ""


def _string_child(node: ListNode, key: str) -> str:
    for child in node.items:
        if isinstance(child, ListNode) and child.key == key and len(child.items) >= 2 and isinstance(child.items[1], StringNode):
            return child.items[1].value
    return ""


def _atom_child(node: ListNode, key: str) -> str:
    for child in node.items:
        if isinstance(child, ListNode) and child.key == key and len(child.items) >= 2 and isinstance(child.items[1], AtomNode):
            return child.items[1].value
    return ""


def _first_string(node: ListNode) -> str:
    return node.items[1].value if len(node.items) >= 2 and isinstance(node.items[1], StringNode) else ""


def _num(node: Node) -> float:
    if not isinstance(node, AtomNode):
        raise UserError("Expected numeric atom in geometry.", code="REFINEMENT_AMBIGUOUS_TARGET")
    try:
        value = float(node.value)
    except ValueError as exc:
        raise UserError("Invalid numeric atom in geometry.", code="REFINEMENT_AMBIGUOUS_TARGET") from exc
    if not math.isfinite(value):
        raise UserError("Non-finite geometry coordinate.", code="REFINEMENT_AMBIGUOUS_TARGET")
    return value


def _snap(value: float, grid: float) -> float:
    return round(round(value / grid) * grid, 9)


def _on_grid(value: float, grid: float) -> bool:
    return abs(value / grid - round(value / grid)) <= 1e-6


def _path_length(points: list[tuple[float, float]]) -> float:
    return sum(abs(a[0] - b[0]) + abs(a[1] - b[1]) for a, b in zip(points, points[1:]))


def _point_on_segment(point: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> bool:
    if a[0] == b[0] == point[0]:
        return min(a[1], b[1]) <= point[1] <= max(a[1], b[1])
    if a[1] == b[1] == point[1]:
        return min(a[0], b[0]) <= point[0] <= max(a[0], b[0])
    return False


def _point_on_polyline_interior(point: tuple[float, float], points: list[tuple[float, float]]) -> bool:
    return any(_point_on_segment(point, a, b) and point not in {a, b} for a, b in zip(points, points[1:]))


def _orthogonal_intersection(a, b, c, d):
    first_vertical = a[0] == b[0]
    second_vertical = c[0] == d[0]
    if first_vertical == second_vertical:
        return None
    v1, v2 = (a, b) if first_vertical else (c, d)
    h1, h2 = (c, d) if first_vertical else (a, b)
    point = (v1[0], h1[1])
    return point if _point_on_segment(point, v1, v2) and _point_on_segment(point, h1, h2) else None


def _segment_crosses_bbox(a, b, bbox):
    if a[0] == b[0]:
        return bbox[0] < a[0] < bbox[2] and max(min(a[1], b[1]), bbox[1]) < min(max(a[1], b[1]), bbox[3])
    return bbox[1] < a[1] < bbox[3] and max(min(a[0], b[0]), bbox[0]) < min(max(a[0], b[0]), bbox[2])
