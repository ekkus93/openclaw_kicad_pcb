"""Structural schematic lint rules (SCH001–SCH010)."""

from __future__ import annotations

from ..sexpr.nodes import AtomNode, ListNode, StringNode
from ..sexpr.utils import find_all, find_first, walk
from .defs import _ERR, _WARN, LintIssue
from .helpers import (
    _check_duplicate_uuids,
    _collect_uuids,
    _get_property_value,
    _is_numeric_atom,
    _symbol_lib_id,
)

# Phase 5.5 — frozenset, defined at module level so it is not recreated on
# every call to lint_schematic.
_SCH_LABEL_KEYS: frozenset[str] = frozenset(
    {"label", "global_label", "hierarchical_label", "net_tie"}
)


def _is_power_symbol(node: ListNode) -> bool:
    """Return True when *node* is a KiCad power symbol.

    Power symbols (e.g. ``power:GND``, ``power:VCC``) carry ``(in_bom no)``
    and ``(on_board no)`` as direct children.  They are intentionally small and
    placed right at pin-stub ends, so they must be excluded from LAY003/LAY004
    checks that use the full component bounding box.
    """
    in_bom_no = False
    on_board_no = False
    for child in node.items:
        if not isinstance(child, ListNode) or len(child.items) < 2:
            continue
        if not isinstance(child.items[1], AtomNode):
            continue
        if child.key == "in_bom" and child.items[1].value == "no":
            in_bom_no = True
        elif child.key == "on_board" and child.items[1].value == "no":
            on_board_no = True
    return in_bom_no and on_board_no


def _uf_find(parent: list[int], i: int) -> int:
    """Path-compressing find for the union-find used in LAY005."""
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _uf_union(parent: list[int], a: int, b: int) -> None:
    """Union step for the union-find used in LAY005."""
    ra, rb = _uf_find(parent, a), _uf_find(parent, b)
    if ra != rb:
        parent[ra] = rb


