"""Stereo channel split, barycentric re-sort, column spreading, and text spacing."""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR

from ..component_types import is_power_net as _is_power_net
from ._snap_types import (
    _PROPERTY_TEXT_NEAR_X_MM,
    _PROPERTY_TEXT_VERTICAL_GAP_MM,
    _STEREO_DEOVERLAP_MIN_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
)


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
