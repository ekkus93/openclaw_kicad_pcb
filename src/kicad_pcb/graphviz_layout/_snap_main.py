"""Composite snap coordinator — the ``_apply_post_layout_snaps`` entry point.

All individual snap passes are imported from the focused sub-modules and
called in the canonical ordering documented in the function docstring.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation as _ComponentAnnotation

from ..block_detection import is_core_like_role
from ._snap_basic import (
    _snap_connectors_to_ic_y,
    _snap_feedback_components,
    _snap_input_connector_signal_attachment,
    _snap_opamp_halo,
    _snap_power_symbols,
)
from ._snap_geometry import (
    _apply_density_spreading,
    _apply_property_text_spacing,
    _apply_stereo_split,
    _center_ics_in_columns,
    _clamp_to_page,
    _compact_y_gap,
    _deoverlap_positions,
    _enforce_connector_x_bounds,
    _post_stereo_barycentric,
    _remediate_crossings,
    _snap_block_zones,
    _snap_multi_unit_sibling_cohesion,
    _snap_page_balance,
    _spread_x_columns,
)
from ._snap_opamp import (
    _snap_buffer_stage_direct_output_support,
    _snap_buffer_stage_feedback_corridor,
    _snap_buffer_stage_input_node_shape,
    _snap_buffer_stage_output_tail_locality,
    _snap_central_composition,
    _snap_core_local_shunts,
    _snap_core_to_output_bridge_passives,
    _snap_explicit_non_inverting_feedback_nodes,
    _snap_feedback_clusters_to_shifted_cores,
    _snap_interstage_handoff_between_stages,
    _snap_major_block_spacing,
    _snap_major_signal_axis,
    _snap_opamp_stage_non_inverting_input_node_shape,
    _snap_opamp_stage_upstream_input_bundle,
    _snap_output_transition_subbands,
    _snap_power_block_cohesion,
)
from ._snap_parse import snap_positions
from ._snap_types import (
    _STEREO_DEOVERLAP_MIN_MM,
    DEFAULT_LAYOUT_HEURISTIC_POLICY,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    LayoutHeuristicPolicy,
    _OpAmpLocalityContext,
)


def _apply_post_layout_snaps(  # noqa: PLR0913, PLR0915
    result: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    feedback_refs: set[str],
    annotations: dict[str, _ComponentAnnotation],
    channels: Mapping[str, str],
    decoupling_map: dict[str, str],
    roles: Mapping[str, str] | None = None,
    halo: Mapping[str, str] | None = None,
    power_unit_refs: frozenset[str] = frozenset(),
    unit_sibling_pairs: tuple[tuple[str, str], ...] = (),
    block_layout: BlockLayout | None = None,
    heuristic_policy: LayoutHeuristicPolicy = DEFAULT_LAYOUT_HEURISTIC_POLICY,
    strict: bool = False,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply all post-layout positional corrections in canonical order.

    The passes must run in the order shown — see the module docstring for the
    ordering rationale:

    1. :func:`snap_positions` — quantise to the KiCad 50-mil grid.
    2. :func:`_snap_power_symbols` — clamp ``#PWR``/``#FLG`` to top/bottom row.
    2b. :func:`_enforce_connector_x_bounds` — clamp input/output connectors
        to the left/right 25 % of the page (skipped when *roles* is ``None``).
    2c. :func:`_snap_connectors_to_ic_y` — pull each connector's y to the
        median y of its signal-net neighbours (Rule 2).
    3. :func:`_snap_feedback_components` — pull feedback passives above anchor
       IC (skipped when *feedback_refs* is empty).
    3b. :func:`_snap_opamp_halo` — normalize halo members into adjacent lanes
        near their anchor IC (skipped when *halo* is ``None``).
    3c. :func:`_snap_block_zones` — bias components toward their functional
        block zones (INPUT left, OUTPUT right, POWER top; skipped when
        *block_layout* is ``None``).
    3d. :func:`_apply_density_spreading` — push apart symbols in dense
        clusters (skipped when *block_layout* is ``None``).
    4. :func:`_apply_stereo_split` — compress L/R components into page halves
       (skipped when no L or R channel is present in *channels*).
    4b. :func:`_post_stereo_barycentric` — reduce intra-channel crossings after
       the stereo split (skipped when no L/R channels present).
    5. :func:`_compact_y_gap` — collapse the largest vertical gap.
    6. :func:`_center_ics_in_columns` — re-sort each column so ICs land at the
       vertical midpoint with passives above and below.
    7. :func:`_post_snap_decoupling_caps` — co-locate bypass caps above their IC.
    7b. :func:`_snap_opamp_locality` — enforce op-amp-centric local staging.
    7c. :func:`_snap_input_stage_cohesion` — keep input connector and
        preconditioning parts as a compact left-side stage.
    7d. :func:`_snap_output_stage_cohesion` — keep output connector and
        feedback parts as a compact right-side stage.
    7e. :func:`_snap_page_balance` — nudge signal-path components toward the
        vertical page centre.
    7f. Re-apply decoupling snap after page balance shift.
    7g. :func:`_snap_central_composition` — enforce sensible vertical composition.
    7h. :func:`_snap_major_signal_axis` — align input/core/output refs onto a
        shared horizontal axis.
    7i. :func:`_snap_major_block_spacing` — normalize adjacent major-block gaps.
    7j. :func:`_snap_output_transition_subbands` — keep downstream transitions
        in readable sub-bands.
    7k. :func:`_snap_power_block_cohesion` — keep POWER_ENTRY refs laterally tied
        to the active signal cluster.
    7l. :func:`_snap_multi_unit_sibling_cohesion` — compact split-unit IC siblings.
    7m. :func:`_snap_interstage_handoff_between_stages` — keep INTERSTAGE refs
        between the gain stage and any buffer stage.
    7n. :func:`_apply_property_text_spacing` — reserve vertical space for
        visible Reference/Value text.
    8. :func:`_spread_x_columns` — split overloaded x-columns.
    9. :func:`_deoverlap_positions` — push any remaining grid collisions apart.
    10. :func:`_remediate_crossings` — fix crossing ratio ≥ 0.30.
    11. :func:`_clamp_to_page` — clamp every position to the A4 printable area.
    """
    from ..block_detection import BlockRole  # noqa: PLC0415

    result = snap_positions(result)
    result = _snap_power_symbols(result, ir)
    if roles:
        result = _enforce_connector_x_bounds(result, roles)
    result = _snap_connectors_to_ic_y(result, ir)
    if feedback_refs:
        result = _snap_feedback_components(result, annotations, ir, strict=strict)
    if halo:
        result = _snap_opamp_halo(result, halo)
    if block_layout:
        result = _snap_block_zones(result, block_layout)
        result = _apply_density_spreading(result, block_layout)
    if any(v in ("L", "R") for v in channels.values()):
        result = _apply_stereo_split(result, channels)
        result = _post_stereo_barycentric(result, ir, channels)
    result = _compact_y_gap(result, ir)
    result = _center_ics_in_columns(result, halo=halo)
    result = heuristic_policy.apply_decoupling_snap(result, decoupling_map, ir)
    # Build canonical skip-pairs from the decoupling map so that intentional
    # one-grid-row cap/IC co-locations are not nudged by _deoverlap_positions.
    decouple_skip: frozenset[tuple[str, str]] = frozenset(
        (min(cap, ic), max(cap, ic)) for cap, ic in decoupling_map.items()
    )
    has_buffer_stage = block_layout is not None and any(
        assignment.role == BlockRole.BUFFER_STAGE
        for assignment in block_layout.assignments.values()
    )
    result = _spread_x_columns(result)
    result = _deoverlap_positions(result, skip_pairs=decouple_skip)
    result = _remediate_crossings(result, ir, skip_pairs=decouple_skip)
    # Re-apply op-amp locality after crossing remediation so op-amp neighborhoods
    # remain readable in the final coordinates.
    if (
        any("AMPLIFIER_OPERATIONAL" in component.symbol.upper() for component in ir.components)
        and not has_buffer_stage
    ):
        result = heuristic_policy.apply_opamp_locality(
            result,
            ir,
            annotations=annotations,
            context=_OpAmpLocalityContext(
                decoupling_map=decoupling_map,
                halo=halo,
                block_layout=block_layout,
            ),
        )
    result = heuristic_policy.apply_input_stage_cohesion(
        result,
        ir,
        block_layout=block_layout,
    )
    result = heuristic_policy.apply_output_stage_cohesion(
        result,
        ir,
        block_layout=block_layout,
    )
    result, page_balance_shift = _snap_page_balance(result, block_layout)
    if page_balance_shift != 0.0:
        result = heuristic_policy.apply_decoupling_snap(result, decoupling_map, ir)
    # 7g: Phase 8.2 — central composition (title-block clearance + op-amp vertical bounds).
    result = _snap_central_composition(result, block_layout)
    result = _snap_major_signal_axis(result, block_layout)
    result = _snap_feedback_clusters_to_shifted_cores(
        result,
        ir,
        annotations,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = heuristic_policy.apply_decoupling_snap(result, decoupling_map, ir)
    result = _snap_major_block_spacing(result, block_layout)
    result = _snap_output_transition_subbands(result, block_layout)
    result = _snap_power_block_cohesion(result, block_layout, decoupling_map=decoupling_map)
    result = _snap_multi_unit_sibling_cohesion(
        result,
        power_unit_refs=power_unit_refs,
        unit_sibling_pairs=unit_sibling_pairs,
    )
    protected_text_refs: frozenset[str] = frozenset()
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        protected_text_roles = {BlockRole.DECOUPLING}
        protected_text_refs = frozenset(
            ref
            for ref, assignment in block_layout.assignments.items()
            if assignment.role in protected_text_roles or is_core_like_role(assignment.role)
        )
    result = _apply_property_text_spacing(result, fixed_refs=protected_text_refs)
    # Use grid-safe max bounds so final clamped coordinates stay on the
    # 1.27 mm KiCad grid even at the right/bottom page edges.
    grid = 1.27
    grid_max_x = round(math.floor(PAGE_MAX_X / grid) * grid, 4)
    grid_max_y = round(math.floor(PAGE_MAX_Y / grid) * grid, 4)
    result = _clamp_to_page(result, max_x=grid_max_x, max_y=grid_max_y)
    late_skip_pairs = set(decouple_skip)
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        protected_by_x: dict[float, list[str]] = defaultdict(list)
        for ref, (x, _y, _rot) in result.items():
            assignment = block_layout.assignments.get(ref)
            if assignment is None or (
                assignment.role not in {BlockRole.DECOUPLING, BlockRole.PRECONDITIONING}
                and not is_core_like_role(assignment.role)
            ):
                continue
            protected_by_x[x].append(ref)
        for refs in protected_by_x.values():
            if len(refs) < 2:
                continue
            refs.sort(key=lambda ref: (result[ref][1], ref))
            for idx, left in enumerate(refs[:-1]):
                for right in refs[idx + 1 :]:
                    left_role = block_layout.assignments[left].role
                    right_role = block_layout.assignments[right].role
                    if (
                        BlockRole.PRECONDITIONING in {left_role, right_role}
                        and abs(result[right][1] - result[left][1])
                        > _STEREO_DEOVERLAP_MIN_MM + 0.01
                    ):
                        continue
                    late_skip_pairs.add((min(left, right), max(left, right)))
    # Late locality/cohesion/composition passes can still reintroduce same-column
    # collisions after the earlier deoverlap step, and final clamping can merge
    # edge-bound components back onto the same grid cell. Run one last deoverlap
    # pass on the final clamped coordinates.
    result = _deoverlap_positions(result, skip_pairs=frozenset(late_skip_pairs))
    result = _snap_output_transition_subbands(result, block_layout)
    result = _snap_multi_unit_sibling_cohesion(
        result,
        power_unit_refs=power_unit_refs,
        unit_sibling_pairs=unit_sibling_pairs,
    )
    result = _snap_interstage_handoff_between_stages(result, block_layout)
    result = _snap_feedback_clusters_to_shifted_cores(
        result,
        ir,
        annotations,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = heuristic_policy.apply_input_stage_cohesion(
        result,
        ir,
        block_layout=block_layout,
    )
    result = _snap_opamp_stage_non_inverting_input_node_shape(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_opamp_stage_upstream_input_bundle(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_input_connector_signal_attachment(
        result,
        ir,
        block_layout=block_layout,
    )
    result = _snap_explicit_non_inverting_feedback_nodes(
        result,
        ir,
        annotations,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_buffer_stage_input_node_shape(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_buffer_stage_direct_output_support(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_buffer_stage_feedback_corridor(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_buffer_stage_output_tail_locality(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_explicit_non_inverting_feedback_nodes(
        result,
        ir,
        annotations,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_input_connector_signal_attachment(
        result,
        ir,
        block_layout=block_layout,
    )
    result = _snap_core_local_shunts(result, ir, block_layout=block_layout)
    result = _snap_core_to_output_bridge_passives(result, ir, block_layout=block_layout)
    result = _snap_opamp_stage_non_inverting_input_node_shape(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_opamp_stage_upstream_input_bundle(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = _snap_input_connector_signal_attachment(
        result,
        ir,
        block_layout=block_layout,
    )
    result = _snap_buffer_stage_input_node_shape(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    result = heuristic_policy.apply_decoupling_snap(result, decoupling_map, ir)
    result = _clamp_to_page(result, max_x=grid_max_x, max_y=grid_max_y)
    # The remaining late locality passes can still reintroduce same-cell
    # collisions after the earlier "final" deoverlap. Run one true last guard
    # before returning the snapped coordinates.
    refreshed_late_skip_pairs = set(late_skip_pairs)
    if block_layout is not None:
        refreshed_protected_by_x: dict[float, list[str]] = defaultdict(list)
        for ref, (x, _y, _rot) in result.items():
            assignment = block_layout.assignments.get(ref)
            if assignment is None or (
                assignment.role not in {BlockRole.DECOUPLING, BlockRole.PRECONDITIONING}
                and not is_core_like_role(assignment.role)
            ):
                continue
            refreshed_protected_by_x[x].append(ref)
        for refs in refreshed_protected_by_x.values():
            if len(refs) < 2:
                continue
            refs.sort(key=lambda ref: (result[ref][1], ref))
            for idx, left in enumerate(refs[:-1]):
                for right in refs[idx + 1 :]:
                    left_role = block_layout.assignments[left].role
                    right_role = block_layout.assignments[right].role
                    if (
                        BlockRole.PRECONDITIONING in {left_role, right_role}
                        and abs(result[right][1] - result[left][1])
                        > _STEREO_DEOVERLAP_MIN_MM + 0.01
                    ):
                        continue
                    refreshed_late_skip_pairs.add((min(left, right), max(left, right)))
    result = _deoverlap_positions(result, skip_pairs=frozenset(refreshed_late_skip_pairs))
    if any("AMPLIFIER_OPERATIONAL" in component.symbol.upper() for component in ir.components):
        result = heuristic_policy.apply_opamp_locality(
            result,
            ir,
            annotations=annotations,
            context=_OpAmpLocalityContext(
                decoupling_map=decoupling_map,
                halo=halo,
                block_layout=block_layout,
            ),
        )
    result = heuristic_policy.apply_decoupling_snap(result, decoupling_map, ir)
    if not has_buffer_stage:
        result = _snap_opamp_stage_upstream_input_bundle(
            result,
            ir,
            block_layout=block_layout,
            power_unit_refs=power_unit_refs,
        )
        result = _snap_input_connector_signal_attachment(
            result,
            ir,
            block_layout=block_layout,
        )
    has_amplifier_symbol = any(
        "AMPLIFIER" in component.symbol.upper() for component in ir.components
    )
    if block_layout is not None and has_amplifier_symbol and not has_buffer_stage:
        result = _snap_opamp_stage_non_inverting_input_node_shape(
            result,
            ir,
            block_layout=block_layout,
            power_unit_refs=power_unit_refs,
            allow_global_shift=False,
        )
    result = _snap_buffer_stage_output_tail_locality(
        result,
        ir,
        block_layout=block_layout,
        power_unit_refs=power_unit_refs,
    )
    if not has_buffer_stage:
        result = heuristic_policy.apply_output_stage_cohesion(
            result,
            ir,
            block_layout=block_layout,
        )
    result = _snap_major_block_spacing(result, block_layout)
    final_skip_pairs = set(late_skip_pairs)
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        final_protected_by_x: dict[float, list[str]] = defaultdict(list)
        for ref, (x, _y, _rot) in result.items():
            assignment = block_layout.assignments.get(ref)
            if assignment is None or assignment.role != BlockRole.PRECONDITIONING:
                continue
            final_protected_by_x[x].append(ref)
        for refs in final_protected_by_x.values():
            if len(refs) < 2:
                continue
            refs.sort(key=lambda ref: (result[ref][1], ref))
            for left, right in zip(refs, refs[1:], strict=False):
                if abs(result[right][1] - result[left][1]) <= _STEREO_DEOVERLAP_MIN_MM + 0.01:
                    final_skip_pairs.add((min(left, right), max(left, right)))
    needs_final_deoverlap = False
    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in result.items():
        by_x[x].append(ref)
    for refs in by_x.values():
        if len(refs) < 2:
            continue
        refs.sort(key=lambda ref: (result[ref][1], ref))
        for left, right in zip(refs, refs[1:], strict=False):
            if math.isclose(result[left][1], result[right][1], abs_tol=0.01):
                needs_final_deoverlap = True
                break
        if needs_final_deoverlap:
            break
    if needs_final_deoverlap:
        result = _deoverlap_positions(result, skip_pairs=frozenset(final_skip_pairs))
    if block_layout is not None and has_amplifier_symbol and not has_buffer_stage:
        result = _snap_opamp_stage_non_inverting_input_node_shape(
            result,
            ir,
            block_layout=block_layout,
            power_unit_refs=power_unit_refs,
            allow_global_shift=False,
        )
    if has_buffer_stage:
        result = _snap_buffer_stage_input_node_shape(
            result,
            ir,
            block_layout=block_layout,
            power_unit_refs=power_unit_refs,
        )
    result = _snap_core_local_shunts(result, ir, block_layout=block_layout)
    result = _clamp_to_page(result, max_x=grid_max_x, max_y=grid_max_y)
    return result
