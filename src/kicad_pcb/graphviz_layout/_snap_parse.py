"""Input parsing and coordinate mapping for the snap pipeline.

Handles dot -Tplain output parsing, Graphviz→KiCad coordinate conversion,
and grid snapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

from ._snap_types import ORIGIN_X, ORIGIN_Y, PAGE_MAX_X, PAGE_MAX_Y, SCALE_MM_PER_GV

# ---------------------------------------------------------------------------
# dot -Tplain parser
# ---------------------------------------------------------------------------


def _parse_plain_positions(plain_output: str) -> dict[str, tuple[float, float]]:
    """Parse ``dot -Tplain`` output and return ``{node_name: (gv_x, gv_y)}``.

    The plain format line structure::

        node <name> <x> <y> <width> <height> <label> …

    We collect only ``node`` lines and only return component nodes (i.e. we
    skip lines whose name starts with ``net_``).
    """
    positions: dict[str, tuple[float, float]] = {}
    for line in plain_output.splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0] != "node":
            continue
        name = parts[1]
        if name.startswith("net_"):
            continue
        try:
            gv_x = float(parts[2])
            gv_y = float(parts[3])
        except ValueError:
            continue
        positions[name] = (gv_x, gv_y)
    return positions


# ---------------------------------------------------------------------------
# Coordinate mapping: Graphviz → KiCad mm
# ---------------------------------------------------------------------------


def _fit_to_page(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Proportionally shrink *positions* so all points fit within the A4 area.

    If any position lies outside the usable area (``origin_x..PAGE_MAX_X`` ×
    ``origin_y..PAGE_MAX_Y``), the entire layout is proportionally scaled down
    so that every position falls within bounds.  Relative distances are
    preserved.  Positions that already fit are returned unchanged.
    """
    if not positions:
        return positions
    max_x = max(pos[0] for pos in positions.values())
    max_y = max(pos[1] for pos in positions.values())
    avail_x = PAGE_MAX_X - origin_x  # available width after margins
    avail_y = PAGE_MAX_Y - origin_y  # available height after margins
    span_x = max_x - origin_x  # current width of laid-out content
    span_y = max_y - origin_y  # current height of laid-out content
    shrink = 1.0
    if span_x > 0 and span_x > avail_x:
        shrink = min(shrink, avail_x / span_x)
    if span_y > 0 and span_y > avail_y:
        shrink = min(shrink, avail_y / span_y)
    if shrink < 1.0:
        return {
            name: (
                round(origin_x + (pos[0] - origin_x) * shrink, 2),
                round(origin_y + (pos[1] - origin_y) * shrink, 2),
                None,
            )
            for name, pos in positions.items()
        }
    return positions


def _gv_to_kicad(
    gv_positions: dict[str, tuple[float, float]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    scale: float = SCALE_MM_PER_GV,
) -> dict[str, tuple[float, float, float | None]]:
    """Map Graphviz node positions to KiCad mm coordinates.

    Graphviz origin is bottom-left; KiCad origin is top-left.  We invert
    the y axis so that higher-ranked nodes appear at the top of the schematic.

    If the scaled positions would exceed the usable A4 area
    (:data:`PAGE_MAX_X` × :data:`PAGE_MAX_Y`) the entire layout is
    proportionally shrunk (preserving relative distances) until it fits
    (via :func:`_fit_to_page`).
    """
    if not gv_positions:
        return {}
    max_gv_y = max(y for _, y in gv_positions.values())
    result: dict[str, tuple[float, float, float | None]] = {}
    for node_name, (gv_x, gv_y) in gv_positions.items():
        x_mm = origin_x + gv_x * scale
        # Clamp to origin_y: dot occasionally places nodes slightly above the
        # reported bounding box (gv_y > max_gv_y), which would produce
        # y_mm < origin_y (above the top margin) without the clamp.
        y_mm = max(origin_y, round(origin_y + (max_gv_y - gv_y) * scale, 2))
        result[node_name] = (round(x_mm, 2), y_mm, None)
    return _fit_to_page(result, origin_x=origin_x, origin_y=origin_y)


# ---------------------------------------------------------------------------
# Grid snapping
# ---------------------------------------------------------------------------


def _snap(v: float, grid: float = 0.254) -> float:
    """Snap *v* to the nearest *grid* multiple (KiCad 10 mil snapping)."""
    return round(round(v / grid) * grid, 4)


def snap_positions(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Snap all (x, y) in *positions* to the nearest *grid* increment (mm).

    KiCad's snap grid is 50 mil (1.27 mm) by default.  Snapping avoids
    off-grid placements that make manual editing awkward.
    """
    return {
        ref: (
            round(round(x / grid) * grid, 4),
            round(round(y / grid) * grid, 4),
            rot,
        )
        for ref, (x, y, rot) in positions.items()
    }
