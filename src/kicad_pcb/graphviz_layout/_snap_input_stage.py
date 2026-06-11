"""Input stage cohesion: find members, place lanes, evict intruders, signal attachment."""

from __future__ import annotations

import math
from collections import deque
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
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_types import (
    GRID_ROW_MM,
    ORIGIN_X,
    _is_connector_ref,
    _is_ic_ref,
)


def _find_input_stage_members(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    adjacency: Mapping[str, set[str]],
    *,
    ic_x: float,
    ic_y: float,
) -> tuple[list[str], list[str], set[str]]:
    """Return ``(input_refs, preconditioning_refs, stage_ref_set)`` for Phase 7.1."""
    from ..block_detection import BlockRole  # noqa: PLC0415

    input_connectors = sorted(
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.INPUT and _is_connector_ref(ref)
    )
    if not input_connectors:
        input_connectors = sorted(
            ref for ref, role in role_by_ref.items() if ref in positions and role == BlockRole.INPUT
        )
    if not input_connectors:
        return [], [], set()

    stage_refs: set[str] = set(input_connectors)
    for start in input_connectors:
        frontier: set[str] = {start}
        seen: set[str] = {start}
        for _ in range(2):
            next_frontier: set[str] = set()
            for ref in frontier:
                for nbr in adjacency.get(ref, set()):
                    if nbr in seen or nbr not in positions:
                        continue
                    seen.add(nbr)
                    role = role_by_ref.get(nbr)
                    if role is not None and is_input_like_role(role):
                        stage_refs.add(nbr)
                        next_frontier.add(nbr)
            frontier = next_frontier
            if not frontier:
                break

    for ref, role in role_by_ref.items():
        if ref not in positions or role is None or not is_input_like_role(role):
            continue
        x, y, _ = positions[ref]
        if x <= ic_x and abs(y - ic_y) <= 6.0 * GRID_ROW_MM:
            stage_refs.add(ref)

    input_refs = sorted(
        [ref for ref in stage_refs if role_by_ref.get(ref) == BlockRole.INPUT],
        key=lambda ref: positions[ref][1],
    )
    pre_refs = sorted(
        [ref for ref in stage_refs if role_by_ref.get(ref) == BlockRole.PRECONDITIONING],
        key=lambda ref: positions[ref][1],
    )
    return input_refs, pre_refs, stage_refs


