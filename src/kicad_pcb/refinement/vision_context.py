"""Stable model-visible object map bound to exact render and schematic hashes."""

from __future__ import annotations

import hashlib
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode

from .metrics import RefinementMetricReport
from .rendering import SchematicRenderArtifact
from .schematic_semantics import (
    extract_schematic_semantics_from_doc,
    resolve_component_pin_position_candidates,
)


@dataclass(frozen=True)
class VisionPinObject:
    object_id: str
    ref: str
    unit: str
    pin: str
    positions_mm: tuple[tuple[float, float], ...]
    positions_px: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class VisionComponentObject:
    object_id: str
    uuid: str
    ref: str
    unit: str
    symbol_id: str
    value: str
    x_mm: float
    y_mm: float
    rotation_deg: int
    x_px: float
    y_px: float


@dataclass(frozen=True)
class VisionWireObject:
    object_id: str
    uuid: str
    points_mm: tuple[tuple[float, float], ...]
    points_px: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class VisionLabelObject:
    object_id: str
    uuid: str
    kind: str
    text: str
    x_mm: float
    y_mm: float
    x_px: float
    y_px: float


@dataclass(frozen=True)
class VisionJunctionObject:
    object_id: str
    uuid: str
    x_mm: float
    y_mm: float
    x_px: float
    y_px: float


@dataclass(frozen=True)
class VisionNetObject:
    object_id: str
    name: str
    terminals: tuple[str, ...]


@dataclass(frozen=True)
class VisionReviewRegion:
    region_id: str
    image_index: int
    row: int
    column: int
    png_hash: str
    view_box_mm: tuple[float, float, float, float]
    image_px: tuple[int, int]
    pixels_per_mm: tuple[float, float]


