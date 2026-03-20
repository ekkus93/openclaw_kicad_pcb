"""Schematic readability metrics.

Provides lightweight, read-only analysis functions over a parsed
:class:`~kicad_pcb.sch_doc.SchematicDoc` (or its root ``ListNode``).  All
functions are pure — they never mutate the document.

Typical usage::

    from pathlib import Path
    from kicad_pcb.sch_doc import SchematicDoc
    from kicad_pcb.schematic_metrics import (
        average_symbol_spacing,
        count_distinct_x_columns,
        count_global_labels,
        count_power_symbols,
        count_short_wire_segments,
        page_region_density,
        run_layout_lints,
        wire_stub_ratio,
    )

    doc = SchematicDoc.load(Path("OpenClaw_Managed.kicad_sch"))
    print(count_distinct_x_columns(doc))
    print(count_global_labels(doc, text="GND"))
    print(count_power_symbols(doc))
    print(count_short_wire_segments(doc))
    print(average_symbol_spacing(doc))
    print(page_region_density(doc))
    print(run_layout_lints(doc))
    print(wire_stub_ratio(doc))

These helpers are used by acceptance tests (Phase 6 golden tests) to enforce
readability criteria on generated schematics.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, TypeVar

from kicad_pcb.block_detection import BlockLayout, BlockRole

if TYPE_CHECKING:
    from kicad_pcb.sch_doc import SchematicDoc

from kicad_pcb.lint.helpers import _collect_wire_segments
from kicad_pcb.lint.sch import LintIssue, lint_schematic_layout
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import walk

__all__ = [
    "average_symbol_spacing",
    "compute_block_separation",
    "compute_block_role_spread",
    "compute_local_density",
    "count_distinct_x_columns",
    "count_global_labels",
    "count_non_power_symbols_in_same_x_column_as",
    "count_power_symbols",
    "count_refs_in_same_x_column_as",
    "count_short_wire_segments",
    "detect_dense_clusters",
    "page_region_density",
    "run_layout_lints",
    "wire_stub_ratio",
]

T = TypeVar("T")


def _symbol_ref_positions(
    doc: SchematicDoc,
    *,
    exclude_power_symbols: bool = False,
) -> dict[str, tuple[float, float]]:
    """Return a mapping of symbol ref to (x, y) position.

    Power symbols can be excluded because they often share crowded support
    lanes and would distort readability metrics aimed at placed components.
    """
    positions: dict[str, tuple[float, float]] = {}
    for sym in doc.list_symbols():
        ref_raw = sym.get("ref")
        x_raw = sym.get("x")
        y_raw = sym.get("y")
        if not isinstance(ref_raw, str):
            continue
        if exclude_power_symbols and ref_raw.startswith("#PWR"):
            continue
        if not isinstance(x_raw, (float, int)) or not isinstance(y_raw, (float, int)):
            continue
        positions[ref_raw] = (float(x_raw), float(y_raw))
    return positions


def _resolve_placed_refs(
    mapping: dict[str, T],
    ref: str,
) -> list[tuple[str, T]]:
    """Resolve a base device ref to matching placed-unit refs when needed."""

    if ref in mapping:
        return [(ref, mapping[ref])]

    unit_suffix = re.compile(rf"^{re.escape(ref)}[A-Z]+$")
    return sorted(
        (
            (candidate_ref, value)
            for candidate_ref, value in mapping.items()
            if unit_suffix.match(candidate_ref)
        ),
        key=lambda item: item[0],
    )


def _anchor_x_coordinate(
    mapping: dict[str, tuple[float, float]],
    anchor_ref: str,
) -> tuple[float, set[str]]:
    """Return a stable x anchor for exact refs or placed-unit siblings."""

    matches = _resolve_placed_refs(mapping, anchor_ref)
    if not matches:
        msg = f"Anchor ref not found in schematic: {anchor_ref}"
        raise ValueError(msg)

    xs = sorted(value[0] for _ref, value in matches)
    anchor_x = xs[len(xs) // 2]
    return anchor_x, {ref for ref, _value in matches}


def count_distinct_x_columns(
    doc: SchematicDoc,
    *,
    tolerance_mm: float = 0.5,
) -> int:
    """Return the number of visually distinct x-column positions.

    Two symbols are considered to share a column when their x-coordinates
    differ by less than *tolerance_mm*.  The algorithm divides the x axis
    into buckets of width *tolerance_mm* and counts non-empty buckets.

    A higher column count indicates better horizontal spreading of components
    across the schematic — a signal-flow layout should produce ≥ 4 columns
    for a multi-stage circuit.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    tolerance_mm:
        Column-width tolerance in millimetres (default: 0.5 mm, approximately
        two KiCad grid steps).

    Returns
    -------
    int
        Number of distinct x-column buckets that contain at least one symbol.
    """
    symbols = doc.list_symbols()
    if not symbols:
        return 0
    buckets: set[int] = set()
    for sym in symbols:
        x = float(sym["x"])  # type: ignore[arg-type]
        buckets.add(int(x / tolerance_mm))
    return len(buckets)


def count_refs_in_same_x_column_as(
    doc: SchematicDoc,
    anchor_ref: str,
    *,
    tolerance_mm: float = 0.5,
    refs: set[str] | None = None,
    include_anchor: bool = False,
) -> int:
    """Count refs whose x-position falls in the same visual column as anchor_ref.

    This is useful for detecting layout collapse around an anchor component,
    such as too many passives sharing the op-amp column.
    """
    positions = _symbol_ref_positions(doc)
    anchor_x, anchor_refs = _anchor_x_coordinate(positions, anchor_ref)
    count = 0
    for ref, (x, _y) in positions.items():
        if refs is not None and ref not in refs:
            continue
        if ref in anchor_refs and not include_anchor:
            continue
        if abs(x - anchor_x) <= tolerance_mm:
            count += 1
    return count


def count_non_power_symbols_in_same_x_column_as(
    doc: SchematicDoc,
    anchor_ref: str,
    *,
    tolerance_mm: float = 0.5,
    include_anchor: bool = False,
) -> int:
    """Count non-power symbols sharing the anchor component's x column."""
    positions = _symbol_ref_positions(doc, exclude_power_symbols=True)
    anchor_x, anchor_refs = _anchor_x_coordinate(positions, anchor_ref)
    return sum(
        1
        for ref, (x, _y) in positions.items()
        if (include_anchor or ref not in anchor_refs) and abs(x - anchor_x) <= tolerance_mm
    )


