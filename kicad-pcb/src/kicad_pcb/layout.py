"""Signal-flow schematic layout for kicad_pcb.

Computes ``{ref: (x, y)}`` placements for Circuit IR components using
BFS-based column assignment so signals flow left→right and connected
components end up adjacent to each other.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .component_types import IC_PREFIXES as _IC_PREFIXES_CT

# ---------------------------------------------------------------------------
# Page dimensions (mm) — must match lint.py _LAY_PAGE_MAX_X / _LAY_PAGE_MAX_Y.
# KiCad A4 schematic page: 297 mm wide × 210 mm tall (landscape orientation).
PAGE_WIDTH_MM: float = 297.0
PAGE_HEIGHT_MM: float = 210.0

# Grid constants (mm)
# ---------------------------------------------------------------------------
GRID_COL_MM: float = 30.48  # 1200 mil column width
GRID_ROW_MM: float = 20.32  # 800 mil row pitch
ORIGIN_X: float = 30.48
ORIGIN_Y: float = 50.80

# Maximum number of components stacked in one visual column before wrapping
# to a new sub-column.  Derived from page height so no symbol ever lands
# outside the A4 boundary (LAY004).
#
#   max_y = ORIGIN_Y + (MAX_ROWS_PER_COL - 1) * GRID_ROW_MM
#         = 50.80 + 6 × 20.32 = 172.72 mm  (< PAGE_HEIGHT_MM = 210 mm).
#
# The previous hard-coded value of 10 put row 8 at 213.36 mm, triggering
# LAY004 whenever a circuit had 9+ components in a single BFS column.
MAX_ROWS_PER_COL: int = int((PAGE_HEIGHT_MM - ORIGIN_Y) / GRID_ROW_MM)

# Minimum centre-to-centre distance (mm) between any two heuristic positions.
# This equals GRID_ROW_MM (the tighter axis: symbols in the same column are
# stacked GRID_ROW_MM apart).  LAY003 fires when both |Δx| and |Δy| are less
# than 2 × _LAY_SYMBOL_HALF_SIZE_MM = 10.16 mm (see lint.py).  Since
# GRID_ROW_MM (20.32) > 10.16 and GRID_COL_MM (30.48) > 10.16, every pair of
# heuristic positions is guaranteed to be overlap-free per the LAY003 rule.
MIN_SEPARATION_MM: float = GRID_ROW_MM

# Reference prefixes treated as signal sources (left edge of layout).
# Imported from component_types to avoid duplication.
_SOURCE_PREFIXES: tuple[str, ...] = _CONNECTOR_PREFIXES_CT

# Hard cap on BFS depth to prevent runaway on pathological inputs.
_MAX_COLS: int = 20

# Op-amp / IC prefixes — kept at standard 0° orientation (inputs left, out right).
# Imported from component_types to avoid duplication.
_OP_AMP_PREFIXES: tuple[str, ...] = _IC_PREFIXES_CT

# Passive component prefixes for orientation/affinity heuristics.  This is a
# deliberate *subset* of component_types.PASSIVE_PREFIXES: diodes (D) and BJTs
# (Q) are intentionally excluded here because the orientation heuristic is
# only meaningful for two-terminal RLC passives.
_PASSIVE_PREFIXES: tuple[str, ...] = ("R", "C", "L")

# Net-name prefixes that are power/ground rails.  Connections through these
# nets are excluded from the orientation heuristic so only signal nets drive
# the rotation decision.
_POWER_NET_PREFIXES: tuple[str, ...] = (
    "GND",
    "VCC",
    "VDD",
    "VSS",
    "PWR",
    "AGND",
    "PGND",
    "DGND",
    "V+",
    "V-",
    "VBAT",
    "VREF",
)


def _is_power_net_layout(name: str) -> bool:
    """Return True when *name* looks like a power/ground rail.

    Inline duplicate of the router-level helper to avoid a circular import.
    """
    upper = name.upper()
    return any(upper == pfx or upper.startswith(pfx) for pfx in _POWER_NET_PREFIXES)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return undirected adjacency graph ``{ref: {neighbour_refs}}`` from net data."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].add(r_j)
                    adjacency[r_j].add(r_i)
    return adjacency


def _build_signal_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return adjacency graph built from *signal* nets only (power nets excluded)."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].add(r_j)
                    adjacency[r_j].add(r_i)
    return adjacency


def _build_power_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return adjacency graph built from *power* nets only (signal nets excluded)."""
    adjacency: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if not _is_power_net_layout(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].add(r_j)
                    adjacency[r_j].add(r_i)
    return adjacency


