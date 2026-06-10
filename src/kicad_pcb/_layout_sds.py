"""Signal-flow layout: SDS scoring, recursive halving, and barycentric sort."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from ._layout_graph import (
    _MAX_COLS,
    _SDS_SENTINEL,
    GRID_COL_MM,
    MAX_ROWS_PER_COL,
    ORIGIN_X,
    _bfs_distances,
    _build_signal_adjacency,
    _find_decoupling_caps_layout,
)

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR


def compute_signal_distance_scores(
    ir: CircuitIR,
    roles: Mapping[str, str],
) -> dict[str, float]:
    """Compute Signal Distance Score (SDS) for every component in *ir*.

    The SDS captures where a component sits in the signal chain:
    0.0 = at the input, 1.0 = at the output, 0.5 = mid-chain.
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
    """Assign column indices by recursively halving the SDS-sorted component set."""
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
    """Convert SDS scores to column indices via :func:`_recursive_halving`."""
    return _recursive_halving(
        refs,
        sds,
        ORIGIN_X,
        ORIGIN_X + _MAX_COLS * GRID_COL_MM,
        max_per_col=MAX_ROWS_PER_COL,
        grid_col_mm=GRID_COL_MM,
    )


def count_wire_crossings(
    positions: dict[str, tuple[float, float]],
    adjacency: dict[str, set[str]],
) -> int:
    """Count wire crossings in a schematic layout using the endpoint inversion heuristic."""
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
            if xl < xcl and (yl > ycl or yr > ycr) or xcl < xl and (ycl > yl or ycr > yr):
                crossings += 1

    return crossings


def barycentric_sort(
    by_col: dict[int, list[str]],
    adjacency: dict[str, set[str]],
    *,
    passes: int = 2,
) -> dict[int, list[str]]:
    """Return a copy of *by_col* with each column sorted to minimise wire crossings."""
    if not by_col:
        return {}

    sorted_cols = sorted(by_col)
    result: dict[int, list[str]] = {k: list(v) for k, v in by_col.items()}

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
        return float(row_map.get(ref, 0))

    for _ in range(passes):
        rm = _row_map()
        for i, k in enumerate(sorted_cols):
            if i == 0:
                continue
            prev_k = sorted_cols[i - 1]
            result[k].sort(
                key=lambda r, _pk=prev_k, _rm=rm: (_avg_nbr_row(r, _pk, _rm), r)  # type: ignore[misc]
            )
            for j, ref in enumerate(result[k]):
                rm[ref] = j

        rm = _row_map()
        for i in range(len(sorted_cols) - 2, -1, -1):
            k = sorted_cols[i]
            next_k = sorted_cols[i + 1]
            result[k].sort(
                key=lambda r, _nk=next_k, _rm=rm: (_avg_nbr_row(r, _nk, _rm), r)  # type: ignore[misc]
            )
            for j, ref in enumerate(result[k]):
                rm[ref] = j

    return result
