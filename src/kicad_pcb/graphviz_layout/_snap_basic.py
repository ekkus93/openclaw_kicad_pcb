"""Basic connector/IC/power alignment and input/output stage cohesion.

Post-layout snap passes for connector y-alignment, power symbol clamping,
feedback component placement, and input/output stage cohesion.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation as _ComponentAnnotation

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import IC_PREFIXES as _IC_PREFIXES_CT
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..errors import ErrorCode, UserError
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import ComponentAnnotation as _ComponentAnnotation
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_types import (
    _OUTPUT_CONNECTOR_CLEARANCE_MM,
    _POWER_BOTTOM_MARGIN_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _decoupling_family_anchor_x,
    _is_connector_ref,
    _is_ic_ref,
    _multi_unit_base_ref,
    _OpAmpLocalityContext,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Post-layout specialised snap passes
# ---------------------------------------------------------------------------


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

    # Build signal-neighbour lists for each connector.
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
        median_y = nbr_ys[len(nbr_ys) // 2]  # lower-median for determinism
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

    # Build: component → set of direct signal-net neighbours.
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


# ---------------------------------------------------------------------------
# Op-amp locality helpers
# ---------------------------------------------------------------------------


def _local_signal_distances(
    anchor_ref: str,
    candidate_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest signal-hop distance from *anchor_ref* within *candidate_refs*."""
    if not candidate_refs:
        return {}

    distances: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque([(anchor_ref, 0)])
    seen = {anchor_ref}
    while frontier:
        ref, dist = frontier.popleft()
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in candidate_refs:
                continue
            seen.add(nbr)
            distances[nbr] = dist + 1
            frontier.append((nbr, dist + 1))
    return distances


def _is_output_local_loop_role(role: BlockRole | None) -> bool:
    """Return True for roles that belong to the compact output-side local loop."""
    return role in {BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE} or is_output_like_role(role)


