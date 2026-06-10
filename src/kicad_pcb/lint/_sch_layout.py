"""Layout readability, wire-crossing, and crowding lint rules (LAY001–LAY008)."""

from __future__ import annotations

import math
from collections import Counter
from typing import TYPE_CHECKING

from ..layout import build_signal_adjacency, count_wire_crossings
from ..sexpr.nodes import AtomNode, ListNode
from ..sexpr.utils import find_first
from ._sch_structural import _is_power_symbol, _uf_find, _uf_union
from .defs import _ERR, _WARN, LintIssue
from .helpers import _collect_wire_segments, _float_from_atom

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..sch_doc import SchematicDoc

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


def lint_schematic_layout(root: AtomNode | ListNode) -> list[LintIssue]:  # noqa: PLR0912
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
    from ..sexpr.nodes import StringNode  # noqa: PLC0415

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
                    f"are stub-length (≤{_WIRE_STUB_LEN_MM} mm); "
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
