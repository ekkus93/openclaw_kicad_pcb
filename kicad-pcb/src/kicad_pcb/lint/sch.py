"""Schematic (SCH) and layout-readability (LAY) lint rules.

Structural checks
-----------------
:func:`lint_schematic` — SCH001–SCH010
    Enforce correctness invariants: valid root, unique UUIDs, required
    properties, well-formed AST nodes.  Call on every schematic; failures
    here may block downstream processing.

Readability checks
------------------
:func:`lint_schematic_layout` — LAY001–LAY005
    Detect visual/readability problems on structurally valid schematics
    (label-stub style, overlapping symbols, isolated wire islands).  Call
    when you want design-quality gating, not just structural validity.

Crowding checks (Phase 2)
------------------------
:func:`lint_layout_crowding` — LAY006, LAY008
    Detect local crowding and insufficient inter-block spacing on generated
    layouts (Phase 2.4 CODE_REVIEW6 roadmap). Uses block detection and
    density metrics to enforce spacing standards.

:func:`lint_layout_wire_crossings` — LAY007
    Detect excessive wire crossing density (signal flow optimization).

Composition checks (Phase 8)
----------------------------
:func:`lint_layout_composition` — LAY012, LAY013
    Detect poor page balance and awkward central composition in generated
    schematics (Phase 8.3 CODE_REVIEW6 roadmap). Uses the page-composition
    helpers from the snap pass plus block-role information when available.

Wire quality checks (Phase 6)
------------------------------
:func:`lint_wire_quality` — LAY009, LAY010
    Detect excessive short wire segments and over-routed local connections
    (Phase 6.2 CODE_REVIEW6 roadmap). Flags nets with too many short jogs
    and simple connections using unnecessary routing complexity.

:func:`lint_local_direct_wiring` — LAY011
    Detect nearby 2-pin nets that could have been wired directly but weren't
    (Phase 6.3 CODE_REVIEW6 roadmap). Prefers simple local connections over
    unnecessarily complex routing.

Both functions take the root :class:`~kicad_pcb.sexpr.nodes.ListNode`
produced by parsing a ``.kicad_sch`` file.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from typing import TYPE_CHECKING

from ..layout import build_signal_adjacency, count_wire_crossings
from ..sexpr.nodes import AtomNode, ListNode, StringNode
from ..sexpr.utils import find_all, find_first, walk
from .defs import _ERR, _WARN, LintIssue
from .helpers import (
    _check_duplicate_uuids,
    _collect_uuids,
    _collect_wire_segments,
    _float_from_atom,
    _get_property_value,
    _is_numeric_atom,
    _symbol_lib_id,
)

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..sch_doc import SchematicDoc

__all__ = [
    "lint_schematic",
    "lint_schematic_layout",
    "lint_layout_composition",
    "lint_layout_crowding",
    "lint_layout_wire_crossings",
    "lint_wire_quality",
    "lint_local_direct_wiring",
]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# LAY001: a net label name appearing more than this many times suggests
# "label-stub everywhere" style rather than connected wires.
_LAY_LABEL_MAX_COUNT: int = 3

# LAY002: if this fraction of wire segments are shorter than the stub
# length, the schematic is mostly stubs rather than real wires.
_LAY_STUB_FRACTION_THRESHOLD: float = 0.60

# LAY003: half-size of an approximate symbol bounding box (mm).
_LAY_SYMBOL_HALF_SIZE_MM: float = 5.08

# LAY004: Page bounds (mm).
#
# Defaulted to A4 (297×210). Some generated schematics (e.g., audio projects with
# multiple passives + connectors) can exceed A4 height with otherwise reasonable
# placement. We allow up to A3 (420×297) so compilation succeeds; users can
# always re-page/re-arrange inside KiCad.
_LAY_PAGE_MAX_X: float = 420.0
_LAY_PAGE_MAX_Y: float = 297.0

# LAY005: more than this many disconnected wire/component islands is suspicious.
_LAY_MAX_ISLANDS: int = 2

# Stub wire threshold in mm (200 mil). Mirrors router.WIRE_EXTEND_MM.
_WIRE_STUB_LEN_MM: float = 5.08

# LAY006: local density threshold — symbols with ≥ this many neighbors
# within 30mm radius trigger crowding warnings (Phase 2.4).
_LAY_CROWDING_THRESHOLD: int = 6

# LAY008: minimum inter-block spacing in mm. Blocks closer than this
# distance trigger insufficient spacing warnings (Phase 2.4).
_LAY_MIN_BLOCK_SPACING_MM: float = 20.0

# LAY009: excessive short wire jogs threshold. If a net has this fraction
# or more of its wire segments shorter than _LAY_SHORT_WIRE_THRESHOLD_MM,
# flag it as having too many short jogs (Phase 6.2).
_LAY_SHORT_WIRE_FRACTION_THRESHOLD: float = 0.50  # 50% of segments short
_LAY_SHORT_WIRE_THRESHOLD_MM: float = 5.0  # 5mm = short wire threshold

# LAY010: over-routed local connection thresholds. A 2-pin net with more
# than this many segments is considered over-routed (Phase 6.2).
_LAY_MAX_SEGMENTS_TWO_PIN: int = 4  # 2 stubs + 2 routing segments is reasonable

# LAY011: local direct wiring preference. A 2-pin net where both pins are
# within this Manhattan distance should prefer direct routing instead of
# multi-segment routing through long trunks (Phase 6.3).
_LAY_LOCAL_DIRECT_DIST_MM: float = 150.0  # ~5.9 inches (nearby = roughly same block)
_LAY_LOCAL_DIRECT_MIN_SEGMENTS: int = 3  # 3+ segments suggests over-routing

# LAY012: page composition imbalance threshold. Uses the Phase 8.1 page-quadrant
# metric where 0 means perfectly balanced and 1 means all symbols in one region.
_LAY_PAGE_IMBALANCE_THRESHOLD: float = 0.55

# Phase 5.5 — frozenset, defined at module level so it is not recreated on
# every call to lint_schematic.
_SCH_LABEL_KEYS: frozenset[str] = frozenset(
    {"label", "global_label", "hierarchical_label", "net_tie"}
)

# ---------------------------------------------------------------------------
# Module-level union-find helpers (Phase 5.3 — lifted out of lint_schematic_layout)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Schematic lint rules (SCH001–SCH010)
# ---------------------------------------------------------------------------


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
    ref_counts: dict[str, int] = {}
    for sym in placed_syms:
        ref = _get_property_value(sym, "Reference")
        if ref:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1
    for ref, count in ref_counts.items():
        if count > 1:
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


# ---------------------------------------------------------------------------
# Layout readability lint (LAY001–LAY005)
# ---------------------------------------------------------------------------


def lint_schematic_layout(root: AtomNode | StringNode | ListNode) -> list[LintIssue]:  # noqa: PLR0912
    # 5 layout rules (LAY001–LAY005), each with inner branches — structural split
    # is out of scope for this refactoring phase.
    """Return layout readability issues (LAY001–LAY005) for *root*.

    These rules fire on structurally valid schematics that are visually poor
    (e.g. label-stub style, overlapping symbols, symbols off-page).
    Call after :func:`lint_schematic` when readability gating is desired.
    """
    issues: list[LintIssue] = []

    if not isinstance(root, ListNode) or root.key != "kicad_sch":
        return issues

    items = root.items[1:]  # skip the root key atom

    # ----------------------------------------------------------------
    # Collect symbol positions
    # Phase 5.4 — use _float_from_atom instead of bare float() + type: ignore
    # Power symbols (in_bom no, on_board no) are excluded: they are tiny and
    # intentionally placed at pin-stub ends — applying the component bounding
    # box to them produces spurious LAY003/LAY004 false positives.
    # ----------------------------------------------------------------
    sym_positions: list[tuple[float, float]] = []
    for node in items:
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue
        if _is_power_symbol(node):
            continue
        at_node = find_first(node, "at")
        if at_node is None or len(at_node.items) < 3:
            continue
        sx = _float_from_atom(at_node.items[1])
        sy = _float_from_atom(at_node.items[2])
        if sx is None or sy is None:
            continue
        sym_positions.append((sx, sy))

    # ----------------------------------------------------------------
    # LAY001 — net label appears more than _LAY_LABEL_MAX_COUNT times
    # ----------------------------------------------------------------
    label_counts: Counter[str] = Counter()
    for node in items:
        if (
            isinstance(node, ListNode)
            and node.key == "label"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            label_counts[node.items[1].value] += 1
    for name, count in label_counts.items():
        if count > _LAY_LABEL_MAX_COUNT:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY001",
                    f"Net label '{name}' appears {count} times "
                    f"(threshold {_LAY_LABEL_MAX_COUNT}); consider using direct wires",
                    path="kicad_sch/label",
                )
            )

    # ----------------------------------------------------------------
    # LAY002 — majority of wires are stub-length (label-stub style)
    # Phase 5.2 — use _collect_wire_segments to avoid duplicated extraction
    # ----------------------------------------------------------------
    segs = _collect_wire_segments(items)
    wire_lengths = [math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in segs]
    if wire_lengths:
        stub_count = sum(1 for w in wire_lengths if w <= _WIRE_STUB_LEN_MM + 0.01)
        fraction = stub_count / len(wire_lengths)
        if fraction > _LAY_STUB_FRACTION_THRESHOLD:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY002",
                    f"{stub_count}/{len(wire_lengths)} wire segments ({fraction:.0%}) "
                    f"are stub-length (\u2264{_WIRE_STUB_LEN_MM} mm); "
                    "schematic may be using label-stub style instead of connected wires",
                    path="kicad_sch/wire",
                )
            )

    # ----------------------------------------------------------------
    # LAY003 — overlapping symbols (approximate bounding boxes)
    # ----------------------------------------------------------------
    if len(sym_positions) >= 2:
        for i, (x1, y1) in enumerate(sym_positions):
            for x2, y2 in sym_positions[i + 1 :]:
                if (
                    abs(x1 - x2) < _LAY_SYMBOL_HALF_SIZE_MM * 2
                    and abs(y1 - y2) < _LAY_SYMBOL_HALF_SIZE_MM * 2
                ):
                    issues.append(
                        LintIssue(
                            _WARN,
                            "LAY003",
                            f"Symbols at ({x1},{y1}) and ({x2},{y2}) overlap "
                            f"(approx bounding box ±{_LAY_SYMBOL_HALF_SIZE_MM} mm)",
                            path="kicad_sch/symbol",
                        )
                    )

    # ----------------------------------------------------------------
    # LAY004 — symbol outside A4 page bounds
    # ----------------------------------------------------------------
    for sx, sy in sym_positions:
        if not (0.0 <= sx <= _LAY_PAGE_MAX_X and 0.0 <= sy <= _LAY_PAGE_MAX_Y):
            issues.append(
                LintIssue(
                    _ERR,
                    "LAY004",
                    f"Symbol at ({sx},{sy}) is outside A4 page bounds "
                    f"(0–{_LAY_PAGE_MAX_X} × 0–{_LAY_PAGE_MAX_Y} mm)",
                    path="kicad_sch/symbol",
                )
            )

    # ----------------------------------------------------------------
    # LAY005 — too many disconnected visual islands (union-find on wires)
    # Phase 5.2 — use _collect_wire_segments; round coordinates for endpoint matching
    # Phase 5.3 — use module-level _uf_find / _uf_union instead of nested functions
    # ----------------------------------------------------------------
    wire_endpoints: list[tuple[float, float, float, float]] = [
        (round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2))
        for x1, y1, x2, y2 in _collect_wire_segments(items)
    ]
    if wire_endpoints:
        pts_list: list[tuple[float, float]] = []
        for ex1, ey1, ex2, ey2 in wire_endpoints:
            if (ex1, ey1) not in pts_list:
                pts_list.append((ex1, ey1))
            if (ex2, ey2) not in pts_list:
                pts_list.append((ex2, ey2))
        parent = list(range(len(pts_list)))
        idx_map = {p: k for k, p in enumerate(pts_list)}
        for ex1, ey1, ex2, ey2 in wire_endpoints:
            if (ex1, ey1) in idx_map and (ex2, ey2) in idx_map:
                _uf_union(parent, idx_map[(ex1, ey1)], idx_map[(ex2, ey2)])
        island_count = len({_uf_find(parent, i) for i in range(len(pts_list))})
        if island_count > _LAY_MAX_ISLANDS:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY005",
                    f"Wire graph has {island_count} disconnected islands "
                    f"(threshold {_LAY_MAX_ISLANDS}); schematic may have isolated regions",
                    path="kicad_sch/wire",
                )
            )

    return issues


def lint_layout_wire_crossings(
    positions: dict[str, tuple[float, float]],
    ir: CircuitIR,
) -> list[LintIssue]:
    """LAY007: warn when signal-wire crossing density exceeds 50 %.

    Parameters
    ----------
    positions:
        Mapping ``{ref: (x_mm, y_mm)}`` from
        :func:`~kicad_pcb.layout.compute_signal_flow_layout`.
    ir:
        Parsed :class:`~kicad_pcb.ir.CircuitIR`; used to build the signal
        adjacency graph.

    Returns
    -------
    list[LintIssue]
        A single LAY007 WARNING when crossings exceed half the total signal
        wires, otherwise an empty list.
    """
    issues: list[LintIssue] = []
    adj = build_signal_adjacency(ir)
    total_wires = sum(len(v) for v in adj.values()) // 2
    if total_wires == 0:
        return issues
    crossings = count_wire_crossings(positions, adj)
    if crossings > 0.5 * total_wires:
        issues.append(
            LintIssue(
                _WARN,
                "LAY007",
                f"Layout has {crossings} wire crossing(s) across "
                f"{total_wires} signal wire(s) "
                f"(ratio {crossings / total_wires:.0%} > 50%)",
                path="layout/positions",
            )
        )
    return issues


def lint_layout_crowding(
    doc: SchematicDoc,
    block_layout: BlockLayout | None = None,
) -> list[LintIssue]:
    """LAY006 & LAY008: warn about local crowding and insufficient block spacing.

    Detects when symbols are crowded (local density ≥ 6 neighbors within
    30mm radius) or when functional blocks are too close together
    (inter-block spacing < 20mm).

    Parameters
    ----------
    doc:
        Schematic document :class:`~kicad_pcb.kicad_sch.SchematicDoc`.
    block_layout:
        Optional functional block classification from
        :func:`~kicad_pcb.block_detection.classify_circuit`.  When supplied,
        LAY008 (insufficient block separation) checks are enabled.

    Returns
    -------
    list[LintIssue]
        LAY006 and/or LAY008 WARNINGs for crowded regions or insufficient
        block spacing, otherwise an empty list.
    """
    # Import locally to avoid circular dependency with schematic_metrics.py
    from ..schematic_metrics import (  # noqa: PLC0415
        compute_block_separation,
        compute_local_density,
    )

    issues: list[LintIssue] = []

    # -------------------------------------------------------------------------
    # LAY006 — local density check (crowded regions)
    # -------------------------------------------------------------------------
    local_density = compute_local_density(doc, radius_mm=30.0)
    crowded_symbols = [
        (ref, count) for ref, count in local_density.items() if count >= _LAY_CROWDING_THRESHOLD
    ]
    if crowded_symbols:
        # Sort by density descending so worst crowding is listed first
        crowded_symbols.sort(key=lambda x: x[1], reverse=True)
        for ref, count in crowded_symbols[:5]:  # Limit to top 5 for brevity
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY006",
                    f"Symbol {ref} is in a crowded region "
                    f"({count} neighbors within 30mm, threshold {_LAY_CROWDING_THRESHOLD}); "
                    "consider spreading components or using smaller values.",
                    path="kicad_sch/symbol",
                )
            )

    # -------------------------------------------------------------------------
    # LAY008 — inter-block spacing check (insufficient separation)
    # -------------------------------------------------------------------------
    if block_layout:
        # Extract symbol positions from doc
        symbols = doc.list_symbols()
        positions: dict[str, tuple[float, float, float | None]] = {}
        for sym in symbols:
            try:
                ref = str(sym["ref"])  # type: ignore[arg-type]
                x = float(sym["x"])  # type: ignore[arg-type]
                y = float(sym["y"])  # type: ignore[arg-type]
                rot = sym.get("rot")  # type: ignore[arg-type]
                rot_float = float(rot) if rot else None  # type: ignore[arg-type]
                positions[ref] = (x, y, rot_float)
            except (KeyError, TypeError, ValueError):
                continue

        block_separation = compute_block_separation(positions, block_layout)
        for (role_a, role_b), min_distance in block_separation.items():
            if min_distance < _LAY_MIN_BLOCK_SPACING_MM:
                issues.append(
                    LintIssue(
                        _WARN,
                        "LAY008",
                        f"Blocks {role_a.value}–{role_b.value} are too close "
                        f"({min_distance:.1f}mm, minimum {_LAY_MIN_BLOCK_SPACING_MM}mm); "
                        "increase inter-block spacing for clarity.",
                        path="kicad_sch/symbol",
                    )
                )

    return issues


def lint_layout_composition(
    doc: SchematicDoc,
    block_layout: BlockLayout | None = None,
) -> list[LintIssue]:
    """LAY012 & LAY013: warn about poor page balance and awkward composition.

    LAY012 uses the Phase 8.1 quadrant-utilization metric to detect when one
    page region is much denser than another. LAY013 covers the Phase 8.2
    central-composition concerns: title-block encroachment, op-amp stages that
    sit too high or too low, and vertically collapsed signal-path layouts.

    Parameters
    ----------
    doc:
        Schematic document :class:`~kicad_pcb.kicad_sch.SchematicDoc`.
    block_layout:
        Optional functional block classification from
        :func:`~kicad_pcb.block_detection.classify_circuit`. When supplied,
        OPAMP_CORE and signal-path role checks become more precise.

    Returns
    -------
    list[LintIssue]
        LAY012 and/or LAY013 WARNINGs for poor page balance or awkward block
        composition, otherwise an empty list.
    """
    from ..graphviz_layout.snap import (  # noqa: PLC0415
        _MIN_CIRCUIT_SPAN_FRACTION,
        _OPAMP_LOWER_LIMIT_FRACTION,
        _OPAMP_UPPER_LIMIT_FRACTION,
        _TITLE_BLOCK_CLEARANCE_MM,
        ORIGIN_Y,
        PAGE_MAX_Y,
        _compute_page_quadrant_utilization,
    )

    issues: list[LintIssue] = []
    comp_positions = _extract_symbol_positions(doc)
    if not comp_positions:
        return issues

    refs_for_balance = [ref for ref in comp_positions if not ref.startswith("#")]
    if len(refs_for_balance) >= 4:  # noqa: PLR2004
        quadrant_positions = {
            ref: (x, y, None) for ref, (x, y) in comp_positions.items() if ref in refs_for_balance
        }
        utilization = _compute_page_quadrant_utilization(quadrant_positions)
        imbalance = float(utilization["imbalance"])
        if imbalance >= _LAY_PAGE_IMBALANCE_THRESHOLD:
            dense_quadrant = str(utilization["dense_quadrant"])
            sparse_quadrant = str(utilization["sparse_quadrant"])
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY012",
                    "Page composition is unbalanced: "
                    f"{dense_quadrant} is dense while {sparse_quadrant} is sparse "
                    f"(imbalance {imbalance:.0%}, threshold {_LAY_PAGE_IMBALANCE_THRESHOLD:.0%}); "
                    "redistribute blocks to use the page more evenly.",
                    path="layout/positions",
                )
            )

    signal_refs = [ref for ref in comp_positions if not ref.startswith("#")]
    opamp_refs: list[str] = []
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        signal_roles = {
            BlockRole.INPUT,
            BlockRole.PRECONDITIONING,
            BlockRole.OPAMP_CORE,
            BlockRole.FEEDBACK,
            BlockRole.OUTPUT,
            BlockRole.DECOUPLING,
        }
        role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
        signal_refs = [
            ref
            for ref in comp_positions
            if role_by_ref.get(ref) in signal_roles and not ref.startswith("#")
        ]
        opamp_refs = [ref for ref in signal_refs if role_by_ref.get(ref) == BlockRole.OPAMP_CORE]

    if not signal_refs:
        return issues

    safe_max_y = PAGE_MAX_Y - _TITLE_BLOCK_CLEARANCE_MM
    title_block_refs = sorted(ref for ref in signal_refs if comp_positions[ref][1] > safe_max_y)
    if title_block_refs:
        shown = ", ".join(title_block_refs[:4])
        extra = "" if len(title_block_refs) <= 4 else f" (+{len(title_block_refs) - 4} more)"
        issues.append(
            LintIssue(
                _WARN,
                "LAY013",
                "Signal components encroach on the title-block clearance band "
                f"below y={safe_max_y:.1f}mm: {shown}{extra}; move the affected "
                "stage upward.",
                path="kicad_sch/symbol",
            )
        )

    if opamp_refs:
        page_height = PAGE_MAX_Y - ORIGIN_Y
        upper_limit_y = ORIGIN_Y + (_OPAMP_UPPER_LIMIT_FRACTION * page_height)
        lower_limit_y = ORIGIN_Y + (_OPAMP_LOWER_LIMIT_FRACTION * page_height)
        opamp_avg_y = sum(comp_positions[ref][1] for ref in opamp_refs) / len(opamp_refs)
        if opamp_avg_y < upper_limit_y:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY013",
                    "Op-amp stage is too high on the page "
                    f"(avg y={opamp_avg_y:.1f}mm, lower bound {upper_limit_y:.1f}mm); "
                    "shift the core stage downward for better central composition.",
                    path="kicad_sch/symbol",
                )
            )
        elif opamp_avg_y > lower_limit_y:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY013",
                    "Op-amp stage is too low on the page "
                    f"(avg y={opamp_avg_y:.1f}mm, upper bound {lower_limit_y:.1f}mm); "
                    "shift the core stage upward for better central composition.",
                    path="kicad_sch/symbol",
                )
            )

    signal_ys = [comp_positions[ref][1] for ref in signal_refs]
    span = max(signal_ys) - min(signal_ys)
    available_height = PAGE_MAX_Y - ORIGIN_Y
    min_span = _MIN_CIRCUIT_SPAN_FRACTION * available_height
    if span < min_span:
        issues.append(
            LintIssue(
                _WARN,
                "LAY013",
                f"Signal-path vertical span is too small ({span:.1f}mm, minimum {min_span:.1f}mm); "
                "the schematic looks vertically collapsed rather than intentionally composed.",
                path="kicad_sch/symbol",
            )
        )

    return issues


def lint_wire_quality(
    doc: SchematicDoc,
    ir: CircuitIR,
) -> list[LintIssue]:
    """LAY009 & LAY010: warn about excessive short wire jogs and over-routed connections.

    Detects when nets have too many short wire segments (excessive jogs)
    or when simple 2-pin connections use unnecessarily complex routing.

    Parameters
    ----------
    doc:
        Schematic document :class:`~kicad_pcb.kicad_sch.SchematicDoc`.
    ir:
        Parsed :class:`~kicad_pcb.circuit_ir.CircuitIR`; used to identify
        nets and their pin counts.

    Returns
    -------
    list[LintIssue]
        LAY009 and/or LAY010 WARNINGs for nets with excessive short segments
        or over-routed local connections, otherwise an empty list.
    """
    issues: list[LintIssue] = []

    # Collect all wire segments from the schematic
    segs = _collect_wire_segments(doc.root.items)

    # Build a mapping of wire segments to nets using bind markers
    # Bind markers are in format: "OpenClaw:bind={json}"
    bind_markers: dict[str, list[tuple[float, float]]] = {}  # {net_name: [(x, y), ...]}
    for node in walk(doc.root):
        if not (isinstance(node, ListNode) and node.key == "text"):
            continue
        if len(node.items) >= 2 and isinstance(node.items[1], StringNode):
            text = node.items[1].value
            # Parse bind marker format: "OpenClaw:bind={...}"
            if text.startswith("OpenClaw:bind="):
                try:
                    bind_json = text[len("OpenClaw:bind=") :]
                    bind_data = json.loads(bind_json)
                    net_name = bind_data.get("net_name", "")
                    # Get position from the bind marker (it's placed at the pin location)
                    at_node = find_first(node, "at")
                    if at_node and len(at_node.items) >= 3:
                        x = _float_from_atom(at_node.items[1])
                        y = _float_from_atom(at_node.items[2])
                        if x is not None and y is not None:
                            if net_name not in bind_markers:
                                bind_markers[net_name] = []
                            # Store rounded coordinates for matching
                            bind_markers[net_name].append((round(x, 2), round(y, 2)))
                except (json.JSONDecodeError, ValueError, KeyError):
                    pass

    # For each net, collect all wire segments where either endpoint matches a bind marker position
    # This is a heuristic since we don't have explicit wire-to-net mapping in the schematic
    net_segments: dict[str, list[tuple[float, float, float, float]]] = {}

    for net_name, positions in bind_markers.items():
        # For each wire segment, check if either endpoint is close to any bind marker for this net
        for x1, y1, x2, y2 in segs:
            x1r, y1r, x2r, y2r = round(x1, 2), round(y1, 2), round(x2, 2), round(y2, 2)
            # Check if either endpoint matches a bind marker position (within tolerance)
            matches = False
            for bx, by in positions:
                if (abs(x1r - bx) < 5.0 and abs(y1r - by) < 5.0) or (
                    abs(x2r - bx) < 5.0 and abs(y2r - by) < 5.0
                ):
                    matches = True
                    break

            if matches:
                if net_name not in net_segments:
                    net_segments[net_name] = []
                # Avoid duplicates
                if (x1, y1, x2, y2) not in net_segments[net_name]:
                    net_segments[net_name].append((x1, y1, x2, y2))

    # -------------------------------------------------------------------------
    # LAY009 — excessive short wire jogs (per net)
    # -------------------------------------------------------------------------
    for net_name, segments in net_segments.items():
        if not segments or len(segments) < 3:
            continue

        # Count short segments in this net
        short_count = 0
        for x1, y1, x2, y2 in segments:
            length = math.hypot(x2 - x1, y2 - y1)
            if length <= _LAY_SHORT_WIRE_THRESHOLD_MM:
                short_count += 1

        # Check if too many segments are short
        short_fraction = short_count / len(segments)
        if short_fraction >= _LAY_SHORT_WIRE_FRACTION_THRESHOLD:
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY009",
                    f"Net '{net_name}' has {short_count}/{len(segments)} short wire segments "
                    f"({short_fraction:.0%}) below {_LAY_SHORT_WIRE_THRESHOLD_MM}mm; "
                    "consider simplifying routing or using more direct connections.",
                    path="kicad_sch/wire",
                )
            )

    # -------------------------------------------------------------------------
    # LAY010 — over-routed local connection (2-pin nets with too many segments)
    # -------------------------------------------------------------------------
    for net in ir.nets:
        # Skip power nets (they typically use hub/spine routing)
        if net.name.upper() in {"GND", "VCC", "V+", "V-", "+5V", "+3.3V", "+12V", "-12V"}:
            continue

        # Only check 2-pin nets (local connections)
        if len(net.pins) == 2:
            segments = net_segments.get(net.name, [])
            if len(segments) > _LAY_MAX_SEGMENTS_TWO_PIN:
                # Calculate total wire length
                total_length = sum(math.hypot(x2 - x1, y2 - y1) for x1, y1, x2, y2 in segments)

                issues.append(
                    LintIssue(
                        _WARN,
                        "LAY010",
                        f"Net '{net.name}' is a 2-pin connection but uses "
                        f"{len(segments)} wire segments "
                        f"(threshold {_LAY_MAX_SEGMENTS_TWO_PIN}, "
                        f"total length {total_length:.1f}mm); "
                        "consider using more direct routing for local connections.",
                        path="kicad_sch/wire",
                    )
                )

    return issues


def _extract_symbol_positions(doc: SchematicDoc) -> dict[str, tuple[float, float]]:
    comp_positions: dict[str, tuple[float, float]] = {}
    for sym_meta in doc.list_symbols():
        ref_val = sym_meta.get("ref")
        if not isinstance(ref_val, str):
            continue
        x_val = sym_meta.get("x")
        if not isinstance(x_val, int | float):
            continue
        y_val = sym_meta.get("y")
        if not isinstance(y_val, int | float):
            continue
        ref = ref_val
        x = float(x_val)
        y = float(y_val)
        comp_positions[ref] = (x, y)
    return comp_positions


def _collect_bind_markers(doc: SchematicDoc) -> dict[str, list[tuple[float, float]]]:
    bind_markers: dict[str, list[tuple[float, float]]] = {}
    for node in walk(doc.root):
        if not (isinstance(node, ListNode) and node.key == "text"):
            continue
        if len(node.items) < 2 or not isinstance(node.items[1], StringNode):
            continue

        text = node.items[1].value
        if not text.startswith("OpenClaw:bind="):
            continue

        try:
            bind_data = json.loads(text[len("OpenClaw:bind=") :])
        except (json.JSONDecodeError, ValueError, TypeError):
            continue

        net_name = bind_data.get("net_name")
        if not isinstance(net_name, str) or not net_name:
            continue

        at_node = find_first(node, "at")
        if at_node is None or len(at_node.items) < 3:
            continue

        x = _float_from_atom(at_node.items[1])
        y = _float_from_atom(at_node.items[2])
        if x is None or y is None:
            continue

        bind_markers.setdefault(net_name, []).append((round(x, 2), round(y, 2)))

    return bind_markers


def _connected_segments_from_positions(
    segs: list[tuple[float, float, float, float]],
    bind_positions: list[tuple[float, float]],
) -> list[tuple[float, float, float, float]]:
    visited: set[tuple[float, float, float, float]] = set()
    queue: list[tuple[float, float]] = list(bind_positions)
    connected_segs: list[tuple[float, float, float, float]] = []

    while queue:
        current_x, current_y = queue.pop(0)
        current_pos = (round(current_x, 2), round(current_y, 2))

        for x1, y1, x2, y2 in segs:
            seg_tuple = (x1, y1, x2, y2)
            if seg_tuple in visited:
                continue

            p1 = (round(x1, 2), round(y1, 2))
            p2 = (round(x2, 2), round(y2, 2))
            endpoint: tuple[float, float] | None = None

            if abs(p1[0] - current_pos[0]) < 0.1 and abs(p1[1] - current_pos[1]) < 0.1:
                endpoint = p2
            elif abs(p2[0] - current_pos[0]) < 0.1 and abs(p2[1] - current_pos[1]) < 0.1:
                endpoint = p1

            if endpoint is None:
                continue

            connected_segs.append(seg_tuple)
            visited.add(seg_tuple)
            if endpoint not in queue and endpoint not in bind_positions:
                queue.append(endpoint)

    return connected_segs


def _build_net_segments(
    segs: list[tuple[float, float, float, float]],
    bind_markers: dict[str, list[tuple[float, float]]],
) -> dict[str, list[tuple[float, float, float, float]]]:
    net_segments: dict[str, list[tuple[float, float, float, float]]] = {}
    for net_name, bind_positions in bind_markers.items():
        if not bind_positions:
            continue
        connected = _connected_segments_from_positions(segs, bind_positions)
        if connected:
            net_segments[net_name] = connected
    return net_segments


def _is_power_net_name(net_name: str) -> bool:
    return net_name.upper() in {"GND", "VCC", "V+", "V-", "+5V", "+3.3V", "+12V", "-12V"}


def lint_local_direct_wiring(doc: SchematicDoc, ir: CircuitIR) -> list[LintIssue]:
    """
    LAY011: Detect nearby 2-pin nets that could/should use direct routing.

    Phase 6.3 concern: if two pins are within `_LAY_LOCAL_DIRECT_DIST_MM` Manhattan
    distance (roughly same block), they should prefer direct L-routing instead of
    multi-segment routing through long trunks or hubs.

    Returns list of LintIssue for 2-pin connections that are routed with too many
    segments for their physical proximity.
    """
    issues: list[LintIssue] = []
    segs = _collect_wire_segments(doc.root.items)
    if not segs:
        return issues

    try:
        comp_positions = _extract_symbol_positions(doc)
    except (AttributeError, KeyError, TypeError, ValueError):
        return issues

    bind_markers = _collect_bind_markers(doc)
    net_segments = _build_net_segments(segs, bind_markers)

    for net in ir.nets:
        if _is_power_net_name(net.name):
            continue
        if len(net.pins) != 2:
            continue

        segments = net_segments.get(net.name, [])
        if len(segments) < _LAY_LOCAL_DIRECT_MIN_SEGMENTS:
            continue

        try:
            pin1 = net.pins[0]
            pin2 = net.pins[1]
            if pin1.ref not in comp_positions or pin2.ref not in comp_positions:
                continue

            x1, y1 = comp_positions[pin1.ref]
            x2, y2 = comp_positions[pin2.ref]
            manhattan_dist = abs(x2 - x1) + abs(y2 - y1)
            if manhattan_dist > _LAY_LOCAL_DIRECT_DIST_MM:
                continue

            total_length = sum(math.hypot(sx2 - sx1, sy2 - sy1) for sx1, sy1, sx2, sy2 in segments)
            issues.append(
                LintIssue(
                    _WARN,
                    "LAY011",
                    f"Net '{net.name}' connects nearby pins "
                    f"({manhattan_dist:.0f}mm apart) but uses "
                    f"{len(segments)} wire segments "
                    f"(length {total_length:.1f}mm); "
                    "prefer simple L-routing for local connections to avoid trunk congestion.",
                    path="kicad_sch/wire",
                )
            )
        except (AttributeError, TypeError, KeyError):
            continue

    return issues
