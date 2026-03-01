"""Structural lint rules for ``.kicad_sch`` and ``.kicad_pcb`` AST documents.

Rules
-----
Schematic (SCH):
    SCH001  Invalid root node (not ``kicad_sch``)
    SCH002  Duplicate UUID
    SCH003  Duplicate reference designator
    SCH004  Symbol instance missing ``Reference`` property
    SCH005  Symbol instance missing ``Value`` property
    SCH006  Malformed ``(at …)`` node — non-numeric or insufficient items
    SCH007  Malformed wire ``(pts …)`` — missing or non-numeric coordinates
    SCH008  ``lib_symbols`` section missing or empty when placed symbols exist
    SCH009  Symbol instance ``lib_id`` not found in embedded ``lib_symbols``
    SCH010  Net/power label node missing ``(at …)`` placement

Layout (LAY):
    LAY001  Net label name appears more than N times (label-stub style indicator)
    LAY002  Majority of wire segments are stub-length (label-stub style)
    LAY003  Symbols have overlapping approximate bounding boxes
    LAY004  Symbol placed outside A4 page bounds
    LAY005  Wire graph has too many disconnected islands

PCB (PCB):
    PCB001  Invalid root node (not ``kicad_pcb``)
    PCB002  Duplicate UUID
    PCB003  Footprint missing ``(at …)`` node
    PCB004  Footprint ``(at …)`` has non-numeric x/y or rotation
    PCB005  No ``Edge.Cuts`` geometry found
    PCB006  ``Edge.Cuts`` has dangling endpoints (outline not closed)
    PCB007  ``Edge.Cuts`` bounding box has zero or negative dimension
    PCB008  ``gr_line`` on ``Edge.Cuts`` has malformed ``layer`` or ``width``
    PCB009  Coordinates out of sane range (|value| > 10 000 mm)
    PCB010  Graphic primitive (``gr_line``/``gr_arc``/etc.) missing ``(layer …)`` declaration
    PCB011  Footprint pad missing ``(layers …)`` declaration
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum

from .errors import KiCadError
from .sexpr.nodes import AtomNode, ListNode, StringNode
from .sexpr.utils import find_all, find_first, walk

__all__ = [
    "LintError",
    "LintIssue",
    "LintSeverity",
    "lint_pcb",
    "lint_schematic",
    "lint_schematic_layout",
]

# Coordinates beyond ±10 000 mm are almost certainly erroneous for hobby PCBs.
_COORD_MAX: float = 10_000.0

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


class LintSeverity(Enum):
    """Severity level of a lint finding."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class LintIssue:
    """A single structured lint finding."""

    severity: LintSeverity
    code: str
    message: str
    path: str | None = None  # optional AST path context (e.g. "kicad_sch/symbol")


class LintError(KiCadError):
    """Raised by the validation pipeline when ERROR-level lint issues are found."""

    def __init__(self, message: str, issues: list[LintIssue]) -> None:
        super().__init__(message)
        self.issues: list[LintIssue] = issues


# ---------------------------------------------------------------------------
# Internal aliases and helpers
# ---------------------------------------------------------------------------

_ERR = LintSeverity.ERROR
_WARN = LintSeverity.WARNING


def _is_numeric_atom(node: object) -> bool:
    """Return ``True`` when *node* is an :class:`AtomNode` holding a valid float."""
    if not isinstance(node, AtomNode):
        return False
    try:
        float(node.value)
        return True
    except ValueError:
        return False


def _float_from_atom(node: object) -> float | None:
    """Return the float value of *node* if it is a numeric :class:`AtomNode`."""
    if not isinstance(node, AtomNode):
        return None
    try:
        return float(node.value)
    except ValueError:
        return None


def _collect_uuids(root: ListNode) -> list[str]:
    """Walk *root* and return every ``(uuid "value")`` string found."""
    uuids: list[str] = []
    for node in walk(root):
        if (
            isinstance(node, ListNode)
            and node.key == "uuid"
            and len(node.items) >= 2
            and isinstance(node.items[1], StringNode)
        ):
            uuids.append(node.items[1].value)
    return uuids