def _bfs_columns(
    refs: list[str],
    adjacency: dict[str, set[str]],
    seeds: list[str],
) -> dict[str, int]:
    """Assign BFS column indices to every ref, starting from *seeds*."""
    col: dict[str, int] = {}
    queue: deque[str] = deque()
    for s in seeds:
        if s not in col:
            col[s] = 0
            queue.append(s)
    while queue:
        ref = queue.popleft()
        c = col[ref]
        for nbr in sorted(adjacency.get(ref, set())):
            if nbr not in col:
                col[nbr] = min(c + 1, _MAX_COLS)
                queue.append(nbr)
    # Assign unreachable nodes to a floater column past the rightmost BFS col.
    max_col = max(col.values(), default=0)
    for r in refs:
        if r not in col:
            col[r] = max_col + 1
    return col


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_signal_flow_layout(ir: CircuitIR) -> dict[str, tuple[float, float]]:
    """Return ``{ref: (x, y)}`` placements for all components in *ir*.

    Algorithm
    ---------
    1. Build an undirected adjacency graph keyed on reference designators.
       Two components are adjacent when they share at least one net.
    2. BFS from connector/source nodes (refs whose prefix matches
       :data:`_SOURCE_PREFIXES`) to assign a *column* index (= BFS depth,
       capped at :data:`_MAX_COLS`) to every component.  If no connector
       refs exist the most-connected component is used as seed.
    3. Within each column, sort components by their average neighbour column
       to reduce wire crossings.
    4. Map ``(column, row)`` pairs to ``(x, y)`` mm coordinates.

    Components not reachable from any seed are placed one column past the
    maximum (a "floater" column, typically power or passive symbols with no
    direct connector path).
    """
    refs = sorted(c.ref for c in ir.components)
    if not refs:
        return {}

    adjacency = _build_adjacency(ir)

    seeds: list[str] = [
        r for r in refs if any(r.upper().startswith(pfx) for pfx in _SOURCE_PREFIXES)
    ]
    if not seeds:
        # Fall back to the most-connected node as seed.
        seeds = [max(refs, key=lambda r: len(adjacency.get(r, set())))]

    col = _bfs_columns(refs, adjacency, seeds)

    # --- Post-BFS: co-locate power-only passives with their anchor IC -------
    # A decoupling capacitor (or similar passive) that connects *only* through
    # power/ground rails has no signal-net neighbours, so BFS places it
    # arbitrarily far from its associated IC.  We fix this by detecting
    # power-only passives and reassigning their column to sit immediately
    # after the column of their nearest IC (found through power adjacency).
    sig_adj = _build_signal_adjacency(ir)
    pwr_adj = _build_power_adjacency(ir)
    max_bfs_col = max(col.values(), default=0)

    for r in refs:
        upper = r.upper()
        if not any(upper.startswith(p) for p in _PASSIVE_PREFIXES):
            continue  # Only passives benefit from this adjustment.
        if sig_adj.get(r):
            continue  # Component has signal connections — BFS placement is correct.
        # Identify IC neighbours through shared power nets.
        ic_neighbors = [
            n
            for n in pwr_adj.get(r, set())
            if any(n.upper().startswith(p) for p in _OP_AMP_PREFIXES)
        ]
        if not ic_neighbors:
            continue
        # Assign to the column immediately after the nearest IC.
        anchor_col = min(col.get(n, max_bfs_col) for n in ic_neighbors)
        col[r] = min(anchor_col + 1, max_bfs_col)
    # -------------------------------------------------------------------------

    # Sort within each column by average-neighbour-column to cut crossings.
    by_col: dict[int, list[str]] = defaultdict(list)
    for r, c in col.items():
        by_col[c].append(r)

    def _avg_nbr_col(ref: str) -> float:
        nbrs = adjacency.get(ref, set())
        if not nbrs:
            return 0.0
        return sum(col.get(n, 0) for n in nbrs) / len(nbrs)

    # Assign coordinates, wrapping tall BFS-columns into sub-columns so the
    # layout stays within a single A4 page.  Each BFS-column occupies at
    # least one visual column; if it has more than MAX_ROWS_PER_COL members
    # it overflows into consecutive additional visual columns.
    #
    # Within each column, op-amps / ICs are placed at the *centre* rows so
    # surrounding passives connect naturally above and below — matching the
    # conventional circuit-diagram convention of "op-amp centred per stage".
    positions: dict[str, tuple[float, float]] = {}
    visual_col = 0
    for c_num, members in sorted(by_col.items()):
        members.sort(key=_avg_nbr_col)
        # Centre op-amps: split into IC refs and others, interleave so ICs
        # occupy the middle rows of the column.
        ic_refs = [r for r in members if any(r.upper().startswith(p) for p in _OP_AMP_PREFIXES)]
        other_refs = [r for r in members if r not in set(ic_refs)]
        mid = len(other_refs) // 2
        ordered = other_refs[:mid] + ic_refs + other_refs[mid:]
        num_sub = max(1, math.ceil(len(ordered) / MAX_ROWS_PER_COL))
        for idx, ref in enumerate(ordered):
            sub = idx // MAX_ROWS_PER_COL
            row = idx % MAX_ROWS_PER_COL
            x = ORIGIN_X + (visual_col + sub) * GRID_COL_MM
            y = ORIGIN_Y + row * GRID_ROW_MM
            positions[ref] = (x, y)
        visual_col += num_sub

    return positions


