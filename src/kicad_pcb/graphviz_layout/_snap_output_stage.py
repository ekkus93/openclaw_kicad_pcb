"""Output stage cohesion: find members, place lanes, evict intruders, and alignment."""

from __future__ import annotations

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
    is_power_like_role,
)
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_opamp_local import _is_output_local_loop_role
from ._snap_types import (
    _OUTPUT_CONNECTOR_CLEARANCE_MM,
    GRID_ROW_MM,
    _is_connector_ref,
    _is_ic_ref,
)


def _find_output_stage_members(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    adjacency: Mapping[str, set[str]],
    *,
    ic_x: float,
    ic_y: float,
) -> tuple[list[str], list[str], set[str]]:
    """Return ``(output_connectors, output_support, stage_ref_set)`` for Phase 7.2."""
    output_connectors = sorted(
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.OUTPUT and _is_connector_ref(ref)
    )
    if not output_connectors:
        return [], [], set()

    stage_refs: set[str] = {
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.INTERSTAGE
    }
    stage_refs.update(output_connectors)
    for start in output_connectors:
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
                    if _is_output_local_loop_role(role):
                        stage_refs.add(nbr)
                        next_frontier.add(nbr)
            frontier = next_frontier
            if not frontier:
                break

    for ref, role in role_by_ref.items():
        if ref not in positions or _is_ic_ref(ref) or not _is_output_local_loop_role(role):
            continue
        x, y, _ = positions[ref]
        if x >= ic_x and abs(y - ic_y) <= 6.0 * GRID_ROW_MM:
            stage_refs.add(ref)

    output_connectors_sorted = sorted(
        [
            ref
            for ref in stage_refs
            if role_by_ref.get(ref) == BlockRole.OUTPUT and _is_connector_ref(ref)
        ],
        key=lambda ref: positions[ref][1],
    )
    output_support = sorted(
        [ref for ref in stage_refs if _is_output_local_loop_role(role_by_ref.get(ref))],
        key=lambda ref: positions[ref][1],
    )
    output_support = [ref for ref in output_support if ref not in output_connectors_sorted]
    return output_connectors_sorted, output_support, stage_refs


