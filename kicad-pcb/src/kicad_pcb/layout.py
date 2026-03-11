"""Signal-flow schematic layout for kicad_pcb.

Computes ``{ref: (x, y)}`` placements for Circuit IR components using
BFS-based column assignment so signals flow left→right and connected
components end up adjacent to each other.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .block_detection import BlockLayout, BlockRole
from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .component_types import IC_PREFIXES as _IC_PREFIXES_CT
from .component_types import POWER_NET_PREFIXES as _POWER_NET_PREFIXES_CT
from .errors import ErrorCode, UserError
from .tier import identify_main_signal_path

_log = logging.getLogger(__name__)

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

# Maximum number of remediation barycentric sweeps (R6-2).
_MAX_REMEDIATION_SWEEPS: int = 3

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
# the rotation decision.  Imported from component_types to avoid duplication.
_POWER_NET_PREFIXES: tuple[str, ...] = _POWER_NET_PREFIXES_CT


def _is_power_net_layout(name: str) -> bool:
    """Return True when *name* looks like a power/ground rail.

    Uses :data:`~kicad_pcb.component_types.POWER_NET_PREFIXES` from
    :mod:`kicad_pcb.component_types`.  The ``startswith`` test intentionally
    matches prefixed variants (e.g. ``VCC_FILTERED``) so that any net whose
    name starts with a known power prefix is excluded from signal-path
    orientation heuristics.
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


#: Sentinel BFS distance for components unreachable from a connector direction.
_SDS_SENTINEL: int = 1000


def _bfs_distances(
    adjacency: dict[str, set[str]],
    seeds: list[str],
) -> dict[str, int]:
    """Return BFS distances from *seeds* over *adjacency*.

    Unreachable nodes are absent from the result.
    """
    dist: dict[str, int] = {}
    queue: deque[str] = deque()
    for s in seeds:
        if s not in dist:
            dist[s] = 0
            queue.append(s)
    while queue:
        ref = queue.popleft()
        d = dist[ref]
        for nbr in adjacency.get(ref, set()):
            if nbr not in dist:
                dist[nbr] = d + 1
                queue.append(nbr)
    return dist


def _find_decoupling_caps_layout(ir: CircuitIR) -> dict[str, str]:  # noqa: PLR0912
    """Return ``{cap_ref: ic_ref}`` for bypass/decoupling capacitors.

    Mirrors :func:`~kicad_pcb.graphviz_layout.dot_builder._find_decoupling_caps`
    but uses :func:`_is_power_net_layout` so the two passes agree on net
    classification.
    """
    ref_to_nets: dict[str, list[str]] = {}
    net_to_refs: dict[str, list[str]] = {}
    for net in ir.nets:
        for pin in net.pins:
            ref_to_nets.setdefault(pin.ref, []).append(net.name)
            net_to_refs.setdefault(net.name, []).append(pin.ref)

    result: dict[str, str] = {}
    for comp in ir.components:
        if not comp.ref.upper().startswith("C"):
            continue
        nets_for_cap = ref_to_nets.get(comp.ref, [])
        signal_nets = [n for n in nets_for_cap if not _is_power_net_layout(n)]
        power_nets = [n for n in nets_for_cap if _is_power_net_layout(n)]
        if len(signal_nets) != 1 or not power_nets:
            continue
        signal_net = signal_nets[0]
        for neighbor_ref in net_to_refs.get(signal_net, []):
            if neighbor_ref == comp.ref:
                continue
            neighbor_upper = neighbor_ref.upper()
            if any(neighbor_upper.startswith(p) for p in _CONNECTOR_PREFIXES_CT):
                continue
            if neighbor_upper.startswith("C"):
                continue
            result[comp.ref] = neighbor_ref
            break
    return result


def compute_signal_distance_scores(
    ir: CircuitIR,
    roles: Mapping[str, str],
) -> dict[str, float]:
    """Compute Signal Distance Score (SDS) for every component in *ir*.

    The SDS captures where a component sits in the signal chain:
    0.0 = at the input, 1.0 = at the output, 0.5 = mid-chain.

    Algorithm
    ---------
    1. Build the undirected signal-only adjacency graph.
    2. BFS from all *input* connectors to obtain *d_in* (distance to the
       nearest input).
    3. BFS from all *output* connectors to obtain *d_out* (distance to the
       nearest output).
    4. ``SDS(c) = d_in / (d_in + d_out)``.
       * Unreachable components use a sentinel distance of
         :data:`_SDS_SENTINEL` = 1000.
       * When both distances are 0 (isolated or single-component), SDS = 0.5.
    5. Power-only decoupling caps inherit the SDS of their anchor IC.

    Parameters
    ----------
    ir:
        Circuit IR.
    roles:
        ``{connector_ref: role}`` where role is ``"input"``, ``"output"``,
        or ``"unknown"``; as returned by
        :func:`~kicad_pcb.tier.classify_connector_roles`.

    Returns
    -------
    dict[str, float]
        ``{ref: sds}`` for every component.  Values in ``[0.0, 1.0]``
        except for degenerate cases (both distances 0) which return 0.5.
    """
    sig_adj = _build_signal_adjacency(ir)

    input_seeds = [r for r, role in roles.items() if role == "input"]
    output_seeds = [r for r, role in roles.items() if role == "output"]

    d_in = _bfs_distances(sig_adj, input_seeds)
    d_out = _bfs_distances(sig_adj, output_seeds)

    result: dict[str, float] = {}
    for comp in ir.components:
        ref = comp.ref
        di = d_in.get(ref, _SDS_SENTINEL)
        do = d_out.get(ref, _SDS_SENTINEL)
        if di == 0 and do == 0:
            result[ref] = 0.5
        else:
            result[ref] = di / (di + do)

    # Power-only decoupling caps inherit SDS from their anchor IC.
    for cap_ref, ic_ref in _find_decoupling_caps_layout(ir).items():
        if ic_ref in result:
            result[cap_ref] = result[ic_ref]

    return result