def count_global_labels(
    doc: SchematicDoc,
    *,
    text: str = "GND",
) -> int:
    """Return the number of ``(global_label "text" …)`` nodes in *doc*.

    KiCad global labels are used to connect power nets (GND, VCC, etc.) across
    schematic sheets.  For a single-sheet design a lower count indicates
    cleaner power routing — ideally replaced by power symbols.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    text:
        The label text to match (case-sensitive, default ``"GND"``).

    Returns
    -------
    int
        Count of matching ``(global_label "text" …)`` nodes in the schematic.
    """
    count = 0
    for node in walk(doc.root):
        if (
            isinstance(node, ListNode)
            and node.key == "global_label"
            and len(node.items) >= 2  # noqa: PLR2004
            and isinstance(node.items[1], StringNode)
            and node.items[1].value == text
        ):
            count += 1
    return count


def run_layout_lints(doc: SchematicDoc) -> list[LintIssue]:
    """Run all layout lint rules and return the resulting issues.

    Thin wrapper around :func:`~kicad_pcb.lint.sch.lint_schematic_layout` that
    accepts a :class:`~kicad_pcb.sch_doc.SchematicDoc` directly (rather than a
    raw ``ListNode``).

    Parameters
    ----------
    doc:
        The schematic document to lint.

    Returns
    -------
    list[LintIssue]
        All lint issues found (may be empty for a clean schematic).
    """
    return lint_schematic_layout(doc.root)


