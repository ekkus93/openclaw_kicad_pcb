"""Coordinate-space transforms for the Graphviz schematic layout engine.

Responsibilities
----------------
This module handles everything that happens *after* ``dot -Tplain`` produces
raw node positions:

1. **Parsing** — extract ``(gv_x, gv_y)`` from the ``dot -Tplain`` plain-text
   format (:func:`_parse_plain_positions`).

2. **Coordinate mapping** — convert Graphviz "inch" coordinates (origin
   bottom-left, y↑) to KiCad mm coordinates (origin top-left, y↓) and
   fit the result within an A4 page (:func:`_gv_to_kicad`,
   :func:`_fit_to_page`).

3. **Grid snapping** — quantise positions to the KiCad 50-mil (1.27 mm)
   grid (:func:`snap_positions`, :func:`_snap`).

4. **Post-layout specialised snaps** — rule-based position overrides that
   enforce schematic readability conventions after the grid snap:

   * :func:`_snap_power_symbols` — pin ``#PWR``/``#FLG`` symbols to the
     top or bottom page row.
   * :func:`_snap_feedback_components` — pull feedback passives above their
     anchor IC/connector.
   * :func:`_post_snap_decoupling_caps` — align bypass caps directly above
     their associated IC.
   * :func:`_apply_stereo_split` — compress L/R stereo components into the
     top or bottom half of the page.

Coordinate system
-----------------
``dot -Tplain`` reports node centres in inches with the origin at the
lower-left corner and y increasing upward.  KiCad uses mm with the origin
at the upper-left and y increasing downward.  The transform is::

    x_mm = ORIGIN_X + gv_x × SCALE_MM_PER_GV
    y_mm = ORIGIN_Y + (max_gv_y − gv_y) × SCALE_MM_PER_GV

Ordering of post-layout snap passes
------------------------------------
The canonical ordering in
:meth:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine.compute_symbol_positions`
is:

1. :func:`snap_positions` (grid)
2. :func:`_snap_power_symbols`
3. :func:`_snap_feedback_components` (if any feedback refs exist)
4. :func:`_apply_stereo_split` (if any L/R channels exist)
5. :func:`_post_snap_decoupling_caps` (if any decoupling caps exist)

Power symbols must run before feedback snap so that a ``#PWR`` ref that
shares a column with a feedback component is correctly clamped first.
Stereo split runs after feedback snap so that feedback-adjusted y values are
used as the input to channel compression.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .component_types import IC_PREFIXES as _IC_PREFIXES_CT
from .component_types import is_power_net as _is_power_net
from .layout import ComponentAnnotation as _ComponentAnnotation

# ---------------------------------------------------------------------------
# Page-layout constants
# ---------------------------------------------------------------------------

ORIGIN_X: float = 30.48  # mm — left margin on an A4 page
ORIGIN_Y: float = 50.80  # mm — top margin on an A4 page

# Usable schematic area on an A4 sheet (page is 297 × 210 mm).
# Leave a 10 mm gutter on the right and bottom edges.
PAGE_MAX_X: float = 287.0  # mm (297 - 10)
PAGE_MAX_Y: float = 200.0  # mm (210 - 10)

# Scale factor: mm per one "graph unit" in dot -Tplain output.
# dot -Tplain reports node centre coordinates in inches (not points).
# _LAY_SYMBOL_HALF_SIZE_MM = 5.08 mm, so symbols need ≥ 10.16 mm
# centre-to-centre to avoid LAY003.  With nodesep=0.5 + node height=0.5
# the minimum same-rank separation is 1.0 inch, so:
#   SCALE ≥ 10.16 mm / 1.0 in  →  use 20 mm/in for comfortable margins.
SCALE_MM_PER_GV: float = 20.0

# Vertical spacing between a decoupling capacitor and its associated IC.
# One KiCad symbol row = 300 mil = 7.62 mm (KiCad default body height).
GRID_ROW_MM: float = 7.62

# Minimum centre-to-centre distance that avoids a LAY003 overlap warning
# (symbol bounding box = ±5.08 mm, so touching copies are 10.16 mm apart).
# Used by _apply_stereo_split to push compressed same-column components apart.
_STEREO_DEOVERLAP_MIN_MM: float = 10.17  # 2 × 5.08 + ε

# Bottom inset for GND/VSS power symbols: keeps them clear of the lower margin
# and one grid row above the very bottom of the usable area.
_POWER_BOTTOM_MARGIN_MM: float = 20.0

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
        y_mm = origin_y + (max_gv_y - gv_y) * scale
        # Snap to 0.01 mm for readability
        result[node_name] = (round(x_mm, 2), round(y_mm, 2), None)
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


# ---------------------------------------------------------------------------
# Post-layout specialised snap passes
# ---------------------------------------------------------------------------


def _snap_power_symbols(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp ``#PWR`` and ``#FLG`` power symbols to the top or bottom page row.

    Identifies all components whose ref begins with ``#PWR`` or ``#FLG``
    (KiCad global power-net and PWR_FLAG markers) and pins their y-coordinate
    to one of two rows:

    * **GND-type** (value upper-cased equals or starts with ``GND``, ``AGND``,
      ``DGND``, ``PGND``, ``SGND``, ``VSS``, or ``0V``) →
      ``y = page_max_y - _POWER_BOTTOM_MARGIN_MM`` (bottom row, clear of the
      lower margin).
    * **All other power symbols** (VCC, VDD, VBAT, VREF, PWR_FLAG, etc.) →
      ``y = origin_y`` (top row).

    The x-coordinate is preserved so that each power symbol stays above or
    below the component it shares a net with in the Graphviz layout.
    Components not present in *positions* are silently skipped.
    """
    _GND_STARTS: tuple[str, ...] = ("GND", "AGND", "DGND", "PGND", "SGND", "VSS", "0V")
    result = dict(positions)
    for comp in ir.components:
        ref = comp.ref
        if not (ref.startswith("#PWR") or ref.startswith("#FLG")):
            continue
        if ref not in result:
            continue
        val = (comp.value or "").upper()
        is_gnd = any(val == g or val.startswith(g) for g in _GND_STARTS)
        target_y = round(page_max_y - _POWER_BOTTOM_MARGIN_MM, 2) if is_gnd else origin_y
        x, _, rot = result[ref]
        result[ref] = (x, target_y, rot)
    return result


