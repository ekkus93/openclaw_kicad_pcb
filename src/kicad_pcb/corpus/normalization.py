"""KiCad-loader normalization for raw model-corpus schematics.

Raw Internet schematics used for the model corpus can be valid S-expressions
but still fail KiCad's own schematic loader.  This module keeps the raw source
unchanged while producing a deterministic, loader-facing copy suitable for
``kicad-cli sch export netlist``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom, string
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, Node, StringNode
from kicad_pcb.sexpr.serializer import serialize


@dataclass(frozen=True)
class NormalizedSchematic:
    """A normalized schematic document plus the applied change identifiers."""

    doc: SchematicDoc
    root_uuid: str
    changes: tuple[str, ...]


def normalize_for_kicad_export(doc: SchematicDoc, *, fixture_id: str) -> NormalizedSchematic:
    """Return a KiCad-loader-friendly copy of a raw corpus schematic.

    The normalization is intentionally conservative and source-preserving:
    callers should still copy the exact raw input as ``source.kicad_sch``.
    This function is only for the auxiliary ``source_normalized.kicad_sch``
    file used when asking KiCad to export an XML netlist.
    """

    original_root_uuid = _root_uuid(doc)
    root_uuid = _valid_or_deterministic_uuid(original_root_uuid, fixture_id=fixture_id)
    changes: list[str] = []
    if root_uuid != original_root_uuid:
        changes.append("replaced_invalid_root_uuid")

    saw_top_level_comment = False
    saw_sheet_instances = False
    normalized_items: list[Node] = []

    for item in doc.root.items:
        if not isinstance(item, ListNode):
            normalized_items.append(item)
            continue

        if item.key == "comment":
            # KiCad schematic root sections do not define arbitrary top-level
            # metadata comments.  CircuitSnips adds them before (paper ...),
            # so keep them in metadata but remove them from the loader-facing
            # file.
            saw_top_level_comment = True
            continue

        if item.key == "uuid":
            normalized_items.append(L(atom("uuid"), string(root_uuid)))
            continue

        if item.key == "sheet_instances":
            saw_sheet_instances = True
            # Re-create below so a standalone extracted snippet has exactly
            # one root-sheet instance regardless of the source fragment.
            continue

        if item.key == "symbol" and _is_placed_symbol(item):
            normalized_items.append(
                _normalize_symbol_instances(
                    item,
                    root_uuid=root_uuid,
                    fixture_id=fixture_id,
                )
            )
            continue

        normalized_items.append(item)

    if saw_top_level_comment:
        changes.append("removed_top_level_comments")

    if saw_sheet_instances:
        changes.append("normalized_root_sheet_instances")
    else:
        changes.append("added_root_sheet_instances")

    normalized_items = _insert_root_sheet_instances(normalized_items)
    normalized_root = ListNode(tuple(normalized_items), doc.root.pos)
    normalized_doc = SchematicDoc(normalized_root)
    return NormalizedSchematic(doc=normalized_doc, root_uuid=root_uuid, changes=tuple(changes))


def serialize_normalized_schematic(normalized: NormalizedSchematic) -> str:
    """Serialize a normalized schematic with a trailing newline."""

    return serialize(normalized.doc.root) + "\n"


def _root_uuid(doc: SchematicDoc) -> str:
    for item in doc.root.items:
        if (
            isinstance(item, ListNode)
            and item.key == "uuid"
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ):
            return item.items[1].value
    return ""


def _valid_or_deterministic_uuid(value: str, *, fixture_id: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"openclaw-kicad-model-corpus:{fixture_id}"))


def _is_placed_symbol(node: ListNode) -> bool:
    return any(isinstance(child, ListNode) and child.key == "lib_id" for child in node.items)


def _normalize_symbol_instances(
    symbol_node: ListNode,
    *,
    root_uuid: str,
    fixture_id: str,
) -> ListNode:
    ref = _symbol_property(symbol_node, "Reference") or "?"
    unit = _symbol_unit(symbol_node)
    new_items = [
        child
        for child in symbol_node.items
        if not (isinstance(child, ListNode) and child.key == "instances")
    ]
    new_items.append(
        L(
            atom("instances"),
            L(
                atom("project"),
                string(fixture_id),
                L(
                    atom("path"),
                    string(f"/{root_uuid}"),
                    L(atom("reference"), string(ref)),
                    L(atom("unit"), atom(unit)),
                ),
            ),
        )
    )
    return ListNode(tuple(new_items), symbol_node.pos)


def _symbol_property(symbol_node: ListNode, name: str) -> str | None:
    for child in symbol_node.items:
        if not isinstance(child, ListNode) or child.key != "property" or len(child.items) < 3:
            continue
        name_node = child.items[1]
        value_node = child.items[2]
        if (
            isinstance(name_node, StringNode)
            and isinstance(value_node, StringNode)
            and name_node.value == name
        ):
            return value_node.value
    return None


def _symbol_unit(symbol_node: ListNode) -> str:
    for child in symbol_node.items:
        if isinstance(child, ListNode) and child.key == "unit" and len(child.items) >= 2:
            unit_node = child.items[1]
            if isinstance(unit_node, AtomNode):
                return unit_node.value
    return "1"


def _insert_root_sheet_instances(items: list[Node]) -> list[Node]:
    sheet_instances = L(
        atom("sheet_instances"),
        L(atom("path"), string("/"), L(atom("page"), string("1"))),
    )
    for idx, item in enumerate(items):
        if isinstance(item, ListNode) and item.key == "embedded_fonts":
            return items[:idx] + [sheet_instances] + items[idx:]
    return [*items, sheet_instances]
