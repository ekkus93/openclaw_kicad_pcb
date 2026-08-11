"""Deterministic presentation-only fingerprints for schematic refinement cycles."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

from kicad_pcb.errors import UserError
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode

from .schematic_semantics import extract_schematic_semantics_from_doc

LAYOUT_FINGERPRINT_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class SchematicLayoutFingerprint:
    """Canonical visual geometry state, deliberately excluding electrical semantics."""

    schema_version: str
    digest: str
    components: tuple[tuple[str, str, str, float, float, int], ...]
    wires: tuple[tuple[str, tuple[tuple[float, float], ...]], ...]
    labels: tuple[tuple[str, str, str, float, float, int], ...]
    junctions: tuple[tuple[str, float, float], ...]
    no_connects: tuple[tuple[float, float], ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def compute_schematic_layout_fingerprint(path: Path) -> SchematicLayoutFingerprint:
    """Hash canonical schematic presentation geometry independent of file formatting."""

    doc = SchematicDoc.load(path)
    semantic = extract_schematic_semantics_from_doc(doc)
    components: list[tuple[str, str, str, float, float, int]] = []
    for component in semantic.components:
        if not component.uuid:
            raise UserError(
                "Layout fingerprint requires stable component UUIDs.",
                code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
                details={"ref": component.ref, "unit": component.unit},
            )
        components.append(
            (
                component.uuid,
                component.ref,
                component.unit,
                _coord(component.x),
                _coord(component.y),
                component.rotation % 360,
            )
        )

    wires: list[tuple[str, tuple[tuple[float, float], ...]]] = []
    labels: list[tuple[str, str, str, float, float, int]] = []
    junctions: list[tuple[str, float, float]] = []
    no_connects: list[tuple[float, float]] = []
    for node in doc.root.items:
        if not isinstance(node, ListNode):
            continue
        if node.key == "wire":
            uuid = _required_uuid(node, kind="wire")
            points = tuple(_wire_points(node))
            reverse = tuple(reversed(points))
            wires.append((uuid, min(points, reverse)))
        elif node.key in {"label", "global_label", "hierarchical_label"}:
            uuid = _required_uuid(node, kind=node.key)
            x, y, rotation = _at(node)
            labels.append(
                (
                    uuid,
                    node.key,
                    _first_string(node),
                    _coord(x),
                    _coord(y),
                    rotation % 360,
                )
            )
        elif node.key == "junction":
            uuid = _required_uuid(node, kind="junction")
            x, y, _rotation = _at(node)
            junctions.append((uuid, _coord(x), _coord(y)))
        elif node.key == "no_connect":
            x, y, _rotation = _at(node)
            no_connects.append((_coord(x), _coord(y)))

    component_tuple = tuple(sorted(components))
    wire_tuple = tuple(sorted(wires))
    label_tuple = tuple(sorted(labels))
    junction_tuple = tuple(sorted(junctions))
    no_connect_tuple = tuple(sorted(no_connects))
    payload = {
        "schema_version": LAYOUT_FINGERPRINT_SCHEMA_VERSION,
        "components": component_tuple,
        "wires": wire_tuple,
        "labels": label_tuple,
        "junctions": junction_tuple,
        "no_connects": no_connect_tuple,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return SchematicLayoutFingerprint(
        schema_version=LAYOUT_FINGERPRINT_SCHEMA_VERSION,
        digest=hashlib.sha256(encoded).hexdigest(),
        components=component_tuple,
        wires=wire_tuple,
        labels=label_tuple,
        junctions=junction_tuple,
        no_connects=no_connect_tuple,
    )


def _required_uuid(node: ListNode, *, kind: str) -> str:
    for child in node.items:
        if (
            isinstance(child, ListNode)
            and child.key == "uuid"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
            and child.items[1].value
        ):
            return child.items[1].value
    raise UserError(
        f"Layout fingerprint requires stable {kind} UUIDs.",
        code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
    )


def _first_string(node: ListNode) -> str:
    if len(node.items) >= 2 and isinstance(node.items[1], StringNode):
        return node.items[1].value
    return ""


def _at(node: ListNode) -> tuple[float, float, int]:
    at = next(
        (child for child in node.items if isinstance(child, ListNode) and child.key == "at"),
        None,
    )
    if at is None or len(at.items) < 3:
        raise UserError(
            "Layout object lacks required coordinates.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        )
    x = _num(at.items[1])
    y = _num(at.items[2])
    rotation = int(round(_num(at.items[3]))) if len(at.items) >= 4 else 0
    return x, y, rotation


def _wire_points(node: ListNode) -> list[tuple[float, float]]:
    pts = next(
        (child for child in node.items if isinstance(child, ListNode) and child.key == "pts"),
        None,
    )
    if pts is None:
        raise UserError(
            "Layout wire lacks pts geometry.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        )
    points = [
        (_coord(_num(child.items[1])), _coord(_num(child.items[2])))
        for child in pts.items
        if isinstance(child, ListNode) and child.key == "xy" and len(child.items) >= 3
    ]
    if len(points) < 2:
        raise UserError(
            "Layout wire has incomplete geometry.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        )
    return points


def _num(node: Node) -> float:
    if not isinstance(node, AtomNode):
        raise UserError(
            "Layout geometry contains a non-numeric atom.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        )
    try:
        value = float(node.value)
    except ValueError as exc:
        raise UserError(
            "Layout geometry contains an invalid numeric atom.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        ) from exc
    if not math.isfinite(value):
        raise UserError(
            "Layout geometry contains a non-finite coordinate.",
            code="REFINEMENT_LAYOUT_FINGERPRINT_INVALID",
        )
    return value


def _coord(value: float) -> float:
    rounded = round(value, 9)
    return 0.0 if rounded == 0 else rounded