def _snap_feedback_components(
    positions: dict[str, tuple[float, float, float | None]],
    annotations: dict[str, _ComponentAnnotation],
    ir: CircuitIR,
) -> dict[str, tuple[float, float, float | None]]:
    """Place feedback components visually above their nearest IC/connector anchor.

    For each component annotated ``feedback=True``, locates the
    IC or connector that shares a signal net with it and snaps the feedback
    component to ``anchor_y - GRID_ROW_MM``.  This produces the U-bend visual
    convention (feedback resistors float above the amplifier stage).

    The x-coordinate is preserved.  Components absent from *positions* or
    with no IC/connector neighbour in *positions* are silently skipped.
    """
    _anchor_prefixes = _IC_PREFIXES_CT + _CONNECTOR_PREFIXES_CT

    signal_nets = [n for n in ir.nets if not _is_power_net(n.name) and len(n.pins) >= 2]

    # Build: component → set of direct signal-net neighbours.
    comp_nbrs: dict[str, set[str]] = defaultdict(set)
    for net in signal_nets:
        for pin in net.pins:
            for other in net.pins:
                if other.ref != pin.ref:
                    comp_nbrs[pin.ref].add(other.ref)

    result = dict(positions)
    for comp in ir.components:
        ref = comp.ref
        ann = annotations.get(ref)
        if ann is None or not ann.feedback or ref not in result:
            continue

        # Find an anchor in the signal-net neighbourhood.  Prefer IC/connector
        # over passive neighbours; fall back to any positioned neighbour.
        anchor_y: float | None = None
        priority_nbrs = sorted(comp_nbrs.get(ref, []))
        for nbr in priority_nbrs:  # IC/connector pass
            if any(nbr.upper().startswith(pfx) for pfx in _anchor_prefixes) and nbr in result:
                anchor_y = result[nbr][1]
                break
        if anchor_y is None:
            for nbr in priority_nbrs:  # fallback: any positioned neighbour
                if nbr in result:
                    anchor_y = result[nbr][1]
                    break

        if anchor_y is None:
            continue

        x, _, rot = result[ref]
        result[ref] = (x, round(anchor_y - GRID_ROW_MM, 2), rot)

    return result


