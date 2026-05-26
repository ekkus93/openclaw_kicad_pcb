"""Embedded-symbol extraction helpers for source schematics."""

from __future__ import annotations

from pathlib import Path

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.serializer import serialize


def extract_embedded_symbol_defs(doc: SchematicDoc) -> dict[str, ListNode]:
    """Return embedded top-level lib-symbol definitions keyed by symbol id."""

    lib_symbols = next(
        (
            item
            for item in doc.root.items
            if isinstance(item, ListNode) and item.key == "lib_symbols"
        ),
        None,
    )
    if lib_symbols is None:
        return {}

    symbols: dict[str, ListNode] = {}
    for child in lib_symbols.items[1:]:
        if not isinstance(child, ListNode) or child.key != "symbol" or len(child.items) < 2:
            continue
        symbol_id_node = child.items[1]
        if isinstance(symbol_id_node, StringNode):
            symbols[symbol_id_node.value] = child
    return dict(sorted(symbols.items()))


def write_embedded_symbol_library(symbols: dict[str, ListNode], output_file: Path) -> None:
    """Write deterministic fallback embedded-symbol artifact for a fixture."""

    ordered_nodes = [symbols[key] for key in sorted(symbols)]
    root = L(atom("lib_symbols"), *ordered_nodes)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(serialize(root) + "\n", encoding="utf-8")