def _recursive_halving(  # noqa: PLR0913
    refs: list[str],
    sds: dict[str, float],
    x_lo: float,
    x_hi: float,
    *,
    max_per_col: int = MAX_ROWS_PER_COL,
    grid_col_mm: float = GRID_COL_MM,
) -> dict[str, int]:
    """Assign column indices by recursively halving the SDS-sorted component set.

    Components are sorted by :data:`SDS` score and split at the median.
    Each half is recursively assigned to the lower or upper part of the
    current spatial band ``[x_lo, x_hi)``.

    Recursion stops when the group is small enough (``len ≤ max_per_col``) or
    the band is narrower than one grid column (``x_hi − x_lo < grid_col_mm``).
    At the base case all *refs* in the slice receive the column index
    corresponding to *x_lo*.

    Parameters
    ----------
    refs:
        All component references to assign.
    sds:
        ``{ref: sds_score}`` as returned by :func:`compute_signal_distance_scores`.
    x_lo, x_hi:
        Left and right edges (mm) of the spatial band available for this
        recursive call.  The caller should pass ``ORIGIN_X`` and
        ``ORIGIN_X + _MAX_COLS * GRID_COL_MM`` for the top-level call.
    max_per_col:
        Maximum number of components allowed in one column before forcing a
        split.  Defaults to :data:`MAX_ROWS_PER_COL`.
    grid_col_mm:
        Width of one schematic column in mm.  Defaults to :data:`GRID_COL_MM`.

    Returns
    -------
    dict[str, int]
        ``{ref: col_index}`` where *col_index* = ``round((x_lo - ORIGIN_X) /
        grid_col_mm)`` at the base-case leaf.
    """
    result: dict[str, int] = {}
    _rh_recurse(refs, sds, x_lo, x_hi, max_per_col, grid_col_mm, result)
    return result


def _rh_recurse(  # noqa: PLR0913
    refs: list[str],
    sds: dict[str, float],
    x_lo: float,
    x_hi: float,
    max_per_col: int,
    grid_col_mm: float,
    out: dict[str, int],
) -> None:
    """Recursive worker for :func:`_recursive_halving`."""
    if not refs:
        return
    col_idx = round((x_lo - ORIGIN_X) / grid_col_mm)
    band = x_hi - x_lo
    if len(refs) <= max_per_col or band < grid_col_mm:
        for r in refs:
            out[r] = col_idx
        return
    sorted_refs = sorted(refs, key=lambda r: (sds.get(r, 0.5), r))
    mid = len(sorted_refs) // 2
    x_mid = (x_lo + x_hi) / 2
    _rh_recurse(sorted_refs[:mid], sds, x_lo, x_mid, max_per_col, grid_col_mm, out)
    _rh_recurse(sorted_refs[mid:], sds, x_mid, x_hi, max_per_col, grid_col_mm, out)


def compute_sds_columns(
    refs: list[str],
    sds: dict[str, float],
) -> dict[str, int]:
    """Convert SDS scores to column indices via :func:`_recursive_halving`.

    This is the public entry-point that wires in the standard page constants
    (:data:`ORIGIN_X`, :data:`_MAX_COLS`, :data:`GRID_COL_MM`,
    :data:`MAX_ROWS_PER_COL`) so callers do not need to repeat them.

    Parameters
    ----------
    refs:
        All component references.
    sds:
        ``{ref: sds_score}`` from :func:`compute_signal_distance_scores`.

    Returns
    -------
    dict[str, int]
        ``{ref: col_index}`` — monotone in SDS order.
    """
    return _recursive_halving(
        refs,
        sds,
        ORIGIN_X,
        ORIGIN_X + _MAX_COLS * GRID_COL_MM,
        max_per_col=MAX_ROWS_PER_COL,
        grid_col_mm=GRID_COL_MM,
    )


def build_signal_adjacency(ir: CircuitIR) -> dict[str, set[str]]:
    """Return the undirected signal-net adjacency graph for *ir*.

    Power nets are excluded so only signal paths influence the result.
    Public wrapper for the internal :func:`_build_signal_adjacency`.
    """
    return _build_signal_adjacency(ir)


