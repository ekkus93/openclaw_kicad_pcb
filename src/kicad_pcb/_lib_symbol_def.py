"""KiCad symbol definition readers (def, chain, flat)."""

from __future__ import annotations

from pathlib import Path

from ._lib_symbol_primitives import (
    _collect_subsymbols,
    _find_lib_symbol,
    _qualify_extends,
    _rename_subsymbol,
    _resolve_sym_chain,
    _strip_id_nodes,
    _symbol_id,
)
from .sexpr.builder import string
from .sexpr.nodes import NO_POS, ListNode, Node


def read_lib_symbol_def(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> ListNode | None:
    """Load a symbol definition from a KiCad symbol library and prepare it for embedding.

    Modifications applied before returning:

    * Root symbol id renamed from ``"sym_name"`` → ``"lib_name:sym_name"``.
    * ``(id N)`` child items stripped (KiCad 9 schematics do not use them).

    .. note::
        This function does **not** follow ``(extends …)`` chains.  For symbols
        that inherit from a base, use :func:`read_lib_symbol_def_chain` or
        :func:`read_lib_symbol_def_flat` so the full inheritance tree is
        present in the schematic's ``lib_symbols`` section.

    Returns ``None`` when the library file or symbol is not found.
    Raises on library parse/read failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Device"``).
    sym_name:    Symbol name within the library (e.g. ``"R"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.  Defaults to the
                 system KiCad symbols directory if ``None``.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return None
    lib_root, _, _complete = resolved
    sym_node = _find_lib_symbol(lib_root, sym_name)
    if sym_node is None:
        return None
    # Rename root symbol: "R" → "Device:R".  Sub-symbol children keep their
    # short names ("R_0_1", "R_1_1") which KiCad requires.
    new_items: list[Node] = list(sym_node.items)
    new_items[1] = string(f"{lib_name}:{sym_name}")
    # Strip (id N) items recursively — not used in KiCad 9 schematics.
    return _strip_id_nodes(ListNode(tuple(new_items), NO_POS))


def read_lib_symbol_def_chain(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> list[ListNode]:
    """Load a symbol and its full ``extends`` ancestor chain for embedding.

    KiCad symbols that use ``(extends "BaseName")`` carry no graphics or
    pins of their own — those are inherited from the base symbol.  A
    schematic's ``lib_symbols`` section must contain *all* nodes in the
    inheritance chain for KiCad to render the symbol correctly.

    Returns nodes in dependency order (**base first**, derived last) so
    callers can embed them with :meth:`~kicad_pcb.sch_doc.SchematicDoc.embed_lib_symbol`
    in order.  Each node has its id qualified to ``lib_name:name`` and
    ``(id N)`` children stripped.  ``(extends "BaseName")`` attributes in
    derived nodes are updated to the fully-qualified
    ``"lib_name:BaseName"`` form required by KiCad schematics.

    Returns an empty list when the library file or root symbol cannot be
    found or the extends chain is broken. Raises on library parse/read
    failures.

    Parameters
    ----------
    lib_name:    Library name (e.g. ``"Amplifier_Operational"``).
    sym_name:    Symbol name within the library (e.g. ``"NE5532"``).
    symbols_dir: Directory containing ``.kicad_sym`` files.
    """
    resolved = _resolve_sym_chain(lib_name, sym_name, symbols_dir)
    if resolved is None:
        return []
    lib_root, chain, complete = resolved
    if not chain or not complete:
        return []  # symbol not found or extends chain is broken

    chain.reverse()  # base-first so KiCad can resolve references in order

    result: list[ListNode] = []
    for name in chain:
        node = _find_lib_symbol(lib_root, name)
        if node is None:
            return []  # shouldn't happen — chain was verified complete

        # Qualify root-level id: "NE5532" → "Amplifier_Operational:NE5532".
        new_items_chain: list[Node] = list(node.items)
        new_items_chain[1] = string(f"{lib_name}:{name}")
        renamed = ListNode(tuple(new_items_chain), NO_POS)

        # Qualify extends reference if present: "LM2904" → "lib:LM2904".
        renamed = _qualify_extends(renamed, lib_name)

        result.append(_strip_id_nodes(renamed))

    return result


def read_lib_symbol_def_flat(
    lib_name: str,
    sym_name: str,
    *,
    symbols_dir: Path | None = None,
) -> ListNode | None:
    """Load and fully flatten a symbol definition for schematic embedding.

    Unlike :func:`read_lib_symbol_def_chain`, this returns a **single**
    ``ListNode`` that is completely self-contained — if the symbol has an
    ``(extends …)`` ancestor chain, the ancestor's geometry (sub-symbol
    drawing units + pins) is merged into the returned node and the
    ``(extends …)`` attribute is removed.  This avoids ``(extends …)``
    references in ``lib_symbols``, which some KiCad versions fail to
    resolve when opening a schematic file.

    Returns ``None`` when the library or symbol cannot be found, or when
    the extends chain is broken. Raises on library parse/read failures.
    """
    chain = read_lib_symbol_def_chain(lib_name, sym_name, symbols_dir=symbols_dir)
    if not chain:
        return None
    if len(chain) == 1:
        return chain[0]  # no extends — already self-contained

    # chain is [root_base, ..., direct_parent, derived_leaf] (base-first).
    root = chain[0]  # has all geometry sub-symbols
    leaf = chain[-1]  # has property overrides + (extends …)

    # Root base name (unqualified, e.g., "LM2904").
    root_full_id = _symbol_id(root) or ""
    root_base = root_full_id.split(":")[-1]

    # Leaf symbol name (unqualified, e.g., "NE5532").
    leaf_full_id = _symbol_id(leaf) or ""
    leaf_base = leaf_full_id.split(":")[-1]

    # Collect sub-symbols from the root (these carry all geometry).
    root_subsymbols = _collect_subsymbols(root)
    renamed_subsymbols: list[Node] = [
        _rename_subsymbol(sub, root_base, leaf_base) for sub in root_subsymbols
    ]

    # Build new leaf items: keep everything except (extends …), then append geometry.
    leaf_items_no_extends: list[Node] = [
        item for item in leaf.items if not (isinstance(item, ListNode) and item.key == "extends")
    ]
    merged_items = leaf_items_no_extends + renamed_subsymbols
    return ListNode(tuple(merged_items), leaf.pos)
