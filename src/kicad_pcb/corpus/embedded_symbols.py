"""Embedded-symbol extraction helpers for source schematics."""

from __future__ import annotations

import re
from pathlib import Path

from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom
from kicad_pcb.sexpr.nodes import ListNode, Node, StringNode
from kicad_pcb.sexpr.parser import parse_file
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

    qualified_ids = _qualified_symbol_ids(doc)
    symbols: dict[str, ListNode] = {}
    for child in lib_symbols.items[1:]:
        if not isinstance(child, ListNode) or child.key != "symbol" or len(child.items) < 2:
            continue
        symbol_node = child
        symbol_id_node = child.items[1]
        if isinstance(symbol_id_node, StringNode):
            symbol_id = symbol_id_node.value
            if ":" not in symbol_id:
                qualified_symbol_id = qualified_ids.get(symbol_id)
                if qualified_symbol_id is not None:
                    symbol_id = qualified_symbol_id
                    symbol_node = _rewrite_embedded_symbol_for_library(child, sym_name=symbol_id)
            symbols[symbol_id] = symbol_node
    return dict(sorted(symbols.items()))


def write_embedded_symbol_library(symbols: dict[str, ListNode], output_file: Path) -> None:
    """Write deterministic fallback embedded-symbol artifact for a fixture."""

    ordered_nodes = [symbols[key] for key in sorted(symbols)]
    root = L(atom("lib_symbols"), *ordered_nodes)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(serialize(root) + "\n", encoding="utf-8")


def materialize_embedded_symbol_libraries(
    source_artifact: Path,
    *,
    output_dir: Path,
) -> Path | None:
    """Convert ``source_embedded_symbols.sexpr`` into real ``.kicad_sym`` files.

    The corpus fixture artifact keeps the original lightweight ``(lib_symbols ...)``
    snapshot for review, but evaluation needs actual KiCad symbol library files so
    the generator can resolve custom ``Lib:Symbol`` ids through ``SymbolIndex``.
    """

    if not source_artifact.exists():
        return None

    root = parse_file(source_artifact)
    grouped_symbols: dict[str, list[ListNode]] = {}
    for item in root.items[1:]:
        if not isinstance(item, ListNode) or item.key != "symbol" or len(item.items) < 2:
            continue
        symbol_id_node = item.items[1]
        if not isinstance(symbol_id_node, StringNode) or ":" not in symbol_id_node.value:
            continue
        lib_name, sym_name = symbol_id_node.value.split(":", 1)
        grouped_symbols.setdefault(lib_name, []).extend(
            _materialize_symbol_variants(item, sym_name=sym_name)
        )

    if not grouped_symbols:
        return None

    output_dir.mkdir(parents=True, exist_ok=True)
    for lib_name, symbols in sorted(grouped_symbols.items()):
        library_root = L(
            atom("kicad_symbol_lib"),
            L(atom("version"), atom("20231120")),
            L(atom("generator"), StringNode("kicad_pcb_corpus", root.pos)),
            *symbols,
        )
        (output_dir / f"{lib_name}.kicad_sym").write_text(
            serialize(library_root) + "\n",
            encoding="utf-8",
        )

    return output_dir


def _rewrite_embedded_symbol_for_library(symbol_node: ListNode, *, sym_name: str) -> ListNode:
    rewritten_items: list[Node] = [symbol_node.items[0], StringNode(sym_name, symbol_node.pos)]
    for child in symbol_node.items[2:]:
        if (
            isinstance(child, ListNode)
            and child.key == "extends"
            and len(child.items) >= 2
            and isinstance(child.items[1], StringNode)
        ):
            rewritten_items.append(
                ListNode(
                    (
                        child.items[0],
                        StringNode(child.items[1].value.split(":", 1)[-1], child.items[1].pos),
                        *child.items[2:],
                    ),
                    child.pos,
                )
            )
            continue
        rewritten_items.append(child)
    return ListNode(tuple(rewritten_items), symbol_node.pos)


_SUBSYMBOL_BASE_RE = re.compile(r"^(?P<base>.+)_\d+_\d+$")


def _materialize_symbol_variants(symbol_node: ListNode, *, sym_name: str) -> list[ListNode]:
    inferred_local_name = _infer_local_symbol_name(symbol_node)
    if inferred_local_name is None or inferred_local_name == sym_name:
        return [_rewrite_embedded_symbol_for_library(symbol_node, sym_name=sym_name)]

    base_symbol = _rewrite_embedded_symbol_for_library(symbol_node, sym_name=inferred_local_name)
    alias_symbol = _build_alias_symbol(
        base_symbol,
        alias_name=sym_name,
        base_name=inferred_local_name,
    )
    return [base_symbol, alias_symbol]


def _infer_local_symbol_name(symbol_node: ListNode) -> str | None:
    candidate_bases: set[str] = set()
    for child in symbol_node.items[2:]:
        if (
            not isinstance(child, ListNode)
            or child.key != "symbol"
            or len(child.items) < 2
            or not isinstance(child.items[1], StringNode)
        ):
            continue
        match = _SUBSYMBOL_BASE_RE.match(child.items[1].value)
        if match is not None:
            candidate_bases.add(match.group("base"))
    if len(candidate_bases) == 1:
        return next(iter(candidate_bases))
    return None


def _build_alias_symbol(base_symbol: ListNode, *, alias_name: str, base_name: str) -> ListNode:
    alias_items: list[Node] = [base_symbol.items[0], StringNode(alias_name, base_symbol.pos)]
    extends_node = L(atom("extends"), StringNode(base_name, base_symbol.pos))
    inserted_extends = False

    for child in base_symbol.items[2:]:
        if isinstance(child, ListNode) and child.key == "symbol":
            continue
        if (
            not inserted_extends
            and isinstance(child, ListNode)
            and child.key == "property"
        ):
            alias_items.append(extends_node)
            inserted_extends = True
        alias_items.append(child)

    if not inserted_extends:
        alias_items.append(extends_node)

    return ListNode(tuple(alias_items), base_symbol.pos)


def _qualified_symbol_ids(doc: SchematicDoc) -> dict[str, str]:
    qualified_ids: dict[str, set[str]] = {}
    for item in doc.root.items:
        if not isinstance(item, ListNode) or item.key != "symbol":
            continue
        local_name = None
        qualified_id = None
        for child in item.items:
            if (
                isinstance(child, ListNode)
                and child.key == "lib_name"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                local_name = child.items[1].value
            elif (
                isinstance(child, ListNode)
                and child.key == "lib_id"
                and len(child.items) >= 2
                and isinstance(child.items[1], StringNode)
            ):
                qualified_id = child.items[1].value
        if local_name and qualified_id and ":" in qualified_id:
            qualified_ids.setdefault(local_name, set()).add(qualified_id)
            qualified_ids.setdefault(qualified_id.split(":", 1)[1], set()).add(qualified_id)
    return {
        local_name: next(iter(symbol_ids))
        for local_name, symbol_ids in qualified_ids.items()
        if len(symbol_ids) == 1
    }
