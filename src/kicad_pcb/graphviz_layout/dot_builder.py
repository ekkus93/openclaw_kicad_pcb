"""DOT source builder for the Graphviz schematic layout engine.

Converts a :class:`~kicad_pcb.circuit_ir.CircuitIR` into a Graphviz DOT
source string ready to be piped to ``dot -Tplain``.

Bipartite graph model
---------------------
The DOT graph is *bipartite*:

* **Component nodes** — one ``box`` node per reference designator (e.g. ``R1``).
* **Net nodes** — one ``ellipse`` node per signal net (id ``net_<name>``).
* **Edges** — connect each component to every net it participates in.

Power nets (GND, VCC, VDD, V+, V−, etc.) are *excluded* from the bipartite
graph to prevent highly-connected power hubs from dominating the layout.
Components connected exclusively via power nets are normally placed in a
dedicated ``cluster_power`` subgraph on the right side of the schematic,
except for semantically-classified decoupling parts that should stay near the
active stage they support.

DOT emission strategy
---------------------
:func:`_build_dot_source` orchestrates the following steps:

1. Collect *signal_nets* (non-power, ≥2 pins).
2. Categorise refs as *signal-connected* or *power-only*.
3. Call :func:`~kicad_pcb.tier.assign_tiers` (longest-path) to determine
   the left-to-right rank of each component.
4. Emit one ``{ rank=source/same/sink }`` subgraph per tier via
   :func:`_emit_tier_subgraphs`.
5. Emit directed ``component → net → component`` edges with optional
   ``weight=N`` hints from :func:`_compute_net_weights`.
6. Emit the ``cluster_power`` subgraph.
7. Optionally emit invisible co-location edges for decoupling caps
   (:func:`_emit_decoupling_constraints`).
8. Optionally emit the ``cluster_feedback`` subgraph for feedback components
   (:func:`_emit_feedback_constraints`).

Relationship to ``tier.assign_tiers`` vs ``_assign_bfs_tiers``
---------------------------------------------------------------
Two tier-assignment algorithms exist:

* :func:`~kicad_pcb.tier.assign_tiers` — **longest-path** from all source
  nodes.  This is the production algorithm used by ``_build_dot_source``.
* :func:`_assign_bfs_tiers` — **BFS from a single connector seed**.  This is
  an earlier, simpler algorithm.  It is currently only reachable through the
  backwards-compatible ``assign_bfs_tiers`` re-export in ``graphviz_layout.py``
  and is exercised by tests; it is **not** called from any production code path
  in this module.
"""

from __future__ import annotations

import re
from collections import deque
from collections.abc import Mapping
from itertools import combinations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from ..component_types import CAPACITOR_PREFIXES as _CAPACITOR_PREFIXES_CT
from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import component_type, power_rail_polarity
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..tier import assign_tiers as _assign_tiers

# ---------------------------------------------------------------------------
# Component classification
# ---------------------------------------------------------------------------


def _is_connector(ref: str) -> bool:
    """Return True if *ref* is a connector designator (``J*``, ``P*``, etc.)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CONNECTOR_PREFIXES_CT)


def _is_capacitor(ref: str) -> bool:
    """Return True if *ref* is a capacitor designator (``C*``)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CAPACITOR_PREFIXES_CT)


def _preferred_decoupling_anchor(
    candidate_refs: list[str],
    ref_to_nets: Mapping[str, list[str]],
) -> str | None:
    """Return the most relevant anchor ref for a decoupling capacitor.

    Prefer explicit IC refs first, then components that participate in more
    non-power nets. This prevents local rail passives from stealing the anchor
    when the same net also touches the actual active stage the capacitor serves.
    """
    unique_candidates = sorted(set(candidate_refs))
    if not unique_candidates:
        return None

    def _sort_key(ref: str) -> tuple[int, int, int, str]:
        nets_for_ref = ref_to_nets.get(ref, [])
        signal_net_count = sum(1 for net_name in nets_for_ref if not _is_power_net(net_name))
        rail_net_count = sum(
            1 for net_name in nets_for_ref if power_rail_polarity(net_name) is not None
        )
        return (
            1 if component_type(ref) == "ic" else 0,
            signal_net_count,
            rail_net_count,
            ref,
        )

    return max(unique_candidates, key=_sort_key)


