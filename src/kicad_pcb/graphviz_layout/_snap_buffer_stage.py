"""Buffer stage shaping: input node, direct output support, feedback corridor, output tail."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import BlockRole
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_basic import _feedback_net_membership, _local_signal_distances
from ._snap_types import (
    GRID_ROW_MM,
    _is_connector_ref,
    _is_ic_ref,
    _multi_unit_base_ref,
)

# ---------------------------------------------------------------------------
# Buffer stage shaping
# ---------------------------------------------------------------------------


def _buffer_stage_input_pair(
    buffer_ref: str,
    input_refs: list[str],
    *,
    ref_to_nets: Mapping[str, set[str]],
    refs_by_net: Mapping[str, set[str]],
) -> tuple[str, str] | None:
    """Return ``(bridge_ref, shunt_ref)`` for a canonical buffer input node."""
    net_anchor_refs = {buffer_ref}
    base_ref = _multi_unit_base_ref(buffer_ref)
    if base_ref is not None:
        net_anchor_refs.add(base_ref)
    candidate_input_refs = {ref for ref in input_refs if ref in ref_to_nets}
    if len(candidate_input_refs) < 2:
        return None

    candidates: list[tuple[int, str, str, str]] = []
    for net_name, net_refs in refs_by_net.items():
        if not (net_anchor_refs & net_refs) or _is_power_net(net_name):
            continue

        shared_input_refs = sorted(candidate_input_refs & net_refs)
        if len(shared_input_refs) != 2:
            continue

        grounded_input_refs = [
            ref
            for ref in shared_input_refs
            if any(
                _is_ground_like_name(other_net)
                for other_net in ref_to_nets.get(ref, set())
                if other_net != net_name
            )
        ]
        if len(grounded_input_refs) == 1:
            shunt_ref = grounded_input_refs[0]
        else:
            terminal_input_refs = [
                ref
                for ref in shared_input_refs
                if not any(
                    not _is_power_net(other_net)
                    for other_net in ref_to_nets.get(ref, set())
                    if other_net != net_name
                )
            ]
            if len(terminal_input_refs) != 1:
                continue
            shunt_ref = terminal_input_refs[0]
        bridge_ref = next(ref for ref in shared_input_refs if ref != shunt_ref)
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


def _place_buffer_stage_input_pair(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    buffer_ref: str,
    bridge_ref: str,
    shunt_ref: str,
) -> tuple[dict[str, tuple[float, float, float | None]], set[str]]:
    """Place a readable follower-stage input node just left of a buffer unit."""
    if buffer_ref not in positions or bridge_ref not in positions or shunt_ref not in positions:
        return dict(positions), set()

    result = dict(positions)
    ic_x, ic_y, _ = result[buffer_ref]
    lane_x = round(ic_x - 0.5 * _GRID_COL_MM, 2)
    bridge_y = round(ic_y, 2)
    shunt_y = round(ic_y + GRID_ROW_MM, 2)

    _bridge_x, _bridge_y, bridge_rot = result[bridge_ref]
    _shunt_x, _shunt_y, shunt_rot = result[shunt_ref]
    result[bridge_ref] = (lane_x, bridge_y, bridge_rot)
    result[shunt_ref] = (lane_x, shunt_y, shunt_rot)
    return result, {bridge_ref, shunt_ref}


def _snap_buffer_stage_input_node_shape(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Shape a canonical follower-stage input node near a ``BUFFER_STAGE`` unit."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}

    def _effective_role(ref: str) -> BlockRole | None:
        direct_role = role_by_ref.get(ref)
        if direct_role is not None:
            return direct_role
        base_ref = _multi_unit_base_ref(ref)
        if base_ref is None:
            return None
        return role_by_ref.get(base_ref)

    result = dict(positions)
    ref_to_nets, refs_by_net = _feedback_net_membership(ir)
    buffer_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.BUFFER_STAGE
    ]
    for buffer_ref in sorted(buffer_refs):
        ic_x, ic_y, _ = result[buffer_ref]
        nearby_refs = [
            ref
            for ref in result
            if not _is_ic_ref(ref)
            and abs(result[ref][0] - ic_x) <= 2.0 * _GRID_COL_MM
            and abs(result[ref][1] - ic_y) <= 4.0 * GRID_ROW_MM
            and _effective_role(ref) in {BlockRole.INTERSTAGE, BlockRole.PRECONDITIONING}
        ]
        input_pair = _buffer_stage_input_pair(
            buffer_ref,
            nearby_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        if input_pair is None:
            continue
        bridge_ref, shunt_ref = input_pair
        result, _placed_input_refs = _place_buffer_stage_input_pair(
            result,
            buffer_ref=buffer_ref,
            bridge_ref=bridge_ref,
            shunt_ref=shunt_ref,
        )

    return result


def _buffer_stage_direct_output_refs(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    buffer_ref: str,
    refs_by_net: Mapping[str, set[str]],
    effective_role: Callable[[str], BlockRole | None],
) -> list[str]:
    """Return same-net direct output-support refs for a placed buffer stage."""
    if buffer_ref not in positions:
        return []

    ic_x, ic_y, _ = positions[buffer_ref]
    net_anchor_refs = {buffer_ref}
    base_ref = _multi_unit_base_ref(buffer_ref)
    if base_ref is not None:
        net_anchor_refs.add(base_ref)

    for net_name, net_refs in refs_by_net.items():
        if _is_power_net(net_name) or not (net_anchor_refs & net_refs):
            continue
        candidates = [
            ref
            for ref in net_refs
            if ref in positions
            and ref not in net_anchor_refs
            and not _is_ic_ref(ref)
            and effective_role(ref) == BlockRole.OUTPUT_CONDITIONING
        ]
        if not candidates:
            continue
        return sorted(
            candidates,
            key=lambda ref: (
                abs(positions[ref][0] - ic_x),
                abs(positions[ref][1] - ic_y),
                ref,
            ),
        )

    return []


def _snap_buffer_stage_direct_output_support(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Keep direct buffer-output support on the buffer row late in the pipeline."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}

    def _effective_role(ref: str) -> BlockRole | None:
        direct_role = role_by_ref.get(ref)
        if direct_role is not None:
            return direct_role
        base_ref = _multi_unit_base_ref(ref)
        if base_ref is None:
            return None
        return role_by_ref.get(base_ref)

    result = dict(positions)
    compact_step = _GRID_COL_MM / 2.0
    _ref_to_nets, refs_by_net = _feedback_net_membership(ir)
    buffer_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.BUFFER_STAGE
    ]
    for buffer_ref in sorted(buffer_refs):
        ic_x, ic_y, _ = result[buffer_ref]
        direct_output_refs = _buffer_stage_direct_output_refs(
            result,
            buffer_ref=buffer_ref,
            refs_by_net=refs_by_net,
            effective_role=_effective_role,
        )

        for idx, ref in enumerate(direct_output_refs):
            _x, _y, rot = result[ref]
            target_x = round(ic_x + (idx + 1) * compact_step, 2)
            result[ref] = (target_x, round(ic_y, 2), rot)

    return result


def _snap_buffer_stage_feedback_corridor(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Keep downstream output parts out of the compact U1B feedback corridor."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    adjacency = _build_signal_adjacency(ir)
    _ref_to_nets, refs_by_net = _feedback_net_membership(ir)

    def _effective_role(ref: str) -> BlockRole | None:
        direct_role = role_by_ref.get(ref)
        if direct_role is not None:
            return direct_role
        base_ref = _multi_unit_base_ref(ref)
        if base_ref is None:
            return None
        return role_by_ref.get(base_ref)

    result = dict(positions)
    compact_step = _GRID_COL_MM / 2.0
    buffer_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.BUFFER_STAGE
    ]
    for buffer_ref in sorted(buffer_refs):
        ic_x, ic_y, _ = result[buffer_ref]
        net_anchor_refs = {buffer_ref}
        base_ref = _multi_unit_base_ref(buffer_ref)
        if base_ref is not None:
            net_anchor_refs.add(base_ref)

        direct_output_refs = _buffer_stage_direct_output_refs(
            result,
            buffer_ref=buffer_ref,
            refs_by_net=refs_by_net,
            effective_role=_effective_role,
        )
        if not direct_output_refs:
            continue

        anchor_ref = direct_output_refs[0]
        anchor_x, _anchor_y, _anchor_rot = result[anchor_ref]
        corridor_candidates = {
            ref
            for ref in result
            if ref not in direct_output_refs
            and ref not in net_anchor_refs
            and not _is_ic_ref(ref)
            and _effective_role(ref) in {BlockRole.OUTPUT_CONDITIONING, BlockRole.OUTPUT}
            and ic_x <= result[ref][0] < anchor_x
            and result[ref][1] <= ic_y
            and result[ref][1] >= round(ic_y - 2.0 * GRID_ROW_MM, 2)
        }
        if not corridor_candidates:
            continue

        local_candidates = set(corridor_candidates)
        local_candidates.add(anchor_ref)
        distances = _local_signal_distances(anchor_ref, local_candidates, adjacency)
        obstructing_refs = [ref for ref in corridor_candidates if ref in distances]
        if not obstructing_refs:
            continue

        obstructing_refs.sort(
            key=lambda ref: (
                distances[ref],
                1 if _effective_role(ref) == BlockRole.OUTPUT and _is_connector_ref(ref) else 0,
                result[ref][0],
                ref,
            )
        )

        tail_y = round(ic_y + GRID_ROW_MM, 2)
        for idx, ref in enumerate(obstructing_refs, start=1):
            _x, _y, rot = result[ref]
            target_x = round(anchor_x + idx * compact_step, 2)
            result[ref] = (target_x, tail_y, rot)

    return result


def _snap_buffer_stage_output_tail_locality(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Compact the downstream U1B output tail below the fixed buffer row."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    adjacency = _build_signal_adjacency(ir)
    _ref_to_nets, refs_by_net = _feedback_net_membership(ir)

    def _effective_role(ref: str) -> BlockRole | None:
        direct_role = role_by_ref.get(ref)
        if direct_role is not None:
            return direct_role
        base_ref = _multi_unit_base_ref(ref)
        if base_ref is None:
            return None
        return role_by_ref.get(base_ref)

    result = dict(positions)
    compact_step = _GRID_COL_MM / 2.0
    buffer_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.BUFFER_STAGE
    ]
    for buffer_ref in sorted(buffer_refs):
        ic_x, ic_y, _ = result[buffer_ref]
        net_anchor_refs = {buffer_ref}
        base_ref = _multi_unit_base_ref(buffer_ref)
        if base_ref is not None:
            net_anchor_refs.add(base_ref)

        direct_output_refs = _buffer_stage_direct_output_refs(
            result,
            buffer_ref=buffer_ref,
            refs_by_net=refs_by_net,
            effective_role=_effective_role,
        )
        if not direct_output_refs:
            continue

        anchor_ref = direct_output_refs[0]
        anchor_x, _anchor_y, _anchor_rot = result[anchor_ref]
        candidate_tail_refs = {
            ref
            for ref in result
            if ref not in direct_output_refs
            and ref not in net_anchor_refs
            and not _is_ic_ref(ref)
            and _effective_role(ref) in {BlockRole.OUTPUT_CONDITIONING, BlockRole.OUTPUT}
            and result[ref][0] >= anchor_x
            and abs(result[ref][1] - ic_y) <= 10.0 * GRID_ROW_MM
        }
        if not candidate_tail_refs:
            continue

        local_candidates = set(candidate_tail_refs)
        local_candidates.add(anchor_ref)
        distances = _local_signal_distances(anchor_ref, local_candidates, adjacency)
        tail_refs = [ref for ref in candidate_tail_refs if ref in distances]
        if not tail_refs:
            continue

        tail_refs.sort(
            key=lambda ref: (
                distances[ref],
                1 if _effective_role(ref) == BlockRole.OUTPUT and _is_connector_ref(ref) else 0,
                result[ref][0],
                ref,
            )
        )

        tail_y = round(ic_y + GRID_ROW_MM, 2)
        for idx, ref in enumerate(tail_refs, start=1):
            _x, _y, rot = result[ref]
            target_x = round(anchor_x + idx * compact_step, 2)
            result[ref] = (target_x, tail_y, rot)

    return result
