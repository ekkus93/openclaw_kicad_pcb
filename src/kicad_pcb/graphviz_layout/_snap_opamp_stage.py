"""Op-amp stage input shaping: non-inverting input node and upstream bundle placement."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import BlockRole, is_input_like_role
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ._snap_basic import _feedback_net_membership
from ._snap_composition import _shift_refs_x
from ._snap_types import (
    GRID_ROW_MM,
    ORIGIN_X,
    _is_connector_ref,
    _is_ic_ref,
    _multi_unit_base_ref,
)

# ---------------------------------------------------------------------------
# Op-amp stage input shaping
# ---------------------------------------------------------------------------


def _opamp_stage_input_pair(
    opamp_ref: str,
    input_refs: list[str],
    *,
    ref_to_nets: Mapping[str, set[str]],
    refs_by_net: Mapping[str, set[str]],
) -> tuple[str, str, str] | None:
    """Return ``(bridge_ref, shunt_ref, net_name)`` for an op-amp input node."""
    net_anchor_refs = {opamp_ref}
    base_ref = _multi_unit_base_ref(opamp_ref)
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

        shunt_only_refs = [
            ref
            for ref in shared_input_refs
            if any(
                _is_ground_like_name(other_net)
                for other_net in ref_to_nets.get(ref, set())
                if other_net != net_name
            )
            and not any(
                not _is_power_net(other_net) and not _is_ground_like_name(other_net)
                for other_net in ref_to_nets.get(ref, set())
                if other_net != net_name
            )
        ]
        if len(shunt_only_refs) != 1:
            continue

        shunt_ref = shunt_only_refs[0]
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
    _member_count, net_name, bridge_ref, shunt_ref = candidates[0]
    return bridge_ref, shunt_ref, net_name


def _opamp_stage_upstream_bundle(
    bridge_ref: str,
    input_node_net: str,
    input_refs: list[str],
    *,
    ref_to_nets: Mapping[str, set[str]],
    refs_by_net: Mapping[str, set[str]],
) -> list[str]:
    """Return nearby upstream bridge refs feeding the non-inverting handoff."""
    candidate_upstream_refs = {
        ref for ref in input_refs if ref in ref_to_nets and ref != bridge_ref
    }
    if not candidate_upstream_refs:
        return []

    candidates: list[tuple[int, int, str, list[str]]] = []
    for net_name in ref_to_nets.get(bridge_ref, set()):
        if net_name == input_node_net or _is_power_net(net_name) or _is_ground_like_name(net_name):
            continue

        shared_refs = sorted(candidate_upstream_refs & refs_by_net.get(net_name, set()))
        if not shared_refs:
            continue

        candidates.append(
            (-len(shared_refs), len(refs_by_net.get(net_name, set())), net_name, shared_refs)
        )

    if not candidates:
        return []

    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return candidates[0][3]


def _place_opamp_stage_input_pair(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    opamp_ref: str,
    bridge_ref: str,
    shunt_ref: str,
    allow_global_shift: bool = True,
) -> tuple[dict[str, tuple[float, float, float | None]], set[str]]:
    """Place a readable non-inverting input node just left of an op-amp stage."""
    if opamp_ref not in positions or bridge_ref not in positions or shunt_ref not in positions:
        return dict(positions), set()

    result = dict(positions)
    ic_x, ic_y, _ = result[opamp_ref]
    upstream_lane_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    if upstream_lane_x < ORIGIN_X:
        if not allow_global_shift:
            return result, set()
        delta_x = round(ORIGIN_X - upstream_lane_x, 2)
        refs_to_shift = [ref for ref, (x, _y, _rot) in result.items() if x >= ic_x - _GRID_COL_MM]
        result = _shift_refs_x(result, refs_to_shift, delta_x)
        ic_x, ic_y, _ = result[opamp_ref]
    lane_x = round(ic_x - _GRID_COL_MM, 2)
    bridge_y = round(ic_y, 2)
    shunt_y = round(ic_y + GRID_ROW_MM, 2) - 1e-6

    _bridge_x, _bridge_y, bridge_rot = result[bridge_ref]
    _shunt_x, _shunt_y, shunt_rot = result[shunt_ref]
    result[bridge_ref] = (lane_x, bridge_y, bridge_rot)
    result[shunt_ref] = (lane_x, shunt_y, shunt_rot)
    return result, {bridge_ref, shunt_ref}


def _place_opamp_stage_upstream_bundle(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    opamp_ref: str,
    upstream_refs: list[str],
    reserve_input_connector_margin: bool = False,
) -> tuple[dict[str, tuple[float, float, float | None]], set[str]]:
    """Place upstream bridge parts into one readable column left of the input node."""
    if opamp_ref not in positions:
        return dict(positions), set()

    ordered_refs = [ref for ref in upstream_refs if ref in positions]
    if not ordered_refs:
        return dict(positions), set()

    result = dict(positions)
    ic_x, ic_y, _ = result[opamp_ref]
    lane_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    if reserve_input_connector_margin and lane_x <= ORIGIN_X + 0.01:
        lane_x = round(ORIGIN_X + _GRID_COL_MM / 2.0, 2)
    ordered_refs.sort(key=lambda ref: (result[ref][1], result[ref][0], ref))
    start_y = round(ic_y - (len(ordered_refs) - 1) * GRID_ROW_MM, 2)

    for idx, ref in enumerate(ordered_refs):
        _x, _y, rot = result[ref]
        target_y = round(start_y + idx * GRID_ROW_MM, 2)
        result[ref] = (lane_x, target_y, rot)

    return result, set(ordered_refs)


def _snap_opamp_stage_non_inverting_input_node_shape(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
    allow_global_shift: bool = True,
) -> dict[str, tuple[float, float, float | None]]:
    """Shape a canonical non-inverting input node near an ``OPAMP_CORE`` unit."""
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
    opamp_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.OPAMP_CORE
    ]
    for opamp_ref in sorted(opamp_refs):
        candidate_input_refs = [
            ref
            for ref in result
            if not _is_ic_ref(ref) and is_input_like_role(_effective_role(ref))
        ]
        input_pair = _opamp_stage_input_pair(
            opamp_ref,
            candidate_input_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        if input_pair is None:
            continue
        bridge_ref, shunt_ref, _input_node_net = input_pair
        result, _placed_input_refs = _place_opamp_stage_input_pair(
            result,
            opamp_ref=opamp_ref,
            bridge_ref=bridge_ref,
            shunt_ref=shunt_ref,
            allow_global_shift=allow_global_shift,
        )

    return result


def _snap_opamp_stage_upstream_input_bundle(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Shape the upstream bridge bundle feeding a non-inverting op-amp stage."""
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
    opamp_refs = [
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) == BlockRole.OPAMP_CORE
    ]
    for opamp_ref in sorted(opamp_refs):
        candidate_input_refs = [
            ref
            for ref in result
            if not _is_ic_ref(ref) and is_input_like_role(_effective_role(ref))
        ]
        input_pair = _opamp_stage_input_pair(
            opamp_ref,
            candidate_input_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        if input_pair is None:
            continue

        bridge_ref, _shunt_ref, input_node_net = input_pair
        upstream_refs = _opamp_stage_upstream_bundle(
            bridge_ref,
            input_node_net,
            candidate_input_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        if not upstream_refs:
            continue

        reserve_input_connector_margin = any(
            ref in result and _is_connector_ref(ref) and _effective_role(ref) == BlockRole.INPUT
            for ref in result
        )

        result, _placed_upstream_refs = _place_opamp_stage_upstream_bundle(
            result,
            opamp_ref=opamp_ref,
            upstream_refs=upstream_refs,
            reserve_input_connector_margin=reserve_input_connector_margin,
        )

    return result