def compute_affinity_groups(
    ir: CircuitIR,
    tiers: dict[str, int],
) -> dict[int, list[str]]:
    """Return ``{tier_index: [ref, …]}`` sorted by signal affinity to the previous tier.

    For each tier, components are ordered so those most strongly coupled to
    any component in the **previous tier** appear first (toward the top of the
    schematic).  Tier 0 components are sorted alphabetically as a stable base.

    Affinity between components *A* and *B* is::

        affinity(A, B) = |shared_signal_nets(A, B)| / min(|signal_nets(A)|, |signal_nets(B)|)

    Power/ground nets are excluded from the affinity calculation.

    Parameters
    ----------
    ir:
        Circuit IR.
    tiers:
        ``{ref: tier_index}`` mapping — typically from ``assign_bfs_tiers()``.

    Returns
    -------
    dict[int, list[str]]
        ``{tier_index: [ref, …]}`` sorted by descending affinity to the
        previous tier (alphabetical tiebreak within same score).
    """
    # Accumulate signal nets per ref.
    ref_nets: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        for pin in net.pins:
            ref_nets[pin.ref].add(net.name)

    def _affinity(a: str, b: str) -> float:
        nets_a = ref_nets.get(a, set())
        nets_b = ref_nets.get(b, set())
        shared = len(nets_a & nets_b)
        denom = min(len(nets_a), len(nets_b))
        if denom == 0:
            return 0.0
        return shared / denom

    # Group refs by tier.
    tier_groups: dict[int, list[str]] = defaultdict(list)
    for ref, tier in tiers.items():
        tier_groups[tier].append(ref)

    result: dict[int, list[str]] = {}
    sorted_tiers = sorted(tier_groups)
    for i, tier in enumerate(sorted_tiers):
        members = tier_groups[tier]
        if i == 0:
            # First tier: stable alphabetical sort — no previous tier to compare.
            result[tier] = sorted(members)
            continue
        prev_tier = sorted_tiers[i - 1]
        prev_members = result.get(prev_tier, [])
        # Score = sum of affinities to all previous-tier members.
        scores = {ref: sum(_affinity(ref, p) for p in prev_members) for ref in members}
        result[tier] = sorted(members, key=lambda r: (-scores[r], r))

    return dict(result)


def _classify_passive_pins(
    ir: CircuitIR,
) -> tuple[frozenset[str], frozenset[str]]:
    """Return ``(refs_with_power_pin, refs_with_signal_pin)`` for components in *ir*.

    Iterates once over ``ir.nets`` and sorts each pin reference into the
    *power* bucket (if the net is a power/ground rail) or the *signal* bucket.
    """
    power_refs: set[str] = set()
    signal_refs: set[str] = set()
    for net in ir.nets:
        bucket = power_refs if _is_power_net_layout(net.name) else signal_refs
        for pin in net.pins:
            bucket.add(pin.ref)
    return frozenset(power_refs), frozenset(signal_refs)


def _series_passive_rotation(
    ref: str,
    positions: dict[str, tuple[float, float]],
    adjacency: dict[str, list[str]],
) -> int:
    """Return 0° or 90° for a series passive using the position heuristic.

    90° when the sum of |Δy| to signal-net neighbours exceeds the sum of |Δx|;
    otherwise 0°.  Defaults to 0° when *ref* is not in *positions*.
    """
    if ref not in positions:
        return 0
    x, y = positions[ref]
    total_dx = total_dy = 0.0
    for nbr in adjacency.get(ref, []):
        if nbr in positions:
            nx, ny = positions[nbr]
            total_dx += abs(nx - x)
            total_dy += abs(ny - y)
    return 90 if total_dy > total_dx else 0