def _input_stage_connector_distances(
    input_refs: list[str],
    stage_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest input-connector hop distance for refs within the input stage."""
    if not input_refs or not stage_refs:
        return {}

    stage_distance: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque((ref, 0) for ref in input_refs)
    seen = set(input_refs)
    while frontier:
        ref, dist = frontier.popleft()
        stage_distance[ref] = dist
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in stage_refs:
                continue
            seen.add(nbr)
            frontier.append((nbr, dist + 1))
    return stage_distance


def _place_input_stage_lane(
    positions: dict[str, tuple[float, float, float | None]],
    input_refs: list[str],
    pre_refs: list[str],
    *,
    anchor: tuple[float, float],
    stage_distance: Mapping[str, int] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Place input and preconditioning refs into compact left-side columns."""
    ic_x, ic_y = anchor
    result = dict(positions)
    connector_x = round(ic_x - 3.0 * _GRID_COL_MM, 2)
    precond_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    precond_inner_x = round(ic_x - _GRID_COL_MM, 2)

    for idx, ref in enumerate(input_refs):
        x, _y, rot = result[ref]
        offset = idx - (len(input_refs) - 1) / 2
        target_y = round(ic_y + offset * GRID_ROW_MM, 2)
        result[ref] = (min(round(x, 2), connector_x), target_y, rot)

    for idx, ref in enumerate(pre_refs):
        x, _y, rot = result[ref]
        offset = idx - (len(pre_refs) - 1) / 2
        target_y = round(ic_y + offset * GRID_ROW_MM, 2)
        target_x = precond_x
        if len(pre_refs) >= 3 and (stage_distance or {}).get(ref, 0) >= 2:
            target_x = precond_inner_x
        result[ref] = (round(target_x, 2), target_y, rot)

    return result


def _evict_input_lane_intruders(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    stage_refs: set[str],
    *,
    precond_x: float,
    ic_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep unrelated roles out of the input lane."""
    result = dict(positions)
    for ref, role in role_by_ref.items():
        if ref not in result or ref in stage_refs:
            continue
        x, y, rot = result[ref]
        if x >= precond_x:
            continue
        if role is not None and (is_output_like_role(role) or is_core_like_role(role)):
            result[ref] = (precond_x, y, rot)
        elif role is not None and is_power_like_role(role):
            top_y = round(ic_y - 5.0 * GRID_ROW_MM, 2)
            result[ref] = (x, min(y, top_y), rot)
    return result


def _snap_input_stage_cohesion(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep the input stage coherent and clearly left-bounded (Phase 7.1)."""
    if not positions or block_layout is None:
        return positions

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    ic_refs = [ref for ref in positions if _is_ic_ref(ref)]
    if not ic_refs:
        return positions

    anchor_ic = max(ic_refs, key=lambda ref: positions[ref][0])
    ic_x, ic_y, _ = positions[anchor_ic]
    adjacency = _build_signal_adjacency(ir)

    input_refs, pre_refs, stage_refs = _find_input_stage_members(
        positions,
        role_by_ref,
        adjacency,
        ic_x=ic_x,
        ic_y=ic_y,
    )
    if not input_refs and not pre_refs:
        return positions

    result = _place_input_stage_lane(
        positions,
        input_refs,
        pre_refs,
        anchor=(ic_x, ic_y),
        stage_distance=_input_stage_connector_distances(input_refs, stage_refs, adjacency),
    )
    precond_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    return _evict_input_lane_intruders(
        result,
        role_by_ref,
        stage_refs,
        precond_x=precond_x,
        ic_y=ic_y,
    )


def _snap_input_connector_signal_attachment(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep each input connector attached to its incoming signal row."""
    if not positions or block_layout is None:
        return dict(positions)

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    adjacency = _build_signal_adjacency(ir)
    result = dict(positions)

    input_connectors = sorted(
        ref
        for ref, role in role_by_ref.items()
        if ref in result and role == BlockRole.INPUT and _is_connector_ref(ref)
    )
    for connector_ref in input_connectors:
        signal_neighbors = [
            ref
            for ref in adjacency.get(connector_ref, set())
            if ref in result
            and not _is_connector_ref(ref)
            and role_by_ref.get(ref) in {BlockRole.INPUT, BlockRole.PRECONDITIONING}
        ]
        if not signal_neighbors:
            continue

        signal_neighbors.sort(key=lambda ref: (result[ref][0], result[ref][1], ref))
        target_ref = signal_neighbors[0]
        target_x_neighbor, target_y, _target_rot = result[target_ref]
        x, _y, rot = result[connector_ref]
        target_x = max(ORIGIN_X, round(target_x_neighbor - _GRID_COL_MM, 2))
        result[connector_ref] = (min(round(x, 2), target_x), round(target_y, 2), rot)

        connector_x, connector_y, _connector_rot = result[connector_ref]
        if math.isclose(connector_x, ORIGIN_X, abs_tol=0.01):
            shifted_x = round(connector_x + _GRID_COL_MM / 2.0, 2)
            for ref, role in role_by_ref.items():
                if ref == connector_ref or ref not in result or _is_connector_ref(ref):
                    continue
                if role not in {BlockRole.INPUT, BlockRole.PRECONDITIONING}:
                    continue
                ref_x, ref_y, ref_rot = result[ref]
                if not math.isclose(ref_x, connector_x, abs_tol=0.01):
                    continue
                if abs(ref_y - connector_y) > 2.0 * GRID_ROW_MM:
                    continue
                result[ref] = (shifted_x, ref_y, ref_rot)

    return result