def _post_snap_decoupling_caps(
    positions: dict[str, tuple[float, float, float | None]],
    decoupling_map: dict[str, str],
) -> dict[str, tuple[float, float, float | None]]:
    """Snap each decoupling cap to sit directly above its associated IC.

    Sets the cap's x-coordinate to match the IC's x-coordinate and offsets
    the cap's y-coordinate by ``-GRID_ROW_MM`` (one KiCad symbol row above
    the IC, given that y increases downward in KiCad coordinates).

    Caps or ICs not present in *positions* are silently skipped (e.g. if the
    cap was not returned by Graphviz because it was isolated).
    """
    result = dict(positions)
    for cap_ref, ic_ref in decoupling_map.items():
        if cap_ref not in result or ic_ref not in result:
            continue
        ic_x, ic_y, _ = result[ic_ref]
        result[cap_ref] = (round(ic_x, 2), round(ic_y - GRID_ROW_MM, 2), None)
    return result


def _apply_stereo_split(
    positions: dict[str, tuple[float, float, float | None]],
    channels: Mapping[str, str],
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Remap y-coordinates to enforce stereo top-half / bottom-half split.

    Components classified as *left-channel* are compressed into the top
    45 % of the usable page height; *right-channel* components occupy the
    bottom band starting at 55 % of the usable page height.  *Mono* /
    unclassified components are left at their original y position.

    Formulae (as specified in Rule §8):

    * L: ``y_final = origin_y + (y_relative × 0.45)``
    * R: ``y_final = origin_y + page_height × 0.55 + (y_relative × 0.45)``
    * mono: unchanged

    where ``y_relative = y − origin_y`` and
    ``page_height = page_max_y − origin_y``.

    Parameters
    ----------
    positions:
        Current KiCad mm positions from ``_gv_to_kicad``.
    channels:
        ``{ref: channel}`` from :func:`detect_stereo_channels`.
    origin_y:
        Top of the usable schematic area (default :data:`ORIGIN_Y`).
    page_max_y:
        Bottom of the usable schematic area (default :data:`PAGE_MAX_Y`).
    """
    # Fast-path: skip entirely when no L or R components are present.
    if not any(v in ("L", "R") for v in channels.values()):
        return positions

    page_height = page_max_y - origin_y
    result: dict[str, tuple[float, float, float | None]] = {}
    for ref, (x, y, rot) in positions.items():
        channel = channels.get(ref, "mono")
        y_rel = y - origin_y
        if channel == "L":
            y_new = origin_y + y_rel * 0.45
        elif channel == "R":
            y_new = origin_y + page_height * 0.55 + y_rel * 0.45
        else:
            y_new = y
        result[ref] = (x, round(y_new, 2), rot)

    # Deoverlap pass: components in the same x-column may end up closer than
    # 10.16 mm after y-compression.  Sort each column by ascending y and push
    # any pair that would violate LAY003 apart.
    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in result.items():
        by_x[x].append(ref)
    for group in by_x.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: result[r][1])
        for i in range(1, len(group)):
            prev_ref = group[i - 1]
            curr_ref = group[i]
            px, py, pr = result[prev_ref]
            cx, cy, cr = result[curr_ref]
            if cy - py < _STEREO_DEOVERLAP_MIN_MM:
                result[curr_ref] = (cx, round(py + _STEREO_DEOVERLAP_MIN_MM, 2), cr)

    return result


# ---------------------------------------------------------------------------
# Composite snap coordinator
# ---------------------------------------------------------------------------


def _apply_post_layout_snaps(  # noqa: PLR0913
    result: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    feedback_refs: set[str],
    annotations: dict[str, _ComponentAnnotation],
    channels: Mapping[str, str],
    decoupling_map: dict[str, str],
) -> dict[str, tuple[float, float, float | None]]:
    """Apply all post-layout positional corrections in canonical order.

    The five passes must run in the order shown — see the module docstring
    for the rationale behind each ordering constraint:

    1. :func:`snap_positions` — quantise to the KiCad 50-mil grid.
    2. :func:`_snap_power_symbols` — clamp ``#PWR``/``#FLG`` to top/bottom row.
    3. :func:`_snap_feedback_components` — pull feedback passives above anchor IC
       (skipped when *feedback_refs* is empty).
    4. :func:`_apply_stereo_split` — compress L/R components into page halves
       (skipped when no L or R channel is present in *channels*).
    5. :func:`_post_snap_decoupling_caps` — co-locate bypass caps above their IC
       (skipped when *decoupling_map* is empty).
    """
    result = snap_positions(result)
    result = _snap_power_symbols(result, ir)
    if feedback_refs:
        result = _snap_feedback_components(result, annotations, ir)
    if any(v in ("L", "R") for v in channels.values()):
        result = _apply_stereo_split(result, channels)
    if decoupling_map:
        result = _post_snap_decoupling_caps(result, decoupling_map)
    return result
