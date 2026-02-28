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
# Grid constants (mm)
# ---------------------------------------------------------------------------
GRID_COL_MM: float = 30.48  # 1200 mil column width
GRID_ROW_MM: float = 20.32  # 800 mil row pitch — fits ~10 rows on A4
ORIGIN_X: float = 30.48
ORIGIN_Y: float = 50.80

# Maximum number of components stacked in one visual column before wrapping
# to a new sub-column.  At 20.32 mm pitch, 10 rows reach y ≈ 253 mm which
# stays within an A4 page (297 mm).
MAX_ROWS_PER_COL: int = 10

# Reference prefixes treated as signal sources (left edge of layout).
# Connectors and headers are the natural entry-points of a PCB circuit.
_SOURCE_PREFIXES: tuple[str, ...] = ("J", "CON", "P", "SJ", "TJ")

# Hard cap on BFS depth to prevent runaway on pathological inputs.
_MAX_COLS: int = 20


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
    positions: dict[str, tuple[float, float]] = {}
    visual_col = 0
    for c_num, members in sorted(by_col.items()):
        members.sort(key=_avg_nbr_col)
        num_sub = max(1, math.ceil(len(members) / MAX_ROWS_PER_COL))
        for idx, ref in enumerate(members):
            sub = idx // MAX_ROWS_PER_COL
            row = idx % MAX_ROWS_PER_COL
            x = ORIGIN_X + (visual_col + sub) * GRID_COL_MM
            y = ORIGIN_Y + row * GRID_ROW_MM
            positions[ref] = (x, y)
        visual_col += num_sub

    return positions
