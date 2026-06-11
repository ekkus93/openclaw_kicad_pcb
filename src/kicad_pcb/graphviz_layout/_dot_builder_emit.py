"""DOT emission helpers: tier subgraphs, constraints, and node declarations."""

from __future__ import annotations

import re
from collections.abc import Mapping
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)


def _safe_id(name: str) -> str:
    """Return a DOT-safe identifier (replace all non-alphanumeric chars with '_')."""
    return re.sub(r"[^A-Za-z0-9]", "_", name)


def _tier_rank_keyword(tier_index: int, n_tiers: int) -> str:
    """Return the Graphviz ``rank=`` keyword for *tier_index* of *n_tiers* tiers.

    * Single-tier graphs → ``same`` (no forced ordering).
    * First tier (index 0) → ``source`` (signal sources at the left edge).
    * Last tier → ``sink`` (signal sinks at the right edge).
    * All intermediate tiers → ``same`` (column grouping without edge constraint).
    """
    if n_tiers == 1:
        return "same"
    if tier_index == 0:
        return "source"
    if tier_index == n_tiers - 1:
        return "sink"
    return "same"


def _compute_net_weights(signal_nets: list) -> dict[str, int]:
    """Return ``{net_name: weight}`` for each signal net.

    Weight is ``5`` when at least one pair of components sharing this net also
    shares **2 or more signal nets** in total.  This indicates a tight
    functional coupling (e.g. two pins of an op-amp feedback network, or
    series/shunt resistor pairs).  Higher weights tell Graphviz to prefer
    short, uncrossed connections between those component pairs.

    All other nets get weight ``1`` (Graphviz default).
    """
    pair_net_count: dict[tuple[str, str], int] = {}
    for net in signal_nets:
        refs = sorted({p.ref for p in net.pins})
        for a, b in combinations(refs, 2):
            key = (a, b)
            pair_net_count[key] = pair_net_count.get(key, 0) + 1

    result: dict[str, int] = {}
    for net in signal_nets:
        refs = sorted({p.ref for p in net.pins})
        max_shared = max(
            (pair_net_count.get((a, b), 0) for a, b in combinations(refs, 2)),
            default=0,
        )
        result[net.name] = 5 if max_shared >= 2 else 1
    return result


def _emit_tier_subgraphs(
    lines: list[str],
    tier_groups: dict[int, list[str]],
    affinity_order: dict[int, list[str]] | None = None,
) -> None:
    """Emit ``{ rank=... }`` subgraphs for each tier into *lines*.

    Parameters
    ----------
    lines:
        DOT source lines accumulated so far (appended in-place).
    tier_groups:
        ``{tier_index: [ref, …]}`` — all signal-connected refs grouped by tier.
    affinity_order:
        Optional ``{tier_index: [ref, …]}`` from :func:`~kicad_pcb.layout.compute_affinity_groups`.
        When supplied, refs within each ``{rank=same}`` block are emitted in
        affinity order (highest coupling to the previous tier first) instead of
        alphabetical order.  Falls back to alphabetical for any tier absent from
        the dict.
    """
    sorted_tier_vals = sorted(tier_groups)
    n_tiers = len(sorted_tier_vals)
    previous_anchor: str | None = None
    for i, tier_val in enumerate(sorted_tier_vals):
        members = tier_groups[tier_val]
        rank_kw = _tier_rank_keyword(i, n_tiers)
        anchor_id = f"__tier_{tier_val}__"
        lines.append(f'  {anchor_id} [label="", shape=point, width=0, height=0, style=invis];')
        lines.append("  {")
        lines.append(f"    rank={rank_kw};")
        lines.append(f"    {anchor_id};")
        if affinity_order is not None and tier_val in affinity_order:
            member_set = set(members)
            ref_list = [ref for ref in affinity_order[tier_val] if ref in member_set]
        else:
            ref_list = sorted(members)
        for ref in ref_list:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")
        if previous_anchor is not None:
            lines.append(f"  {previous_anchor} -> {anchor_id} [style=invis, weight=20];")
        previous_anchor = anchor_id


def _emit_unit_sibling_constraints(
    lines: list[str],
    unit_sibling_pairs: list[tuple[str, str]],
    tiers: dict[str, int],
    col_source: dict[str, int] | None = None,
) -> None:
    """Emit invisible constraints that keep sibling units visually related.

    When *col_source* is supplied (SDS-derived column indices), the hard
    ``rank=same`` constraint is only applied to unit pairs that share the
    same SDS column.  Pairs at different SDS columns (e.g. the two halves of
    a dual op-amp used as cascaded stages) get a soft ``constraint=false``
    invisible edge so Graphviz can separate them by rank while still biasing
    them toward spatial proximity.
    """
    _col = col_source if col_source is not None else tiers
    for left_ref, right_ref in unit_sibling_pairs:
        if _col.get(left_ref) == _col.get(right_ref):
            lines.append("  {")
            lines.append("    rank=same;")
            lines.append(f"    {_safe_id(left_ref)};")
            lines.append(f"    {_safe_id(right_ref)};")
            lines.append("  }")
            lines.append(
                f"  {_safe_id(left_ref)} -> {_safe_id(right_ref)} "
                "[style=invis, weight=8, constraint=false];"
            )
            continue
        lines.append(
            f"  {_safe_id(left_ref)} -> {_safe_id(right_ref)} "
            "[style=invis, weight=4, constraint=false];"
        )