def wire_stub_ratio(
    doc: SchematicDoc,
    *,
    stub_len_mm: float = 5.08,
    tol: float = 0.2,
) -> float:
    """Return the fraction of all wire segments that are pin stubs.

    A wire segment is classified as a *stub* when its Euclidean length is
    ≤ ``stub_len_mm + tol``.  The default ``stub_len_mm = 5.08`` corresponds
    to 200 mil — the standard KiCad pin-stub extension length.

    A lower stub ratio indicates better direct-wire routing.  A schematic
    where every net is represented by label stubs will have a ratio close to
    1.0; a fully wired schematic will have a ratio close to 0.0 (only the
    mandatory pin-end stubs remain).

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    stub_len_mm:
        Maximum length (mm) for a wire to be classified as a stub.
        Defaults to 5.08 mm (200 mil).
    tol:
        Tolerance added to *stub_len_mm* to absorb floating-point imprecision.
        Defaults to 0.2 mm.

    Returns
    -------
    float
        Ratio of stub wires to total wires, in ``[0.0, 1.0]``.
        Returns ``0.0`` if there are no wire segments.
    """
    segs = _collect_wire_segments(doc.root.items)
    if not segs:
        return 0.0
    threshold = stub_len_mm + tol
    stubs = sum(1 for x1, y1, x2, y2 in segs if math.hypot(x2 - x1, y2 - y1) <= threshold)
    return stubs / len(segs)


def count_power_symbols(
    doc: SchematicDoc,
    *,
    power_pin_name: str = "GND",
) -> int:
    """Return the number of power symbol instances in the schematic.

    Power symbols are special schematic symbols with ``(power yes)`` property
    that connect to power nets (GND, VCC, +5V, etc.).  A lower count indicates
    cleaner power routing with fewer visual ground/power flag repetitions.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    power_pin_name:
        The power net name to match (case-sensitive, default ``"GND"``).
        Note: This implementation counts power symbols by looking for the
        ``(power yes)`` marker, regardless of pin name.

    Returns
    -------
    int
        Count of power symbol instances in the schematic.
    """
    count = 0
    for node in walk(doc.root):
        if isinstance(node, ListNode) and node.key == "symbol":
            # Look for (power yes) child node
            for child in node.items:
                if (
                    isinstance(child, ListNode)
                    and child.key == "power"
                    and len(child.items) >= 2  # noqa: PLR2004
                    and isinstance(child.items[1], StringNode)
                    and child.items[1].value == "yes"
                ):
                    count += 1
                    break
    return count


def count_short_wire_segments(
    doc: SchematicDoc,
    *,
    threshold_mm: float = 10.0,
) -> int:
    """Return the number of short wire segments (jogs) in the schematic.

    Short wire segments contribute to a "jaggy" or over-routed appearance.
    A lower count indicates cleaner, more direct routing with fewer
    unnecessary jogs.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    threshold_mm:
        Maximum length (mm) for a wire to be classified as short.
        Defaults to 10.0 mm (approximately 400 mil).

    Returns
    -------
    int
        Count of wire segments shorter than *threshold_mm*.
    """
    segs = _collect_wire_segments(doc.root.items)
    return sum(1 for x1, y1, x2, y2 in segs if math.hypot(x2 - x1, y2 - y1) <= threshold_mm)


def average_symbol_spacing(
    doc: SchematicDoc,
) -> float:
    """Return the average nearest-neighbor distance between symbols.

    A higher average spacing indicates less crowding and better whitespace
    distribution.  This metric helps detect overly dense regions.

    Parameters
    ----------
    doc:
        The schematic document to analyse.

    Returns
    -------
    float
        Average nearest-neighbor distance in millimetres.
        Returns 0.0 if there are fewer than 2 symbols.
    """
    symbols = doc.list_symbols()
    if len(symbols) < 2:  # noqa: PLR2004
        return 0.0

    # Extract (x, y) positions
    positions = [(float(sym["x"]), float(sym["y"])) for sym in symbols]  # type: ignore[arg-type]

    # Compute nearest-neighbor distance for each symbol
    total_distance = 0.0
    for i, (x1, y1) in enumerate(positions):
        min_dist = float("inf")
        for j, (x2, y2) in enumerate(positions):
            if i != j:
                dist = math.hypot(x2 - x1, y2 - y1)
                min_dist = min(min_dist, dist)
        total_distance += min_dist

    return total_distance / len(symbols)


