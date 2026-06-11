"""Deoverlap, gap compaction, connector bounds, crossing remediation, and page balance."""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
)
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import barycentric_sort as _barycentric_sort
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ..layout import count_wire_crossings as _count_wire_crossings
from ._snap_types import (
    _PAGE_BALANCE_CORRECTION,
    _PAGE_BALANCE_DEAD_ZONE_MM,
    _STEREO_DEOVERLAP_MIN_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _QuadrantUtilization,
)

_log = logging.getLogger(__name__)


def _deoverlap_positions(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    skip_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Push coincident positions apart so every component occupies a distinct grid cell."""
    _SNAP_GRID: float = 1.27
    _min_steps: int = math.ceil(_STEREO_DEOVERLAP_MIN_MM / _SNAP_GRID)
    min_sep: float = _min_steps * _SNAP_GRID

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        by_x[x].append(ref)

    result = dict(positions)
    for group in by_x.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (result[r][1], r))
        for i in range(1, len(group)):
            prev_ref = group[i - 1]
            curr_ref = group[i]
            _px, py, _pr = result[prev_ref]
            cx, cy, cr = result[curr_ref]
            if cy - py < min_sep:
                pair = (min(prev_ref, curr_ref), max(prev_ref, curr_ref))
                if pair in skip_pairs and not math.isclose(cy, py, abs_tol=0.01):
                    continue
                new_y = round(py + min_sep, 4)
                result[curr_ref] = (cx, new_y, cr)
    return result


def _compact_y_gap(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    min_gap_mm: float = 30.0,
) -> dict[str, tuple[float, float, float | None]]:
    """Collapse the largest vertical gap in the layout for non-power-symbol components.

    DOT sometimes places isolated source-tier connectors at the very top of the
    Graphviz graph (``gv_y ≈ max_gv_y``), which maps to ``y ≈ ORIGIN_Y`` in
    KiCad coordinates.  The bulk of the circuit then lands at a lower y,
    producing a visually jarring gap.  This pass:

    1. Identifies *regular* components (refs that do **not** start with ``#PWR``
       or ``#FLG``), which have programmatically fixed y positions assigned by
       :func:`_snap_power_symbols` and must not be moved.
    2. Finds consecutive distinct y-values in the regular set and locates the
       largest gap.  If it exceeds *min_gap_mm*, the components **below** the
       gap (larger y) are shifted *upward* (toward ORIGIN_Y) by
       ``gap_size − GRID_ROW_MM`` so a single-row separation remains.
    3. Power/flag symbols are left at their original positions.

    Parameters
    ----------
    positions:
        KiCad mm positions after all specialised snap passes.
    ir:
        Circuit IR, used only to detect power-symbol refs.
    min_gap_mm:
        Gaps smaller than this threshold are ignored (default 30 mm ≈ 4 grid rows).
    """
    if not positions:
        return positions

    power_sym_refs: set[str] = {
        comp.ref
        for comp in ir.components
        if comp.ref.startswith("#PWR") or comp.ref.startswith("#FLG")
    }
    regular_refs = [r for r in positions if r not in power_sym_refs]
    if not regular_refs:
        return positions

    ys = sorted({round(positions[r][1], 2) for r in regular_refs})
    if len(ys) < 2:
        return positions

    max_gap = 0.0
    gap_threshold_y = 0.0
    for i in range(1, len(ys)):
        gap = ys[i] - ys[i - 1]
        if gap > max_gap:
            max_gap = gap
            gap_threshold_y = ys[i - 1]

    if max_gap <= min_gap_mm:
        return positions

    collapse = round(max_gap - GRID_ROW_MM, 4)
    result = dict(positions)
    for ref in regular_refs:
        x, y, rot = result[ref]
        if y > gap_threshold_y:
            result[ref] = (x, round(y - collapse, 4), rot)
    return result


def _enforce_connector_x_bounds(
    positions: dict[str, tuple[float, float, float | None]],
    roles: Mapping[str, str],
    *,
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp connector x-coordinates to their designated page region."""
    if not roles:
        return positions

    usable_w = page_max_x - origin_x
    input_x_limit = round(round((origin_x + usable_w * 0.25) / grid) * grid, 4)
    output_x_limit = round(round((origin_x + usable_w * 0.75) / grid) * grid, 4)

    result = dict(positions)
    for ref, role in roles.items():
        if ref not in result:
            continue
        x, y, rot = result[ref]
        if role == "input" and x > input_x_limit:
            new_x = input_x_limit
            _log.debug("snap: clamping input connector %r x %.2f → %.2f", ref, x, new_x)
            result[ref] = (new_x, y, rot)
        elif role == "output" and x < output_x_limit:
            new_x = output_x_limit
            _log.debug("snap: clamping output connector %r x %.2f → %.2f", ref, x, new_x)
            result[ref] = (new_x, y, rot)
    return result


def _remediate_crossings(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    max_sweeps: int = 3,
    crossing_ratio_threshold: float = 0.30,
    skip_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Reduce wire crossings re-introduced by snap passes via barycentric re-sort."""
    sig_adj = _build_signal_adjacency(ir)
    total_sig_wires = sum(len(v) for v in sig_adj.values()) // 2
    if total_sig_wires == 0:
        return positions

    by_col: dict[int, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        if ref.startswith("#"):
            continue
        col_idx = round((x - ORIGIN_X) / _GRID_COL_MM)
        by_col[col_idx].append(ref)

    result = dict(positions)

    for sweep in range(max_sweeps):
        pos2: dict[str, tuple[float, float]] = {r: (x, y) for r, (x, y, _) in result.items()}
        crossing_count = _count_wire_crossings(pos2, sig_adj)
        ratio = crossing_count / total_sig_wires
        _log.debug(
            "remediate_crossings: sweep %d crossings=%d wires=%d ratio=%.2f",
            sweep + 1,
            crossing_count,
            total_sig_wires,
            ratio,
        )
        if ratio < crossing_ratio_threshold or sweep == max_sweeps - 1:
            break

        by_col = _barycentric_sort(dict(by_col), sig_adj)

        new_result = dict(result)
        for col_refs in by_col.values():
            if len(col_refs) < 2:
                continue
            y_slots = sorted(result[r][1] for r in col_refs)
            for ref, new_y in zip(col_refs, y_slots):
                x, _old_y, rot = result[ref]
                new_result[ref] = (x, new_y, rot)
        result = new_result

    result = _deoverlap_positions(result, skip_pairs=skip_pairs)
    return result


def _clamp_to_page(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    min_x: float = ORIGIN_X,
    min_y: float = ORIGIN_Y,
    max_x: float = PAGE_MAX_X,
    max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp every position so it stays within the printable A4 area."""
    out: dict[str, tuple[float, float, float | None]] = {}
    for ref, (x, y, rot) in positions.items():
        cx = max(min_x, min(max_x, x))
        cy = max(min_y, min(max_y, y))
        out[ref] = (cx, cy, rot)
    return out


def _compute_page_quadrant_utilization(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    page_max_x: float = PAGE_MAX_X,
    page_max_y: float = PAGE_MAX_Y,
) -> _QuadrantUtilization:
    """Measure how components are distributed across the four page quadrants."""
    _QUADRANTS = ("top_left", "top_right", "bottom_left", "bottom_right")
    if not positions:
        return {
            "top_left": 0.0,
            "top_right": 0.0,
            "bottom_left": 0.0,
            "bottom_right": 0.0,
            "imbalance": 0.0,
            "dense_quadrant": _QUADRANTS[0],
            "sparse_quadrant": _QUADRANTS[0],
        }

    cx = (origin_x + page_max_x) / 2.0
    cy = (origin_y + page_max_y) / 2.0

    counts: dict[str, int] = {q: 0 for q in _QUADRANTS}
    for x, y, _ in positions.values():
        if x <= cx and y <= cy:
            counts["top_left"] += 1
        elif x > cx and y <= cy:
            counts["top_right"] += 1
        elif x <= cx:
            counts["bottom_left"] += 1
        else:
            counts["bottom_right"] += 1

    total = len(positions)
    fractions: dict[str, float] = {k: v / total for k, v in counts.items()}
    dense = max(fractions, key=lambda k: fractions[k])
    sparse = min(fractions, key=lambda k: fractions[k])
    return {
        "top_left": fractions["top_left"],
        "top_right": fractions["top_right"],
        "bottom_left": fractions["bottom_left"],
        "bottom_right": fractions["bottom_right"],
        "imbalance": round(fractions[dense] - fractions[sparse], 4),
        "dense_quadrant": dense,
        "sparse_quadrant": sparse,
    }


def _snap_page_balance(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> tuple[dict[str, tuple[float, float, float | None]], float]:
    """Nudge signal-path components toward the vertical page centre (Phase 8.1)."""
    if not positions or block_layout is None:
        return dict(positions), 0.0

    role_by_ref = {ref: a.role for ref, a in block_layout.assignments.items()}
    signal_refs = [
        ref
        for ref in positions
        if (
            (role := role_by_ref.get(ref)) is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
        )
        and not ref.startswith("#")
    ]
    if not signal_refs:
        return dict(positions), 0.0

    signal_ys = [positions[ref][1] for ref in signal_refs]
    circuit_center_y = sum(signal_ys) / len(signal_ys)
    page_center_y = (origin_y + page_max_y) / 2.0

    delta = page_center_y - circuit_center_y
    if abs(delta) < _PAGE_BALANCE_DEAD_ZONE_MM:
        _log.debug(
            "page balance: δy=%.2f mm < dead zone %.2f mm — skipping",
            delta,
            _PAGE_BALANCE_DEAD_ZONE_MM,
        )
        return dict(positions), 0.0

    shift = round(delta * _PAGE_BALANCE_CORRECTION, 2)
    _log.debug(
        "page balance: circuit_center_y=%.2f page_center_y=%.2f δy=%.2f shift=%.2f mm",
        circuit_center_y,
        page_center_y,
        delta,
        shift,
    )

    _GRID_SNAP = 1.27
    shift = round(round(shift / _GRID_SNAP) * _GRID_SNAP, 4)

    current_ys = [round(positions[ref][1], 2) for ref in signal_refs]
    shifted_ys_check = [round(positions[ref][1] + shift, 2) for ref in signal_refs]
    current_y_set = set(current_ys)
    shifted_y_set = set(shifted_ys_check)
    if len(shifted_y_set) < len(current_y_set):
        _log.debug("page balance: shift %.2f mm would create new overlaps — skipping", shift)
        return dict(positions), 0.0

    result = dict(positions)
    for ref in signal_refs:
        x, y, rot = result[ref]
        result[ref] = (x, round(y + shift, 2), rot)
    return result, shift