def count_wire_crossings(
    positions: dict[str, tuple[float, float]],
    adjacency: dict[str, set[str]],
) -> int:
    """Count wire crossings in a schematic layout using the endpoint inversion heuristic.

    Two wires ``(A, B)`` and ``(C, D)`` are considered to **cross** when
    their left endpoints (lower x) are in opposite vertical order relative
    to their right endpoints, as defined by:

    * ``x(A) < x(C)`` and ``y(A) > y(C)`` (left-endpoint row inversion), OR
    * ``x(A) < x(C)`` and ``y(B) > y(D)`` (right-endpoint row inversion)

    Same-column adjacency (``x(A) == x(C)``) is excluded.

    Parameters
    ----------
    positions:
        ``{ref: (x_mm, y_mm)}`` for all components.
    adjacency:
        Undirected signal-net adjacency ``{ref: {neighbour_ref, …}}``.
        Only signal-net neighbours should be included (power nets excluded).

    Returns
    -------
    int
        Total number of crossing wire pairs.  Returns 0 for layouts with
        fewer than 2 edges.
    """
    # Build deduplicated edge list, each edge normalised (left, right) by x, then y.
    seen: set[tuple[str, str]] = set()
    edges: list[tuple[str, str]] = []
    for a, nbrs in adjacency.items():
        if a not in positions:
            continue
        for b in nbrs:
            if b not in positions:
                continue
            key = (min(a, b), max(a, b))
            if key not in seen:
                seen.add(key)
                xa, ya = positions[a]
                xb, yb = positions[b]
                # Normalize: left = lower x; ties broken by lower y.
                if (xa, ya) <= (xb, yb):
                    edges.append((a, b))
                else:
                    edges.append((b, a))

    n = len(edges)
    if n < 2:
        return 0

    crossings = 0
    for i in range(n):
        la, ra = edges[i]
        xl, yl = positions[la]
        xr, yr = positions[ra]
        for j in range(i + 1, n):
            lc, rc = edges[j]
            xcl, ycl = positions[lc]
            xcr, ycr = positions[rc]
            if xl < xcl and (yl > ycl or yr > ycr):
                # Edge i is left of edge j; row order inverted → crossing.
                crossings += 1
            elif xcl < xl and (ycl > yl or ycr > yr):
                # Edge j is left of edge i; row order inverted → crossing.
                crossings += 1
            # xl == xcl: same column — excluded per spec.

    return crossings


