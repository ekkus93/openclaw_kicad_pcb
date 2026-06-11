"""Core bridge passives, local shunts, and power block cohesion snap passes."""

from __future__ import annotations

import logging
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
)
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..component_types import power_rail_polarity
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ._snap_basic import _feedback_net_membership
from ._snap_types import (
    _POWER_BLOCK_MAX_X_OFFSET_MM,
    ORIGIN_X,
    PAGE_MAX_X,
    _is_connector_ref,
    _is_ic_ref,
)

_log = logging.getLogger(__name__)


def _snap_core_to_output_bridge_passives(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep a lone passive bridge between a core stage and a downstream output ref."""
    if not positions or block_layout is None:
        return dict(positions)

    from ..block_detection import BlockRole  # noqa: PLC0415
    from ._snap_parse import _snap as _snap_grid  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    result = dict(positions)
    ref_to_nets, refs_by_net = _feedback_net_membership(ir)

    candidate_passives = sorted(
        ref
        for ref in result
        if not _is_ic_ref(ref)
        and not _is_connector_ref(ref)
        and role_by_ref.get(ref) == BlockRole.OUTPUT
    )
    for passive_ref in candidate_passives:
        non_power_nets = [
            net_name
            for net_name in ref_to_nets.get(passive_ref, set())
            if not _is_power_net(net_name)
        ]
        if len(non_power_nets) != 2:
            continue

        upstream_net: str | None = None
        core_ref: str | None = None
        for net_name in non_power_nets:
            net_refs = refs_by_net.get(net_name, set())
            core_refs = sorted(
                ref for ref in net_refs if ref in result and is_core_like_role(role_by_ref.get(ref))
            )
            if len(core_refs) == 1:
                upstream_net = net_name
                core_ref = core_refs[0]
                break
        if upstream_net is None or core_ref is None:
            continue

        downstream_net = next(net_name for net_name in non_power_nets if net_name != upstream_net)
        downstream_candidates = sorted(
            ref
            for ref in refs_by_net.get(downstream_net, set())
            if ref in result and ref != passive_ref
        )
        if not downstream_candidates:
            continue

        core_x, _core_y, _core_rot = result[core_ref]
        downstream_ref = max(downstream_candidates, key=lambda ref: (result[ref][0], ref))
        downstream_x, _downstream_y, _downstream_rot = result[downstream_ref]
        if downstream_x <= core_x + 0.01:
            continue

        passive_x, passive_y, passive_rot = result[passive_ref]
        target_x = _snap_grid((core_x + downstream_x) / 2.0, grid=1.27)
        min_x = round(core_x + (_GRID_COL_MM / 2.0), 2)
        max_x = round(downstream_x - (_GRID_COL_MM / 2.0), 2)
        if max_x <= min_x:
            continue
        target_x = min(max(target_x, min_x), max_x)
        if abs(target_x - passive_x) < 0.01:
            continue
        result[passive_ref] = (round(target_x, 2), passive_y, passive_rot)

    return result


def _snap_core_local_shunts(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep simple core-to-shunt support parts laterally aligned with the core."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    result = dict(positions)
    ref_to_nets, refs_by_net = _feedback_net_membership(ir)

    candidate_passives = sorted(
        ref
        for ref in result
        if not _is_ic_ref(ref)
        and not _is_connector_ref(ref)
        and role_by_ref.get(ref) == BlockRole.OUTPUT
    )
    for passive_ref in candidate_passives:
        passive_nets = [
            net_name
            for net_name in ref_to_nets.get(passive_ref, set())
            if not _is_power_net(net_name)
        ]
        if not any(
            _is_ground_like_name(other_net) or power_rail_polarity(other_net) is not None
            for other_net in ref_to_nets.get(passive_ref, set())
            if other_net not in passive_nets
        ):
            continue

        anchor_core_ref: str | None = None
        for net_name in passive_nets:
            net_refs = refs_by_net.get(net_name, set())
            if any(
                ref in result and ref != passive_ref and _is_connector_ref(ref) for ref in net_refs
            ):
                continue
            core_refs = sorted(
                ref
                for ref in net_refs
                if ref in result and ref != passive_ref and is_core_like_role(role_by_ref.get(ref))
            )
            if len(core_refs) == 1:
                anchor_core_ref = core_refs[0]
                break
        if anchor_core_ref is None:
            continue

        core_x, _core_y, _core_rot = result[anchor_core_ref]
        passive_x, passive_y, passive_rot = result[passive_ref]
        if abs(passive_x - core_x) <= 10.0:
            continue
        result[passive_ref] = (round(core_x, 2), passive_y, passive_rot)

    return result


def _snap_power_block_cohesion(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    decoupling_map: Mapping[str, str] | None = None,
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep POWER_ENTRY refs laterally close to the active signal cluster."""
    if not positions or block_layout is None:
        return dict(positions)

    from ..block_detection import BlockRole, is_power_like_role  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    power_refs = sorted(
        ref
        for ref in positions
        if role_by_ref.get(ref) == BlockRole.POWER_ENTRY and not ref.startswith("#")
    )
    if not power_refs:
        return dict(positions)

    anchor_refs: list[str] = []
    if decoupling_map:
        anchor_refs = sorted({ic_ref for ic_ref in decoupling_map.values() if ic_ref in positions})
    if not anchor_refs:
        anchor_refs = sorted(
            ref
            for ref in positions
            if (role := role_by_ref.get(ref)) is not None and is_core_like_role(role)
        )
    if not anchor_refs:
        anchor_refs = sorted(
            ref
            for ref in positions
            if (role := role_by_ref.get(ref)) is not None
            and not is_power_like_role(role)
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
        )
    if not anchor_refs:
        return dict(positions)

    anchor_x = sum(positions[ref][0] for ref in anchor_refs) / len(anchor_refs)
    anchor_x = round(round(anchor_x / 1.27) * 1.27, 2)

    slot_offsets: list[int] = [0]
    step = 1
    while len(slot_offsets) < len(power_refs):
        slot_offsets.extend([-step, step])
        step += 1
    sorted_offsets = sorted(slot_offsets[: len(power_refs)])
    target_slots = [
        round(
            min(
                max(origin_x, anchor_x + offset * _POWER_BLOCK_MAX_X_OFFSET_MM),
                page_max_x,
            ),
            2,
        )
        for offset in sorted_offsets
    ]

    ordered_power_refs = sorted(power_refs, key=lambda ref: (positions[ref][0], ref))
    result = dict(positions)
    for ref, target_x in zip(ordered_power_refs, target_slots):
        x, y, rot = result[ref]
        if abs(x - target_x) < 0.01:
            continue
        _log.debug(
            "power cohesion: %r x %.2f → %.2f (anchor_x=%.2f)",
            ref,
            x,
            target_x,
            anchor_x,
        )
        result[ref] = (target_x, y, rot)

    return result