def _get_property_value(sym: ListNode, prop_name: str) -> str | None:
    """Return the value of a ``(property "prop_name" "value" …)`` direct child."""
    for child in sym.items:
        if (
            isinstance(child, ListNode)
            and child.key == "property"
            and len(child.items) >= 3
            and isinstance(child.items[1], StringNode)
            and child.items[1].value == prop_name
            and isinstance(child.items[2], StringNode)
        ):
            return child.items[2].value
    return None


def _symbol_lib_id(sym: ListNode) -> str | None:
    """Return the ``(lib_id "…")`` value from a placed symbol node."""
    lib_id_node = find_first(sym, "lib_id")
    if lib_id_node is not None and len(lib_id_node.items) >= 2:
        item = lib_id_node.items[1]
        if isinstance(item, StringNode):
            return item.value
    return None


# ---------------------------------------------------------------------------
# Schematic lint rules
# ---------------------------------------------------------------------------


def lint_schematic(root: ListNode) -> list[LintIssue]:  # noqa: PLR0912, PLR0915
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
    # SCH002 — duplicate UUIDs
    # ------------------------------------------------------------------
    uuids = _collect_uuids(root)
    seen_uuids: set[str] = set()
    for u in uuids:
        if u in seen_uuids:
            issues.append(LintIssue(_ERR, "SCH002", f"Duplicate UUID '{u}'"))
        seen_uuids.add(u)

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
    # ------------------------------------------------------------------
    _LABEL_KEYS = {"label", "global_label", "hierarchical_label", "net_tie"}
    for node in walk(root):
        if not (isinstance(node, ListNode) and node.key in _LABEL_KEYS):
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
# Layout readability lint (LAY) — schematic-level geometry rules
# ---------------------------------------------------------------------------

# LAY001: a net label name appearing more than this many times suggests
# "label-stub everywhere" style rather than connected wires.
_LAY_LABEL_MAX_COUNT: int = 3

# LAY002: if this fraction of wire segments are shorter than the stub
# length, the schematic is mostly stubs rather than real wires.
_LAY_STUB_FRACTION_THRESHOLD: float = 0.60

# LAY003: half-size of an approximate symbol bounding box (mm).
_LAY_SYMBOL_HALF_SIZE_MM: float = 5.08

# LAY004: A4 page bounds (mm).
_LAY_PAGE_MAX_X: float = 297.0
_LAY_PAGE_MAX_Y: float = 210.0

# LAY005: more than this many disconnected wire/component islands is suspicious.
_LAY_MAX_ISLANDS: int = 2

# Stub wire threshold in mm (200 mil). Mirrors router.WIRE_EXTEND_MM.
_WIRE_STUB_LEN_MM: float = 5.08


