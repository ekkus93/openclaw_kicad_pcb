"""Graph primitives for tier assignment: BFS, directed graph, cycle breaking, DP."""

from __future__ import annotations

import logging
import re
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from .component_types import component_type
from .component_types import is_power_net as _component_is_power_net
from .errors import ErrorCode, UserError

if TYPE_CHECKING:  # pragma: no cover
    from .circuit_ir import NetIR

#: I/O role for a connector: ``"input"`` (signal source), ``"output"``
#: (signal sink), or ``"unknown"`` (cannot be determined from topology).
ConnectorRole = Literal["input", "output", "power", "unknown"]

_log = logging.getLogger(__name__)

#: Matches multi-unit IC refs such as "U1A", "OA3B", "IC12AB".
#: Group 1 is the base ref (e.g. "U1"), group 2 is the unit suffix (e.g. "A").
_MULTI_UNIT_RE: re.Pattern[str] = re.compile(r"^([A-Za-z]+[0-9]+)([A-Za-z]+)$")


# ---------------------------------------------------------------------------
# Multi-unit IC detection
# ---------------------------------------------------------------------------


@dataclass
class IcUnitGroup:
    """Detected grouping of multi-unit IC symbol references.

    Parameters
    ----------
    base_ref:
        The stem shared by all units, e.g. ``"U1"`` for ``U1A`` / ``U1B``.
    units:
        Sorted list of individual unit reference strings.
    power_unit:
        Reference of the unit whose every net connection is a power rail (VCC,
        GND, etc.), or ``None`` when no such unit exists.  The power unit is
        placed in the ``cluster_power`` DOT subgraph rather than the main
        signal-flow tier grid.
    """

    base_ref: str
    units: list[str] = field(default_factory=list)
    power_unit: str | None = None

    @property
    def signal_units(self) -> list[str]:
        """Return unit refs that belong in the main signal-flow graph."""
        return [unit_ref for unit_ref in self.units if unit_ref != self.power_unit]

    def sibling_pairs(self) -> list[tuple[str, str]]:
        """Return adjacent ordered signal-unit pairs for layout constraints."""
        signal_units = self.signal_units
        return list(zip(signal_units, signal_units[1:]))


def _is_power_net(name: str) -> bool:
    """Return ``True`` when *name* is recognized as a power/ground rail."""
    return _component_is_power_net(name)


# ---------------------------------------------------------------------------
# Component-type ordering (tiebreaker within same BFS tier)
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
# Seed connector selection
# ---------------------------------------------------------------------------


def _choose_seed_connector(
    refs: list[str],
    signal_nets: list[NetIR],
    *,
    strict: bool = False,
) -> str:
    """Return the connector ref most suitable as the BFS seed (input connector).

    Among all connectors, selects the one with the **longest shortest-path** to
    any IC component in the undirected signal graph.  This heuristic reliably
    identifies true *input* connectors, which are separated from the active
    amplifier by more passive stages (e.g. coupling capacitors, bias resistors)
    than output connectors (which tend to connect to the amplifier output
    through fewer components).

    Example — NE5532 headphone amplifier:

    * ``J1`` (headphone out) → RV1 (vol pot) → U1A output: **2 hops** to IC.
    * ``J2`` (audio in) → C2 (coupling cap) → R (bias) → U1A input: **3 hops**.

    ``J2`` wins (most hops) → BFS seeds from ``J2`` → ``J2`` gets tier 0
    (leftmost / ``rank=source``) and ``J1`` gets the last tier
    (rightmost / ``rank=sink``).  This matches the standard convention of
    inputs on the left and outputs on the right.

    Falls back to the alphabetically-first connector when:

    * There are no ICs in the circuit (BFS comparison is meaningless).
    * All connectors are equidistant from every IC (tie → alphabetical order).

    Parameters
    ----------
    refs:
        All component references in the circuit.
    signal_nets:
        Non-power nets with ≥ 2 pins, used to build the adjacency graph.

    Returns
    -------
    str
        The reference string of the preferred BFS seed connector.
    """
    connectors = sorted(r for r in refs if component_type(r) == "connector")
    if not connectors:
        return sorted(refs)[0]

    ics = {r for r in refs if component_type(r) == "ic"}
    if not ics:
        if strict:
            raise UserError(
                "Cannot determine connector seed without IC components in strict mode",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={"connectors": connectors},
            )
        return connectors[0]  # no ICs — fall back to alphabetical

    # Build undirected signal adjacency.
    ref_set = set(refs)
    adj: dict[str, list[str]] = {r: [] for r in refs}
    for net in signal_nets:
        pin_refs = [p.ref for p in net.pins if p.ref in ref_set]
        for i, a in enumerate(pin_refs):
            for b in pin_refs[i + 1 :]:
                adj[a].append(b)
                adj[b].append(a)

    def _min_hops_to_ic(start: str) -> int:
        """Return the minimum BFS hop count from *start* to any IC, or ``maxsize``."""
        visited: set[str] = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            node, dist = queue.popleft()
            if node in ics:
                return dist
            for nbr in adj[node]:
                if nbr not in visited:
                    visited.add(nbr)
                    queue.append((nbr, dist + 1))
        return 10_000  # IC unreachable

    distances = {c: _min_hops_to_ic(c) for c in connectors}
    max_dist = max(distances.values())

    # Among connectors sharing the maximum distance, keep alphabetical order
    # for determinism.
    best = min(c for c in connectors if distances[c] == max_dist)
    _log.debug(
        "tier: seed connector chosen = %r (IC hop-distances: %s)",
        best,
        {c: d for c, d in sorted(distances.items())},
    )
    return best


# ---------------------------------------------------------------------------
# Step 1 — Undirected BFS from source connector
# ---------------------------------------------------------------------------


def _undirected_bfs(
    refs: list[str],
    signal_nets: list[NetIR],
    *,
    strict: bool = False,
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

    # Seed: connector with maximum hop-distance to nearest IC (prefers input
    # connector; falls back to alphabetically-first connector when equidistant).
    seeds = [_choose_seed_connector(refs, signal_nets, strict=strict)]

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