def lint_schematic(root: ListNode) -> list[LintIssue]:  # noqa: PLR0912, PLR0915
    # 10 distinct lint rules (SCH001–SCH010), each with its own branches — splitting
    # further would harm readability more than the branch count harms it now.
    """Run all schematic lint rules against *root* and return the findings.

    Returns an empty list when no issues are found.  The caller is responsible
    for deciding which severities are fatal
    (see :func:`~kicad_pcb.pipeline.mutate_and_validate_sch`).
    """
    issues: list[LintIssue] = []

    # ------------------------------------------------------------------
    # SCH001 — invalid root node
    # ------------------------------------------------------------------
    if root.key != "kicad_sch":
        issues.append(
            LintIssue(
                _ERR,
                "SCH001",
                f"Invalid root node '{root.key}', expected 'kicad_sch'",
            )
        )
        return issues  # nothing else is meaningful with the wrong root

    # ------------------------------------------------------------------
    # SCH002 — duplicate UUIDs  (Phase 5.1 — deduplicated via helper)
    # ------------------------------------------------------------------
    issues.extend(_check_duplicate_uuids(_collect_uuids(root), "SCH002"))

    # ------------------------------------------------------------------
    # Gather lib_symbols and placed symbol instances
    # ------------------------------------------------------------------
    lib_syms_node = find_first(root, "lib_symbols")
    lib_sym_ids: set[str] = set()
    if lib_syms_node is not None:
        for item in lib_syms_node.items:
            if (
                isinstance(item, ListNode)
                and item.key == "symbol"
                and len(item.items) >= 2
                and isinstance(item.items[1], StringNode)
            ):
                lib_sym_ids.add(item.items[1].value)

    # Placed symbols = direct children of kicad_sch that have a lib_id child.
    # This distinguishes them from (lib_symbols ...) definitions.
    placed_syms: list[ListNode] = [
        item
        for item in root.items
        if (
            isinstance(item, ListNode)
            and item.key == "symbol"
            and find_first(item, "lib_id") is not None
        )
    ]

    # ------------------------------------------------------------------
    # SCH003 — duplicate reference designators
    # ------------------------------------------------------------------
    ref_groups: dict[str, list[tuple[str, str | None]]] = {}
    for sym in placed_syms:
        ref = _get_property_value(sym, "Reference")
        if ref:
            unit = next(
                (
                    child.items[1].value
                    for child in sym.items
                    if isinstance(child, ListNode)
                    and child.key == "unit"
                    and len(child.items) >= 2
                    and isinstance(child.items[1], AtomNode)
                ),
                "",
            )
            ref_groups.setdefault(ref, []).append((unit, _symbol_lib_id(sym)))
    for ref, entries in ref_groups.items():
        count = len(entries)
        if count <= 1:
            continue
        units = [unit for unit, _lib_id in entries]
        lib_ids = {lib_id for _unit, lib_id in entries}
        repeated_multi_unit = (
            all(unit for unit in units) and len(set(units)) == count and len(lib_ids) == 1
        )
        if not repeated_multi_unit:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH003",
                    f"Duplicate reference designator '{ref}' ({count} instances)",
                )
            )

    # ------------------------------------------------------------------
    # SCH004 + SCH005 — symbol missing required properties
    # ------------------------------------------------------------------
    for sym in placed_syms:
        lib_id = _symbol_lib_id(sym) or "?"
        if _get_property_value(sym, "Reference") is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH004",
                    f"Symbol '{lib_id}' is missing a 'Reference' property",
                    path="kicad_sch/symbol",
                )
            )
        if _get_property_value(sym, "Value") is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH005",
                    f"Symbol '{lib_id}' is missing a 'Value' property",
                    path="kicad_sch/symbol",
                )
            )

    # ------------------------------------------------------------------
    # SCH006 — malformed (at …) nodes
    # ------------------------------------------------------------------
    for node in walk(root):
        if not (isinstance(node, ListNode) and node.key == "at"):
            continue
        items = node.items
        if len(items) < 3:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH006",
                    f"Malformed '(at …)': expected at least (at x y), got {len(items) - 1} arg(s)",
                )
            )
        elif not _is_numeric_atom(items[1]) or not _is_numeric_atom(items[2]):
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH006",
                    f"Malformed '(at …)': x={items[1]!r} y={items[2]!r} are not numeric",
                )
            )

    # ------------------------------------------------------------------
    # SCH007 — malformed wire (pts …)
    # ------------------------------------------------------------------
    for wire in find_all(root, "wire"):
        pts = find_first(wire, "pts")
        if pts is None:
            issues.append(LintIssue(_ERR, "SCH007", "Wire is missing a '(pts …)' node"))
            continue
        xys = find_all(pts, "xy")
        if len(xys) < 2:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH007",
                    f"Wire '(pts …)' has {len(xys)} xy node(s); expected at least 2",
                )
            )
            continue
        for i, xy in enumerate(xys[:2]):
            xy_items = xy.items
            if (
                len(xy_items) < 3
                or not _is_numeric_atom(xy_items[1])
                or not _is_numeric_atom(xy_items[2])
            ):
                issues.append(
                    LintIssue(
                        _ERR,
                        "SCH007",
                        f"Wire xy[{i}] has non-numeric coordinates",
                    )
                )

    # ------------------------------------------------------------------
    # SCH008 — missing/empty lib_symbols when placed symbols exist
    # ------------------------------------------------------------------
    if placed_syms:
        if lib_syms_node is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH008",
                    "Placed symbols exist but '(lib_symbols …)' section is missing",
                )
            )
        elif not lib_sym_ids:
            issues.append(
                LintIssue(
                    _WARN,
                    "SCH008",
                    "Placed symbols exist but '(lib_symbols …)' section is empty",
                )
            )

    # ------------------------------------------------------------------
    # SCH009 — symbol lib_id not found in embedded lib_symbols
    # ------------------------------------------------------------------
    if lib_syms_node is not None:
        for sym in placed_syms:
            lid = _symbol_lib_id(sym)
            if lid and lid not in lib_sym_ids:
                issues.append(
                    LintIssue(
                        _ERR,
                        "SCH009",
                        f"Symbol instance references lib_id '{lid}' not found in lib_symbols",
                        path="kicad_sch/symbol",
                    )
                )

    # ------------------------------------------------------------------
    # SCH010 — net/power label nodes missing (at …) placement
    # Phase 5.5 — uses module-level _SCH_LABEL_KEYS frozenset
    # ------------------------------------------------------------------
    for node in walk(root):
        if not (isinstance(node, ListNode) and node.key in _SCH_LABEL_KEYS):
            continue
        if find_first(node, "at") is None:
            label_name = (
                node.items[1].value
                if len(node.items) >= 2 and isinstance(node.items[1], StringNode)
                else "?"
            )
            issues.append(
                LintIssue(
                    _ERR,
                    "SCH010",
                    f"Label '{label_name}' ({node.key}) is missing an '(at …)' placement node",
                    path=f"kicad_sch/{node.key}",
                )
            )

    return issues