def page_region_density(
    doc: SchematicDoc,
    *,
    regions: int = 4,
) -> dict[str, float]:
    """Return symbol density by page quadrant or region.

    Divides the schematic page into *regions* quadrants (default 4: top-left,
    top-right, bottom-left, bottom-right) and computes the density (symbols
    per unit area) in each region.  Helps identify unbalanced page composition
    and crowded areas.

    Parameters
    ----------
    doc:
        The schematic document to analyse.
    regions:
        Number of regions to divide the page into.  Currently only supports
        4 (quadrants).  Future versions may support finer-grained grids.

    Returns
    -------
    dict[str, float]
        Mapping from region name (e.g., ``"top_left"``) to symbol count in
        that region.  Density is computed as count / total_symbols.

    Raises
    ------
    ValueError:
        If *regions* is not 4 (only quadrants are currently supported).
    """
    if regions != 4:  # noqa: PLR2004
        msg = "Only 4-region (quadrant) division is currently supported"
        raise ValueError(msg)

    symbols = doc.list_symbols()
    if not symbols:
        return {"top_left": 0.0, "top_right": 0.0, "bottom_left": 0.0, "bottom_right": 0.0}

    # Compute bounding box
    positions = [(float(sym["x"]), float(sym["y"])) for sym in symbols]  # type: ignore[arg-type]
    xs = [x for x, _ in positions]
    ys = [y for _, y in positions]
    x_mid = (min(xs) + max(xs)) / 2
    y_mid = (min(ys) + max(ys)) / 2

    # Count symbols in each quadrant
    counts = {"top_left": 0, "top_right": 0, "bottom_left": 0, "bottom_right": 0}
    for x, y in positions:
        if x <= x_mid and y <= y_mid:
            counts["top_left"] += 1
        elif x > x_mid and y <= y_mid:
            counts["top_right"] += 1
        elif x <= x_mid and y > y_mid:
            counts["bottom_left"] += 1
        else:
            counts["bottom_right"] += 1

    # Return as density fractions
    total = len(symbols)
    return {region: count / total for region, count in counts.items()}


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

    # Extract positions with refs
    positions: list[tuple[str, float, float]] = []
    for sym in symbols:
        try:
            ref = str(sym["ref"])  # type: ignore[arg-type]
            x = float(sym["x"])  # type: ignore[arg-type]
            y = float(sym["y"])  # type: ignore[arg-type]
            positions.append((ref, x, y))
        except (KeyError, TypeError, ValueError):
            continue

    # Compute neighbor counts
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

    # Find symbols exceeding threshold
    dense_refs = [ref for ref, count in local_density.items() if count >= threshold]
    if not dense_refs:
        return []

    # Extract cluster centers
    clusters: list[tuple[float, float, int]] = []
    for ref in dense_refs:
        if ref not in sym_map:
            continue
        sym = sym_map[ref]
        x = float(sym["x"])  # type: ignore[arg-type]
        y = float(sym["y"])  # type: ignore[arg-type]
        count = int(local_density[ref])
        clusters.append((x, y, count))

    # Sort by density descending
    clusters.sort(key=lambda c: c[2], reverse=True)
    return clusters


def compute_block_separation(
    positions: dict[str, tuple[float, float, float | None]],
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
    # Group components by block role
    by_role: dict[BlockRole, list[tuple[float, float]]] = {}
    for ref, assignment in block_layout.assignments.items():
        for _resolved_ref, (x, y, _rot) in _resolve_placed_refs(positions, ref):
            role = assignment.role
            if role not in by_role:
                by_role[role] = []
            by_role[role].append((x, y))

    # Compute minimum separation between each block pair
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
                # Store both orderings for easy lookup
                separations[(role_a, role_b)] = min_dist
                separations[(role_b, role_a)] = min_dist

    return separations


def compute_block_role_spread(
    positions: dict[str, tuple[float, float, float | None]],
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
