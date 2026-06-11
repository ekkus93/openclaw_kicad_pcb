"""Op-amp locality, composition, stage input/output shaping, and block spacing.

Contains Phase 8 composition passes (central composition, major signal axis,
feedback cluster alignment, block spacing) and buffer/opamp stage shaping.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
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
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..component_types import power_rail_polarity
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import ComponentAnnotation as _ComponentAnnotation
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ._snap_basic import (
    _feedback_net_membership,
    _local_signal_distances,
    _non_inverting_feedback_pair,
    _place_non_inverting_feedback_pair,
)
from ._snap_types import (
    _MAJOR_BLOCK_MAX_GAP_MM,
    _MAJOR_BLOCK_MIN_GAP_MM,
    _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
    _MIN_CIRCUIT_SPAN_FRACTION,
    _OPAMP_LOWER_LIMIT_FRACTION,
    _OPAMP_UPPER_LIMIT_FRACTION,
    _POWER_BLOCK_MAX_X_OFFSET_MM,
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
# Composition helpers (Phase 8.2)
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