def compute_orientations(
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
    tiers: dict[str, int] | None = None,
) -> dict[str, int]:
    """Return ``{ref: rotation_degrees}`` orientation for every component.

    Parameters
    ----------
    ir:
        Parsed circuit IR.
    positions:
        ``{ref: (x_mm, y_mm)}`` layout positions (used for the passive
        position heuristic).
    tiers:
        Optional ``{ref: tier_index}`` from :func:`~kicad_pcb.tier.assign_tiers`.
        When provided, connector orientation is determined by tier:
        tier 0 → 0° (input, pins point right); max tier → 180° (output,
        pins point left toward the circuit).  When *None*, all connectors
        default to 0°.

    Rules (applied in priority order)
    -----------------------------------
    * **Connectors** (J/CON/P/SJ/TJ):
      - With *tiers*: tier 0 → 0°; max tier → 180°; intermediate → 0°.
      - Without *tiers*: always 0°.
    * **Op-amps / ICs** (U/IC/OA): 0° — standard orientation keeps inputs on the
      left and output on the right, which is correct for the usual KiCad symbols.
    * **Passives — shunt topology** (R/C/L with ≥1 power-net pin AND ≥1 signal-net
      pin): 90°.  A bypass capacitor, pull-up, or pull-down resistor straddles a
      power rail and the signal path, so a vertical (90°) orientation visually
      shows the connection from signal wire down to the rail.
    * **Passives — position heuristic** (R/C/L with all pins on signal nets):
      90° when the sum of |Δy| to signal-net neighbours exceeds the sum of |Δx|;
      otherwise 0°.  This orients in-column feedback or coupling components to
      match the dominant wire direction.
    * **Diodes** (D*): always 0° (anode left, cathode right for forward-biased
      series placement).
    * **Default**: 0°.

    Power / ground nets (identified by :func:`_is_power_net_layout`) are excluded
    from the position-based neighbour calculation so they do not bias series
    passives.  They ARE used for the shunt-topology check above.
    """
    # Pre-compute shunt topology: which refs have power-net pins / signal-net pins.
    power_pin_refs, signal_pin_refs = _classify_passive_pins(ir)

    # Build adjacency through signal nets only (for the position heuristic).
    adjacency: dict[str, list[str]] = defaultdict(list)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for i, r_i in enumerate(pin_refs):
            for r_j in pin_refs[i + 1 :]:
                if r_i != r_j:
                    adjacency[r_i].append(r_j)
                    adjacency[r_j].append(r_i)

    # Pre-compute max tier for connector direction decisions.
    _max_tier: int = max(tiers.values()) if tiers else 0

    result: dict[str, int] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        # Connectors: 0° for input (tier 0), 180° for output (max tier).
        if any(upper.startswith(pfx) for pfx in _SOURCE_PREFIXES):
            if tiers is not None and _max_tier > 0:
                result[ref] = 180 if tiers.get(ref, 0) == _max_tier else 0
            else:
                result[ref] = 0
            continue

        # Op-amps / ICs: always 0°.
        if any(upper.startswith(pfx) for pfx in _OP_AMP_PREFIXES):
            result[ref] = 0
            continue

        if any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            # Shunt-topology: passive straddles a power rail and a signal path.
            if ref in power_pin_refs and ref in signal_pin_refs:
                result[ref] = 90
                continue
            # Position heuristic for pure-signal (series) passives.
            result[ref] = _series_passive_rotation(ref, positions, adjacency)
            continue

        # Diodes (D*), and all other unmatched components default to 0°.
        result[ref] = 0

    return result


# ---------------------------------------------------------------------------
# LayoutEngine wrapper (satisfies layout_engine.LayoutEngine Protocol)
# ---------------------------------------------------------------------------


class HeuristicLayoutEngine:
    """BFS signal-flow layout engine; requires no external tools.

    Wraps :func:`compute_signal_flow_layout` to satisfy the
    :class:`~kicad_pcb.layout_engine.LayoutEngine` Protocol.  Rotation is
    not supported (always returns ``None`` for the third tuple element).
    """

    def compute_symbol_positions(
        self,
        ir: CircuitIR,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Delegate to :func:`compute_signal_flow_layout`.

        Returns ``{ref: (x_mm, y_mm, None)}`` — rotation is always ``None``.
        """
        raw = compute_signal_flow_layout(ir)
        return {ref: (x, y, None) for ref, (x, y) in raw.items()}

    def __repr__(self) -> str:  # pragma: no cover
        return "HeuristicLayoutEngine()"
