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

from .component_types import (
    ORIGIN_X_MM,
    TIER_SPACING_MM,
    component_type,
)

if TYPE_CHECKING:  # pragma: no cover
    from .circuit_ir import CircuitIR, NetIR

__all__ = [
    "assign_tiers",
    "TIER_SPACING_MM",
    "ORIGIN_X_MM",
]

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Power-net detection (local; avoids import of regex-heavy graphviz_layout)
# ---------------------------------------------------------------------------

_POWER_NET_STARTS: tuple[str, ...] = (
    "GND",
    "VCC",
    "VDD",
    "VSS",
    "PWR",
    "AGND",
    "PGND",
    "DGND",
    "VBAT",
    "VREF",
    "V+",
    "V-",
    "+3V",
    "+5V",
    "+12V",
    "-12V",
)


def _is_power_net(name: str) -> bool:
    """Return ``True`` when *name* looks like a power/ground rail."""
    upper = name.upper()
    return any(upper == p or upper.startswith(p) for p in _POWER_NET_STARTS)


# ---------------------------------------------------------------------------
# Component-type ordering (used as tiebreaker within same BFS tier)
# ---------------------------------------------------------------------------

_TYPE_ORDER: dict[str, int] = {
    "connector": 0,
    "passive": 1,
    "misc": 1,
    "unknown": 1,
    "ic": 2,
}


def _type_order(ref: str) -> int:
    """Return a numeric ordering for *ref* based on its component type."""
    return _TYPE_ORDER.get(component_type(ref), 1)


# ---------------------------------------------------------------------------
# Step 1 — Undirected BFS from source connector
# ---------------------------------------------------------------------------


def _undirected_bfs(
    refs: list[str],
    signal_nets: list[NetIR],
) -> dict[str, int]:
    """Return ``{ref: bfs_distance}`` seeded from the alphabetically-first connector.

    The alphabetically-first connector is the most natural signal source.
    Seeding from a single connector (not all connectors simultaneously) means
    that downstream connectors naturally receive higher BFS distances —
    correctly placing output connectors in a later tier.

    Components unreachable from the seed (power-only, isolated) default to
    tier ``0``.
    """
    ref_set = set(refs)

    # Build undirected adjacency from signal nets.
    adj: dict[str, list[str]] = {r: [] for r in refs}
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, a in enumerate(pin_refs):
            for b in pin_refs[i + 1 :]:
                adj[a].append(b)
                adj[b].append(a)

    # Seed: alphabetically-first connector.
    all_connectors = sorted(r for r in refs if component_type(r) == "connector")
    seeds = [all_connectors[0]] if all_connectors else sorted(refs)[:1]

    tier: dict[str, int] = {}
    queue: deque[str] = deque()
    for seed in seeds:
        if seed not in tier:
            tier[seed] = 0
            queue.append(seed)

    while queue:
        ref = queue.popleft()
        for nbr in adj[ref]:
            if nbr not in tier:
                tier[nbr] = tier[ref] + 1
                queue.append(nbr)

    # Unreached refs default to tier 0.
    for r in refs:
        if r not in tier:
            tier[r] = 0

    return tier


# ---------------------------------------------------------------------------
# Step 2 — Build directed component graph
# ---------------------------------------------------------------------------


def _build_directed_graph(
    refs: list[str],
    signal_nets: list[NetIR],
    bfs_tiers: dict[str, int],
) -> tuple[dict[str, set[str]], dict[str, set[str]], dict[tuple[str, str], int]]:
    """Return ``(succ, pred, edge_net_count)`` for the directed component graph.

    Edge direction is determined by the preliminary BFS tiers; type-order
    (connector < passive/misc < IC) is used as a tiebreaker when two
    components share the same BFS tier.

    ``edge_net_count[(a, b)]`` counts the number of signal nets that produce
    the directed edge ``a → b`` (or any edge between the pair).  This is used
    later to choose which back-edge to remove when breaking cycles.
    """
    ref_set = set(refs)
    succ: dict[str, set[str]] = {r: set() for r in refs}
    pred: dict[str, set[str]] = {r: set() for r in refs}
    # Canonical edge key: (min(a,b), max(a,b)) → count.
    pair_count: dict[tuple[str, str], int] = {}

    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, a in enumerate(pin_refs):
            for b in pin_refs[i + 1 :]:
                pair_key = (min(a, b), max(a, b))
                pair_count[pair_key] = pair_count.get(pair_key, 0) + 1

                bfs_a = bfs_tiers.get(a, 0)
                bfs_b = bfs_tiers.get(b, 0)
                to_a = _type_order(a)
                to_b = _type_order(b)

                if bfs_a < bfs_b or (bfs_a == bfs_b and to_a < to_b):
                    succ[a].add(b)
                    pred[b].add(a)
                elif bfs_a > bfs_b or (bfs_a == bfs_b and to_a > to_b):
                    succ[b].add(a)
                    pred[a].add(b)
                # bfs_a == bfs_b and to_a == to_b → no directed edge (parallel)

    return succ, pred, pair_count


