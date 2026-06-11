"""Block zones, density spreading, decoupling, IC centering, and multi-unit cohesion."""

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
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import power_rail_polarity
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ._snap_types import (
    _MULTI_UNIT_SIGNAL_SIBLING_GAP_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    _decoupling_family_anchor_x,
    _is_ic_ref,
    _multi_unit_base_ref,
)

_log = logging.getLogger(__name__)


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
