from __future__ import annotations

from pathlib import Path
from uuid import UUID

from kicad_pcb.corpus.normalization import (
    normalize_for_kicad_export,
    serialize_normalized_schematic,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.nodes import ListNode, StringNode

MODEL_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "model_kicad_files"
    / "mcp2551-can-transciever.kicad_sch"
)


def test_normalize_for_kicad_export_removes_circuitsnips_root_comments() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)

    normalized = normalize_for_kicad_export(doc, fixture_id="mcp2551-can-transciever")

    root_keys = [item.key for item in normalized.doc.root.items if isinstance(item, ListNode)]
    assert "comment" not in root_keys
    assert "removed_top_level_comments" in normalized.changes


def test_normalize_for_kicad_export_replaces_invalid_root_uuid() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)

    normalized = normalize_for_kicad_export(doc, fixture_id="mcp2551-can-transciever")

    UUID(normalized.root_uuid)
    assert normalized.root_uuid != "circuit-1779827547560"
    assert "replaced_invalid_root_uuid" in normalized.changes


def test_normalize_for_kicad_export_rewrites_standalone_symbol_instance_paths() -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)

    normalized = normalize_for_kicad_export(doc, fixture_id="mcp2551-can-transciever")

    symbol_paths: set[str] = set()
    sheet_paths: set[str] = set()
    for item in normalized.doc.root.items:
        if not isinstance(item, ListNode):
            continue
        if item.key == "symbol":
            _collect_path_values(item, symbol_paths)
        elif item.key == "sheet_instances":
            _collect_path_values(item, sheet_paths)

    assert symbol_paths == {f"/{normalized.root_uuid}"}
    assert sheet_paths == {"/"}


def test_normalized_schematic_is_still_parseable_by_internal_parser(tmp_path: Path) -> None:
    doc = SchematicDoc.load(MODEL_FIXTURE)
    normalized = normalize_for_kicad_export(doc, fixture_id="mcp2551-can-transciever")
    output = tmp_path / "source_normalized.kicad_sch"
    output.write_text(serialize_normalized_schematic(normalized), encoding="utf-8")

    reparsed = SchematicDoc.load(output)

    assert reparsed.root.key == "kicad_sch"


def _collect_path_values(node: ListNode, output: set[str]) -> None:
    if node.key == "path" and len(node.items) >= 2 and isinstance(node.items[1], StringNode):
        output.add(node.items[1].value)
    for child in node.items:
        if isinstance(child, ListNode):
            _collect_path_values(child, output)
