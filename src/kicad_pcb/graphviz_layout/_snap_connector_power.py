"""Connector y-alignment, power symbol clamping, feedback placement, and opamp halo."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR

from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import IC_PREFIXES as _IC_PREFIXES_CT
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..errors import ErrorCode, UserError
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import ComponentAnnotation as _ComponentAnnotation
from ._snap_types import (
    _POWER_BOTTOM_MARGIN_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _is_connector_ref,
)

_log = logging.getLogger(__name__)


def _snap_connectors_to_ic_y(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Snap each connector's y-coordinate to the median y of its signal-net neighbours."""
    connector_refs: set[str] = {c.ref for c in ir.components if _is_connector_ref(c.ref)}
    if not connector_refs:
        return positions

    sig_nbrs: dict[str, list[str]] = {r: [] for r in connector_refs}
    for net in ir.nets:
        if _is_power_net(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for ref in pin_refs:
            if ref not in connector_refs:
                continue
            for other in pin_refs:
                if other == ref or other in connector_refs:
                    continue
                if other in positions:
                    sig_nbrs[ref].append(other)

    result = dict(positions)
    for con_ref in connector_refs:
        if con_ref not in result:
            continue
        nbrs = sig_nbrs.get(con_ref, [])
        if not nbrs:
            continue
        nbr_ys = sorted(result[n][1] for n in nbrs)
        median_y = nbr_ys[len(nbr_ys) // 2]
        snapped_y = round(round(median_y / grid) * grid, 4)
        x, _, rot = result[con_ref]
        result[con_ref] = (x, snapped_y, rot)
    return result


def _snap_power_symbols(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp ``#PWR`` and ``#FLG`` power symbols to the top or bottom page row."""
    result = dict(positions)
    for comp in ir.components:
        ref = comp.ref
        if not (ref.startswith("#PWR") or ref.startswith("#FLG")):
            continue
        if ref not in result:
            continue
        is_gnd = _is_ground_like_name(comp.value or "")
        target_y = round(page_max_y - _POWER_BOTTOM_MARGIN_MM, 2) if is_gnd else origin_y
        x, _, rot = result[ref]
        result[ref] = (x, target_y, rot)
    return result


def _snap_feedback_components(
    positions: dict[str, tuple[float, float, float | None]],
    annotations: dict[str, _ComponentAnnotation],
    ir: CircuitIR,
    *,
    strict: bool = False,
) -> dict[str, tuple[float, float, float | None]]:
    """Place feedback components visually above their nearest IC/connector anchor."""
    _anchor_prefixes = _IC_PREFIXES_CT + _CONNECTOR_PREFIXES_CT

    signal_nets = [n for n in ir.nets if not _is_power_net(n.name) and len(n.pins) >= 2]

    comp_nbrs: dict[str, set[str]] = defaultdict(set)
    for net in signal_nets:
        for pin in net.pins:
            for other in net.pins:
                if other.ref != pin.ref:
                    comp_nbrs[pin.ref].add(other.ref)

    result = dict(positions)
    for comp in ir.components:
        ref = comp.ref
        ann = annotations.get(ref)
        if ann is None or not ann.feedback or ref not in result:
            continue

        anchor_y = _resolve_feedback_anchor_y(
            ref=ref,
            neighbors=sorted(comp_nbrs.get(ref, [])),
            positions=result,
            anchor_prefixes=_anchor_prefixes,
            strict=strict,
        )

        if anchor_y is None:
            continue

        x, _, rot = result[ref]
        result[ref] = (x, round(anchor_y - GRID_ROW_MM, 2), rot)

    return result


def _resolve_feedback_anchor_y(
    *,
    ref: str,
    neighbors: list[str],
    positions: dict[str, tuple[float, float, float | None]],
    anchor_prefixes: tuple[str, ...],
    strict: bool,
) -> float | None:
    """Resolve preferred y-anchor for a feedback component."""
    for nbr in neighbors:
        if any(nbr.upper().startswith(pfx) for pfx in anchor_prefixes) and nbr in positions:
            return positions[nbr][1]

    positioned_nbrs = [nbr for nbr in neighbors if nbr in positions]
    if strict and positioned_nbrs:
        raise UserError(
            "Feedback component has no IC/connector anchor",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={
                "ref": ref,
                "positioned_neighbors": positioned_nbrs,
            },
        )
    if positioned_nbrs:
        return positions[positioned_nbrs[0]][1]
    return None


def _snap_opamp_halo(
    positions: Mapping[str, tuple[float, float, float | None]],
    halo: Mapping[str, str],
) -> dict[str, tuple[float, float, float | None]]:
    """Normalize halo members into adjacent lanes around their anchor IC."""
    if not halo:
        return dict(positions)

    result = dict(positions)

    by_anchor: dict[str, list[str]] = defaultdict(list)
    for halo_ref, anchor_ref in halo.items():
        if halo_ref in result and anchor_ref in result:
            by_anchor[anchor_ref].append(halo_ref)

    for anchor_ref, halo_refs in by_anchor.items():
        anchor_x, anchor_y, _ = result[anchor_ref]
        for i, halo_ref in enumerate(sorted(halo_refs)):
            halo_x, halo_y, halo_rot = result[halo_ref]
            place_left = i % 2 == 0
            left_x = round(max(ORIGIN_X, anchor_x - _GRID_COL_MM), 2)
            right_x = round(min(PAGE_MAX_X, anchor_x + _GRID_COL_MM), 2)

            if halo_x < anchor_x - 1.0:
                target_x = left_x
            elif halo_x > anchor_x + 1.0 or left_x == anchor_x:
                target_x = right_x
            elif right_x == anchor_x:
                target_x = left_x
            else:
                target_x = left_x if place_left else right_x

            if abs(halo_x - target_x) <= 1.0:
                continue

            level = i // 2 + 1
            sign = -1 if (i % 2 == 0) else 1
            new_y = round(anchor_y + sign * level * GRID_ROW_MM, 2)
            _log.debug(
                "halo snap: %r x=%.2f normalized near anchor %r x=%.2f target_x=%.2f y %.2f → %.2f",
                halo_ref,
                halo_x,
                anchor_ref,
                anchor_x,
                target_x,
                halo_y,
                new_y,
            )
            result[halo_ref] = (target_x, new_y, halo_rot)

    return result