def barycentric_sort(
    by_col: dict[int, list[str]],
    adjacency: dict[str, set[str]],
    *,
    passes: int = 2,
) -> dict[int, list[str]]:
    """Return a copy of *by_col* with each column\'s member list sorted to minimise
    wire crossings using a two-pass barycentric sweep.

    Each full sweep consists of two direction passes:

    * **Pass 1 (left → right):** for each column ``k > 0``, sort members by
      the average row-index of their signal-adjacent neighbours in column
      ``k-1``.  Updates are sequential so column ``k`` sees the already-sorted
      order of column ``k-1``.
    * **Pass 2 (right → left):** symmetrically sort each column ``k <
      max_col`` by the average row-index of neighbours in column ``k+1``.

    The *row-index* of a component is its position (0-based) in the
    current column list, which changes as each column is sorted.

    When a component has no signal-adjacent neighbour in the reference column,
    its own current row-index is used as a neutral fallback so it stays
    stable relative to components that do have cross-column neighbours.

    Parameters
    ----------
    by_col:
        ``{col_index: [ref, …]}`` — initial column membership and ordering.
        The value lists are used to determine the initial row order only;
        the returned dict contains fresh sorted copies.
    adjacency:
        Undirected signal-net adjacency ``{ref: {neighbour_ref, …}}``.
        Power-net neighbours should be excluded by the caller.
    passes:
        Number of full sweeps (each sweep = one L→R pass + one R→L pass).
        Default is 2.

    Returns
    -------
    dict[int, list[str]]
        ``{col_index: [ref, …]}`` — same keys as *by_col* but with each
        list sorted to reduce crossings.
    """
    if not by_col:
        return {}

    sorted_cols = sorted(by_col)
    result: dict[int, list[str]] = {k: list(v) for k, v in by_col.items()}

    # ref → column index (static — only ordering within cols changes).
    ref_to_col: dict[str, int] = {ref: k for k, members in result.items() for ref in members}

    def _row_map() -> dict[str, int]:
        return {ref: i for k in sorted_cols for i, ref in enumerate(result[k])}

    def _avg_nbr_row(
        ref: str,
        target_col: int,
        row_map: dict[str, int],
    ) -> float:
        nbrs = [r for r in adjacency.get(ref, set()) if ref_to_col.get(r) == target_col]
        if nbrs:
            return sum(row_map[r] for r in nbrs) / len(nbrs)
        # No cross-col neighbour: use own row index for a stable neutral weight.
        return float(row_map.get(ref, 0))

    for _ in range(passes):
        # Pass 1: left → right.
        rm = _row_map()
        for i, k in enumerate(sorted_cols):
            if i == 0:
                # Column 0 has no left neighbour — skip in L→R pass.
                # It will be sorted in the R→L pass using col1 as the reference,
                # so subsequent sweeps see the R→L-improved col0 ordering.
                continue
            prev_k = sorted_cols[i - 1]
            result[k].sort(
                key=lambda r, _pk=prev_k, _rm=rm: (_avg_nbr_row(r, _pk, _rm), r)  # type: ignore[misc]  # mypy cannot infer lambda default-arg types
            )
            for j, ref in enumerate(result[k]):
                rm[ref] = j

        # Pass 2: right → left.
        rm = _row_map()
        for i in range(len(sorted_cols) - 2, -1, -1):
            k = sorted_cols[i]
            next_k = sorted_cols[i + 1]
            result[k].sort(
                key=lambda r, _nk=next_k, _rm=rm: (_avg_nbr_row(r, _nk, _rm), r)  # type: ignore[misc]  # mypy cannot infer lambda default-arg types
            )
            for j, ref in enumerate(result[k]):
                rm[ref] = j

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_signal_flow_layout(  # noqa: PLR0912, PLR0915
    ir: CircuitIR,
    halo: dict[str, str] | None = None,
    roles: Mapping[str, str] | None = None,
    strict: bool = False,
) -> dict[str, tuple[float, float]]:
    """Return ``{ref: (x, y)}`` placements for all components in *ir*.

    .. note::
        **Not used by the production pipeline.**  The live layout engine is
        :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.  This
        function is kept as a self-contained algorithm reference and test
        harness: it implements every layout rule (SDS recursive halving,
        barycentric crossing reduction, IC centering, decoupling-cap snap)
        in pure Python without requiring a Graphviz binary, making it
        convenient for unit-testing algorithm properties in isolation.

    Algorithm
    ---------
    1. Build an undirected adjacency graph keyed on reference designators.
       Two components are adjacent when they share at least one net.
    2. Assign column indices via one of two strategies:

       * **SDS recursive halving** (R2) — used when *roles* contains at
         least one input and one output connector.  Computes a Signal
         Distance Score for each component via BFS from input and output
         connectors, then assigns columns by recursively halving the
         SDS-sorted list.
     * **BFS fallback** — used only when *roles* is ``None``.

         Incomplete connector roles are treated as a configuration error
         and raise :class:`~kicad_pcb.errors.UserError` instead of silently
         degrading to BFS.

    3. Within each column, sort components using a two-pass barycentric sweep
       (:func:`_barycentric_sort`) to reduce wire crossings: pass 1 sorts
       left-to-right using neighbours' row positions in the previous column;
       pass 2 sorts right-to-left using the next column.
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

    # R2-2: prefer SDS-based recursive halving when connector roles are known.
    # Missing or incomplete roles are a configuration error; the old SDS→BFS
    # degradation path has been removed.
    if roles is not None:
        input_refs = [r for r, role in roles.items() if role == "input"]
        output_refs = [r for r, role in roles.items() if role == "output"]
        if not input_refs or not output_refs:
            missing_role = "input" if not input_refs else "output"
            raise UserError(
                "SDS layout requires both input and output connector roles",
                code=ErrorCode.IR_SEMANTIC_INVALID,
                details={
                    "missing_role": missing_role,
                    "roles": dict(roles),
                },
            )
        else:
            sds_scores = compute_signal_distance_scores(ir, roles)
            col = _recursive_halving(
                refs,
                sds_scores,
                ORIGIN_X,
                ORIGIN_X + _MAX_COLS * GRID_COL_MM,
                max_per_col=MAX_ROWS_PER_COL,
                grid_col_mm=GRID_COL_MM,
            )
    else:
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

    # R4-2: keep halo members adjacent to their anchor IC instead of forcing
    # exact same-column co-location.
    if halo:
        for halo_ref, anchor_ref in halo.items():
            if halo_ref in col and anchor_ref in col:
                old_col = col[halo_ref]
                anchor_col = col[anchor_ref]
                if old_col < anchor_col:
                    new_col = max(anchor_col - 1, 0)
                elif old_col > anchor_col or anchor_col <= 0:
                    new_col = anchor_col + 1
                else:
                    new_col = anchor_col - 1
                if old_col != new_col:
                    _log.debug(
                        "halo: softening %r column %d \u2192 %d (anchor %r col %d)",
                        halo_ref,
                        old_col,
                        new_col,
                        anchor_ref,
                        anchor_col,
                    )
                    col[halo_ref] = new_col

    # R3-2: two-pass barycentric sort across all columns to reduce wire crossings.
    by_col: dict[int, list[str]] = defaultdict(list)
    for r, c in col.items():
        by_col[c].append(r)

    # Use signal-only adjacency for barycentric weights (power nets excluded).
    by_col = barycentric_sort(by_col, sig_adj)

    # R6-2: crossing remediation — if crossing ratio ≥ 0.30, run up to
    # _MAX_REMEDIATION_SWEEPS total barycentric sweeps.
    _total_sig_wires = sum(len(v) for v in sig_adj.values()) // 2
    if _total_sig_wires > 0:
        for _sweep in range(_MAX_REMEDIATION_SWEEPS):
            _tentative: dict[str, tuple[float, float]] = {
                ref: (ORIGIN_X + ci * GRID_COL_MM, ORIGIN_Y + ri * GRID_ROW_MM)
                for ci, mems in by_col.items()
                for ri, ref in enumerate(mems)
            }
            _crossings = count_wire_crossings(_tentative, sig_adj)
            _ratio = _crossings / _total_sig_wires
            _log.debug(
                "R6: sweep %d crossings=%d wires=%d ratio=%.2f",
                _sweep + 1,
                _crossings,
                _total_sig_wires,
                _ratio,
            )
            if _ratio < 0.30 or _sweep == _MAX_REMEDIATION_SWEEPS - 1:
                break
            _log.debug(
                "R6: ratio %.2f >= 0.30; running remediation sweep %d",
                _ratio,
                _sweep + 2,
            )
            by_col = barycentric_sort(by_col, sig_adj)

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
        # Members are already in barycentric order; IC-centring (R4-3) then
        # re-interleaves them so op-amps land in the visual middle of the column.
        # Centre op-amps: split into IC refs and others, interleave so ICs
        # occupy the middle rows of the column.
        ic_refs = [r for r in members if any(r.upper().startswith(p) for p in _OP_AMP_PREFIXES)]
        other_refs = [r for r in members if r not in set(ic_refs)]
        # R4-3: halo members sit immediately adjacent to the IC; non-halo
        # others are pushed to the column edges so the feedback network
        # wraps tightly around the op-amp symbol.
        _halo_set = set(halo) if halo else set()
        halo_other = [r for r in other_refs if r in _halo_set]
        plain_other = [r for r in other_refs if r not in _halo_set]
        mid_plain = len(plain_other) // 2
        mid_halo = len(halo_other) // 2
        ordered = (
            plain_other[:mid_plain]
            + halo_other[:mid_halo]
            + ic_refs
            + halo_other[mid_halo:]
            + plain_other[mid_plain:]
        )
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

    Used by the Graphviz pipeline in ``graphviz_layout/__init__.py`` to order
    nodes within each ``{rank=same}`` DOT subgraph so that Graphviz starts its
    own barycentric heuristic from a signal-flow-aware ordering rather than an
    arbitrary alphabetical one.  This typically reduces wire crossings before
    any post-snap remediation pass runs.

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

    main_path = identify_main_signal_path(ir, tiers=tiers)
    main_path_rank = {ref: idx for idx, ref in enumerate(main_path)}

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
            result[tier] = sorted(
                members,
                key=lambda r: (0 if r in main_path_rank else 1, main_path_rank.get(r, 10_000), r),
            )
            continue
        prev_tier = sorted_tiers[i - 1]
        prev_members = result.get(prev_tier, [])
        # Score = sum of affinities to all previous-tier members.
        scores = {ref: sum(_affinity(ref, p) for p in prev_members) for ref in members}
        result[tier] = sorted(
            members,
            key=lambda r: (
                0 if r in main_path_rank else 1,
                main_path_rank.get(r, 10_000),
                -scores[r],
                r,
            ),
        )

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


def _normalize_passive_orientations_by_role(
    orientations: dict[str, int],
    ir: CircuitIR,
    block_layout: BlockLayout,
    positions: dict[str, tuple[float, float]],
) -> dict[str, int]:
    """Normalize passive orientations to ensure consistency within block roles.

    **Phase 9.2 — Normalize similar part presentation**

    When multiple passives are assigned to the same block role
    (e.g., FEEDBACK, INPUT, OUTPUT), they should have consistent visual
    presentation. This function analyzes orientations assigned to passives
    in each role and normalizes them to avoid arbitrary rotation differences.

    Algorithm:
    1. Group passives by their block role.
    2. For each role group with ≥2 passives:
       a. Count the orientations assigned: 0° vs 90° vs other.
       b. Identify the "canonical" orientation for that role:
          - FEEDBACK: prefer 90° (vertical feedback loop) if most are 90°,
            else check if all are in op-amp column (then 90°), else 0°.
          - INPUT/PRECONDITIONING/OUTPUT: prefer 0° (horizontal flow) if
            most are 0°, else check if layout strongly prefers 90°.
          - Other roles: no normalization enforced.
       c. Apply the canonical orientation to all passives in the group
          (unless there are strong position-based exceptions).
    3. Return the normalized orientations dict.

    Parameters
    ----------
    orientations:
        ``{ref: rotation_degrees}`` from compute_orientations.
    ir:
        Circuit IR (for component inspection).
    block_layout:
        BlockLayout with role assignments.
    positions:
        ``{ref: (x_mm, y_mm)}`` layout positions.

    Returns
    -------
    dict[str, int]
        Normalized orientations with consistency applied within role groups.
    """
    # Group passives by their role.
    role_groups: dict[BlockRole, list[str]] = {}
    for ref in orientations:
        role = block_layout.get_role(ref)
        if role is None:
            continue
        upper = ref.upper()
        # Only normalize passive prefixes
        if not any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            continue
        if role not in role_groups:
            role_groups[role] = []
        role_groups[role].append(ref)

    # Process each role group with ≥2 passives.
    result = dict(orientations)
    for role, refs in role_groups.items():
        if len(refs) < 2:
            # Single passive in role: no consistency issue.
            continue

        # Count current orientations in this role group.
        orientation_counts = Counter(orientations[ref] for ref in refs)
        most_common_rot, count_most = orientation_counts.most_common(1)[0]

        # Determine canonical orientation based on role.
        canonical_rot: int | None = None

        if role == BlockRole.FEEDBACK:
            # Feedback passives should prefer 90° (vertical loop).
            # If majority are 90°, use 90°.
            if most_common_rot == 90 or count_most > len(refs) / 2:
                canonical_rot = 90
            else:
                # Check if all feedback passives are in op-amp column.
                # If yes, enforce 90°.
                all_in_opamp_col = _all_feedback_in_opamp_column(refs, ir, positions)
                canonical_rot = 90 if all_in_opamp_col else 0

        elif role in (
            BlockRole.INPUT,
            BlockRole.PRECONDITIONING,
            BlockRole.OUTPUT,
        ):
            # Input/preconditioning/output should prefer 0° (horizontal flow).
            # Default to 0°; position heuristic may disagree, but flow
            # direction preference overrides for consistent visibility.
            canonical_rot = 0

        # Apply canonical orientation to all passives in group.
        if canonical_rot is not None:
            for ref in refs:
                result[ref] = canonical_rot

    return result


def _all_feedback_in_opamp_column(
    feedback_refs: list[str],
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
) -> bool:
    """Return True if all feedback passives are in the same column as an op-amp.

    Uses GRID_COL_MM / 2 as the "same column" tolerance.
    """
    if not feedback_refs or not positions:
        return False

    # Get x-coordinates of all feedback passives that have positions.
    feedback_xs = [positions[ref][0] for ref in feedback_refs if ref in positions]
    if not feedback_xs:
        return False

    # Get x-coordinates of all op-amps.
    opamp_xs = [
        positions[comp.ref][0]
        for comp in ir.components
        if any(comp.ref.upper().startswith(p) for p in _OP_AMP_PREFIXES) and comp.ref in positions
    ]
    if not opamp_xs:
        return False

    # Check if all feedback passives are within GRID_COL_MM/2 of any op-amp.
    tolerance = GRID_COL_MM / 2
    for fb_x in feedback_xs:
        near_opamp = any(abs(fb_x - opamp_x) < tolerance for opamp_x in opamp_xs)
        if not near_opamp:
            return False

    return True


def compute_orientations(  # noqa: PLR0912, PLR0913, PLR0915
    ir: CircuitIR,
    positions: dict[str, tuple[float, float]],
    tiers: dict[str, int] | None = None,
    roles: Mapping[str, str] | None = None,
    block_layout: BlockLayout | None = None,
) -> dict[str, int]:
    """Return ``{ref: rotation_degrees}`` orientation for every component.

    **Phase 9.1–9.2 — Orientation Conventions and Normalization**

    Component orientations support both function and reading flow. This function
    implements conventions that ensure schematics read like human-drafted circuits:
    - Resistors/capacitors in signal flow align with flow direction (horizontal
      for series, vertical for shunt/feedback).
    - Connectors face inward toward the circuit (input at 0°, output at 180°).
    - Op-amps maintain stable preferred orientation (0°: inputs left, output right).
    - Power/decoupling components orientations improve clarity (vertical for shunt).

    After computing individual orientations (Phase 9.1), Phase 9.2 normalizes them
    to ensure similar parts in the same functional role have consistent presentation.
    For example, all feedback resistors align together, all input coupling caps
    align together, preventing arbitrary rotation that harms readability.

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
    roles:
        Optional ``{ref: "input" | "output" | "unknown"}`` from
        :func:`~kicad_pcb.tier.classify_connector_roles`.  When provided,
        role takes precedence over tier for connector orientation: ``"output"``
        → 180°; ``"input"`` / ``"unknown"`` → 0°.
    block_layout:
        Optional :class:`~kicad_pcb.block_detection.BlockLayout` with functional
        block role assignments.  When provided, passive orientation is influenced
        by block role to support signal flow direction (Phase 4.3 / Phase 9.1).

    Rules (applied in priority order)
    -----------------------------------
    * **Connectors** (J/CON/P/SJ/TJ) — *Phase 9.1: Face inward*:
      - With *roles*: ``"output"`` → 180° (face left);
        ``"input"`` / ``"unknown"`` → 0° (face right).
      - With *tiers* (fallback): tier 0 → 0°; max tier → 180°;
        intermediate → 0°.
      - Without either: always 0°.
    * **Op-amps / ICs** (U/IC/OA) — *Phase 9.1: Stable orientation*:
      Always 0° — standard orientation keeps inputs on the left and output on
      the right, enabling rapid pattern recognition across schematics.
    * **Passives — block-role-aware** (R/C/L with *block_layout*) —
      *Phase 9.1*:
      - **Feedback role near op-amp**: prefer 90° (vertical) when positioned
        in same column as op-amp (supports vertical feedback loop).
      - **Input/preconditioning/output roles**: prefer 0° (horizontal) to
        support left-to-right signal flow unless position heuristic strongly
        disagrees.
      - Falls through to shunt topology or position heuristic when block role
        is absent or indeterminate.
    * **Passives — shunt topology** (R/C/L with ≥1 power-net pin AND ≥1
      signal-net pin) — *Phase 9.1: Show vertical connection*:
      90° (vertical) — bypass capacitors, pull-ups, pull-downs straddle power
      and signal. Vertical orientation visually shows the connection from
      signal wire down to the power rail.
    * **Passives — position heuristic** (R/C/L with all pins on signal nets):
      90° when the sum of |Δy| to signal-net neighbours exceeds the sum of
      |Δx|; otherwise 0°.  This orients in-column feedback or coupling
      components to match the dominant wire direction.
    * **Diodes** (D*) — *Forward-bias series placement*:
      Always 0° (anode left, cathode right).
    * **Default**: 0°.

    Power / ground nets (identified by :func:`_is_power_net_layout`) are excluded
    from the position-based neighbour calculation so they do not bias series
    passives.  They ARE used for the shunt-topology check above.

    See Also
    --------
    tests/unit/test_phase9_orientation.py
        Phase 9.1 orientation convention tests. Validates that:
        - Series passives prefer horizontal (0°) orientations
        - Shunt passives prefer vertical (90°) orientations
        - Feedback components near op-amps prefer vertical
        - Connectors face inward (0° for input, 180° for output)
        - Op-amps maintain consistent 0° orientation
        - Similar passives in same role have consistent orientations
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

        # Connectors: prefer role-based orientation; fall back to tier-based.
        if any(upper.startswith(pfx) for pfx in _SOURCE_PREFIXES):
            if roles is not None and ref in roles:
                result[ref] = 180 if roles[ref] == "output" else 0
            elif tiers is not None and _max_tier > 0:
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

            # Block-role-aware orientation (Phase 4.3): support signal flow direction.
            if block_layout is not None:
                from .block_detection import BlockRole  # noqa: PLC0415

                role = block_layout.get_role(ref)
                if role is not None:
                    # Feedback components: prefer vertical (90°) when positioned in the
                    # same column as a nearby op-amp (supports vertical feedback path).
                    if role == BlockRole.FEEDBACK:
                        # Find nearby op-amps in the same column (within GRID_COL_MM / 2).
                        if ref in positions:
                            x, _y = positions[ref]
                            opamp_refs = [
                                r
                                for r in ir.components
                                if any(r.ref.upper().startswith(p) for p in _OP_AMP_PREFIXES)
                            ]
                            for opamp in opamp_refs:
                                if opamp.ref in positions:
                                    ox, _oy = positions[opamp.ref]
                                    if abs(ox - x) < GRID_COL_MM / 2:
                                        result[ref] = 90
                                        continue
                    # Input/preconditioning/output roles: prefer horizontal (0°) to
                    # support left-to-right signal flow.
                    elif role in (
                        BlockRole.INPUT,
                        BlockRole.PRECONDITIONING,
                        BlockRole.OUTPUT,
                    ):
                        # Default to horizontal unless position heuristic strongly disagrees.
                        position_rot = _series_passive_rotation(ref, positions, adjacency)
                        # Only override if position heuristic is weak (sum |Δy| ≈ sum |Δx|).
                        if ref in positions:
                            x, y = positions[ref]
                            total_dx = total_dy = 0.0
                            for nbr in adjacency.get(ref, []):
                                if nbr in positions:
                                    nx, ny = positions[nbr]
                                    total_dx += abs(nx - x)
                                    total_dy += abs(ny - y)
                            # If vertical dominance is weak (ratio < 1.5), use horizontal.
                            if total_dx > 0 and total_dy / total_dx < 1.5:
                                result[ref] = 0
                                continue
                        result[ref] = position_rot
                        continue

            # Fallback: position heuristic for pure-signal (series) passives.
            result[ref] = _series_passive_rotation(ref, positions, adjacency)
            continue

        # Diodes (D*), and all other unmatched components default to 0°.
        result[ref] = 0

    # Phase 9.2: Normalize orientations by role to ensure similar parts
    # in the same stage have consistent visual presentation.
    if block_layout is not None:
        result = _normalize_passive_orientations_by_role(result, ir, block_layout, positions)

    return result