@dataclass(frozen=True)
class VisionObjectMap:
    schema_version: str
    source_schematic_hash: str
    render_png_hash: str
    sheet_id: str
    page_mm: tuple[float, float]
    image_px: tuple[int, int]
    components: tuple[VisionComponentObject, ...]
    pins: tuple[VisionPinObject, ...]
    wires: tuple[VisionWireObject, ...]
    labels: tuple[VisionLabelObject, ...]
    junctions: tuple[VisionJunctionObject, ...]
    nets: tuple[VisionNetObject, ...]
    deterministic_metrics: dict[str, object]
    review_regions: tuple[VisionReviewRegion, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    @property
    def object_ids(self) -> frozenset[str]:
        return frozenset(
            obj.object_id
            for collection in (
                self.components,
                self.pins,
                self.wires,
                self.labels,
                self.junctions,
                self.nets,
            )
            for obj in collection
        )


def build_vision_object_map(
    schematic: Path,
    *,
    authoritative_ir: CircuitIR,
    render: SchematicRenderArtifact,
    metrics: RefinementMetricReport,
) -> VisionObjectMap:
    if render.schematic_hash != metrics.schematic_hash:
        raise UserError(
            "Render and metric artifacts refer to different schematic bytes.",
            code="REFINEMENT_STALE",
        )
    if render.schematic_hash != _sha(schematic):
        raise UserError("Schematic changed after render/metric capture.", code="REFINEMENT_STALE")
    doc = SchematicDoc.load(schematic)
    semantic = extract_schematic_semantics_from_doc(doc)
    components: list[VisionComponentObject] = []
    pins: list[VisionPinObject] = []
    for component in semantic.components:
        px = _px(render, component.x, component.y)
        components.append(
            VisionComponentObject(
                f"component:{component.uuid}",
                component.uuid,
                component.ref,
                component.unit,
                component.symbol_id,
                component.value,
                component.x,
                component.y,
                component.rotation,
                px[0],
                px[1],
            )
        )
        for terminal, positions in resolve_component_pin_position_candidates(
            doc, component
        ).items():
            pin_id = f"pin:{terminal.ref}:{terminal.unit}:{terminal.pin}"
            pins.append(
                VisionPinObject(
                    pin_id,
                    terminal.ref,
                    terminal.unit,
                    terminal.pin,
                    positions,
                    tuple(_px(render, *p) for p in positions),
                )
            )

    wires: list[VisionWireObject] = []
    labels: list[VisionLabelObject] = []
    junctions: list[VisionJunctionObject] = []
    for node in doc.root.items:
        if not isinstance(node, ListNode):
            continue
        if node.key == "wire":
            uuid = _uuid(node)
            if not uuid:
                raise UserError(
                    "Vision object map requires stable wire UUIDs.",
                    code="REFINEMENT_OBJECT_ID_MISSING",
                )
            points = tuple(_wire_points(node))
            wires.append(
                VisionWireObject(
                    f"wire:{uuid}", uuid, points, tuple(_px(render, *p) for p in points)
                )
            )
        elif node.key in {"label", "global_label", "hierarchical_label"}:
            uuid = _uuid(node)
            if not uuid:
                raise UserError(
                    "Vision object map requires stable label UUIDs.",
                    code="REFINEMENT_OBJECT_ID_MISSING",
                )
            x, y = _at(node)
            px = _px(render, x, y)
            labels.append(
                VisionLabelObject(
                    f"label:{uuid}", uuid, node.key, _first_string(node), x, y, px[0], px[1]
                )
            )
        elif node.key == "junction":
            uuid = _uuid(node)
            if not uuid:
                raise UserError(
                    "Vision object map requires stable junction UUIDs.",
                    code="REFINEMENT_OBJECT_ID_MISSING",
                )
            x, y = _at(node)
            px = _px(render, x, y)
            junctions.append(VisionJunctionObject(f"junction:{uuid}", uuid, x, y, px[0], px[1]))

    nets = tuple(
        sorted(
            (
                VisionNetObject(
                    f"net:{net.name}",
                    net.name,
                    tuple(sorted(f"pin:{pin.ref}:{pin.unit or '1'}:{pin.pin}" for pin in net.pins)),
                )
                for net in authoritative_ir.nets
            ),
            key=lambda item: item.name,
        )
    )
    review_regions = _vision_review_regions(render)
    return VisionObjectMap(
        "1.1",
        render.schematic_hash,
        render.png_hash,
        render.sheet_id,
        (render.svg_view_box_mm[2], render.svg_view_box_mm[3]),
        (render.width_px, render.height_px),
        tuple(sorted(components, key=lambda c: c.object_id)),
        tuple(sorted(pins, key=lambda p: p.object_id)),
        tuple(sorted(wires, key=lambda w: w.object_id)),
        tuple(sorted(labels, key=lambda label: label.object_id)),
        tuple(sorted(junctions, key=lambda j: j.object_id)),
        nets,
        metrics.to_dict(),
        review_regions,
    )


def _vision_review_regions(render: SchematicRenderArtifact) -> tuple[VisionReviewRegion, ...]:
    if not render.review_regions:
        return (
            VisionReviewRegion(
                region_id="r00-c00",
                image_index=0,
                row=0,
                column=0,
                png_hash=render.png_hash,
                view_box_mm=render.svg_view_box_mm,
                image_px=(render.width_px, render.height_px),
                pixels_per_mm=(render.pixels_per_mm_x, render.pixels_per_mm_y),
            ),
        )
    return tuple(
        VisionReviewRegion(
            region_id=region.region_id,
            image_index=region.image_index,
            row=region.row,
            column=region.column,
            png_hash=region.png_hash,
            view_box_mm=region.view_box_mm,
            image_px=(region.width_px, region.height_px),
            pixels_per_mm=(region.pixels_per_mm_x, region.pixels_per_mm_y),
        )
        for region in render.review_regions
    )


def _px(render: SchematicRenderArtifact, x: float, y: float) -> tuple[float, float]:
    vx, vy, _vw, _vh = render.svg_view_box_mm
    return (
        round((x - vx) * render.pixels_per_mm_x, 3),
        round((y - vy) * render.pixels_per_mm_y, 3),
    )


def _uuid(node: ListNode) -> str:
    for child in node.items:
        if (
            isinstance(child, ListNode)
            and child.key == "uuid"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            return child.items[1].value
    return ""


def _first_string(node: ListNode) -> str:
    return (
        node.items[1].value
        if len(node.items) >= 2 and isinstance(node.items[1], StringNode)
        else ""
    )


def _at(node: ListNode) -> tuple[float, float]:
    at = next((c for c in node.items if isinstance(c, ListNode) and c.key == "at"), None)
    if at is None or len(at.items) < 3:
        raise UserError(
            "Vision geometry object lacks coordinates.", code="REFINEMENT_OBJECT_ID_MISSING"
        )
    return _num(at.items[1]), _num(at.items[2])


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = next((c for c in node.items if isinstance(c, ListNode) and c.key == "pts"), None)
    if pts is None:
        raise UserError("Vision wire lacks pts geometry.", code="REFINEMENT_OBJECT_ID_MISSING")
    result = []
    for xy in pts.items:
        if isinstance(xy, ListNode) and xy.key == "xy" and len(xy.items) >= 3:
            result.append((_num(xy.items[1]), _num(xy.items[2])))
    if len(result) < 2:
        raise UserError("Vision wire has incomplete geometry.", code="REFINEMENT_OBJECT_ID_MISSING")
    return result


def _num(node: Node) -> float:
    if not isinstance(node, AtomNode):
        raise UserError(
            "Invalid vision geometry numeric atom.", code="REFINEMENT_OBJECT_ID_MISSING"
        )
    value = float(node.value)
    if not math.isfinite(value):
        raise UserError("Non-finite vision geometry.", code="REFINEMENT_OBJECT_ID_MISSING")
    return value


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