def _output_stage_connector_distances(
    output_connectors: list[str],
    stage_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest connector-hop distance for refs within the output stage."""
    if not output_connectors or not stage_refs:
        return {}

    stage_distance: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque((ref, 0) for ref in output_connectors)
    seen = set(output_connectors)
    while frontier:
        ref, dist = frontier.popleft()
        stage_distance[ref] = dist
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in stage_refs:
                continue
            seen.add(nbr)
            frontier.append((nbr, dist + 1))
    return stage_distance


def _place_output_stage_lane(
    positions: dict[str, tuple[float, float, float | None]],
    output_connectors: list[str],
    output_support: list[str],
    *,
    anchor: tuple[float, float],
    stage_distance: Mapping[str, int] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Place output connector/support refs into right-side columns."""
    ic_x, ic_y = anchor
    result = dict(positions)
    connector_x = round(ic_x + 3.0 * _GRID_COL_MM + _OUTPUT_CONNECTOR_CLEARANCE_MM, 2)
    support_x = round(ic_x + 2.0 * _GRID_COL_MM, 2)
    support_inner_x = round(ic_x + _GRID_COL_MM, 2)

    output_lane_y = ic_y + GRID_ROW_MM

    for idx, ref in enumerate(output_connectors):
        x, _y, rot = result[ref]
        offset = idx - (len(output_connectors) - 1) / 2
        target_y = round(output_lane_y + offset * GRID_ROW_MM, 2)
        result[ref] = (max(round(x, 2), connector_x), target_y, rot)

    for idx, ref in enumerate(output_support):
        x, _y, rot = result[ref]
        offset = idx - (len(output_support) - 1) / 2
        target_y = round(output_lane_y + offset * GRID_ROW_MM, 2)
        target_x = support_x
        if len(output_support) >= 3 and (stage_distance or {}).get(ref, 0) >= 2:
            target_x = support_inner_x
        result[ref] = (max(round(x, 2), target_x), target_y, rot)

    return result


def _align_output_connectors_without_ic(
    positions: dict[str, tuple[float, float, float | None]],
    output_connectors: list[str],
    output_support: list[str],
) -> dict[str, tuple[float, float, float | None]]:
    """Align output connectors to nearby output support when no IC anchor exists."""
    if not output_connectors:
        return dict(positions)

    result = dict(positions)
    connector_lane_x: float | None = None
    if output_support:
        support_rows = [result[ref][1] for ref in output_support]
        support_xs = [result[ref][0] for ref in output_support]
        connector_lane_x = round(max(support_xs) + _GRID_COL_MM, 2)
        if len(support_rows) == len(output_connectors):
            target_rows = support_rows
        else:
            center_y = sum(support_rows) / len(support_rows)
            target_rows = [
                round(center_y + (index - (len(output_connectors) - 1) / 2) * GRID_ROW_MM, 2)
                for index in range(len(output_connectors))
            ]
    else:
        connector_rows = [result[ref][1] for ref in output_connectors]
        center_y = sum(connector_rows) / len(connector_rows)
        target_rows = [
            round(center_y + (index - (len(output_connectors) - 1) / 2) * GRID_ROW_MM, 2)
            for index in range(len(output_connectors))
        ]

    for ref, target_y in zip(output_connectors, target_rows, strict=False):
        x, _y, rot = result[ref]
        target_x = x if connector_lane_x is None else max(round(x, 2), connector_lane_x)
        result[ref] = (round(target_x, 2), target_y, rot)

    return result


def _evict_output_lane_intruders(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    stage_refs: set[str],
    *,
    support_x: float,
    ic_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep unrelated roles out of the output lane."""
    result = dict(positions)
    for ref, role in role_by_ref.items():
        if ref not in result or ref in stage_refs:
            continue
        x, y, rot = result[ref]
        if x <= support_x:
            continue
        if role is not None and (is_input_like_role(role) or is_core_like_role(role)):
            result[ref] = (support_x, y, rot)
        elif role is not None and is_power_like_role(role):
            bottom_y = round(ic_y + 5.0 * GRID_ROW_MM, 2)
            result[ref] = (x, max(y, bottom_y), rot)
    return result


def _snap_output_stage_cohesion(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep the output stage coherent and clearly right-bounded (Phase 7.2)."""
    if not positions or block_layout is None:
        return positions

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    ic_refs = [ref for ref in positions if _is_ic_ref(ref)]
    if not ic_refs:
        output_connectors = sorted(
            ref
            for ref, role in role_by_ref.items()
            if ref in positions and role == BlockRole.OUTPUT and _is_connector_ref(ref)
        )
        output_support = sorted(
            ref
            for ref, role in role_by_ref.items()
            if ref in positions
            and not _is_ic_ref(ref)
            and _is_output_local_loop_role(role)
            and ref not in output_connectors
        )
        return _align_output_connectors_without_ic(positions, output_connectors, output_support)

    anchor_ic = max(ic_refs, key=lambda ref: positions[ref][0])
    ic_x, ic_y, _ = positions[anchor_ic]
    adjacency = _build_signal_adjacency(ir)

    output_connectors, output_support, stage_refs = _find_output_stage_members(
        positions,
        role_by_ref,
        adjacency,
        ic_x=ic_x,
        ic_y=ic_y,
    )
    if not output_connectors and not output_support:
        return positions

    result = _place_output_stage_lane(
        positions,
        output_connectors,
        output_support,
        anchor=(ic_x, ic_y),
        stage_distance=_output_stage_connector_distances(output_connectors, stage_refs, adjacency),
    )
    support_x = round(ic_x + 2.0 * _GRID_COL_MM, 2)
    return _evict_output_lane_intruders(
        result,
        role_by_ref,
        stage_refs,
        support_x=support_x,
        ic_y=ic_y,
    )