def _partition_power_unit_refs(
    refs: list[str],
    signal_refs: set[str],
    power_only_refs: list[str],
    power_unit_refs: set[str],
) -> tuple[list[str], set[str]]:
    """Return updated ``(power_only_refs, signal_refs)`` without mutating the originals.

    Multi-unit IC power units (e.g. ``U1B``) that happen to connect via a net
    whose name is not caught by :func:`_is_power_net` would otherwise slip into
    the signal tier grid.  This helper forces them into ``cluster_power``.

    Returns a new ``(power_only_refs, signal_refs)`` pair with additional refs
    absorbed from *power_unit_refs*.
    """
    already: set[str] = set(power_only_refs)
    new_power_only = list(power_only_refs)
    new_signal_refs = set(signal_refs)
    for r in refs:
        if r in power_unit_refs and r not in already:
            new_power_only.append(r)
            new_signal_refs.discard(r)
            already.add(r)
    return new_power_only, new_signal_refs


def _partition_power_cluster_refs(
    power_only_refs: list[str],
    block_layout: BlockLayout | None,
) -> tuple[list[str], list[str]]:
    """Return ``(cluster_power_refs, retained_support_refs)`` for power-only refs.

    When block classification is available, true bypass/decoupling capacitors
    should not be forced into ``cluster_power`` just because both pins land on
    recognized power nets. Keeping them out of the power cluster lets SDS/block
    zoning place them near the op-amp stage instead of dumping them into the
    far-right power bucket.
    """
    if block_layout is None:
        return list(power_only_refs), []

    cluster_power_refs: list[str] = []
    retained_support_refs: list[str] = []
    for ref in power_only_refs:
        assignment = block_layout.assignments.get(ref)
        if assignment is not None and assignment.role == BlockRole.DECOUPLING:
            retained_support_refs.append(ref)
        else:
            cluster_power_refs.append(ref)
    return cluster_power_refs, retained_support_refs


def _emit_feedback_constraints(lines: list[str], feedback_refs: set[str]) -> None:
    """Append feedback dummy-node constraints for *feedback_refs*.

    Each feedback component gets an invisible dummy node and an
    ``[style=invis, weight=10]`` edge to that dummy, biasing Graphviz to
    place the feedback component above (earlier rank than) the amplifier.

    These lines are emitted directly into the main graph instead of a
    ``cluster_*`` subgraph. Graphviz 12 can abort in ``flat_reorder`` when a
    feedback cluster interacts with block-zone anchor constraints, while the
    equivalent flat dummy-node edges remain stable.
    """
    sorted_fb = sorted(feedback_refs)
    for ref in sorted_fb:
        safe = _safe_id(ref)
        dummy = f"__fbdummy_{safe}__"
        lines.append(f'  {dummy} [label="", shape=point, style=invis, width=0, height=0];')
        lines.append(f"  {safe} -> {dummy} [style=invis, weight=10];")


def _emit_halo_constraints(
    lines: list[str],
    halo: dict[str, str],
) -> None:
    """Append soft invisible-edge affinity constraints for op-amp halo members.

    For each ``{halo_ref: anchor_ic_ref}`` pair emits an invisible directed
    edge ``halo_ref → anchor_ic_ref [style=invis, weight=6, constraint=false]``
    to pull the halo member toward the IC without hard-locking both refs into
    the same Graphviz column.
    """
    for halo_ref, anchor_ref in sorted(halo.items()):
        halo_id = _safe_id(halo_ref)
        anchor_id = _safe_id(anchor_ref)
        lines.append(f"  {halo_id} -> {anchor_id} [style=invis, weight=6, constraint=false];")


def _emit_decoupling_constraints(
    lines: list[str],
    decoupling_map: dict[str, str],
    *,
    tiers: dict[str, int] | None = None,
) -> None:
    """Append invisible-edge + rank=same lines to *lines* for *decoupling_map*.

    Emitted for each ``{cap_ref: ic_ref}`` pair:

    * An invisible directed edge ``cap → ic [style=invis, weight=10, constraint=false]`` so that
      Graphviz pulls the cap toward the IC without affecting the visible graph.
    * A ``{rank=same; ic; cap}`` subgraph to place both in the same column.
    """
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
        cap_id = _safe_id(cap_ref)
        ic_id = _safe_id(ic_ref)
        lines.append(f"  {cap_id} -> {ic_id} [style=invis, weight=10, constraint=false];")
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
        if tiers is not None and tiers.get(cap_ref) != tiers.get(ic_ref):
            continue
        lines.append("  {")
        lines.append("    rank=same;")
        lines.append(f"    {_safe_id(ic_ref)};")
        lines.append(f"    {_safe_id(cap_ref)};")
        lines.append("  }")


