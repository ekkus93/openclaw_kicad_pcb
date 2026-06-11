"""Op-amp locality helpers: signal distances, feedback pairs, and placement refinements."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation as _ComponentAnnotation

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
)
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_types import (
    GRID_ROW_MM,
    _decoupling_family_anchor_x,
    _is_ic_ref,
    _multi_unit_base_ref,
    _OpAmpLocalityContext,
)


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