def _find_decoupling_caps(ir: CircuitIR) -> dict[str, str]:
    """Return ``{cap_ref: ic_ref}`` for decoupling/bypass capacitors.

    A decoupling cap is a ``C*`` component where **exactly one** pin is on a
    non-power signal net and the remaining pin(s) are on power nets.  The
    associated IC is the first *non-connector, non-capacitor* component that
    shares the same non-power net as the cap.

    This handles bypass caps wired as::

        VCC_LOCAL(signal net) ─── [C1] ─── GND(power net)

    where ``VCC_LOCAL`` is a local distribution net not matched by
    :func:`_is_power_net` (e.g. the IC's filtered supply net).  True
    VCC→GND caps (both pins on recognised power nets) are left in
    ``cluster_power``.
    """
    # Build: ref → list of net names for that ref.
    ref_to_nets: dict[str, list[str]] = {}
    # Build: net_name → list of refs participating in that net.
    net_to_refs: dict[str, list[str]] = {}

    for net in ir.nets:
        for pin in net.pins:
            ref_to_nets.setdefault(pin.ref, []).append(net.name)
            net_to_refs.setdefault(net.name, []).append(pin.ref)

    active_ics_by_rail: dict[str, list[str]] = {}
    for comp in ir.components:
        if component_type(comp.ref) != "ic":
            continue
        nets_for_ic = ref_to_nets.get(comp.ref, [])
        if not any(not _is_power_net(net_name) for net_name in nets_for_ic):
            continue
        for net_name in nets_for_ic:
            if power_rail_polarity(net_name) is not None:
                active_ics_by_rail.setdefault(net_name, []).append(comp.ref)

    signal_ic_refs = {
        comp.ref
        for comp in ir.components
        if component_type(comp.ref) == "ic"
        and any(not _is_power_net(net_name) for net_name in ref_to_nets.get(comp.ref, []))
    }

    def _rail_anchor_candidates(rail_net: str) -> list[str]:
        direct_candidates = active_ics_by_rail.get(rail_net, [])
        if direct_candidates:
            return direct_candidates

        sibling_candidates: list[str] = []
        for neighbor_ref in net_to_refs.get(rail_net, []):
            if component_type(neighbor_ref) != "ic" or len(neighbor_ref) < 2:
                continue
            suffix = neighbor_ref[-1]
            if not suffix.isalpha():
                continue
            parent_ref = neighbor_ref[:-1]
            sibling_candidates.extend(
                candidate_ref
                for candidate_ref in signal_ic_refs
                if candidate_ref[:-1] == parent_ref and candidate_ref != neighbor_ref
            )
        return sorted(set(sibling_candidates))

    result: dict[str, str] = {}
    for comp in ir.components:
        if not _is_capacitor(comp.ref):
            continue
        nets_for_cap = ref_to_nets.get(comp.ref, [])
        signal_nets_for_cap = [n for n in nets_for_cap if not _is_power_net(n)]
        power_nets_for_cap = [n for n in nets_for_cap if _is_power_net(n)]
        if len(signal_nets_for_cap) == 1 and power_nets_for_cap:
            # Exactly one signal net — find the IC on that shared net.
            signal_net = signal_nets_for_cap[0]
            candidate_refs = [
                neighbor_ref
                for neighbor_ref in net_to_refs.get(signal_net, [])
                if neighbor_ref != comp.ref
                and not _is_connector(neighbor_ref)
                and not _is_capacitor(neighbor_ref)
            ]
            anchor_ref = _preferred_decoupling_anchor(candidate_refs, ref_to_nets)
            if anchor_ref is not None:
                result[comp.ref] = anchor_ref

        if comp.ref in result:
            continue

        ground_nets_for_cap = [n for n in nets_for_cap if _is_ground_like_name(n)]
        rail_nets_for_cap = [n for n in nets_for_cap if power_rail_polarity(n) is not None]
        if signal_nets_for_cap or len(ground_nets_for_cap) != 1 or len(rail_nets_for_cap) != 1:
            continue

        candidate_refs = _rail_anchor_candidates(rail_nets_for_cap[0])
        anchor_ref = _preferred_decoupling_anchor(candidate_refs, ref_to_nets)
        if anchor_ref is not None:
            result[comp.ref] = anchor_ref

    return result


# ---------------------------------------------------------------------------
# BFS tier assignment (test-only; production code uses tier.assign_tiers)
# ---------------------------------------------------------------------------