def _emit_connector_rank_constraints(
    lines: list[str],
    connector_roles: Mapping[str, str],
) -> None:
    """Emit explicit ``rank=source`` / ``rank=sink`` subgraphs for connectors.

    This is belt-and-suspenders on top of the tier-based rank subgraphs:
    even if the longest-path DP and tier-forcing in :func:`assign_tiers`
    already place connectors in the correct tier groups, explicitly marking
    them as ``source`` / ``sink`` reinforces the constraint directly in the
    DOT graph.  Graphviz treats duplicate rank constraints as merged, so
    including a connector in both a tier group and a connector constraint is
    safe and idempotent.

    Parameters
    ----------
    lines:
        DOT source lines accumulated so far (appended to in-place).
    connector_roles:
        ``{ref: "input" | "output" | "unknown"}`` from
        :func:`~kicad_pcb.tier.classify_connector_roles`.
    """
    inputs = sorted(ref for ref, role in connector_roles.items() if role == "input")
    outputs = sorted(ref for ref, role in connector_roles.items() if role == "output")
    if inputs:
        lines.append("  {")
        lines.append("    rank=source;")
        for ref in inputs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")
    if outputs:
        lines.append("  {")
        lines.append("    rank=sink;")
        for ref in outputs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")


def _emit_block_zone_constraints(lines: list[str], block_layout: BlockLayout) -> None:
    """Append soft block-anchor constraints to bias left/center/right zones.

    Uses invisible anchor nodes and weighted invisible edges so Graphviz
    prefers a left-to-right block order without hard-locking every component
    into the same rank or exact column.
    """
    input_refs = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if is_input_like_role(assignment.role)
    )
    core_refs = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if is_core_like_role(assignment.role)
    )
    output_refs = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if is_output_like_role(assignment.role)
    )
    power_refs = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if is_power_like_role(assignment.role)
    )
    if not any((input_refs, core_refs, output_refs, power_refs)):
        return

    input_anchor = "__blk_input__"
    core_anchor = "__blk_core__"
    output_anchor = "__blk_output__"
    power_anchor = "__blk_power__"

    for anchor in (input_anchor, core_anchor, output_anchor, power_anchor):
        lines.append(f'  {anchor} [label="", shape=point, width=0, height=0, style=invis];')

    if input_refs:
        lines.append("  {")
        lines.append("    rank=source;")
        lines.append(f"    {input_anchor};")
        lines.append("  }")
    if output_refs:
        lines.append("  {")
        lines.append("    rank=sink;")
        lines.append(f"    {output_anchor};")
        lines.append("  }")

    lines.append(f"  {input_anchor} -> {core_anchor} [style=invis, weight=30];")
    lines.append(f"  {core_anchor} -> {output_anchor} [style=invis, weight=30];")

    for ref in input_refs:
        safe = _safe_id(ref)
        lines.append(f"  {input_anchor} -> {safe} [style=invis, weight=12];")
        lines.append(f"  {safe} -> {core_anchor} [style=invis, weight=8];")

    for ref in core_refs:
        safe = _safe_id(ref)
        lines.append(f"  {input_anchor} -> {safe} [style=invis, weight=6];")
        lines.append(f"  {safe} -> {output_anchor} [style=invis, weight=6];")

    for ref in output_refs:
        safe = _safe_id(ref)
        lines.append(f"  {core_anchor} -> {safe} [style=invis, weight=12];")

    stage_role_groups = [
        sorted(
            ref for ref, assignment in block_layout.assignments.items() if assignment.role == role
        )
        for role in (
            BlockRole.INPUT,
            BlockRole.PRECONDITIONING,
            BlockRole.OPAMP_CORE,
            BlockRole.INTERSTAGE,
            BlockRole.BUFFER_STAGE,
            BlockRole.OUTPUT_CONDITIONING,
            BlockRole.OUTPUT,
        )
    ]
    ordered_stage_groups = [group for group in stage_role_groups if group]
    for left_group, right_group in zip(
        ordered_stage_groups,
        ordered_stage_groups[1:],
        strict=False,
    ):
        for left_ref in left_group:
            left_safe = _safe_id(left_ref)
            for right_ref in right_group:
                lines.append(f"  {left_safe} -> {_safe_id(right_ref)} [style=invis, weight=10];")

    lines.append(f"  {input_anchor} -> {power_anchor} [style=invis, weight=4, constraint=false];")
    for ref in power_refs:
        safe = _safe_id(ref)
        lines.append(f"  {power_anchor} -> {safe} [style=invis, weight=4, constraint=false];")
