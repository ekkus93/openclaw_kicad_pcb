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
   * :func:`_compact_y_gap` — collapse the largest vertical gap in the
     layout for non-power-symbol components so the circuit appears as one
     connected region (fixes the "split circuit" artifact that arises when
     isolated source-tier connectors land far from the amp body).
   * :func:`_deoverlap_positions` — push any components that share the
     same grid cell apart after all previous snaps complete (fixes grid
     collisions introduced by snapping or compression).

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
2b. :func:`_snap_connectors_to_ic_y` (Rule 2: connector y-alignment)
3. :func:`_snap_feedback_components` (if any feedback refs exist)
4. :func:`_apply_stereo_split` (if any L/R channels exist)
5. :func:`_compact_y_gap` (always; no-op when gap ≤ threshold)
6. :func:`_post_snap_decoupling_caps` (if any decoupling caps exist)
7. :func:`_deoverlap_positions` (always; final guard against grid collisions)

Power symbols must run before connector-y-snap so that ``#PWR``/``#FLG``
refs are already at their fixed rows before connectors compute their median.
Connector-y-snap runs before feedback snap so feedback-adjusted y values
take a correctly-anchored connector y as their starting point.
Stereo split runs after feedback snap so that feedback-adjusted y values are
used as the input to channel compression.
``_compact_y_gap`` runs before decoupling caps so that bypass-cap y positions
are relative to the compacted IC y values.
``_deoverlap_positions`` runs last so it resolves every collision regardless
of which earlier pass introduced it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR

from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import IC_PREFIXES as _IC_PREFIXES_CT
from ..component_types import is_power_net as _is_power_net
from ..layout import ComponentAnnotation as _ComponentAnnotation

# ---------------------------------------------------------------------------
# Connector-classification helpers (used by _snap_connectors_to_ic_y)
# ---------------------------------------------------------------------------


def _is_ic_ref(ref: str) -> bool:
    """Return True if *ref* looks like an IC designator (U*, IC*, OA*, OP*, etc)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _IC_PREFIXES_CT)


def _is_connector_ref(ref: str) -> bool:
    """Return True if *ref* looks like a connector designator (J*, P*, CON*, etc)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CONNECTOR_PREFIXES_CT)


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
# centre-to-centre to avoid LAY003.  With nodesep=0.8 + node height=0.5
# the minimum same-rank separation is 1.3 inches, so:
#   SCALE ≥ 10.16 mm / 1.3 in  →  use 24 mm/in for comfortable margins.
# (Rule 4: increased from 20.0 to give more mm per Graphviz unit, spreading
# the overall layout and reducing visual crowding.)
SCALE_MM_PER_GV: float = 24.0

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


# ---------------------------------------------------------------------------
# Post-layout specialised snap passes
# ---------------------------------------------------------------------------