def lint_schematic_layout(root: ListNode) -> list[LintIssue]:  # noqa: PLR0912, PLR0915
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
    # ----------------------------------------------------------------
    sym_positions: list[tuple[float, float]] = []
    for node in items:
        if not isinstance(node, ListNode) or node.key != "symbol":
            continue
        at_node = find_first(node, "at")
        if at_node is None or len(at_node.items) < 3:
            continue
        try:
            sx = float(at_node.items[1].value)  # type: ignore[union-attr]
            sy = float(at_node.items[2].value)  # type: ignore[union-attr]
            sym_positions.append((sx, sy))
        except (AttributeError, ValueError):
            pass

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
    # ----------------------------------------------------------------
    wire_lengths: list[float] = []
    for node in items:
        if not isinstance(node, ListNode) or node.key != "wire":
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        xy_nodes = [n for n in pts.items[1:] if isinstance(n, ListNode) and n.key == "xy"]
        if len(xy_nodes) < 2:
            continue
        try:
            x1 = float(xy_nodes[0].items[1].value)  # type: ignore[union-attr]
            y1 = float(xy_nodes[0].items[2].value)  # type: ignore[union-attr]
            x2 = float(xy_nodes[1].items[1].value)  # type: ignore[union-attr]
            y2 = float(xy_nodes[1].items[2].value)  # type: ignore[union-attr]
            wire_lengths.append(math.hypot(x2 - x1, y2 - y1))
        except (AttributeError, ValueError, IndexError):
            pass
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
    # ----------------------------------------------------------------
    wire_endpoints: list[tuple[float, float, float, float]] = []
    for node in items:
        if not isinstance(node, ListNode) or node.key != "wire":
            continue
        pts = find_first(node, "pts")
        if pts is None:
            continue
        xy_nodes = [n for n in pts.items[1:] if isinstance(n, ListNode) and n.key == "xy"]
        if len(xy_nodes) < 2:
            continue
        try:
            ex1 = round(float(xy_nodes[0].items[1].value), 2)  # type: ignore[union-attr]
            ey1 = round(float(xy_nodes[0].items[2].value), 2)  # type: ignore[union-attr]
            ex2 = round(float(xy_nodes[1].items[1].value), 2)  # type: ignore[union-attr]
            ey2 = round(float(xy_nodes[1].items[2].value), 2)  # type: ignore[union-attr]
            wire_endpoints.append((ex1, ey1, ex2, ey2))
        except (AttributeError, ValueError, IndexError):
            pass
    if wire_endpoints:
        pts_list: list[tuple[float, float]] = []
        for ex1, ey1, ex2, ey2 in wire_endpoints:
            if (ex1, ey1) not in pts_list:
                pts_list.append((ex1, ey1))
            if (ex2, ey2) not in pts_list:
                pts_list.append((ex2, ey2))
        parent = list(range(len(pts_list)))

        def _find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def _union(a: int, b: int) -> None:
            ra, rb = _find(a), _find(b)
            if ra != rb:
                parent[ra] = rb

        idx_map = {p: k for k, p in enumerate(pts_list)}
        for ex1, ey1, ex2, ey2 in wire_endpoints:
            if (ex1, ey1) in idx_map and (ex2, ey2) in idx_map:
                _union(idx_map[(ex1, ey1)], idx_map[(ex2, ey2)])
        island_count = len({_find(i) for i in range(len(pts_list))})
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


# ---------------------------------------------------------------------------
# PCB lint helpers
# ---------------------------------------------------------------------------


def _edge_cuts_lines(root: ListNode) -> list[ListNode]:
    """Return all top-level ``(gr_line …)`` nodes on the ``Edge.Cuts`` layer."""
    result: list[ListNode] = []
    for item in root.items:
        if not (isinstance(item, ListNode) and item.key == "gr_line"):
            continue
        layer_node = find_first(item, "layer")
        if (
            layer_node is not None
            and len(layer_node.items) >= 2
            and isinstance(layer_node.items[1], StringNode)
            and layer_node.items[1].value == "Edge.Cuts"
        ):
            result.append(item)
    return result