@dataclass
class ComponentAnnotation:
    """Metadata annotations produced by layout analysis passes.

    Attributes
    ----------
    feedback:
        ``True`` when the component has been identified as a feedback
        (back-edge) element by :func:`find_feedback_paths`.
    sds:
        Signal Distance Score in ``[0.0, 1.0]``:  0.0 = at the input
        connector, 1.0 = at the output connector, 0.5 = mid-chain or
        unknown.  Populated by :func:`find_feedback_paths` when *roles*
        are supplied.
    """

    feedback: bool = False
    sds: float = 0.5


def find_feedback_paths(
    ir: CircuitIR,
    tiers: dict[str, int],
    roles: Mapping[str, str] | None = None,
) -> dict[str, ComponentAnnotation]:
    """Detect passive components that act as feedback (back-edge) connections.

    A passive component ``C`` is classified as *feedback* when the two (or
    more) signal nets it connects to **share at least one other component**
    in common.  This is the topological fingerprint of a feedback connection:
    the same IC or connector appears on both of ``C``'s terminals, meaning
    ``C`` forms a loop from one pin of that component back to another pin of
    the same component.

    Classic example — inverting op-amp with feedback resistor R_fb:

    .. code-block:: text

        J1 ──[NET_IN]──► U1(inv-input)
                         U1 ──[NET_OUT]──► J2
              R_fb ──pin1──[NET_IN]──► (U1)
              R_fb ──pin2──[NET_OUT]──► (U1)

    Both NET_IN and NET_OUT contain U1, so the intersection is {U1} ≠ ∅
    → R_fb is detected as feedback.

    A series resistor between two distinct components does *not* share any
    component across its two nets, so it is correctly left unmarked.

    Parameters
    ----------
    ir:
        Parsed circuit IR.
    tiers:
        ``{ref: tier_index}`` from :func:`~kicad_pcb.tier.assign_tiers`.
        Currently used as context but the primary detection criterion is
        topological (shared component); reserved for future refinement.

    Returns
    -------
    dict[str, ComponentAnnotation]
        One entry per component in *ir*.  Only passive components with the
        feedback topology have ``annotation.feedback == True``; all others
        have ``annotation.feedback == False``.
    """
    # Collect signal nets (non-power, ≥2 pins).
    signal_nets = [n for n in ir.nets if not _is_power_net_layout(n.name) and len(n.pins) >= 2]
    signal_refs: set[str] = {p.ref for net in signal_nets for p in net.pins}

    # Build: component → list of signal nets it participates in.
    comp_to_nets: dict[str, list] = defaultdict(list)
    for net in signal_nets:
        for pin in net.pins:
            comp_to_nets[pin.ref].append(net)

    result: dict[str, ComponentAnnotation] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        # Only passives can be feedback components.
        if not any(upper.startswith(pfx) for pfx in _PASSIVE_PREFIXES):
            result[ref] = ComponentAnnotation(feedback=False)
            continue

        # Must have at least 2 distinct signal nets to form a loop.
        own_nets = comp_to_nets.get(ref, [])
        if len(own_nets) < 2 or ref not in signal_refs:
            result[ref] = ComponentAnnotation(feedback=False)
            continue

        # For each pair of signal nets C participates in, check whether
        # any OTHER component appears on both nets.  If so, C bridges two
        # pins of the same component → feedback loop topology.
        is_feedback = False
        for i, net_a in enumerate(own_nets):
            others_a = {p.ref for p in net_a.pins if p.ref != ref}
            for net_b in own_nets[i + 1 :]:
                others_b = {p.ref for p in net_b.pins if p.ref != ref}
                if others_a & others_b:
                    # Shared component found: C loops across the same device.
                    is_feedback = True
                    break
            if is_feedback:
                break

        result[ref] = ComponentAnnotation(feedback=is_feedback)

    # R1-3: populate SDS scores when connector roles are provided.
    if roles:
        sds_scores = compute_signal_distance_scores(ir, roles)
        for ref in list(result):
            result[ref] = ComponentAnnotation(
                feedback=result[ref].feedback,
                sds=sds_scores.get(ref, 0.5),
            )

    return result


