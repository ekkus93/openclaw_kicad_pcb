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
# Connectors and headers are the natural entry-points of a PCB circuit.
_SOURCE_PREFIXES: tuple[str, ...] = ("J", "CON", "P", "SJ", "TJ")

# Hard cap on BFS depth to prevent runaway on pathological inputs.
_MAX_COLS: int = 20

# Op-amp / IC prefixes — kept at standard 0° orientation (inputs left, out right).
_OP_AMP_PREFIXES: tuple[str, ...] = ("U", "IC", "OA")

# Passive component prefixes — rotated to 90° when neighbours are vertically arranged.
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


def compute_orientations(
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
) -> dict[str, int]:
    """Return ``{ref: rotation_degrees}`` orientation for every component.

    Rules
    -----
    * **Connectors** (J/CON/P/SJ/TJ): 0° — standard library orientation places
      pins on the right edge so wires flow left→right from the connector.
    * **Op-amps / ICs** (U/IC/OA): 0° — standard orientation keeps inputs on the
      left and output on the right, which is correct for the usual KiCad symbols.
    * **Passives** (R/C/L): 90° when the sum of |Δy| to signal-net neighbours
      exceeds the sum of |Δx|; otherwise 0°.  This aligns resistors and capacitors
      with their dominant wire direction.
    * **Default**: 0°.

    Power / ground nets (identified by :func:`_is_power_net_layout`) are excluded
    from the neighbour calculation so pull-ups / bypass capacitors that only
    connect to rails are not biased by them.
    """
    # Build adjacency through signal nets only.
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

    result: dict[str, int] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        # Connectors: always 0°.
        if any(upper.startswith(pfx) for pfx in _SOURCE_PREFIXES):
            result[ref] = 0
            continue

        # Op-amps / ICs: always 0°.
        if any(upper.startswith(pfx) for pfx in _OP_AMP_PREFIXES):
            result[ref] = 0
            continue

        # Passives: 90° when vertically-arranged neighbours dominate.
        if any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES) and ref in positions:
            x, y = positions[ref]
            total_dx = 0.0
            total_dy = 0.0
            for nbr in adjacency.get(ref, []):
                if nbr in positions:
                    nx, ny = positions[nbr]
                    total_dx += abs(nx - x)
                    total_dy += abs(ny - y)
            result[ref] = 90 if total_dy > total_dx else 0
            continue

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
