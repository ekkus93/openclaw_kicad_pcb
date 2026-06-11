"""Longest-path tier assignment for KiCad PCB auto-placement.

:func:`assign_tiers` converts a :class:`~kicad_pcb.circuit_ir.CircuitIR` into a
``{ref: tier_index}`` mapping used by the Graphviz layout engine to enforce
left-to-right signal-flow ordering via ``rank=same`` subgraphs.

Algorithm overview
------------------
1. Collect signal nets (non-power, ≥ 2 pins).
2. Run an undirected BFS from the alphabetically-first connector to obtain
   *preliminary* tier values for every component.
3. Use those preliminary values — plus type-order as a tiebreaker — to assign
   a direction to every signal-net edge, producing a directed component graph.
4. Remove back-edges (DFS-based cycle detection) with ties broken by lowest
   net-pin count, which targets whichever edge carries the least signal
   (typically a feedback path).
5. Run a longest-path DP (Kahn topological sort) on the resulting DAG.
   ``tier[v] = max(tier[u] + 1 for every u→v edge)``.
6. Components unreachable from the source seed (power-only, isolated) default
   to tier ``0``.

Re-exports
----------
``TIER_SPACING_MM`` and ``ORIGIN_X_MM`` are re-exported from
:mod:`component_types` for callers that import everything from this module.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING

from ._tier_connectors import (  # noqa: F401
    _classify_connector_roles,
    _has_connector_hint,
    _infer_connector_roles_from_ir,
)
from ._tier_graph import (  # noqa: F401
    _MULTI_UNIT_RE,
    ConnectorRole,
    IcUnitGroup,
    _break_cycles,
    _build_directed_graph,
    _choose_seed_connector,
    _is_power_net,
    _longest_path_dp,
    _undirected_bfs,
)
from .component_types import (
    ORIGIN_X_MM,
    TIER_SPACING_MM,
    component_type,
)

if TYPE_CHECKING:  # pragma: no cover
    from .circuit_ir import CircuitIR, NetIR

__all__ = [
    "assign_tiers",
    "identify_main_signal_path",
    "assign_ic_units_to_tiers",
    "build_ic_unit_sibling_constraints",
    "choose_seed_connector",
    "classify_connector_roles",
    "ConnectorRole",
    "IcUnitGroup",
    "TIER_SPACING_MM",
    "ORIGIN_X_MM",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assign_tiers(ir: CircuitIR, *, strict: bool = False) -> dict[str, int]:
    """Return ``{ref: tier_index}`` for every component in *ir*.

    The tier index determines the left-to-right column in the auto-placement
    grid:

    * **Tier 0** — input connectors / signal sources (alphabetically-first
      connector and any component at BFS distance 0 from it).
    * **Tier 1 … N-2** — signal-chain components ordered by the *longest*
      path from any tier-0 source.
    * **Tier N-1** — output connectors / signal sinks (components in the last
      tier naturally reached by longest-path propagation).

    Components with no signal-net connections (power-only or isolated) are
    assigned tier ``0`` by BFS default.

    Parameters
    ----------
    ir:
        Parsed circuit IR whose :attr:`~CircuitIR.components` and
        :attr:`~CircuitIR.nets` are already populated.

    Returns
    -------
    dict[str, int]
        Maps every component reference string to a non-negative integer tier.
    """
    refs = [c.ref for c in ir.components]
    if not refs:
        return {}

    signal_nets: list[NetIR] = [
        n for n in ir.nets if not _is_power_net(n.name) and len(n.pins) >= 2
    ]

    # Step 1: preliminary BFS tiers for direction inference.
    bfs_tiers = _undirected_bfs(refs, signal_nets, strict=strict)

    # Step 2: directed graph.
    succ, pred, pair_count = _build_directed_graph(refs, signal_nets, bfs_tiers)

    # Step 3: break cycles.
    _break_cycles(refs, succ, pred, pair_count)

    # Step 4: longest-path DP.
    lp_tiers = _longest_path_dp(refs, succ, pred)

    # Step 4c: classify connector I/O roles and enforce tier pinning (Rule 0).
    # Output connectors are forced to max_tier; input connectors to tier 0.
    # In a correctly processed circuit the DP already achieves this; the
    # forcing guards against edge cases (e.g. single-hop output pats, ties).
    roles = _classify_connector_roles(refs, lp_tiers, ir=ir)
    max_tier = max(lp_tiers.values(), default=0)
    for ref, role in roles.items():
        if role == "output" and lp_tiers.get(ref, 0) != max_tier:
            _log.debug(
                "tier: forcing %r to output tier %d (was %d)",
                ref,
                max_tier,
                lp_tiers[ref],
            )
            lp_tiers[ref] = max_tier
        elif role == "input" and lp_tiers.get(ref, 0) != 0:
            _log.debug(
                "tier: forcing %r to input tier 0 (was %d)",
                ref,
                lp_tiers[ref],
            )
            lp_tiers[ref] = 0

    return lp_tiers


def identify_main_signal_path(
    ir: CircuitIR,
    *,
    tiers: dict[str, int] | None = None,
) -> list[str]:
    """Return the probable primary signal chain as an ordered ref list.

    The path is inferred over non-power signal nets from the connector
    classified as input (tier 0) to the most downstream output connector.
    Traversal prefers monotonic tier progression to avoid selecting obvious
    feedback/support loops as the main forward path.

    Parameters
    ----------
    ir:
        Parsed circuit IR.
    tiers:
        Optional precomputed tier mapping from :func:`assign_tiers`.

    Returns
    -------
    list[str]
        Ordered component references representing the probable main signal
        chain. Returns an empty list when no suitable connector-to-connector
        signal path can be inferred.
    """
    refs = [c.ref for c in ir.components]
    if not refs:
        return []

    active_tiers = tiers or assign_tiers(ir)
    roles = _classify_connector_roles(refs, active_tiers, ir=ir)

    inputs = sorted(r for r, role in roles.items() if role == "input")
    outputs = sorted(
        (r for r, role in roles.items() if role == "output"),
        key=lambda r: (-active_tiers.get(r, 0), r),
    )
    if not inputs or not outputs:
        return []

    start = inputs[0]

    # Build signal-only undirected adjacency (component graph).
    adj: dict[str, set[str]] = {r: set() for r in refs}
    ref_set = set(refs)
    for net in ir.nets:
        if _is_power_net(net.name) or len(net.pins) < 2:
            continue
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for idx, a in enumerate(pin_refs):
            for b in pin_refs[idx + 1 :]:
                if a == b:
                    continue
                adj[a].add(b)
                adj[b].add(a)

    def _bfs_path(end: str, *, monotonic: bool) -> list[str]:
        parents: dict[str, str | None] = {start: None}
        queue: deque[str] = deque([start])

        while queue:
            node = queue.popleft()
            if node == end:
                break

            nbrs = sorted(
                adj.get(node, set()),
                key=lambda r: (active_tiers.get(r, 0), r),
            )
            node_tier = active_tiers.get(node, 0)
            for nbr in nbrs:
                if monotonic and active_tiers.get(nbr, 0) < node_tier:
                    continue
                if nbr in parents:
                    continue
                parents[nbr] = node
                queue.append(nbr)

        if end not in parents:
            return []

        path: list[str] = []
        cursor: str | None = end
        while cursor is not None:
            path.append(cursor)
            cursor = parents[cursor]
        path.reverse()
        return path

    # Try most downstream outputs first, and prefer tier-monotonic paths.
    for out_ref in outputs:
        path = _bfs_path(out_ref, monotonic=True)
        if path:
            return path

    # Fallback: allow non-monotonic traversal if strict forward traversal
    # found no path (rare topologies with unavoidable return edges).
    for out_ref in outputs:
        path = _bfs_path(out_ref, monotonic=False)
        if path:
            return path

    return []


def assign_ic_units_to_tiers(
    ir: CircuitIR,
    tiers: dict[str, int],
) -> dict[str, IcUnitGroup]:
    """Group multi-unit IC references and identify their power unit.

    Scans *ir.components* for IC references that carry a letter-suffix unit
    designator (e.g. ``U1A``, ``U1B``, ``U1C``).  Components whose reference
    does not match the ``<IC_PREFIX><digits><letters>`` pattern are ignored.

    For each group the function checks all nets in *ir.nets*: if every net a
    unit appears on is a *power net* (GND, VCC, etc.) then that unit is
    flagged as the *power unit*.  The power unit should be placed in the
    ``cluster_power`` DOT subgraph rather than the main signal-flow tier grid.

    Parameters
    ----------
    ir:
        Parsed circuit IR.
    tiers:
        Per-component tier mapping from :func:`assign_tiers` (reserved for
        future ranking logic; topology-driven detection does not use it).

    Returns
    -------
    dict[str, IcUnitGroup]
        Maps each *base_ref* (e.g. ``"U1"``) to its :class:`IcUnitGroup`.
        Only base refs with ≥ 2 unit variants in *ir.components* are included.
    """
    # Build mapping ref → set of net names it appears on.
    ref_to_nets: dict[str, set[str]] = {c.ref: set() for c in ir.components}
    for net in ir.nets:
        for pin in net.pins:
            if pin.ref in ref_to_nets:
                ref_to_nets[pin.ref].add(net.name)

    # Detect multi-unit IC refs and group by base ref.
    raw_groups: dict[str, list[str]] = {}
    for comp in ir.components:
        if component_type(comp.ref) != "ic":
            continue
        m = _MULTI_UNIT_RE.match(comp.ref)
        if m is None:
            continue
        base = m.group(1)
        if component_type(base) != "ic":
            # Guard against false positives with passive-prefixed refs.
            continue
        raw_groups.setdefault(base, []).append(comp.ref)

    # Build IcUnitGroup for each base with ≥ 2 units.
    result: dict[str, IcUnitGroup] = {}
    for base, unit_refs in raw_groups.items():
        if len(unit_refs) < 2:
            continue
        units = sorted(unit_refs)
        # Power unit: every connected net is a power rail.
        power_unit: str | None = None
        for unit_ref in units:
            connected = ref_to_nets.get(unit_ref, set())
            if connected and all(_is_power_net(n) for n in connected):
                power_unit = unit_ref
                break
        result[base] = IcUnitGroup(base_ref=base, units=units, power_unit=power_unit)

    return result


def build_ic_unit_sibling_constraints(
    unit_groups: dict[str, IcUnitGroup],
) -> list[tuple[str, str]]:
    """Return ordered signal-unit pairs that should remain visually related.

    Each returned pair expresses a preferred left-to-right sibling order for
    placed multi-unit symbols while excluding any power-only unit that should
    live in ``cluster_power``.
    """
    pairs: list[tuple[str, str]] = []
    for base_ref in sorted(unit_groups):
        pairs.extend(unit_groups[base_ref].sibling_pairs())
    return pairs


# ---------------------------------------------------------------------------
# Public re-exports (private implementations exposed under public names)
# ---------------------------------------------------------------------------

#: Public alias for :func:`_choose_seed_connector`.
choose_seed_connector = _choose_seed_connector

#: Public alias for :func:`_classify_connector_roles`.
classify_connector_roles = _classify_connector_roles