def _compute_opamp_halo(  # noqa: PLR0912
    ir: CircuitIR,
    annotations: dict[str, ComponentAnnotation],
    tiers: dict[str, int],
) -> dict[str, str]:
    """Detect passive components that belong to an op-amp's halo network.

    A component is a **halo member** when ALL of the following hold:

    1. It is a passive (``R``, ``C``, or ``L`` prefix).
    2. ``annotations[ref].feedback is True`` (back-edge topology detected by
       :func:`find_feedback_paths`), **OR** all of its signal-net neighbours
       belong to exactly one IC and there are no other (non-IC) signal
       neighbours (*exclusive IC coupling*).
    3. It does *not* connect to any power net (not a shunt/bypass component).

    For each qualifying component the anchor IC is chosen as the IC in the
    signal neighbourhood that is closest in tier distance (alphabetical
    tiebreak).

    Parameters
    ----------
    ir:
        Circuit IR.
    annotations:
        Per-component annotations from :func:`find_feedback_paths`.
    tiers:
        ``{ref: tier_index}`` from :func:`~kicad_pcb.tier.assign_tiers`.
        Used to choose the closest-tier anchor when multiple ICs are
        signal-adjacent to the halo candidate.

    Returns
    -------
    dict[str, str]
        ``{halo_ref: anchor_ic_ref}`` — one entry per halo member.  Empty
        when the circuit has no op-amp halo topology.
    """
    # Build set of refs that appear on any power net (condition 3).
    power_refs: set[str] = set()
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            for pin in net.pins:
                power_refs.add(pin.ref)

    # Build signal-net neighbour sets per component.
    sig_nbrs: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net_layout(net.name):
            continue
        pin_refs_net = [p.ref for p in net.pins]
        for ref_i in pin_refs_net:
            for ref_j in pin_refs_net:
                if ref_i != ref_j:
                    sig_nbrs[ref_i].add(ref_j)

    result: dict[str, str] = {}
    for comp in ir.components:
        ref = comp.ref
        upper = ref.upper()

        # Condition 1: must be a passive (R, C, L).
        if not any(upper.startswith(p) for p in _PASSIVE_PREFIXES):
            continue

        # Condition 3: must not appear on any power net.
        if ref in power_refs:
            continue

        nbrs = sig_nbrs.get(ref, set())
        ic_set = {n for n in nbrs if any(n.upper().startswith(p) for p in _OP_AMP_PREFIXES)}
        if not ic_set:
            continue  # No IC anchor available.

        # Condition 2a: feedback annotation from find_feedback_paths.
        ann = annotations.get(ref)
        is_feedback = ann is not None and ann.feedback

        # Condition 2b: exclusive IC coupling — all signal neighbours are a
        # single IC with no other components mixed in.
        non_ic_nbrs = nbrs - ic_set
        is_exclusive = len(ic_set) == 1 and not non_ic_nbrs

        if not (is_feedback or is_exclusive):
            continue

        # Choose anchor IC: closest in tier distance, alphabetical tiebreak.
        anchor = min(
            ic_set,
            key=lambda n: (abs(tiers.get(n, 0) - tiers.get(ref, 0)), n),
        )
        result[ref] = anchor

    return result