def _assign_bfs_tiers(
    refs: list[str],
    signal_nets: list,  # list[NetIR] — avoid circular import at runtime
) -> dict[str, int]:
    """Return ``{ref: tier}`` for all *refs* using BFS from connector seeds.

    .. note::
        This function is **not called from any production code path** in
        ``gv_dot_builder`` or ``graphviz_layout``.  It is exposed as a
        backwards-compatible re-export (``assign_bfs_tiers``) for tests.
        Production layout uses :func:`~kicad_pcb.tier.assign_tiers`
        (longest-path from all sources) instead.

    Connector refs (``J*``, ``P*``, ``CON*``, etc.) are used as BFS seeds
    processed in sorted (alphabetical) order.  The alphabetically-first
    connector is the most likely signal source; downstream connectors receive
    higher tier values naturally via BFS traversal.

    Non-connector components receive intermediate tiers.  Power-only or
    isolated components default to tier ``0``.
    """
    # Build undirected adjacency from signal nets.
    ref_set = set(refs)
    adj: dict[str, list[str]] = {r: [] for r in refs}
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, r1 in enumerate(pin_refs):
            for r2 in pin_refs[i + 1 :]:
                adj[r1].append(r2)
                adj[r2].append(r1)

    # Seeds: only the alphabetically-first connector.
    #
    # Using all connectors as seeds simultaneously causes both input AND
    # output connectors to start at tier 0, which collapses the entire
    # circuit into one column.  By seeding only the first connector we let
    # BFS propagate naturally: any output connector at the far end of the
    # signal chain reaches a high tier via BFS, while parallel input
    # connectors that are NOT reachable from the seed fall through to the
    # "unreached → tier 0" default at the end of the function — correct
    # because they are at the same input stage as the primary seed.
    all_connectors = sorted(r for r in refs if _is_connector(r))
    seeds = [all_connectors[0]] if all_connectors else sorted(refs)

    tier: dict[str, int] = {}
    queue: deque[str] = deque()
    for seed in seeds:
        if seed not in tier:
            tier[seed] = 0
            queue.append(seed)

    while queue:
        ref = queue.popleft()
        for neighbor in adj.get(ref, []):
            if neighbor not in tier:
                tier[neighbor] = tier[ref] + 1
                queue.append(neighbor)

    # Unreached refs (isolated or power-only) → tier 0.
    for r in refs:
        if r not in tier:
            tier[r] = 0

    return tier


# ---------------------------------------------------------------------------
# DOT identifier helpers
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Net weight computation
# ---------------------------------------------------------------------------


def _compute_net_weights(signal_nets: list) -> dict[str, int]:
    """Return ``{net_name: weight}`` for each signal net.

    Weight is ``5`` when at least one pair of components sharing this net also
    shares **2 or more signal nets** in total.  This indicates a tight
    functional coupling (e.g. two pins of an op-amp feedback network, or
    series/shunt resistor pairs).  Higher weights tell Graphviz to prefer
    short, uncrossed connections between those component pairs.

    All other nets get weight ``1`` (Graphviz default).
    """
    # Map (ref_a, ref_b) → number of signal nets they share.
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


# ---------------------------------------------------------------------------
# DOT subgraph emitters
# ---------------------------------------------------------------------------


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
        # Different SDS columns: soft proximity bias only — no rank=same so
        # the tier anchor chain can place the units in their respective stage
        # columns without interference.
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
    """Append ``cluster_feedback`` DOT subgraph for *feedback_refs*.

    Each feedback component gets an invisible dummy node and an
    ``[style=invis, weight=10]`` edge to that dummy, biasing Graphviz to
    place the feedback component above (earlier rank than) the amplifier.
    The subgraph itself has no visible border (``style=invis``).
    """
    sorted_fb = sorted(feedback_refs)
    lines.append("  subgraph cluster_feedback {")
    lines.append('    label="";')
    lines.append("    style=invis;")
    for ref in sorted_fb:
        safe = _safe_id(ref)
        dummy = f"__fbdummy_{safe}__"
        lines.append(f"    {dummy} [style=invis, width=0, height=0];")
        lines.append(f"    {safe} -> {dummy} [style=invis, weight=10];")
    lines.append("  }")


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
    # Invisible pull-toward edges (constraint=false so they don't shift ranks).
    for halo_ref, anchor_ref in sorted(halo.items()):
        halo_id = _safe_id(halo_ref)
        anchor_id = _safe_id(anchor_ref)
        lines.append(f"  {halo_id} -> {anchor_id} [style=invis, weight=6, constraint=false];")


