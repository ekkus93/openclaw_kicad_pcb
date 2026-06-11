"""Major block spacing and transition subbands snap passes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
)
from ._snap_composition import _shift_refs_x
from ._snap_types import (
    _MAJOR_BLOCK_MAX_GAP_MM,
    _MAJOR_BLOCK_MIN_GAP_MM,
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