# ---------------------------------------------------------------------------
# Stereo channel detection (Rule §8)
# ---------------------------------------------------------------------------

#: Channel label type — ``"L"``, ``"R"``, or ``"mono"``.
StereoChannel = Literal["L", "R", "mono"]

#: Regex matching the stereo-channel suffix at the end of a net name.
#: Captures ``_L``, ``-L``, ``_R``, ``-R`` (case-insensitive).
_STEREO_SUFFIX_RE: re.Pattern[str] = re.compile(r"[_-]([LR])$", re.IGNORECASE)


def detect_stereo_channels(
    ir: CircuitIR,
) -> dict[str, StereoChannel]:
    """Classify every component as left-channel, right-channel, or mono.

    Algorithm
    ---------
    For each component, collect all signal nets (non-power, \u22652 pins) that it
    participates in.  A net is *left-channel* when its name ends with ``_L``
    or ``-L`` (case-insensitive); similarly for ``_R`` / ``-R`` (right).

    Channel assignment rules:

    * **L** — at least one L-channel net, zero R-channel nets.
    * **R** — at least one R-channel net, zero L-channel nets.
    * **mono** — both L and R nets, or no stereo-suffix nets at all.

    This is the topological signature of a stereo headphone-amp pair:
    ``R1`` sits on ``IN_L`` only \u2192 ``R1`` is ``\"L\"``;  ``R2`` sits on ``IN_R``
    only \u2192 ``R2`` is ``\"R\"``;  ``J1`` (both channels) \u2192 ``\"mono\"``.

    Parameters
    ----------
    ir:
        Parsed circuit IR.

    Returns
    -------
    dict[str, StereoChannel]
        Maps every component reference to its channel.  All components in
        *ir* are present in the result; components with no signal-net
        connections receive ``\"mono\"``.
    """
    # Collect signal nets (non-power, \u22652 pins).
    signal_nets = [n for n in ir.nets if not _is_power_net_layout(n.name) and len(n.pins) >= 2]

    # Build: component \u2192 set of stereo channel labels from its signal nets.
    comp_channels: dict[str, set[str]] = {c.ref: set() for c in ir.components}
    for net in signal_nets:
        m = _STEREO_SUFFIX_RE.search(net.name)
        if m is None:
            continue  # no stereo suffix; doesn't affect channel assignment
        letter = m.group(1).upper()  # "L" or "R"
        for pin in net.pins:
            if pin.ref in comp_channels:
                comp_channels[pin.ref].add(letter)

    result: dict[str, StereoChannel] = {}
    for ref, letters in comp_channels.items():
        if letters == {"L"}:
            result[ref] = "L"
        elif letters == {"R"}:
            result[ref] = "R"
        else:
            result[ref] = "mono"

    return result
