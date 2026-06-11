"""Block zones, density spreading, decoupling, stereo, column spreading, deoverlap.

Handles the geometric passes of the snap pipeline: block zone bias, density
spreading, decoupling cap placement, stereo channel split, column spreading,
deoverlap, crossing remediation, and page balance.
"""

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
    is_power_like_role,
)
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..component_types import power_rail_polarity
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import barycentric_sort as _barycentric_sort
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ..layout import count_wire_crossings as _count_wire_crossings
from ._snap_types import (
    _MULTI_UNIT_SIGNAL_SIBLING_GAP_MM,
    _PAGE_BALANCE_CORRECTION,
    _PAGE_BALANCE_DEAD_ZONE_MM,
    _PROPERTY_TEXT_NEAR_X_MM,
    _PROPERTY_TEXT_VERTICAL_GAP_MM,
    _STEREO_DEOVERLAP_MIN_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _decoupling_family_anchor_x,
    _is_ic_ref,
    _multi_unit_base_ref,
    _QuadrantUtilization,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Block zones and density
# ---------------------------------------------------------------------------


def _snap_block_zones(
    positions: dict[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Bias component positions toward their designated functional block zones."""
    result = dict(positions)

    LEFT_ZONE_MAX_X = origin_x + 90.0
    RIGHT_ZONE_MIN_X = page_max_x - 117.0
    TOP_ZONE_MAX_Y = origin_y + 50.0

    OUTPUT_MIN_X = origin_x + 140.0
    INPUT_MAX_X = origin_x + 100.0

    for ref, assignment in block_layout.assignments.items():
        if ref not in result:
            continue
        if ref.startswith("#"):
            continue

        x, y, rot = result[ref]
        role = assignment.role

        if is_power_like_role(role):
            if y > TOP_ZONE_MAX_Y + 20.0:
                new_y = round(y - GRID_ROW_MM, 2)
                _log.debug(
                    "block zone snap: %r (%s) too low (y=%.2f), nudging to %.2f",
                    ref,
                    role.value,
                    y,
                    new_y,
                )
                result[ref] = (x, new_y, rot)

        elif is_input_like_role(role):
            if x > LEFT_ZONE_MAX_X:
                new_x = round(max(origin_x + 20.0, min(INPUT_MAX_X, x - 50.8)), 2)
                _log.debug(
                    "block zone snap: %r (%s) too far right (x=%.2f), pulling to %.2f",
                    ref,
                    role.value,
                    x,
                    new_x,
                )
                result[ref] = (new_x, y, rot)

        elif is_output_like_role(role) and x < RIGHT_ZONE_MIN_X:
            new_x = round(max(OUTPUT_MIN_X, x + 80.0), 2)
            new_x = min(page_max_x - 20.0, new_x)
            _log.debug(
                "block zone snap: %r (%s) too far left (x=%.2f), pulling to %.2f",
                ref,
                role.value,
                x,
                new_x,
            )
            result[ref] = (new_x, y, rot)

    return result


def _apply_density_spreading(  # noqa: PLR0912, PLR0915
    positions: dict[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    radius_mm: float = 30.0,
    threshold: int = 5,
) -> dict[str, tuple[float, float, float | None]]:
    """Push apart symbols in dense clusters to reduce local crowding (Phase 2.3)."""
    result = dict(positions)
    refs = list(positions.keys())

    pos_map = {ref: (x, y) for ref, (x, y, _) in positions.items()}

    density: dict[str, int] = {}
    radius_sq = radius_mm**2
    for ref_i in refs:
        if ref_i.startswith("#"):
            continue
        if ref_i not in pos_map:
            continue
        x_i, y_i = pos_map[ref_i]
        neighbors = 0
        for ref_j in refs:
            if ref_j.startswith("#"):
                continue
            if ref_i == ref_j or ref_j not in pos_map:
                continue
            x_j, y_j = pos_map[ref_j]
            dist_sq = (x_i - x_j) ** 2 + (y_i - y_j) ** 2
            if dist_sq <= radius_sq:
                neighbors += 1
        density[ref_i] = neighbors

    dense_refs = {ref for ref, count in density.items() if count >= threshold}

    if not dense_refs:
        return result

    if block_layout:
        groups: dict[tuple[str, float], list[str]] = defaultdict(list)
        for ref in dense_refs:
            if ref not in block_layout.assignments:
                continue
            role = block_layout.assignments[ref].role.value
            x, _, _ = result[ref]
            col_x = round(x / 25.4) * 25.4
            groups[(role, col_x)].append(ref)
    else:
        groups = defaultdict(list)
        for ref in dense_refs:
            x, _, _ = result[ref]
            col_x = round(x / 25.4) * 25.4
            groups[("ALL", col_x)].append(ref)

    for (role_or_all, col_x), group_refs in groups.items():
        if len(group_refs) < 2:
            continue

        sorted_refs = sorted(group_refs, key=lambda r: result[r][1])

        min_spacing = GRID_ROW_MM

        y_positions: list[float] = []
        current_y = result[sorted_refs[0]][1]
        for ref in sorted_refs:
            _, old_y, _ = result[ref]
            if y_positions and current_y < y_positions[-1] + min_spacing:
                current_y = round(y_positions[-1] + min_spacing, 2)
            else:
                current_y = round(old_y, 2)
            y_positions.append(current_y)
            current_y += min_spacing

        for i, ref in enumerate(sorted_refs):
            x, old_y, rot = result[ref]
            new_y = y_positions[i]
            if abs(new_y - old_y) > 0.1:
                _log.debug(
                    "density spread: %r (%s col=%.1f) y %.2f → %.2f (neighbors=%d)",
                    ref,
                    role_or_all,
                    col_x,
                    old_y,
                    new_y,
                    density[ref],
                )
                result[ref] = (x, new_y, rot)

    return result


# ---------------------------------------------------------------------------
# Decoupling and rail
# ---------------------------------------------------------------------------


def _post_snap_decoupling_caps(
    positions: dict[str, tuple[float, float, float | None]],
    decoupling_map: dict[str, str],
    *,
    rail_polarities: Mapping[str, str | None] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Snap each decoupling cap to sit directly above its associated IC."""
    result = dict(positions)

    def _decoupling_bank_x(anchor_x: float, lane_index: int) -> float:
        if lane_index <= 1:
            return round(anchor_x, 2)
        side_step = ((lane_index - 2) // 2) + 1
        side_sign = -1 if lane_index % 2 == 0 else 1
        return round(anchor_x + side_sign * side_step * _GRID_COL_MM, 2)

    caps_by_ic: dict[str, list[str]] = defaultdict(list)
    for cap_ref, ic_ref in decoupling_map.items():
        if cap_ref in result and ic_ref in result:
            caps_by_ic[ic_ref].append(cap_ref)

    for ic_ref, cap_refs in caps_by_ic.items():
        ic_x, ic_y, _ = result[ic_ref]
        bank_anchor_x = _decoupling_family_anchor_x(ic_ref, result)
        positive_caps = sorted(
            cap_ref for cap_ref in cap_refs if (rail_polarities or {}).get(cap_ref) != "negative"
        )
        negative_caps = sorted(
            cap_ref for cap_ref in cap_refs if (rail_polarities or {}).get(cap_ref) == "negative"
        )
        for idx, cap_ref in enumerate(positive_caps):
            cap_x = _decoupling_bank_x(bank_anchor_x, idx)
            row_offset = idx + 1
            if idx > 0 and math.isclose(cap_x, bank_anchor_x, abs_tol=0.01):
                row_offset += 2
            cap_y = round(ic_y - row_offset * GRID_ROW_MM, 2)
            result[cap_ref] = (cap_x, cap_y, None)
        for idx, cap_ref in enumerate(negative_caps):
            cap_x = _decoupling_bank_x(bank_anchor_x, idx)
            row_offset = idx + 1
            if idx > 0 and math.isclose(cap_x, bank_anchor_x, abs_tol=0.01):
                row_offset += 2
            cap_y = round(ic_y + row_offset * GRID_ROW_MM, 2)
            result[cap_ref] = (cap_x, cap_y, None)
    return result


def _decoupling_rail_polarities(
    ir: CircuitIR | None,
    decoupling_map: Mapping[str, str],
) -> dict[str, str | None]:
    """Return the rail polarity for each decoupling capacitor in *decoupling_map*."""
    if ir is None or not decoupling_map:
        return {}

    cap_refs = set(decoupling_map)
    cap_nets: dict[str, set[str]] = {cap_ref: set() for cap_ref in cap_refs}
    for net in ir.nets:
        for pin_ref in net.pins:
            if pin_ref.ref in cap_refs:
                cap_nets[pin_ref.ref].add(net.name)

    result: dict[str, str | None] = {}
    for cap_ref, net_names in cap_nets.items():
        polarity: str | None = None
        for net_name in sorted(net_names):
            if _is_ground_like_name(net_name):
                continue
            rail_polarity = power_rail_polarity(net_name)
            if rail_polarity is not None:
                polarity = rail_polarity
                break
        result[cap_ref] = polarity
    return result


def _center_ics_in_columns(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    halo: Mapping[str, str] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Re-sort each x-column so ICs sit at the vertical midpoint."""
    halo_keys: frozenset[str] = frozenset(halo) if halo else frozenset()

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        if ref.startswith("#"):
            continue
        by_x[x].append(ref)

    result = dict(positions)
    for group in by_x.values():
        if len(group) < 2:
            continue
        if not any(_is_ic_ref(r) for r in group):
            continue

        group_sorted = sorted(group, key=lambda r: (positions[r][1], r))
        y_slots = [positions[r][1] for r in group_sorted]

        ic_refs = [r for r in group_sorted if _is_ic_ref(r)]
        other = [r for r in group_sorted if not _is_ic_ref(r)]
        halo_other = [r for r in other if r in halo_keys]
        plain_other = [r for r in other if r not in halo_keys]

        mid_plain = len(plain_other) // 2
        mid_halo = len(halo_other) // 2
        ordered = (
            plain_other[:mid_plain]
            + halo_other[:mid_halo]
            + ic_refs
            + halo_other[mid_halo:]
            + plain_other[mid_plain:]
        )

        for ref, y in zip(ordered, y_slots):
            x, _old_y, rot = result[ref]
            result[ref] = (x, y, rot)

    return result


def _snap_multi_unit_sibling_cohesion(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    unit_sibling_pairs: tuple[tuple[str, str], ...] = (),
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Compact split-unit IC siblings into one readable x-cluster."""
    if not unit_sibling_pairs and not power_unit_refs:
        return positions

    successors: dict[str, str] = {}
    predecessors: dict[str, str] = {}
    for left_ref, right_ref in unit_sibling_pairs:
        successors[left_ref] = right_ref
        predecessors[right_ref] = left_ref

    ordered_groups: list[list[str]] = []
    visited: set[str] = set()
    sibling_refs = {ref for pair in unit_sibling_pairs for ref in pair}
    for start_ref in sorted(sibling_refs):
        if start_ref in predecessors or start_ref in visited:
            continue
        group: list[str] = []
        current_ref: str = start_ref
        while current_ref not in visited:
            group.append(current_ref)
            visited.add(current_ref)
            next_ref = successors.get(current_ref)
            if next_ref is None:
                break
            current_ref = next_ref
        if len(group) >= 2:
            ordered_groups.append(group)

    if not ordered_groups:
        return positions

    power_units_by_base: dict[str, str] = {}
    for power_unit_ref in sorted(power_unit_refs):
        base_ref = _multi_unit_base_ref(power_unit_ref)
        if base_ref is not None:
            power_units_by_base[base_ref] = power_unit_ref

    result = dict(positions)
    for group in ordered_groups:
        placed_signal_units = [ref for ref in group if ref in result]
        if len(placed_signal_units) < 2:
            continue

        min_signal_x = min(result[ref][0] for ref in placed_signal_units)
        anchor_x = round(min_signal_x, 2)
        for index, ref in enumerate(placed_signal_units):
            _old_x, y, rotation = result[ref]
            target_x = round(anchor_x + index * _MULTI_UNIT_SIGNAL_SIBLING_GAP_MM, 2)
            result[ref] = (target_x, y, rotation)

        base_ref = _multi_unit_base_ref(placed_signal_units[0])
        grouped_power_ref = power_units_by_base.get(base_ref or "")
        if grouped_power_ref is None or grouped_power_ref not in result:
            continue

        cluster_center_x = round(
            sum(result[ref][0] for ref in placed_signal_units) / len(placed_signal_units),
            2,
        )
        _power_x, power_y, power_rotation = result[grouped_power_ref]
        result[grouped_power_ref] = (cluster_center_x, power_y, power_rotation)

    return result


# ---------------------------------------------------------------------------
# Stereo split
# ---------------------------------------------------------------------------


def _apply_stereo_split(
    positions: dict[str, tuple[float, float, float | None]],
    channels: Mapping[str, str],
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Remap y-coordinates to enforce stereo top-half / bottom-half split."""
    if not any(v in ("L", "R") for v in channels.values()):
        return positions

    page_height = page_max_y - origin_y
    result: dict[str, tuple[float, float, float | None]] = {}
    for ref, (x, y, rot) in positions.items():
        channel = channels.get(ref, "mono")
        y_rel = y - origin_y
        if channel == "L":
            y_new = origin_y + y_rel * 0.45
        elif channel == "R":
            y_new = origin_y + page_height * 0.55 + y_rel * 0.45
        else:
            y_new = y
        result[ref] = (x, round(y_new, 2), rot)

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in result.items():
        by_x[x].append(ref)
    for group in by_x.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: result[r][1])
        for i in range(1, len(group)):
            prev_ref = group[i - 1]
            curr_ref = group[i]
            px, py, pr = result[prev_ref]
            cx, cy, cr = result[curr_ref]
            if cy - py < _STEREO_DEOVERLAP_MIN_MM:
                result[curr_ref] = (cx, round(py + _STEREO_DEOVERLAP_MIN_MM, 2), cr)

    return result


def _post_stereo_barycentric(  # noqa: PLR0912
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    channels: Mapping[str, str],
    *,
    passes: int = 2,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply barycentric vertical re-ordering within each stereo channel band."""
    if not any(v in ("L", "R") for v in channels.values()):
        return positions

    sig_adj: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for ri in pin_refs:
            for rj in pin_refs:
                if ri != rj:
                    sig_adj[ri].add(rj)

    result = dict(positions)

    for band_ch in ("L", "R"):
        band_refs = [r for r, ch in channels.items() if ch == band_ch]
        if not band_refs:
            continue

        by_x: dict[float, list[str]] = defaultdict(list)
        for ref in band_refs:
            by_x[result[ref][0]].append(ref)

        sorted_xs = sorted(by_x)
        if len(sorted_xs) < 2:
            continue

        for x in sorted_xs:
            by_x[x].sort(key=lambda r: result[r][1])

        ref_to_x: dict[str, float] = {ref: result[ref][0] for ref in band_refs}

        def _row_map_stereo() -> dict[str, int]:
            return {ref: i for x in sorted_xs for i, ref in enumerate(by_x[x])}

        def _avg_nbr_row_stereo(
            ref: str,
            target_x: float,
            row_map: dict[str, int],
        ) -> float:
            nbrs = [r for r in sig_adj.get(ref, set()) if ref_to_x.get(r) == target_x]
            if nbrs:
                return sum(row_map[r] for r in nbrs) / len(nbrs)
            return float(row_map.get(ref, 0))

        for _ in range(passes):
            rm = _row_map_stereo()
            for i, x in enumerate(sorted_xs):
                if i == 0:
                    by_x[x].sort(key=lambda r: (0.0, r))
                    for j, ref in enumerate(by_x[x]):
                        rm[ref] = j
                else:
                    prev_x = sorted_xs[i - 1]
                    by_x[x].sort(
                        key=lambda r, _px=prev_x, _rm=rm: (  # type: ignore[misc]
                            _avg_nbr_row_stereo(r, _px, _rm),
                            r,
                        )
                    )
                    for j, ref in enumerate(by_x[x]):
                        rm[ref] = j

            rm = _row_map_stereo()
            for i in range(len(sorted_xs) - 2, -1, -1):
                x = sorted_xs[i]
                next_x = sorted_xs[i + 1]
                by_x[x].sort(
                    key=lambda r, _nx=next_x, _rm=rm: (  # type: ignore[misc]
                        _avg_nbr_row_stereo(r, _nx, _rm),
                        r,
                    )
                )
                for j, ref in enumerate(by_x[x]):
                    rm[ref] = j

        for x in sorted_xs:
            sorted_ys = sorted(result[r][1] for r in by_x[x])
            for ref, new_y in zip(by_x[x], sorted_ys):
                rx, _ry, rrot = result[ref]
                result[ref] = (rx, new_y, rrot)

    return result


# ---------------------------------------------------------------------------
# Column spreading
# ---------------------------------------------------------------------------


def _spread_x_columns(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    max_per_column: int = 3,
    col_step_mm: float = 25.4,
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Spread overloaded x-columns into multiple sub-columns."""
    _GRID: float = 1.27

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _r) in positions.items():
        by_x[x].append(ref)

    result = dict(positions)
    for x, group in by_x.items():
        if len(group) <= max_per_column:
            continue
        group.sort(key=lambda r: (result[r][1], r))
        n_cols = math.ceil(len(group) / max_per_column)
        half = (n_cols - 1) / 2.0
        for col_idx in range(n_cols):
            offset = (col_idx - half) * col_step_mm
            raw_x = x + offset
            new_x: float = round(round(raw_x / _GRID) * _GRID, 4)
            new_x = max(origin_x, min(page_max_x, new_x))
            start = col_idx * max_per_column
            end = min(start + max_per_column, len(group))
            for ref in group[start:end]:
                _cx, cy, cr = result[ref]
                result[ref] = (new_x, cy, cr)
    return result


def _apply_property_text_spacing(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    near_x_mm: float = _PROPERTY_TEXT_NEAR_X_MM,
    min_vertical_gap_mm: float = _PROPERTY_TEXT_VERTICAL_GAP_MM,
    fixed_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Reserve a readable vertical lane for generated Reference/Value text."""
    if len(positions) < 2:
        return positions

    result = dict(positions)
    movable_refs = [ref for ref in positions if not ref.startswith("#")]
    if len(movable_refs) < 2:
        return result

    changed = True
    while changed:
        changed = False
        ordered_refs = sorted(movable_refs, key=lambda ref: (result[ref][1], result[ref][0], ref))
        for idx, upper_ref in enumerate(ordered_refs[:-1]):
            upper_x, upper_y, _upper_rot = result[upper_ref]
            for lower_ref in ordered_refs[idx + 1 :]:
                lower_x, lower_y, lower_rot = result[lower_ref]
                target_y = round(upper_y + min_vertical_gap_mm, 4)
                if lower_y + 1e-6 >= target_y:
                    break
                if abs(lower_x - upper_x) > near_x_mm:
                    continue
                if lower_ref in fixed_refs:
                    continue
                if target_y > lower_y + 1e-6:
                    result[lower_ref] = (lower_x, target_y, lower_rot)
                    changed = True

    return result


# ---------------------------------------------------------------------------
# Deoverlap and y-gap compact
# ---------------------------------------------------------------------------


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

    # Distinct y values for regular components, ascending.
    ys = sorted({round(positions[r][1], 2) for r in regular_refs})
    if len(ys) < 2:
        return positions

    # Find the largest consecutive gap.
    max_gap = 0.0
    gap_threshold_y = 0.0  # y-value *above* the gap (the lower of the two boundary rows)
    for i in range(1, len(ys)):
        gap = ys[i] - ys[i - 1]
        if gap > max_gap:
            max_gap = gap
            gap_threshold_y = ys[i - 1]  # components with y > this are "below the gap"

    if max_gap <= min_gap_mm:
        return positions

    # Shift all regular components below the gap upward to close it.
    # Keep one GRID_ROW_MM separation so the two clusters remain visually distinct.
    collapse = round(max_gap - GRID_ROW_MM, 4)
    result = dict(positions)
    for ref in regular_refs:
        x, y, rot = result[ref]
        if y > gap_threshold_y:
            result[ref] = (x, round(y - collapse, 4), rot)
    return result


# ---------------------------------------------------------------------------
# Connector bounds
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Crossing remediation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Page-bounds clamp
# ---------------------------------------------------------------------------


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
