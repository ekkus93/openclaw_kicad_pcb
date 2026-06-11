"""Op-amp composition helpers: central composition, major signal axis, feedback alignment."""

from __future__ import annotations

import logging
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
)
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import ComponentAnnotation as _ComponentAnnotation
from ._snap_basic import (
    _feedback_net_membership,
    _non_inverting_feedback_pair,
    _place_non_inverting_feedback_pair,
)
from ._snap_types import (
    _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
    _MIN_CIRCUIT_SPAN_FRACTION,
    _OPAMP_LOWER_LIMIT_FRACTION,
    _OPAMP_UPPER_LIMIT_FRACTION,
    _TITLE_BLOCK_CLEARANCE_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _is_connector_ref,
    _is_ic_ref,
    _multi_unit_base_ref,
)

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# General position shift helpers (shared by spacing/stage sub-modules)
# ---------------------------------------------------------------------------


def _shift_refs_y(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    delta_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Return a copy of *positions* with the selected refs shifted by *delta_y*."""
    result = dict(positions)
    for ref in refs:
        x, y, rot = result[ref]
        result[ref] = (x, round(y + delta_y, 4), rot)
    return result


def _shift_creates_y_overlap(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    delta_y: float,
) -> bool:
    """Return True when shifting *refs* by *delta_y* collapses distinct y-values."""
    current_ys = [round(positions[ref][1], 2) for ref in refs]
    shifted_ys = [round(positions[ref][1] + delta_y, 2) for ref in refs]
    return len(set(shifted_ys)) < len(set(current_ys))


def _align_refs_to_axis(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    *,
    axis_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Return a copy of *positions* with the given refs moved onto *axis_y*."""
    result = dict(positions)
    for ref in refs:
        x, y, rot = result[ref]
        if abs(y - axis_y) < 0.01:
            continue
        result[ref] = (x, axis_y, rot)
    return result


def _shift_refs_x(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    delta_x: float,
    *,
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Return a copy of *positions* with the selected refs shifted by *delta_x*."""
    if not refs or abs(delta_x) < 0.01:
        return dict(positions)

    min_x = min(positions[ref][0] for ref in refs)
    max_x = max(positions[ref][0] for ref in refs)
    bounded_delta = min(max(delta_x, origin_x - min_x), page_max_x - max_x)
    bounded_delta = round(round(bounded_delta / 1.27) * 1.27, 2)
    if abs(bounded_delta) < 0.01:
        return dict(positions)

    result = dict(positions)
    for ref in refs:
        x, y, rot = result[ref]
        result[ref] = (round(x + bounded_delta, 2), y, rot)
    return result


# ---------------------------------------------------------------------------
# Composition helpers (Phase 8.2)
# ---------------------------------------------------------------------------


def _central_composition_refs(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None,
) -> tuple[list[str], list[str]]:
    """Return the signal-path refs and op-amp refs used by Phase 8.2 checks."""
    if block_layout is None:
        signal_refs = [ref for ref in positions if not ref.startswith("#")]
        return signal_refs, []

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    signal_refs = [
        ref
        for ref in positions
        if (
            (role := role_by_ref.get(ref)) is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
            and not ref.startswith("#")
        )
    ]
    opamp_refs = [ref for ref in signal_refs if role_by_ref.get(ref) == BlockRole.OPAMP_CORE]
    return signal_refs, opamp_refs


def _apply_opamp_vertical_nudge(
    positions: Mapping[str, tuple[float, float, float | None]],
    signal_refs: list[str],
    opamp_refs: list[str],
    page_bounds: tuple[float, float],
) -> dict[str, tuple[float, float, float | None]]:
    """Nudge signal-path refs when the op-amp stage is too high or too low."""
    if not opamp_refs:
        return dict(positions)

    origin_y, page_max_y = page_bounds
    page_height = page_max_y - origin_y
    lower_limit_y = origin_y + _OPAMP_LOWER_LIMIT_FRACTION * page_height
    upper_limit_y = origin_y + _OPAMP_UPPER_LIMIT_FRACTION * page_height
    page_center_y = (origin_y + page_max_y) / 2.0
    opamp_avg_y = sum(positions[ref][1] for ref in opamp_refs) / len(opamp_refs)

    shift = 0.0
    limit_y = 0.0
    direction = ""
    if opamp_avg_y > lower_limit_y:
        shift = -round(round((opamp_avg_y - page_center_y) / 1.27) * 1.27, 4)
        limit_y = lower_limit_y
        direction = "low"
    elif opamp_avg_y < upper_limit_y:
        shift = round(round((page_center_y - opamp_avg_y) / 1.27) * 1.27, 4)
        limit_y = upper_limit_y
        direction = "high"

    if shift == 0.0:
        return dict(positions)

    if _shift_creates_y_overlap(positions, signal_refs, shift):
        _log.debug(
            "central composition: op-amp-%s nudge %.2f mm would create overlaps — skipping",
            direction,
            abs(shift),
        )
        return dict(positions)

    _log.debug(
        "central composition: op-amp too %s (avg_y=%.2f limit=%.2f); nudging %s %.2f mm",
        direction,
        opamp_avg_y,
        limit_y,
        "up" if shift < 0 else "down",
        abs(shift),
    )
    return _shift_refs_y(positions, signal_refs, shift)


def _snap_central_composition(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Phase 8.2: enforce sensible vertical composition."""
    import math  # noqa: PLC0415

    if not positions:
        return dict(positions)

    _GRID_SNAP = 1.27
    signal_refs, opamp_refs = _central_composition_refs(positions, block_layout)

    if not signal_refs:
        return dict(positions)

    result = dict(positions)

    safe_max_y = page_max_y - _TITLE_BLOCK_CLEARANCE_MM
    lowest_signal_y = max(result[ref][1] for ref in signal_refs)
    if lowest_signal_y > safe_max_y:
        raw_push = lowest_signal_y - safe_max_y
        push_up = round(math.ceil(raw_push / _GRID_SNAP) * _GRID_SNAP, 4)
        _log.debug(
            "central composition: title-block encroachment"
            " (lowest=%.2f safe=%.2f); shifting all signal refs up %.2f mm",
            lowest_signal_y,
            safe_max_y,
            push_up,
        )
        result = _shift_refs_y(result, signal_refs, -push_up)

    result = _apply_opamp_vertical_nudge(
        result,
        signal_refs,
        opamp_refs,
        (origin_y, page_max_y),
    )

    span_ys = [result[ref][1] for ref in signal_refs]
    circuit_span = max(span_ys) - min(span_ys)
    available_height = page_max_y - origin_y
    if circuit_span < _MIN_CIRCUIT_SPAN_FRACTION * available_height:
        _log.debug(
            "central composition: small vertical span %.2f mm"
            " (%.0f%% of %.0f mm available);"
            " consider spreading components vertically",
            circuit_span,
            100.0 * circuit_span / available_height,
            available_height,
        )

    return result


# ---------------------------------------------------------------------------
# Major signal axis (Phase 8.4)
# ---------------------------------------------------------------------------


def _major_signal_axis_refs(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> tuple[list[str], list[str], list[str]]:
    """Return representative input/core/output refs for the main signal axis."""
    input_refs: list[str] = []
    core_refs: list[str] = []
    output_refs: list[str] = []
    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}

    for ref in sorted(positions):
        if ref.startswith("#"):
            continue
        role = role_by_ref.get(ref)
        if role in {BlockRole.INPUT, BlockRole.PRECONDITIONING}:
            input_refs.append(ref)
        elif role in {BlockRole.OPAMP_CORE, BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}:
            core_refs.append(ref)
        elif role in {BlockRole.OUTPUT, BlockRole.OUTPUT_CONDITIONING}:
            output_refs.append(ref)

    input_reps: list[str] = []
    input_connectors = [ref for ref in input_refs if _is_connector_ref(ref)]
    if input_connectors:
        input_reps = [min(input_connectors, key=lambda ref: (positions[ref][0], ref))]
    elif input_refs:
        input_reps = [max(input_refs, key=lambda ref: (positions[ref][0], ref))]

    preferred_core_refs = [
        ref
        for ref in core_refs
        if block_layout.assignments[ref].role in {BlockRole.OPAMP_CORE, BlockRole.BUFFER_STAGE}
    ]
    core_reps = sorted(preferred_core_refs or core_refs)

    output_reps: list[str] = []
    output_connectors = [ref for ref in output_refs if _is_connector_ref(ref)]
    if output_connectors:
        output_reps = [max(output_connectors, key=lambda ref: (positions[ref][0], ref))]
    elif output_refs:
        output_reps = [min(output_refs, key=lambda ref: (positions[ref][0], ref))]

    return input_reps, core_reps, output_reps


def _snap_major_signal_axis(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Align the major input/core/output path refs onto one shared horizontal axis."""
    if not positions or block_layout is None:
        return dict(positions)

    input_refs, core_refs, output_refs = _major_signal_axis_refs(positions, block_layout)
    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    opamp_anchor_refs = sorted(
        ref
        for ref in positions
        if not ref.startswith("#") and role_by_ref.get(ref) == BlockRole.OPAMP_CORE
    )
    core_chain_refs = sorted(
        ref
        for ref in positions
        if not ref.startswith("#")
        and role_by_ref.get(ref)
        in {
            BlockRole.OPAMP_CORE,
            BlockRole.INTERSTAGE,
            BlockRole.BUFFER_STAGE,
        }
    )
    stage_groups = [group for group in (input_refs, core_refs, output_refs) if group]
    if len(stage_groups) < 2:
        return dict(positions)

    if core_refs:
        has_transition_chain = any(
            role_by_ref.get(ref) in {BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}
            for ref in core_chain_refs
        )
        if opamp_anchor_refs and has_transition_chain:
            axis_seed = sum(positions[ref][1] for ref in opamp_anchor_refs) / len(opamp_anchor_refs)
            major_refs = core_chain_refs
        else:
            axis_seed = sum(positions[ref][1] for ref in core_refs) / len(core_refs)
            major_refs = sorted({ref for group in (input_refs, output_refs) for ref in group})
            major_refs = sorted(set(major_refs) | set(core_chain_refs))
    else:
        connector_refs = [ref for ref in (*input_refs, *output_refs) if _is_connector_ref(ref)]
        if connector_refs:
            axis_seed = sum(positions[ref][1] for ref in connector_refs) / len(connector_refs)
        else:
            group_centres = [
                sum(positions[ref][1] for ref in group) / len(group) for group in stage_groups
            ]
            axis_seed = sum(group_centres) / len(group_centres)
        major_refs = sorted({ref for group in stage_groups for ref in group})

    if not major_refs:
        return dict(positions)

    axis_y = round(
        round(axis_seed / _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM)
        * _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
        2,
    )
    result = _align_refs_to_axis(positions, major_refs, axis_y=axis_y)

    _log.debug(
        "major signal axis: aligned %d refs to y=%.2f (core=%d input=%d output=%d)",
        len(major_refs),
        axis_y,
        len(core_refs),
        len(input_refs),
        len(output_refs),
    )
    return result


def _snap_feedback_clusters_to_shifted_cores(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    annotations: Mapping[str, _ComponentAnnotation],
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Keep feedback parts vertically local after the core chain shifts."""
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
    ic_refs = sorted(
        ref
        for ref in result
        if _is_ic_ref(ref)
        and ref not in power_unit_refs
        and _effective_role(ref) in {BlockRole.OPAMP_CORE, BlockRole.BUFFER_STAGE}
    )
    if not ic_refs:
        return result

    for ic_ref in ic_refs:
        ic_x, ic_y, _ = result[ic_ref]
        feedback_refs = sorted(
            ref
            for ref in result
            if not _is_ic_ref(ref)
            and (
                bool(annotations.get(ref) and annotations[ref].feedback)
                or role_by_ref.get(ref) == BlockRole.FEEDBACK
            )
            and abs(result[ref][0] - ic_x) <= 3.0 * _GRID_COL_MM
        )

        placed_feedback_refs: set[str] = set()
        feedback_pair = _non_inverting_feedback_pair(
            ic_ref,
            feedback_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        used_feedback_y: set[float] = set()
        if feedback_pair is not None:
            bridge_ref, shunt_ref = feedback_pair
            result, placed_feedback_refs = _place_non_inverting_feedback_pair(
                result,
                ic_ref=ic_ref,
                bridge_ref=bridge_ref,
                shunt_ref=shunt_ref,
            )
            used_feedback_y.update(result[ref][1] for ref in placed_feedback_refs)

        remaining_feedback = [ref for ref in feedback_refs if ref not in placed_feedback_refs]
        for idx, ref in enumerate(remaining_feedback):
            x, _y, rot = result[ref]
            level = 1
            target_y = round(ic_y + level * GRID_ROW_MM, 2)
            while target_y in used_feedback_y:
                level += 1
                target_y = round(ic_y + level * GRID_ROW_MM, 2)
            result[ref] = (x, target_y, rot)
            used_feedback_y.add(target_y)

    return result


def _snap_explicit_non_inverting_feedback_nodes(
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    annotations: Mapping[str, _ComponentAnnotation],
    *,
    block_layout: BlockLayout | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Finalize readable bridge/shunt feedback-node shapes near placed op-amp units."""
    if not positions:
        return dict(positions)

    role_by_ref: dict[str, BlockRole] = {}
    if block_layout is not None:
        role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}

    def _effective_role(ref: str) -> BlockRole | None:
        direct_role = role_by_ref.get(ref)
        if direct_role is not None:
            return direct_role
        base_ref = _multi_unit_base_ref(ref)
        if base_ref is None:
            return None
        return role_by_ref.get(base_ref)

    def _is_feedback_ref(ref: str) -> bool:
        return bool(annotations.get(ref) and annotations[ref].feedback) or (
            _effective_role(ref) == BlockRole.FEEDBACK
        )

    result = dict(positions)
    ref_to_nets, refs_by_net = _feedback_net_membership(ir)
    for ic_ref in sorted(ref for ref in result if _is_ic_ref(ref) and ref not in power_unit_refs):
        ic_x, ic_y, _ = result[ic_ref]
        role_feedback_refs = [
            ref for ref in result if not _is_ic_ref(ref) and _is_feedback_ref(ref)
        ]
        nearby_refs = [
            ref
            for ref in result
            if not _is_ic_ref(ref)
            and abs(result[ref][0] - ic_x) <= 1.25 * _GRID_COL_MM
            and abs(result[ref][1] - ic_y) <= 4.0 * GRID_ROW_MM
        ]
        candidate_feedback_refs = sorted(set(role_feedback_refs) | set(nearby_refs))
        if not any(_is_feedback_ref(ref) for ref in candidate_feedback_refs):
            continue
        feedback_pair = _non_inverting_feedback_pair(
            ic_ref,
            candidate_feedback_refs,
            ref_to_nets=ref_to_nets,
            refs_by_net=refs_by_net,
        )
        if feedback_pair is None:
            continue
        bridge_ref, shunt_ref = feedback_pair
        result, _placed_feedback_refs = _place_non_inverting_feedback_pair(
            result,
            ic_ref=ic_ref,
            bridge_ref=bridge_ref,
            shunt_ref=shunt_ref,
        )

    return result
