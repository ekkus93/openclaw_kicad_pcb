"""Block spacing and transition subbands: major block spacing, interstage, passives, power."""

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
from ._snap_composition import _shift_refs_x
from ._snap_types import (
    _MAJOR_BLOCK_MAX_GAP_MM,
    _MAJOR_BLOCK_MIN_GAP_MM,
    _POWER_BLOCK_MAX_X_OFFSET_MM,
    ORIGIN_X,
    PAGE_MAX_X,
    _is_connector_ref,
    _is_ic_ref,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Major block spacing (Phase 8.5)
# ---------------------------------------------------------------------------


def _major_block_spacing_groups(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> list[list[str]]:
    """Return ordered major block groups used by the block-spacing pass."""
    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    input_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and is_input_like_role(role_by_ref.get(ref))
    )
    core_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#")
        and role_by_ref.get(ref)
        in {BlockRole.OPAMP_CORE, BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}
    )
    output_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and is_output_like_role(role_by_ref.get(ref))
    )

    return [group for group in (input_block, core_block, output_block) if group]


def _snap_core_anchored_major_block_spacing(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
    *,
    min_gap_mm: float,
    max_gap_mm: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Normalize input/core/output spacing while keeping the core fixed."""
    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    input_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and is_input_like_role(role_by_ref.get(ref))
    )
    core_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and is_core_like_role(role_by_ref.get(ref))
    )
    output_block = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and is_output_like_role(role_by_ref.get(ref))
    )
    if not core_block:
        return dict(positions)

    result = dict(positions)
    core_min_x = min(result[ref][0] for ref in core_block)
    core_max_x = max(result[ref][0] for ref in core_block)

    if input_block:
        input_max_x = max(result[ref][0] for ref in input_block)
        gap = round(core_min_x - input_max_x, 2)
        delta_x = 0.0
        if gap > max_gap_mm:
            delta_x = gap - max_gap_mm
        elif gap < min_gap_mm:
            delta_x = gap - min_gap_mm
        if abs(delta_x) >= 0.01:
            result = _shift_refs_x(result, input_block, delta_x)

    if output_block:
        output_min_x = min(result[ref][0] for ref in output_block)
        gap = round(output_min_x - core_max_x, 2)
        delta_x = 0.0
        if gap > max_gap_mm:
            delta_x = max_gap_mm - gap
        elif gap < min_gap_mm:
            delta_x = min_gap_mm - gap
        if abs(delta_x) >= 0.01:
            result = _shift_refs_x(result, output_block, delta_x)

    return result


def _snap_major_block_spacing(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    min_gap_mm: float = _MAJOR_BLOCK_MIN_GAP_MM,
    max_gap_mm: float = _MAJOR_BLOCK_MAX_GAP_MM,
) -> dict[str, tuple[float, float, float | None]]:
    """Normalize adjacent major-block x-gaps without disturbing internal geometry."""
    if not positions or block_layout is None:
        return dict(positions)

    groups = _major_block_spacing_groups(positions, block_layout)
    if len(groups) < 2:
        return dict(positions)
    has_core_group = any(
        role in {BlockRole.OPAMP_CORE, BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}
        for role in (assignment.role for assignment in block_layout.assignments.values())
    )

    if has_core_group:
        return _snap_core_anchored_major_block_spacing(
            positions,
            block_layout,
            min_gap_mm=min_gap_mm,
            max_gap_mm=max_gap_mm,
        )

    result = dict(positions)
    for index, left_group in enumerate(groups[:-1]):
        right_groups = groups[index + 1 :]
        right_group = right_groups[0]
        left_max_x = max(result[ref][0] for ref in left_group)
        right_min_x = min(result[ref][0] for ref in right_group)
        gap = round(right_min_x - left_max_x, 2)

        delta_x = 0.0
        if gap > max_gap_mm:
            delta_x = max_gap_mm - gap
        elif gap < min_gap_mm:
            delta_x = min_gap_mm - gap
        if abs(delta_x) < 0.01:
            continue

        refs_to_shift = sorted({ref for group in right_groups for ref in group})
        result = _shift_refs_x(
            result,
            refs_to_shift,
            delta_x,
        )

        new_right_min_x = min(result[ref][0] for ref in right_group)
        _log.debug(
            "major block spacing: adjusted gap %.2f → %.2f between groups %d and %d",
            gap,
            round(new_right_min_x - left_max_x, 2),
            index,
            index + 1,
        )

    return result


# ---------------------------------------------------------------------------
# Transition subbands and interstage handoff
# ---------------------------------------------------------------------------


def _transition_subband_groups(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> list[list[str]]:
    """Return ordered refs for the core-to-output transition sub-bands."""
    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    core_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.OPAMP_CORE
    )
    interstage_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.INTERSTAGE
    )
    buffer_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.BUFFER_STAGE
    )
    output_conditioning_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.OUTPUT_CONDITIONING
    )
    output_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.OUTPUT
    )

    return [
        group
        for group in (
            core_group,
            interstage_group,
            buffer_group,
            output_conditioning_group,
            output_group,
        )
        if group
    ]


def _snap_output_transition_subbands(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    min_gap_mm: float = _MAJOR_BLOCK_MIN_GAP_MM,
    max_gap_mm: float = _MAJOR_BLOCK_MAX_GAP_MM,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep downstream transition roles in distinct ordered x-bands."""
    if not positions or block_layout is None:
        return dict(positions)

    groups = _transition_subband_groups(positions, block_layout)
    if len(groups) < 3 or groups[0] == groups[-1]:
        return dict(positions)

    current_gaps = [
        round(
            min(positions[right][0] for right in right_group)
            - max(positions[left][0] for left in left_group),
            2,
        )
        for left_group, right_group in zip(groups, groups[1:], strict=False)
    ]
    if all(min_gap_mm - 0.01 <= gap <= max_gap_mm + 0.01 for gap in current_gaps):
        return dict(positions)

    group_widths = [
        round(max(positions[ref][0] for ref in group) - min(positions[ref][0] for ref in group), 2)
        for group in groups
    ]
    first_group_end = max(positions[ref][0] for ref in groups[0])
    last_group_start = min(positions[ref][0] for ref in groups[-1])
    gap_count = len(groups) - 1
    intermediate_width = sum(group_widths[1:-1])
    natural_gap = 0.0
    if gap_count > 0:
        natural_gap = round(
            (last_group_start - first_group_end - intermediate_width) / gap_count,
            2,
        )
    target_gap = min(max_gap_mm, max(0.0, natural_gap))

    result = dict(positions)
    previous_group_end = max(result[ref][0] for ref in groups[0])
    for index, group in enumerate(groups[1:], start=1):
        current_group_start = min(result[ref][0] for ref in group)
        target_group_start = round(previous_group_end + target_gap, 2)
        delta_x = round(target_group_start - current_group_start, 2)
        if abs(delta_x) >= 0.01:
            result = _shift_refs_x(result, group, delta_x)
        previous_group_end = max(result[ref][0] for ref in group)

    return result


def _snap_interstage_handoff_between_stages(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep interstage coupling parts between the gain stage and buffer stage."""
    if not positions or block_layout is None:
        return dict(positions)

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    core_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.OPAMP_CORE
    )
    interstage_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.INTERSTAGE
    )
    buffer_group = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.BUFFER_STAGE
    )
    if not core_group or not interstage_group or not buffer_group:
        return dict(positions)

    core_end = max(positions[ref][0] for ref in core_group)
    buffer_start = min(positions[ref][0] for ref in buffer_group)
    interstage_start = min(positions[ref][0] for ref in interstage_group)
    interstage_end = max(positions[ref][0] for ref in interstage_group)
    if core_end - 0.01 <= interstage_start and interstage_end <= buffer_start + 0.01:
        return dict(positions)

    interstage_width = round(interstage_end - interstage_start, 2)
    left_bound = core_end
    right_bound = buffer_start - interstage_width
    if right_bound < left_bound:
        target_start = left_bound
    else:
        target_start = left_bound + (right_bound - left_bound) / 2.0
    target_start = round(round(target_start / 1.27) * 1.27, 2)
    delta_x = round(target_start - interstage_start, 2)
    if abs(delta_x) < 0.01:
        return dict(positions)

    return _shift_refs_x(positions, interstage_group, delta_x)


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