def _gr_line_endpoints(
    line: ListNode,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return ``((sx, sy), (ex, ey))`` for a ``(gr_line …)`` or ``None`` if malformed."""
    start_node = find_first(line, "start")
    end_node = find_first(line, "end")
    if start_node is None or end_node is None:
        return None
    sx = _float_from_atom(start_node.items[1] if len(start_node.items) > 1 else None)
    sy = _float_from_atom(start_node.items[2] if len(start_node.items) > 2 else None)
    ex = _float_from_atom(end_node.items[1] if len(end_node.items) > 1 else None)
    ey = _float_from_atom(end_node.items[2] if len(end_node.items) > 2 else None)
    if sx is None or sy is None or ex is None or ey is None:
        return None
    return (sx, sy), (ex, ey)


def _has_any_edge_cuts(root: ListNode) -> bool:
    """Return ``True`` if the PCB has any geometry on the ``Edge.Cuts`` layer."""
    geo_keys = {"gr_line", "gr_arc", "gr_rect", "gr_poly", "gr_curve"}
    for item in root.items:
        if not (isinstance(item, ListNode) and item.key in geo_keys):
            continue
        layer_node = find_first(item, "layer")
        if (
            layer_node is not None
            and len(layer_node.items) >= 2
            and isinstance(layer_node.items[1], StringNode)
            and layer_node.items[1].value == "Edge.Cuts"
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# PCB lint rules
# ---------------------------------------------------------------------------


def lint_pcb(root: ListNode) -> list[LintIssue]:  # noqa: PLR0912, PLR0915
    """Run all PCB lint rules against *root* and return the findings.

    Returns an empty list when no issues are found.
    """
    issues: list[LintIssue] = []

    # ------------------------------------------------------------------
    # PCB001 — invalid root node
    # ------------------------------------------------------------------
    if root.key != "kicad_pcb":
        issues.append(
            LintIssue(
                _ERR,
                "PCB001",
                f"Invalid root node '{root.key}', expected 'kicad_pcb'",
            )
        )
        return issues

    # ------------------------------------------------------------------
    # PCB002 — duplicate UUIDs
    # ------------------------------------------------------------------
    uuids = _collect_uuids(root)
    seen_uuids: set[str] = set()
    for u in uuids:
        if u in seen_uuids:
            issues.append(LintIssue(_ERR, "PCB002", f"Duplicate UUID '{u}'"))
        seen_uuids.add(u)

    # ------------------------------------------------------------------
    # PCB003 + PCB004 — footprint at node validity
    # ------------------------------------------------------------------
    footprints = [
        item for item in root.items if isinstance(item, ListNode) and item.key == "footprint"
    ]
    for fp in footprints:
        fp_name = (
            fp.items[1].value if len(fp.items) >= 2 and isinstance(fp.items[1], StringNode) else "?"
        )
        at_node = find_first(fp, "at")
        if at_node is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "PCB003",
                    f"Footprint '{fp_name}' is missing an '(at …)' node",
                    path="kicad_pcb/footprint",
                )
            )
        else:
            at_items = at_node.items
            if len(at_items) < 3:
                issues.append(
                    LintIssue(
                        _ERR,
                        "PCB004",
                        f"Footprint '{fp_name}' '(at …)' has insufficient"
                        f" items ({len(at_items) - 1})",
                        path="kicad_pcb/footprint/at",
                    )
                )
            elif not _is_numeric_atom(at_items[1]) or not _is_numeric_atom(at_items[2]):
                issues.append(
                    LintIssue(
                        _ERR,
                        "PCB004",
                        f"Footprint '{fp_name}' '(at …)' has non-numeric x or y",
                        path="kicad_pcb/footprint/at",
                    )
                )
            elif len(at_items) >= 4 and not _is_numeric_atom(at_items[3]):
                issues.append(
                    LintIssue(
                        _WARN,
                        "PCB004",
                        f"Footprint '{fp_name}' '(at …)' has non-numeric rotation",
                        path="kicad_pcb/footprint/at",
                    )
                )

    # ------------------------------------------------------------------
    # PCB005 — no Edge.Cuts geometry
    # ------------------------------------------------------------------
    if not _has_any_edge_cuts(root):
        issues.append(
            LintIssue(
                _WARN,
                "PCB005",
                "No 'Edge.Cuts' geometry found — board has no outline",
            )
        )

    # ------------------------------------------------------------------
    # PCB006 — dangling endpoints (outline not closed)
    # ------------------------------------------------------------------
    ec_lines = _edge_cuts_lines(root)
    if ec_lines:
        valid_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for line in ec_lines:
            pts = _gr_line_endpoints(line)
            if pts is not None:
                valid_segs.append(pts)

        if valid_segs:
            _Pt = tuple[float, float]

            def _round_pt(pt: _Pt) -> _Pt:  # type: ignore[misc]
                return (round(pt[0], 3), round(pt[1], 3))

            endpoint_counts: dict[tuple[float, float], int] = {}
            for s, e in valid_segs:
                rs, re = _round_pt(s), _round_pt(e)
                endpoint_counts[rs] = endpoint_counts.get(rs, 0) + 1
                endpoint_counts[re] = endpoint_counts.get(re, 0) + 1

            dangling = [pt for pt, c in endpoint_counts.items() if c % 2 != 0]
            if dangling:
                issues.append(
                    LintIssue(
                        _WARN,
                        "PCB006",
                        f"Edge.Cuts has {len(dangling)} dangling endpoint(s)"
                        " — outline may not be closed",
                    )
                )

        # ------------------------------------------------------------------
        # PCB007 — impossible / absurd bounding box
        # ------------------------------------------------------------------
        xs: list[float] = []
        ys: list[float] = []
        for s, e in valid_segs:
            xs.extend([s[0], e[0]])
            ys.extend([s[1], e[1]])
        if xs and ys:
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if w <= 0 or h <= 0:
                issues.append(
                    LintIssue(
                        _ERR,
                        "PCB007",
                        f"Edge.Cuts bounding box has zero or negative dimension: "
                        f"{w:.3f} × {h:.3f} mm",
                    )
                )
            elif w > _COORD_MAX or h > _COORD_MAX:
                issues.append(
                    LintIssue(
                        _WARN,
                        "PCB007",
                        f"Edge.Cuts bounding box is unusually large: {w:.3f} × {h:.3f} mm",
                    )
                )

    # ------------------------------------------------------------------
    # PCB008 — malformed layer / width on Edge.Cuts gr_lines
    # ------------------------------------------------------------------
    for line in ec_lines:
        layer_node = find_first(line, "layer")
        if layer_node is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "PCB008",
                    "Edge.Cuts gr_line is missing a '(layer …)' node",
                    path="kicad_pcb/gr_line",
                )
            )
        width_node = find_first(line, "width")
        if (
            width_node is not None
            and len(width_node.items) >= 2
            and not _is_numeric_atom(width_node.items[1])
        ):
            issues.append(
                LintIssue(
                    _WARN,
                    "PCB008",
                    "Edge.Cuts gr_line has a non-numeric 'width' value",
                    path="kicad_pcb/gr_line/width",
                )
            )

    # ------------------------------------------------------------------
    # PCB009 — coordinates out of sane range
    # ------------------------------------------------------------------
    coord_keys = {"at", "start", "end", "xy"}
    for node in walk(root):
        if not (isinstance(node, ListNode) and node.key in coord_keys):
            continue
        for idx in (1, 2):
            if idx >= len(node.items):
                break
            v = _float_from_atom(node.items[idx])
            if v is not None and abs(v) > _COORD_MAX:
                issues.append(
                    LintIssue(
                        _WARN,
                        "PCB009",
                        f"Coordinate {v:.1f} mm in '({node.key} …)' exceeds "
                        f"±{_COORD_MAX:.0f} mm sane range",
                    )
                )
                break  # one warning per node is enough

    # ------------------------------------------------------------------
    # PCB010 — graphic primitives missing (layer …) declaration
    # ------------------------------------------------------------------
    _GR_KEYS = {"gr_line", "gr_arc", "gr_rect", "gr_poly", "gr_curve"}
    for item in root.items:
        if not (isinstance(item, ListNode) and item.key in _GR_KEYS):
            continue
        if find_first(item, "layer") is None:
            issues.append(
                LintIssue(
                    _ERR,
                    "PCB010",
                    f"Graphic primitive '({item.key} …)' is missing a '(layer …)' declaration",
                    path=f"kicad_pcb/{item.key}",
                )
            )

    # ------------------------------------------------------------------
    # PCB011 — footprint pads missing (layers …) declaration
    # ------------------------------------------------------------------
    for fp in footprints:
        fp_name = (
            fp.items[1].value if len(fp.items) >= 2 and isinstance(fp.items[1], StringNode) else "?"
        )
        for child in fp.items:
            if not (isinstance(child, ListNode) and child.key == "pad"):
                continue
            pad_num = (
                child.items[1].value
                if len(child.items) >= 2 and isinstance(child.items[1], (AtomNode, StringNode))
                else "?"
            )
            if find_first(child, "layers") is None:
                issues.append(
                    LintIssue(
                        _WARN,
                        "PCB011",
                        f"Pad '{pad_num}' in footprint '{fp_name}'"
                        " is missing a '(layers \u2026)' declaration",
                        path="kicad_pcb/footprint/pad",
                    )
                )

    return issues


# ---------------------------------------------------------------------------
# Fix suggestions (presented by the CLI diagnostics layer)
# ---------------------------------------------------------------------------

LINT_SUGGESTIONS: dict[str, str] = {
    "SCH001": "Check that the file is a KiCad schematic (.kicad_sch).",
    "SCH002": "Re-save the schematic in KiCad to regenerate unique UUIDs.",
    "SCH003": "Rename duplicate reference designators (e.g. change second R1 to R2).",
    "SCH004": "Add a 'Reference' property to the symbol in KiCad's symbol editor.",
    "SCH005": "Add a 'Value' property to the symbol in KiCad's symbol editor.",
    "SCH006": "Ensure the '(at x y)' coordinates contain valid numbers.",
    "SCH007": "Ensure wire '(pts (xy …) (xy …))' contains valid numeric coordinates.",
    "SCH008": "Embed the symbol definition via 'Save Symbol Copy' in KiCad.",
    "SCH009": "Embed the missing library symbol or check that the lib_id matches.",
    "SCH010": (
        "Add an '(at x y rotation)' node to every label/global_label/hierarchical_label "
        "so KiCad knows where to place it on the schematic."
    ),
    "PCB001": "Check that the file is a KiCad PCB layout (.kicad_pcb).",
    "PCB002": "Re-save the PCB in KiCad to regenerate unique UUIDs.",
    "PCB003": "Ensure every footprint has an '(at x y)' placement node.",
    "PCB004": "Ensure the footprint '(at x y [rotation])' contains valid numbers.",
    "PCB005": (
        "Add a board outline: run `set-board-size WxH` or draw Edge.Cuts in KiCad's PCB editor."
    ),
    "PCB006": "Close the board outline — all Edge.Cuts segments must connect end-to-end.",
    "PCB007": "Check that Edge.Cuts dimensions are non-zero and within a sane range.",
    "PCB008": (
        "Ensure all Edge.Cuts gr_lines have a valid '(layer \"Edge.Cuts\")' "
        "node and a numeric width."
    ),
    "PCB009": (
        "Move footprints closer to the origin (coordinates should be < 10 000 mm from origin)."
    ),
    "PCB010": (
        "Add a '(layer \"<layer_name>\")' node to every gr_line/gr_arc/gr_rect/gr_poly/gr_curve."
    ),
    "PCB011": (
        "Add a '(layers \"<Cu_layer>\")' node to every pad "
        "so KiCad knows which copper layers it belongs to."
    ),
    "LAY001": (
        "Reduce repeated net labels by connecting symbols with wires "
        "instead of placing the same label stub more than 3 times."
    ),
    "LAY002": (
        "Replace short stub wires + labels with direct wire connections between symbols "
        "to improve schematic readability."
    ),
    "LAY003": (
        "Move overlapping symbols apart using 'apply-netlist --layout graphviz' "
        "or manually reposition them in KiCad's schematic editor."
    ),
    "LAY004": (
        "Move the symbol inside the A4 page area (0–297 × 0–210 mm). "
        "Re-run 'apply-netlist' to recompute positions from the netlist."
    ),
    "LAY005": (
        "Add net labels or wires to connect isolated wire islands, "
        "or verify that all schematic sections are intentionally separate sheets."
    ),
}