# ---------------------------------------------------------------------------
# Step 3 — DFS-based cycle (back-edge) removal
# ---------------------------------------------------------------------------


def _break_cycles(
    refs: list[str],
    succ: dict[str, set[str]],
    pred: dict[str, set[str]],
    pair_count: dict[tuple[str, str], int],
) -> None:
    """Remove back-edges in-place using iterative DFS (modifies *succ*/*pred*).

    When a back-edge is detected, the edge with the *smallest* net-pin count
    in the current DFS path leading to the cycle is removed.  This targets
    lightly-coupled feedback paths (e.g. a single feedback resistor) rather
    than main signal paths.

    Logs each removed edge at DEBUG level so circuit problems become visible
    in verbose output.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {r: WHITE for r in refs}
    # Stack frames: (node, iterator_over_successors, path_from_root)
    # path_from_root is a list of (edge_src, edge_dst) in the current DFS path.

    for start in refs:
        if color[start] != WHITE:
            continue
        dfs_stack: list[tuple[str, list[str], list[tuple[str, str]]]] = [
            (start, list(succ[start]), [])
        ]
        color[start] = GRAY

        while dfs_stack:
            node, children, path = dfs_stack[-1]

            if not children:
                color[node] = BLACK
                dfs_stack.pop()
                continue

            child = children.pop()
            if color[child] == BLACK:
                continue  # Already fully explored — forward/cross edge.

            if color[child] == GRAY:
                # Back-edge found: node → child.  Find the edge on the current
                # DFS path (including this new edge) with the lowest net count.
                candidate_edges = [*path, (node, child)]
                weakest = min(
                    candidate_edges,
                    key=lambda e: pair_count.get((min(e[0], e[1]), max(e[0], e[1])), 0),
                )
                src, dst = weakest
                succ[src].discard(dst)
                pred[dst].discard(src)
                _log.debug("tier: broke cycle by removing edge %s → %s", src, dst)
                continue

            # WHITE child — recurse.
            color[child] = GRAY
            dfs_stack.append((child, list(succ[child]), [*path, (node, child)]))


# ---------------------------------------------------------------------------
# Step 4 — Longest-path DP (Kahn's topological order)
# ---------------------------------------------------------------------------


def _longest_path_dp(
    refs: list[str],
    succ: dict[str, set[str]],
    pred: dict[str, set[str]],
) -> dict[str, int]:
    """Return ``{ref: tier}`` via longest-path DP on the DAG in *succ*/*pred*.

    Uses Kahn's algorithm (in-degree queue) so the DP runs in *O(V + E)*.
    Any node not reached via the queue (should not occur after cycle breaking,
    but guarded defensively) defaults to tier ``0``.
    """
    in_deg: dict[str, int] = {r: len(pred[r]) for r in refs}
    queue: deque[str] = deque(r for r in refs if in_deg[r] == 0)
    tier: dict[str, int] = {r: 0 for r in refs}

    while queue:
        node = queue.popleft()
        for child in succ[node]:
            tier[child] = max(tier[child], tier[node] + 1)
            in_deg[child] -= 1
            if in_deg[child] == 0:
                queue.append(child)

    return tier


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def assign_tiers(ir: CircuitIR) -> dict[str, int]:
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
    bfs_tiers = _undirected_bfs(refs, signal_nets)

    # Step 2: directed graph.
    succ, pred, pair_count = _build_directed_graph(refs, signal_nets, bfs_tiers)

    # Step 3: break cycles.
    _break_cycles(refs, succ, pred, pair_count)

    # Step 4: longest-path DP.
    return _longest_path_dp(refs, succ, pred)