def _snap_connectors_to_ic_y(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Snap each connector's y-coordinate to the median y of its signal-net neighbours.

    Connectors placed by Graphviz in ``rank=source`` or ``rank=sink`` can land
    far above or below the main circuit body when they are the only component
    in their tier (e.g. the audio-in jack floating at ``y = ORIGIN_Y`` while
    the opamp body sits 40–60 mm lower on the page).

    This pass sets each connector's y to the median y of all components it is
    directly connected to via **signal nets** (power nets excluded, and other
    connectors excluded from the neighbour list so connectors don't push each
    other around).  The median is grid-snapped to the KiCad 50-mil grid.

    Only the y-coordinate is adjusted; x and rotation are preserved.
    Components absent from *positions* are silently skipped.

    Why median rather than mean?  The median is robust to outlier positions
    (e.g. a decoupling capacitor already snapped to the top of its IC) that
    would pull a mean-based average to an incorrect y.
    """
    connector_refs: set[str] = {c.ref for c in ir.components if _is_connector_ref(c.ref)}
    if not connector_refs:
        return positions

    # Build signal-neighbour lists for each connector.
    sig_nbrs: dict[str, list[str]] = {r: [] for r in connector_refs}
    for net in ir.nets:
        if _is_power_net(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for ref in pin_refs:
            if ref not in connector_refs:
                continue
            for other in pin_refs:
                # Exclude self and other connectors — only use circuit-body
                # components (passives, ICs) as anchors.
                if other == ref or other in connector_refs:
                    continue
                if other in positions:
                    sig_nbrs[ref].append(other)

    result = dict(positions)
    for con_ref in connector_refs:
        if con_ref not in result:
            continue
        nbrs = sig_nbrs.get(con_ref, [])
        if not nbrs:
            continue
        nbr_ys = sorted(result[n][1] for n in nbrs)
        median_y = nbr_ys[len(nbr_ys) // 2]  # lower-median for determinism
        snapped_y = round(round(median_y / grid) * grid, 4)
        x, _, rot = result[con_ref]
        result[con_ref] = (x, snapped_y, rot)
    return result


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
# General deoverlap and y-gap compact
# ---------------------------------------------------------------------------


def _deoverlap_positions(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    skip_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Push coincident positions apart so every component occupies a distinct grid cell.

    After all snap passes multiple components may land on the same (x, y).  This
    pass groups refs by x-column, sorts each group by ascending y (then by ref
    for determinism), and nudges any component whose y-distance from the previous
    component is less than the LAY003-safe minimum apart.

    The minimum separation is the next grid multiple strictly above
    ``_STEREO_DEOVERLAP_MIN_MM`` (10.17 mm = 2 × 5.08 mm + ε), which equals
    9 × 1.27 mm = 11.43 mm.  This guarantees that no two components in the
    same x-column are closer than the LAY003 overlap threshold (10.16 mm).

    Only the y-coordinate is modified; x and rotation are preserved.

    Parameters
    ----------
    positions:
        KiCad mm positions to deoverlap in-place (a copy is made).
    skip_pairs:
        A set of ``(min_ref, max_ref)`` canonical pairs that should be left
        at their existing separation even if it is below the minimum gap.
        Used to preserve intentional co-locations such as decoupling caps
        placed exactly one ``GRID_ROW_MM`` above their IC.
    """
    # Minimum grid-snapped separation that clears the LAY003 threshold:
    # ceil(_STEREO_DEOVERLAP_MIN_MM / 1.27) × 1.27 = ceil(8.007) × 1.27 = 11.43 mm.
    _SNAP_GRID: float = 1.27
    _min_steps: int = math.ceil(_STEREO_DEOVERLAP_MIN_MM / _SNAP_GRID)
    min_sep: float = _min_steps * _SNAP_GRID

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        by_x[x].append(ref)

    result = dict(positions)
    for group in by_x.values():
        if len(group) < 2:
            continue
        # Stable sort: ascending y, then by ref name to break ties.
        group.sort(key=lambda r: (result[r][1], r))
        for i in range(1, len(group)):
            prev_ref = group[i - 1]
            curr_ref = group[i]
            _px, py, _pr = result[prev_ref]
            cx, cy, cr = result[curr_ref]
            if cy - py < min_sep:
                pair = (min(prev_ref, curr_ref), max(prev_ref, curr_ref))
                if pair in skip_pairs:
                    continue  # intentional co-location (e.g. decoupling cap)
                new_y = round(py + min_sep, 4)
                result[curr_ref] = (cx, new_y, cr)
    return result


def _compact_y_gap(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    min_gap_mm: float = 30.0,
) -> dict[str, tuple[float, float, float | None]]:
    """Collapse the largest vertical gap in the layout for non-power-symbol components.

    DOT sometimes places isolated source-tier connectors at the very top of the
    Graphviz graph (``gv_y ≈ max_gv_y``), which maps to ``y ≈ ORIGIN_Y`` in
    KiCad coordinates.  The bulk of the circuit then lands at a lower y,
    producing a visually jarring gap.  This pass:

    1. Identifies *regular* components (refs that do **not** start with ``#PWR``
       or ``#FLG``), which have programmatically fixed y positions assigned by
       :func:`_snap_power_symbols` and must not be moved.
    2. Finds consecutive distinct y-values in the regular set and locates the
       largest gap.  If it exceeds *min_gap_mm*, the components **below** the
       gap (larger y) are shifted *upward* (toward ORIGIN_Y) by
       ``gap_size − GRID_ROW_MM`` so a single-row separation remains.
    3. Power/flag symbols are left at their original positions.

    Parameters
    ----------
    positions:
        KiCad mm positions after all specialised snap passes.
    ir:
        Circuit IR, used only to detect power-symbol refs.
    min_gap_mm:
        Gaps smaller than this threshold are ignored (default 30 mm ≈ 4 grid rows).
    """
    if not positions:
        return positions

    power_sym_refs: set[str] = {
        comp.ref
        for comp in ir.components
        if comp.ref.startswith("#PWR") or comp.ref.startswith("#FLG")
    }
    regular_refs = [r for r in positions if r not in power_sym_refs]
    if not regular_refs:
        return positions

    # Distinct y values for regular components, ascending.
    ys = sorted({round(positions[r][1], 2) for r in regular_refs})
    if len(ys) < 2:
        return positions

    # Find the largest consecutive gap.
    max_gap = 0.0
    gap_threshold_y = 0.0  # y-value *above* the gap (the lower of the two boundary rows)
    for i in range(1, len(ys)):
        gap = ys[i] - ys[i - 1]
        if gap > max_gap:
            max_gap = gap
            gap_threshold_y = ys[i - 1]  # components with y > this are "below the gap"

    if max_gap <= min_gap_mm:
        return positions

    # Shift all regular components below the gap upward to close it.
    # Keep one GRID_ROW_MM separation so the two clusters remain visually distinct.
    collapse = round(max_gap - GRID_ROW_MM, 4)
    result = dict(positions)
    for ref in regular_refs:
        x, y, rot = result[ref]
        if y > gap_threshold_y:
            result[ref] = (x, round(y - collapse, 4), rot)
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

    The eight passes must run in the order shown — see the module docstring
    for the rationale behind each ordering constraint:

    1. :func:`snap_positions` — quantise to the KiCad 50-mil grid.
    2. :func:`_snap_power_symbols` — clamp ``#PWR``/``#FLG`` to top/bottom row.
    2b. :func:`_snap_connectors_to_ic_y` — pull each connector's y to the median
        y of its signal-net neighbours, preventing connectors from floating far
        above or below the main circuit body (Rule 2).
    3. :func:`_snap_feedback_components` — pull feedback passives above anchor IC
       (skipped when *feedback_refs* is empty).
    4. :func:`_apply_stereo_split` — compress L/R components into page halves
       (skipped when no L or R channel is present in *channels*).
    5. :func:`_compact_y_gap` — collapse the largest vertical gap in the layout
       for non-power-symbol components so the circuit appears as one connected
       region rather than two separate clusters.
    6. :func:`_post_snap_decoupling_caps` — co-locate bypass caps above their IC
       (skipped when *decoupling_map* is empty).
    7. :func:`_deoverlap_positions` — push any remaining grid collisions apart so
       no two components share the same (x, y) cell.
    """
    result = snap_positions(result)
    result = _snap_power_symbols(result, ir)
    result = _snap_connectors_to_ic_y(result, ir)
    if feedback_refs:
        result = _snap_feedback_components(result, annotations, ir)
    if any(v in ("L", "R") for v in channels.values()):
        result = _apply_stereo_split(result, channels)
    result = _compact_y_gap(result, ir)
    if decoupling_map:
        result = _post_snap_decoupling_caps(result, decoupling_map)
    # Build canonical skip-pairs from the decoupling map so that intentional
    # one-grid-row cap/IC co-locations are not nudged by _deoverlap_positions.
    decouple_skip: frozenset[tuple[str, str]] = frozenset(
        (min(cap, ic), max(cap, ic)) for cap, ic in decoupling_map.items()
    )
    result = _deoverlap_positions(result, skip_pairs=decouple_skip)
    return result
