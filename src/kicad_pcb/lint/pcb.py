"""PCB lint rules (PCB001–PCB011).

:func:`lint_pcb` — PCB001–PCB011
    Enforce correctness invariants for ``.kicad_pcb`` files: valid root,
    unique UUIDs, footprint placement, Edge.Cuts outline validity, coordinate
    sanity, and layer declarations on graphic primitives and pads.

Edge.Cuts sub-group (PCB005–PCB008)
    These four rules together verify that the board outline is present,
    closed, and structurally sound.
"""

from __future__ import annotations

from ..sexpr.nodes import AtomNode, ListNode, StringNode
from ..sexpr.utils import find_first, walk
from .defs import _ERR, _WARN, LintIssue
from .helpers import (
    _check_duplicate_uuids,
    _collect_uuids,
    _float_from_atom,
    _is_numeric_atom,
)

__all__ = ["lint_pcb"]

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Coordinates beyond ±10 000 mm are almost certainly erroneous for hobby PCBs.
_COORD_MAX: float = 10_000.0

# Phase 5.6 — frozensets defined at module level; not recreated on every call.
_PCB_GR_KEYS: frozenset[str] = frozenset({"gr_line", "gr_arc", "gr_rect", "gr_poly", "gr_curve"})
_PCB_COORD_KEYS: frozenset[str] = frozenset({"at", "start", "end", "xy"})

# ---------------------------------------------------------------------------
# Module-level helpers for PCB006 (Phase 5.3 — lifted out of lint_pcb)
# ---------------------------------------------------------------------------

_Pt = tuple[float, float]


def _round_pt(pt: _Pt) -> _Pt:
    """Round a coordinate pair to 3 decimal places for endpoint matching."""
    return (round(pt[0], 3), round(pt[1], 3))


# ---------------------------------------------------------------------------
# PCB-specific private helpers
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
    for item in root.items:
        if not (isinstance(item, ListNode) and item.key in _PCB_GR_KEYS):
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
# PCB lint rules (PCB001–PCB011)
# ---------------------------------------------------------------------------


def lint_pcb(root: ListNode) -> list[LintIssue]:  # noqa: PLR0912, PLR0915
    # 11 distinct lint rules (PCB001–PCB011), each with its own branches;
    # splitting further would harm readability more than the counts warrant.
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
    # PCB002 — duplicate UUIDs  (Phase 5.1 — deduplicated via helper)
    # ------------------------------------------------------------------
    issues.extend(_check_duplicate_uuids(_collect_uuids(root), "PCB002"))

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
    # Phase 5.3 — _round_pt is now a module-level function
    # ------------------------------------------------------------------
    ec_lines = _edge_cuts_lines(root)
    if ec_lines:
        valid_segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
        for line in ec_lines:
            pts = _gr_line_endpoints(line)
            if pts is not None:
                valid_segs.append(pts)

        if valid_segs:
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
    # Phase 5.6 — uses module-level _PCB_COORD_KEYS frozenset
    # ------------------------------------------------------------------
    for node in walk(root):
        if not (isinstance(node, ListNode) and node.key in _PCB_COORD_KEYS):
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
    # Phase 5.6 — uses module-level _PCB_GR_KEYS frozenset
    # ------------------------------------------------------------------
    for item in root.items:
        if not (isinstance(item, ListNode) and item.key in _PCB_GR_KEYS):
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
