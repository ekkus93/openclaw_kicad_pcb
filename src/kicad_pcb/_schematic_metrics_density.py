"""Local density, cluster detection, and block metrics for schematic analysis."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from kicad_pcb.block_detection import BlockLayout, BlockRole

from ._schematic_metrics_base import _resolve_placed_refs

if TYPE_CHECKING:
    from kicad_pcb.sch_doc import SchematicDoc


def compute_local_density(
    doc: SchematicDoc,
    *,
    radius_mm: float = 30.0,
) -> dict[str, float]:
    """Compute local density around each symbol (neighbors within radius).

    For each symbol, counts how many other symbols fall within *radius_mm*
    of its position.  Returns a mapping from symbol reference to neighbor
    count, which serves as a local crowding metric.

    High neighbor counts (e.g., ≥ 5 symbols within 30mm) indicate crowded
    areas that may benefit from spreading.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    radius_mm:
        Search radius around each symbol (default: 30mm, about 4 KiCad grid cells).

    Returns
    -------
    dict[str, float]:
        Mapping from symbol reference to neighbor count within radius.
    """
    symbols = doc.list_symbols()
    if not symbols:
        return {}

    positions: list[tuple[str, float, float]] = []
    for sym in symbols:
        try:
            ref = str(sym["ref"])  # type: ignore[arg-type]
            x = float(sym["x"])  # type: ignore[arg-type]
            y = float(sym["y"])  # type: ignore[arg-type]
            positions.append((ref, x, y))
        except (KeyError, TypeError, ValueError):
            continue

    density: dict[str, float] = {}
    radius_sq = radius_mm * radius_mm
    for i, (ref_i, x_i, y_i) in enumerate(positions):
        count = 0
        for j, (ref_j, x_j, y_j) in enumerate(positions):
            if i == j:
                continue
            dist_sq = (x_i - x_j) ** 2 + (y_i - y_j) ** 2
            if dist_sq <= radius_sq:
                count += 1
        density[ref_i] = float(count)

    return density


def detect_dense_clusters(
    doc: SchematicDoc,
    *,
    radius_mm: float = 30.0,
    threshold: int = 5,
) -> list[tuple[float, float, int]]:
    """Identify spatial clusters with high local density.

    Scans the schematic for regions where ≥ *threshold* symbols are packed
    within *radius_mm* of each other.  Returns cluster centers and their
    density counts, sorted by density (highest first).

    Used to identify crowded areas for Phase 2 crowding reduction.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    radius_mm:
        Cluster radius (default: 30mm).
    threshold:
        Minimum neighbor count to classify as "dense" (default: 5).

    Returns
    -------
    list[tuple[float, float, int]]:
        List of (center_x, center_y, neighbor_count) for each dense cluster,
        sorted by neighbor_count descending.
    """
    local_density = compute_local_density(doc, radius_mm=radius_mm)
    if not local_density:
        return []

    symbols = doc.list_symbols()
    sym_map = {str(sym["ref"]): sym for sym in symbols}  # type: ignore[arg-type]

    dense_refs = [ref for ref, count in local_density.items() if count >= threshold]
    if not dense_refs:
        return []

    clusters: list[tuple[float, float, int]] = []
    for ref in dense_refs:
        if ref not in sym_map:
            continue
        sym = sym_map[ref]
        x = float(sym["x"])  # type: ignore[arg-type]
        y = float(sym["y"])  # type: ignore[arg-type]
        count = int(local_density[ref])
        clusters.append((x, y, count))

    clusters.sort(key=lambda c: c[2], reverse=True)
    return clusters


def compute_block_separation(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> dict[tuple[BlockRole, BlockRole], float]:
    """Compute minimum spacing between functional blocks.

    Measures the smallest inter-symbol distance between each pair of
    functional blocks.  Helps enforce Phase 2.3 requirement that blocks
    maintain visual separation.

    Parameters
    ----------
    positions:
        Component positions dict from layout engine (ref → (x, y, rot)).
    block_layout:
        Block classification from block_detection.classify_circuit().

    Returns
    -------
    dict[tuple[BlockRole, BlockRole], float]:
        Mapping from (block_a, block_b) role pairs to minimum distance (mm)
        between any component in block_a and any component in block_b.
        Only includes pairs where both blocks have ≥ 1 component.
    """
    by_role: dict[BlockRole, list[tuple[float, float]]] = {}
    for ref, assignment in block_layout.assignments.items():
        for _resolved_ref, (x, y, _rot) in _resolve_placed_refs(positions, ref):
            role = assignment.role
            if role not in by_role:
                by_role[role] = []
            by_role[role].append((x, y))

    separations: dict[tuple[BlockRole, BlockRole], float] = {}
    roles = list(by_role.keys())
    for i, role_a in enumerate(roles):
        for role_b in roles[i + 1 :]:
            min_dist = float("inf")
            for x_a, y_a in by_role[role_a]:
                for x_b, y_b in by_role[role_b]:
                    dist = math.sqrt((x_a - x_b) ** 2 + (y_a - y_b) ** 2)
                    min_dist = min(min_dist, dist)
            if min_dist != float("inf"):
                separations[(role_a, role_b)] = min_dist
                separations[(role_b, role_a)] = min_dist

    return separations


def compute_block_role_spread(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
    *,
    tolerance_mm: float = 0.5,
) -> dict[str, dict[str, float | int]]:
    """Summarize x/y spread metrics for each functional block role.

    Returns JSON-serializable per-role stats so regression fixtures can track
    whether a role has collapsed into a narrow column or drifted across the page.
    """
    by_role: dict[BlockRole, list[tuple[float, float]]] = {}
    for ref, assignment in block_layout.assignments.items():
        for _resolved_ref, (x, y, _rot) in _resolve_placed_refs(positions, ref):
            by_role.setdefault(assignment.role, []).append((x, y))

    spread: dict[str, dict[str, float | int]] = {}
    for role, role_positions in by_role.items():
        xs = [x for x, _y in role_positions]
        ys = [y for _x, y in role_positions]
        column_count = len({int(x / tolerance_mm) for x in xs}) if xs else 0
        spread[role.value] = {
            "count": len(role_positions),
            "column_count": column_count,
            "min_x": min(xs),
            "max_x": max(xs),
            "width_mm": max(xs) - min(xs),
            "min_y": min(ys),
            "max_y": max(ys),
            "height_mm": max(ys) - min(ys),
        }

    return spread