def _feedback_net_membership(
    ir: CircuitIR,
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return component/net membership maps used by feedback-node shaping."""
    ref_to_nets: dict[str, set[str]] = defaultdict(set)
    refs_by_net: dict[str, set[str]] = {}
    for net in ir.nets:
        net_refs = {pin.ref for pin in net.pins}
        refs_by_net[net.name] = net_refs
        for ref in net_refs:
            ref_to_nets[ref].add(net.name)
    return ref_to_nets, refs_by_net


def _non_inverting_feedback_pair(
    ic_ref: str,
    feedback_refs: list[str],
    *,
    ref_to_nets: Mapping[str, set[str]],
    refs_by_net: Mapping[str, set[str]],
) -> tuple[str, str] | None:
    """Return ``(bridge_ref, shunt_ref)`` for a canonical two-part gain node."""
    net_anchor_refs = {ic_ref}
    base_ref = _multi_unit_base_ref(ic_ref)
    if base_ref is not None:
        net_anchor_refs.add(base_ref)
    candidate_feedback_refs = {ref for ref in feedback_refs if ref in ref_to_nets}
    if len(candidate_feedback_refs) < 2:
        return None

    candidates: list[tuple[int, str, str, str]] = []
    for net_name, net_refs in refs_by_net.items():
        if not (net_anchor_refs & net_refs) or _is_power_net(net_name):
            continue

        shared_feedback_refs = sorted(candidate_feedback_refs & net_refs)
        if len(shared_feedback_refs) != 2:
            continue

        grounded_feedback_refs = [
            ref
            for ref in shared_feedback_refs
            if any(
                _is_ground_like_name(other_net)
                for other_net in ref_to_nets.get(ref, set())
                if other_net != net_name
            )
        ]
        if len(grounded_feedback_refs) != 1:
            continue

        shunt_ref = grounded_feedback_refs[0]
        bridge_ref = next(ref for ref in shared_feedback_refs if ref != shunt_ref)
        if not any(
            not _is_power_net(other_net)
            for other_net in ref_to_nets.get(bridge_ref, set())
            if other_net != net_name
        ):
            continue

        candidates.append((len(net_refs), net_name, bridge_ref, shunt_ref))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    _member_count, _net_name, bridge_ref, shunt_ref = candidates[0]
    return bridge_ref, shunt_ref


def _place_non_inverting_feedback_pair(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    ic_ref: str,
    bridge_ref: str,
    shunt_ref: str,
    reserved_y: set[float] | frozenset[float] = frozenset(),
) -> tuple[dict[str, tuple[float, float, float | None]], set[str]]:
    """Place a bridge/shunt feedback pair as a readable non-inverting node."""
    if ic_ref not in positions or bridge_ref not in positions or shunt_ref not in positions:
        return dict(positions), set()

    result = dict(positions)
    ic_x, ic_y, _ = result[ic_ref]
    lane_x = round(ic_x - 0.5 * _GRID_COL_MM, 2)
    bridge_y = round(ic_y, 2)
    shunt_y = round(ic_y + GRID_ROW_MM, 2) - 1e-6

    while bridge_y in reserved_y or shunt_y in reserved_y:
        bridge_y = round(bridge_y + GRID_ROW_MM, 2)
        shunt_y = round(shunt_y + GRID_ROW_MM, 2)

    _bridge_x, _bridge_old_y, bridge_rot = result[bridge_ref]
    _shunt_x, _shunt_old_y, shunt_rot = result[shunt_ref]
    result[bridge_ref] = (lane_x, bridge_y, bridge_rot)
    result[shunt_ref] = (lane_x, shunt_y, shunt_rot)
    return result, {bridge_ref, shunt_ref}


def _snap_opamp_locality(  # noqa: PLR0912, PLR0915
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    annotations: Mapping[str, _ComponentAnnotation],
    context: _OpAmpLocalityContext,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply op-amp-centric neighborhood placement refinements."""
    from ..block_detection import BlockRole  # noqa: PLC0415

    if not positions:
        return dict(positions)

    result = dict(positions)
    adjacency = _build_signal_adjacency(ir)
    ref_to_nets, refs_by_net = _feedback_net_membership(ir)

    ic_refs = sorted(ref for ref in result if _is_ic_ref(ref))
    if not ic_refs:
        return result

    role_by_ref: dict[str, BlockRole] = {}
    if context.block_layout is not None:
        role_by_ref = {
            ref: assignment.role for ref, assignment in context.block_layout.assignments.items()
        }

    for ic_ref in ic_refs:
        if ic_ref not in result:
            continue
        base_ref = _multi_unit_base_ref(ic_ref)
        if base_ref is not None and any(
            other_ref != ic_ref and _multi_unit_base_ref(other_ref) == base_ref
            for other_ref in ic_refs
        ):
            continue
        ic_x, ic_y, _ = result[ic_ref]

        signal_neighbors = sorted(r for r in adjacency.get(ic_ref, set()) if r in result)
        local_role_neighbors = {
            ref
            for ref, role in role_by_ref.items()
            if ref in result
            and ref != ic_ref
            and not _is_ic_ref(ref)
            and role is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
            and abs(result[ref][0] - ic_x) <= 2.0 * _GRID_COL_MM
            and abs(result[ref][1] - ic_y) <= 8.0 * GRID_ROW_MM
        }
        candidates = sorted(set(signal_neighbors) | local_role_neighbors)
        input_like: list[str] = []
        handoff_like: list[str] = []
        output_like: list[str] = []
        feedback_like: list[str] = []
        halo_like: list[str] = []
        local_distances = _local_signal_distances(ic_ref, set(candidates), adjacency)

        def _local_order_key(ref: str) -> tuple[int, float, str]:
            return (local_distances.get(ref, 999), result[ref][1], ref)

        for ref in candidates:
            if _is_ic_ref(ref):
                continue
            role = role_by_ref.get(ref)
            is_feedback = bool(annotations.get(ref) and annotations[ref].feedback)
            is_decoupling = context.decoupling_map.get(ref) == ic_ref
            is_halo = context.halo is not None and context.halo.get(ref) == ic_ref

            if is_halo:
                halo_like.append(ref)
                continue

            if is_feedback or role == BlockRole.FEEDBACK:
                feedback_like.append(ref)
                continue
            if is_decoupling or role == BlockRole.DECOUPLING:
                continue
            if role == BlockRole.INTERSTAGE:
                handoff_like.append(ref)
            elif role is not None and is_input_like_role(role):
                if ic_ref in adjacency.get(ref, set()) and any(
                    is_output_like_role(role_by_ref.get(nbr)) for nbr in adjacency.get(ref, set())
                ):
                    handoff_like.append(ref)
                else:
                    input_like.append(ref)
            elif _is_output_local_loop_role(role):
                output_like.append(ref)

        input_like.sort(key=_local_order_key)
        handoff_like.sort(key=_local_order_key)
        output_like.sort(key=_local_order_key)
        feedback_like.sort(key=_local_order_key)
        halo_like.sort(key=_local_order_key)

        target_input_x = round(ic_x - _GRID_COL_MM, 2)
        for idx, ref in enumerate(input_like):
            x, y, rot = result[ref]
            offset = idx - (len(input_like) - 1) / 2
            target_y = round(ic_y - 1.5 * GRID_ROW_MM + offset * GRID_ROW_MM, 2)
            result[ref] = (min(round(x, 2), target_input_x), target_y, rot)

        target_handoff_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(handoff_like):
            x, _y, rot = result[ref]
            offset = idx - (len(handoff_like) - 1) / 2
            target_y = round(ic_y + offset * GRID_ROW_MM, 2)
            result[ref] = (max(round(x, 2), target_handoff_x), target_y, rot)

        target_output_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(output_like):
            x, y, rot = result[ref]
            offset = idx - (len(output_like) - 1) / 2
            target_y = round(ic_y + 1.5 * GRID_ROW_MM + offset * GRID_ROW_MM, 2)
            result[ref] = (max(round(x, 2), target_output_x), target_y, rot)

        dec_refs = sorted(
            ref
            for ref, anchor in context.decoupling_map.items()
            if anchor == ic_ref and ref in result
        )
        reserved_decoupling_y: set[float] = set()
        decoupling_anchor_x = _decoupling_family_anchor_x(ic_ref, result)
        for idx, dec_ref in enumerate(dec_refs):
            _x, _y, dec_rot = result[dec_ref]
            dec_y = round(ic_y - (idx + 1) * GRID_ROW_MM, 2)
            dec_x = decoupling_anchor_x
            if idx >= 2:
                side_step = idx - 1
                side_sign = -1 if idx % 2 == 0 else 1
                dec_x = round(decoupling_anchor_x + side_sign * side_step * _GRID_COL_MM, 2)
            result[dec_ref] = (dec_x, dec_y, dec_rot)
            reserved_decoupling_y.add(dec_y)

        target_halo_left_x = round(ic_x - _GRID_COL_MM, 2)
        target_halo_right_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(halo_like):
            x, _y, rot = result[ref]
            role = role_by_ref.get(ref)
            target_x = target_halo_right_x
            if x < ic_x - 1.0:
                target_x = target_halo_left_x
            elif x > ic_x + 1.0:
                target_x = target_halo_right_x
            elif role != BlockRole.FEEDBACK and not is_output_like_role(role):
                target_x = target_halo_left_x if idx % 2 == 0 else target_halo_right_x

            offset = idx - (len(halo_like) - 1) / 2
            halo_y = round(ic_y + offset * GRID_ROW_MM, 2)
            while halo_y in reserved_decoupling_y:
                halo_y = round(halo_y + GRID_ROW_MM, 2)
            result[ref] = (target_x, halo_y, rot)

        placed_feedback_refs: set[str] = set()
        feedback_pair = _non_inverting_feedback_pair(
            ic_ref,
            feedback_like,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        used_feedback_y = set(reserved_decoupling_y)
        if feedback_pair is not None:
            bridge_ref, shunt_ref = feedback_pair
            result, placed_feedback_refs = _place_non_inverting_feedback_pair(
                result,
                ic_ref=ic_ref,
                bridge_ref=bridge_ref,
                shunt_ref=shunt_ref,
                reserved_y=used_feedback_y,
            )
            used_feedback_y.update(result[ref][1] for ref in placed_feedback_refs)

        remaining_feedback = [ref for ref in feedback_like if ref not in placed_feedback_refs]
        for idx, ref in enumerate(remaining_feedback):
            _x, _y, rot = result[ref]
            level = 1
            fb_y = round(ic_y + level * GRID_ROW_MM, 2)
            while fb_y in used_feedback_y:
                level += 1
                fb_y = round(ic_y + level * GRID_ROW_MM, 2)
            fb_x = round(ic_x, 2)
            if idx >= 2:
                side_step = idx - 1
                side_sign = -1 if idx % 2 == 0 else 1
                fb_x = round(ic_x + side_sign * side_step * _GRID_COL_MM, 2)
            result[ref] = (fb_x, fb_y, rot)
            used_feedback_y.add(fb_y)

    return result


# ---------------------------------------------------------------------------
# Input stage cohesion
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Output stage cohesion
# ---------------------------------------------------------------------------


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