def _emit_decoupling_constraints(
    lines: list[str],
    decoupling_map: dict[str, str],
) -> None:
    """Append invisible-edge + rank=same lines to *lines* for *decoupling_map*.

    Emitted for each ``{cap_ref: ic_ref}`` pair:

    * An invisible directed edge ``cap → ic [style=invis, weight=10]`` so that
      Graphviz pulls the cap toward the IC without affecting the visible graph.
    * A ``{rank=same; ic; cap}`` subgraph to place both in the same column.
    """
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
        cap_id = _safe_id(cap_ref)
        ic_id = _safe_id(ic_ref)
        lines.append(f"  {cap_id} -> {ic_id} [style=invis, weight=10];")
    for cap_ref, ic_ref in sorted(decoupling_map.items()):
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


# ---------------------------------------------------------------------------
# Top-level DOT source builder
# ---------------------------------------------------------------------------


def _build_dot_source(  # noqa: PLR0912, PLR0913, PLR0915
    ir: CircuitIR,
    *,
    decoupling_map: dict[str, str] | None = None,
    feedback_refs: set[str] | None = None,
    power_unit_refs: set[str] | None = None,
    unit_sibling_pairs: list[tuple[str, str]] | None = None,
    tiers: dict[str, int] | None = None,
    connector_roles: Mapping[str, str] | None = None,
    halo: dict[str, str] | None = None,
    sds_cols: dict[str, int] | None = None,
    affinity_order: dict[int, list[str]] | None = None,
    block_layout: BlockLayout | None = None,
) -> str:
    """Build a Graphviz DOT source string for *ir* with signal-flow directionality.

    Uses longest-path tier assignment (:func:`tier.assign_tiers`) to determine
    the left-to-right rank of each component, then emits:

    * One ``{ rank=same; }`` (or ``rank=source`` / ``rank=sink`` for the first
      and last tiers) subgraph per BFS tier so Graphviz respects signal-flow
      column ordering.
    * Directed ``component → net_node → component`` edges where the upstream
      component has a lower BFS tier than the downstream component.  This
      gives Graphviz correct rank information so components spread across
      multiple columns instead of collapsing into a single column.
    * A ``cluster_power`` subgraph (``rank=max``) for components that are
      only connected via power nets (GND, VCC, etc.).
    * **Decoupling cap co-location** (optional): if *decoupling_map* is
      supplied, each ``{cap → ic}`` pair gets an invisible zero-weight edge
      (``style=invis, weight=10``) and a ``{rank=same; ic; cap}`` subgraph to
      pull the cap into the same Graphviz column as its associated IC.
    * **Block-based node sizing** (Phase 2.2): if *block_layout* is supplied,
      connector blocks (INPUT/OUTPUT) receive wider node dimensions to improve
      spacing and reduce visual crowding in pin-dense regions.
    """
    lines: list[str] = [
        "digraph sch {",
        "  rankdir=LR;",
        "  nodesep=0.8;",  # Rule 3: increased from 0.5 — more vertical room within tiers
        "  ranksep=2.5;",  # Rule 3: increased from 1.5 — more horizontal room between tiers
        "  ordering=out;",
        "  node [shape=box, width=0.8, height=0.5, fixedsize=true];",
    ]

    refs = sorted(c.ref for c in ir.components)

    # Collect signal nets (non-power, ≥2 pins).
    signal_nets = [net for net in ir.nets if not _is_power_net(net.name) and len(net.pins) >= 2]

    # Compute per-net edge weights: boost to 5 for tightly-coupled pairs.
    net_weights = _compute_net_weights(signal_nets)

    # Categorise: power-only refs have no signal net connections.
    signal_refs: set[str] = set()
    for net in signal_nets:
        signal_refs.update(p.ref for p in net.pins)
    power_only_refs = [r for r in refs if r not in signal_refs]

    # Multi-unit IC power units: force into cluster_power even when they happen
    # to appear on a signal-looking net (belt-and-suspenders; in practice VCC/
    # GND are caught by _is_power_net so they are already in power_only_refs).
    if power_unit_refs:
        power_only_refs, signal_refs = _partition_power_unit_refs(
            refs, signal_refs, power_only_refs, power_unit_refs
        )

    power_only_refs, retained_power_support_refs = _partition_power_cluster_refs(
        power_only_refs,
        block_layout,
    )

    # Longest-path tier assignment — determines left-to-right rank for each component.
    # Use pre-computed tiers if supplied (avoids redundant work when the caller already
    # ran assign_tiers).
    # NOTE: the local _tiers shadows the parameter name intentionally to avoid
    # accidentally using the potentially-None parameter after this point.
    _tiers = tiers if tiers is not None else _assign_tiers(ir)

    # Group signal-connected refs by column.  When SDS-derived column indices
    # are provided they replace tier-based ranking so Graphviz spreads
    # components according to signal-flow position rather than longest-path
    # tier depth.  Fall back to tier-based grouping when sds_cols is absent.
    _col_source: dict[str, int] = sds_cols if sds_cols is not None else _tiers
    tier_groups: dict[int, list[str]] = {}
    for ref in refs:
        if ref in power_only_refs:
            continue
        tier_groups.setdefault(_col_source.get(ref, 0), []).append(ref)
    for ref in retained_power_support_refs:
        tier_groups.setdefault(_col_source.get(ref, 0), []).append(ref)

    # Emit component nodes.
    # Phase 2.2: Use block role to adjust node size for improved spacing.
    for ref in refs:
        safe = _safe_id(ref)
        # Determine node dimensions based on block role (if block layout provided).
        if block_layout and ref in block_layout.assignments:
            role = block_layout.assignments[ref].role
            # Connector blocks (INPUT/OUTPUT) are pin-dense; give them extra width.
            # Op-amp core/feedback blocks get slightly larger sizing for clarity.
            if is_input_like_role(role) or is_output_like_role(role):
                width, height = 1.0, 0.6
            elif is_core_like_role(role):
                width, height = 0.9, 0.55
            else:
                width, height = 0.8, 0.5  # Default for POWER, PASSTHROUGH, UNASSIGNED
        else:
            width, height = 0.8, 0.5  # Default sizing when no block info
        lines.append(f'  {safe} [label="{ref}", shape=box, width={width}, height={height}];')

    # Emit rank subgraphs: rank=source for tier 0, rank=sink for last tier,
    # rank=same for all intermediate tiers.
    _emit_tier_subgraphs(lines, tier_groups, affinity_order=affinity_order)
    if unit_sibling_pairs:
        _emit_unit_sibling_constraints(lines, unit_sibling_pairs, _tiers, col_source=_col_source)

    # Reinforce connector source/sink constraints (belt-and-suspenders on top
    # of the tier subgraphs; merged/idempotent if already in the correct tier).
    if connector_roles:
        _emit_connector_rank_constraints(lines, connector_roles)
    if block_layout:
        _emit_block_zone_constraints(lines, block_layout)

    # Emit net nodes + directional edges.
    # For each signal net, sort pins by ascending BFS tier so edges flow
    # left → right through the net hub node.
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins]
        net_id = "net_" + _safe_id(net.name)
        lines.append(
            f'  {net_id} [label="{net.name}", shape=ellipse, width=0.6, '
            "height=0.4, fixedsize=false];"
        )
        # Sort by the same column source used for rank grouping so the net hub
        # direction reinforces the intended left-to-right placement order.
        sorted_pins = sorted(pin_refs, key=lambda r: (_col_source.get(r, 0), r))
        upstream = sorted_pins[0]
        w = net_weights.get(net.name, 1)
        weight_attr = f" [weight={w}]" if w > 1 else ""
        lines.append(f"  {_safe_id(upstream)} -> {net_id}{weight_attr};")
        for downstream in sorted_pins[1:]:
            # Feedback components sit at a higher tier than all their
            # neighbours; constrain=false prevents dot from pulling the
            # ranks backward when these "downstream" slots are feedback refs.
            is_fb = feedback_refs is not None and downstream in feedback_refs
            extra = " [constraint=false]" if is_fb else (weight_attr if weight_attr else "")
            lines.append(f"  {net_id} -> {_safe_id(downstream)}{extra};")

    # Power-only refs in a subgraph at the right so they don't disrupt flow.
    if power_only_refs:
        lines.append("  subgraph cluster_power {")
        lines.append('    label="power";')
        lines.append("    rank=max;")
        for ref in power_only_refs:
            lines.append(f"    {_safe_id(ref)};")
        lines.append("  }")

    # Decoupling cap co-location: invisible edges + rank=same pull each
    # bypass cap into the same column as its associated IC.
    if decoupling_map:
        _emit_decoupling_constraints(lines, decoupling_map)

    # Feedback components: invisible upward edge pushes them above the
    # amplifier tier.  Grouped in a style=invis cluster so they don't
    # disrupt the power cluster.
    if feedback_refs:
        _emit_feedback_constraints(lines, feedback_refs)

    # Op-amp halo: rank=same + pull-toward edges co-locate feedback network
    # passives in the same DOT column as their anchor IC.
    if halo:
        _emit_halo_constraints(lines, halo)

    lines.append("}")
    return "\n".join(lines)
