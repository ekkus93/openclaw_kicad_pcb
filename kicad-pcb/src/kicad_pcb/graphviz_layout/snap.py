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
   * :func:`_spread_x_columns` — when > *max_per_column* symbols share the
     same x-coordinate after grid-snapping, split them into sub-columns
     spaced 25.4 mm apart (centered on the original column x), preserving
     the tier-ordered y-sort within each group.
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
2c. :func:`_snap_block_zones` (Phase 1.2: bias toward functional block zones)
2d. :func:`_apply_density_spreading` (Phase 2.3: reduce local crowding)
3. :func:`_snap_feedback_components` (if any feedback refs exist)
4. :func:`_apply_stereo_split` (if any L/R channels exist)
5. :func:`_compact_y_gap` (always; no-op when gap ≤ threshold)
6. :func:`_center_ics_in_columns` (always; no-op when no ICs present)
7. :func:`_post_snap_decoupling_caps` (if any decoupling caps exist)
8. :func:`_spread_x_columns` (always; spreads overloaded x-columns into sub-columns)
9. :func:`_deoverlap_positions` (always; final guard against grid collisions after X-spread)
10. :func:`_remediate_crossings` (always; includes its own inner deoverlap)

Power symbols must run before connector-y-snap so that ``#PWR``/``#FLG``
refs are already at their fixed rows before connectors compute their median.
Connector-y-snap runs before feedback snap so feedback-adjusted y values
take a correctly-anchored connector y as their starting point.
Block zone bias and density spreading run before feedback snap so that
functional block organization and crowding reduction happen early in the
pipeline, providing a cleaner baseline for specialized adjustments.
Density spreading runs after block zones so that block assignments are
respected when distributing dense clusters along the y-axis.
Stereo split runs after feedback snap so that feedback-adjusted y values are
used as the input to channel compression.
``_compact_y_gap`` runs before decoupling caps so that bypass-cap y positions
are relative to the compacted IC y values.
``_center_ics_in_columns`` runs before decoupling caps so that bypass caps
are re-snapped relative to the ICs' newly-centred positions.
``_spread_x_columns`` runs before ``_deoverlap_positions`` so that the
Y deoverlap operates on already-spread columns rather than tall stacks.
``_deoverlap_positions`` runs after X-spread to resolve any residual Y
collisions regardless of which earlier pass introduced them.
``_remediate_crossings`` runs after deoverlap as a final sweep: it measures the
crossing ratio and iteratively applies barycentric column-sort if the ratio
exceeds the threshold, then re-runs deoverlap to fix any new collisions.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR

from ..block_detection import (
    BlockRole,
    is_core_like_role,
    is_input_like_role,
    is_output_like_role,
    is_power_like_role,
)
from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import IC_PREFIXES as _IC_PREFIXES_CT
from ..component_types import is_ground_like_name as _is_ground_like_name
from ..component_types import is_power_net as _is_power_net
from ..errors import ErrorCode, UserError
from ..layout import GRID_COL_MM as _GRID_COL_MM
from ..layout import ComponentAnnotation as _ComponentAnnotation
from ..layout import barycentric_sort as _barycentric_sort
from ..layout import build_signal_adjacency as _build_signal_adjacency
from ..layout import count_wire_crossings as _count_wire_crossings

_log = logging.getLogger(__name__)

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

# Generated Reference/Value properties now sit one full field-clearance step
# away from the symbol body. Reserve two grid rows of vertical whitespace for
# components that share the same or a very nearby x-lane so those text bands do
# not collapse back onto adjacent symbols or short local wire corridors.
_PROPERTY_TEXT_NEAR_X_MM: float = 12 * 1.27
_PROPERTY_TEXT_VERTICAL_GAP_MM: float = 2 * GRID_ROW_MM

# Keep output connectors one snap step farther outward than the nominal
# connector lane so left-facing connector stubs do not fall back into the
# nearest output-support body column.
_OUTPUT_CONNECTOR_CLEARANCE_MM: float = 1.27

# Bottom inset for shared ground-family power symbols: used when
# ``is_ground_like_name(...)`` classifies a ``#PWR`` / ``#FLG`` value as a
# bottom-row symbol, keeping it clear of the lower margin and one grid row
# above the very bottom of the usable area.
_POWER_BOTTOM_MARGIN_MM: float = 20.0

# Phase 8.1: page-balance pass thresholds.
# The signal-circuit centre of gravity must deviate from the page centre Y by
# at least this many mm before a corrective vertical nudge is applied.
_PAGE_BALANCE_DEAD_ZONE_MM: float = 1.5 * GRID_ROW_MM  # ~11.43 mm

# Fraction of the detected deviation that is corrected per pass.
# Phase 3 block zoning creates a stronger left/centre/right structure, so the
# balancing pass needs a meaningful vertical correction to keep the signal
# circuit out of the top half on small fixtures.
_PAGE_BALANCE_CORRECTION: float = 0.85

# Phase 8.2: central composition thresholds.
# Any signal-path component must stay at least this far from the bottom of the
# usable page area — the KiCad title block occupies roughly this band.
_TITLE_BLOCK_CLEARANCE_MM: float = 30.0

# Fraction of the usable vertical extent beyond which the op-amp centre is
# considered "too low" (0.75 → y > ORIGIN_Y + 75 % × height triggers a nudge).
_OPAMP_LOWER_LIMIT_FRACTION: float = 0.75

# Fraction of the usable vertical extent below which the op-amp centre is
# considered "too high" (0.15 → y < ORIGIN_Y + 15 % × height triggers a nudge).
_OPAMP_UPPER_LIMIT_FRACTION: float = 0.15

# If the vertical span of all signal-path components is less than this fraction
# of the available page height a debug warning is emitted.
_MIN_CIRCUIT_SPAN_FRACTION: float = 0.25

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

        * **GND-type** (value satisfies
            :func:`kicad_pcb.component_types.is_ground_like_name`) →
      ``y = page_max_y - _POWER_BOTTOM_MARGIN_MM`` (bottom row, clear of the
      lower margin).
    * **All other power symbols** (VCC, VDD, VBAT, VREF, PWR_FLAG, etc.) →
      ``y = origin_y`` (top row).

    The x-coordinate is preserved so that each power symbol stays above or
    below the component it shares a net with in the Graphviz layout.
    Components not present in *positions* are silently skipped.
    """
    result = dict(positions)
    for comp in ir.components:
        ref = comp.ref
        if not (ref.startswith("#PWR") or ref.startswith("#FLG")):
            continue
        if ref not in result:
            continue
        is_gnd = _is_ground_like_name(comp.value or "")
        target_y = round(page_max_y - _POWER_BOTTOM_MARGIN_MM, 2) if is_gnd else origin_y
        x, _, rot = result[ref]
        result[ref] = (x, target_y, rot)
    return result


def _snap_feedback_components(
    positions: dict[str, tuple[float, float, float | None]],
    annotations: dict[str, _ComponentAnnotation],
    ir: CircuitIR,
    *,
    strict: bool = False,
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
        # over passive neighbours; fall back to any positioned neighbour in
        # non-strict mode.
        anchor_y = _resolve_feedback_anchor_y(
            ref=ref,
            neighbors=sorted(comp_nbrs.get(ref, [])),
            positions=result,
            anchor_prefixes=_anchor_prefixes,
            strict=strict,
        )

        if anchor_y is None:
            continue

        x, _, rot = result[ref]
        result[ref] = (x, round(anchor_y - GRID_ROW_MM, 2), rot)

    return result


def _resolve_feedback_anchor_y(
    *,
    ref: str,
    neighbors: list[str],
    positions: dict[str, tuple[float, float, float | None]],
    anchor_prefixes: tuple[str, ...],
    strict: bool,
) -> float | None:
    """Resolve preferred y-anchor for a feedback component.

    Prefers IC/connector neighbors. In non-strict mode, falls back to any
    positioned neighbor. In strict mode, raises when only fallback neighbors
    are available.
    """
    for nbr in neighbors:
        if any(nbr.upper().startswith(pfx) for pfx in anchor_prefixes) and nbr in positions:
            return positions[nbr][1]

    positioned_nbrs = [nbr for nbr in neighbors if nbr in positions]
    if strict and positioned_nbrs:
        raise UserError(
            "Feedback component has no IC/connector anchor",
            code=ErrorCode.IR_SEMANTIC_INVALID,
            details={
                "ref": ref,
                "positioned_neighbors": positioned_nbrs,
            },
        )
    if positioned_nbrs:
        return positions[positioned_nbrs[0]][1]
    return None


def _snap_opamp_halo(
    positions: Mapping[str, tuple[float, float, float | None]],
    halo: Mapping[str, str],
) -> dict[str, tuple[float, float, float | None]]:
    """Normalize halo members into adjacent lanes around their anchor IC.

    After :func:`_snap_feedback_components` and other passes, a halo member
    may have drifted too far from its anchor IC or landed in the exact same
    x-column. This pass keeps halo members close to their anchor but places
    them in adjacent lanes instead of returning them to the anchor x-position.

    Components absent from *positions* are silently skipped.

    Parameters
    ----------
    positions:
        KiCad mm positions from the previous snap pass.
    halo:
        ``{halo_ref: anchor_ic_ref}`` from
        :func:`~kicad_pcb.layout._compute_opamp_halo`.
    """
    if not halo:
        return dict(positions)

    result = dict(positions)

    # Group halo members per anchor IC so we can distribute them.
    by_anchor: dict[str, list[str]] = defaultdict(list)
    for halo_ref, anchor_ref in halo.items():
        if halo_ref in result and anchor_ref in result:
            by_anchor[anchor_ref].append(halo_ref)

    for anchor_ref, halo_refs in by_anchor.items():
        anchor_x, anchor_y, _ = result[anchor_ref]
        for i, halo_ref in enumerate(sorted(halo_refs)):
            halo_x, halo_y, halo_rot = result[halo_ref]
            place_left = i % 2 == 0
            left_x = round(max(ORIGIN_X, anchor_x - _GRID_COL_MM), 2)
            right_x = round(min(PAGE_MAX_X, anchor_x + _GRID_COL_MM), 2)

            if halo_x < anchor_x - 1.0:
                target_x = left_x
            elif halo_x > anchor_x + 1.0 or left_x == anchor_x:
                target_x = right_x
            elif right_x == anchor_x:
                target_x = left_x
            else:
                target_x = left_x if place_left else right_x

            if abs(halo_x - target_x) <= 1.0:
                continue

            # Distribute alternately above/below the anchor IC.
            level = i // 2 + 1
            sign = -1 if (i % 2 == 0) else 1  # even → above (y decreases in KiCad)
            new_y = round(anchor_y + sign * level * GRID_ROW_MM, 2)
            _log.debug(
                "halo snap: %r x=%.2f normalized near anchor %r x=%.2f target_x=%.2f y %.2f → %.2f",
                halo_ref,
                halo_x,
                anchor_ref,
                anchor_x,
                target_x,
                halo_y,
                new_y,
            )
            result[halo_ref] = (target_x, new_y, halo_rot)

    return result


@dataclass(frozen=True, slots=True)
class _OpAmpLocalityContext:
    decoupling_map: Mapping[str, str]
    halo: Mapping[str, str] | None = None
    block_layout: BlockLayout | None = None


@dataclass(frozen=True, slots=True)
class LayoutHeuristicPolicy:
    """Policy seam for analog-specific post-layout snap passes."""

    enable_decoupling_snap: bool = True
    enable_opamp_locality: bool = True
    enable_input_stage_cohesion: bool = True
    enable_output_stage_cohesion: bool = True

    def apply_decoupling_snap(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        decoupling_map: Mapping[str, str],
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply the decoupling-cap snap when enabled."""
        if not self.enable_decoupling_snap or not decoupling_map:
            return dict(positions)
        return _post_snap_decoupling_caps(dict(positions), dict(decoupling_map))

    def apply_opamp_locality(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        annotations: Mapping[str, _ComponentAnnotation],
        context: _OpAmpLocalityContext,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply op-amp-centric locality staging when enabled."""
        if not self.enable_opamp_locality:
            return dict(positions)
        return _snap_opamp_locality(dict(positions), ir, annotations=annotations, context=context)

    def apply_input_stage_cohesion(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        block_layout: BlockLayout | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply analog input-stage cohesion when enabled."""
        if not self.enable_input_stage_cohesion:
            return dict(positions)
        return _snap_input_stage_cohesion(dict(positions), ir, block_layout=block_layout)

    def apply_output_stage_cohesion(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        block_layout: BlockLayout | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply analog output-stage cohesion when enabled."""
        if not self.enable_output_stage_cohesion:
            return dict(positions)
        return _snap_output_stage_cohesion(dict(positions), ir, block_layout=block_layout)


DEFAULT_LAYOUT_HEURISTIC_POLICY: LayoutHeuristicPolicy = LayoutHeuristicPolicy()


def _local_signal_distances(
    anchor_ref: str,
    candidate_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest signal-hop distance from *anchor_ref* within *candidate_refs*."""
    if not candidate_refs:
        return {}

    distances: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque([(anchor_ref, 0)])
    seen = {anchor_ref}
    while frontier:
        ref, dist = frontier.popleft()
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in candidate_refs:
                continue
            seen.add(nbr)
            distances[nbr] = dist + 1
            frontier.append((nbr, dist + 1))
    return distances


def _snap_opamp_locality(  # noqa: PLR0912, PLR0915
    positions: Mapping[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    annotations: Mapping[str, _ComponentAnnotation],
    context: _OpAmpLocalityContext,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply op-amp-centric neighborhood placement refinements.

    Heuristics implemented for readability (Phase 4.1/4.2):

    * input-side components are biased to the left of the op-amp,
    * output-side components are biased to the right of the op-amp,
        * feedback components stay close to the op-amp body,
        * decoupling caps remain near the op-amp and reserved from feedback slots,
        * support roles are staged in distinct local clusters to avoid visual
            mixing (feedback vs input-conditioning vs output-support vs decoupling).
    """
    from ..block_detection import BlockRole  # noqa: PLC0415

    if not positions:
        return dict(positions)

    result = dict(positions)
    adjacency = _build_signal_adjacency(ir)

    ic_refs = sorted(ref for ref in result if _is_ic_ref(ref))
    if not ic_refs:
        return result

    role_by_ref: dict[str, BlockRole] = {}
    if context.block_layout is not None:
        role_by_ref = {
            ref: assignment.role for ref, assignment in context.block_layout.assignments.items()
        }

    for ic_ref in ic_refs:
        if ic_ref not in result:
            continue
        ic_x, ic_y, _ = result[ic_ref]

        signal_neighbors = sorted(r for r in adjacency.get(ic_ref, set()) if r in result)
        local_role_neighbors = {
            ref
            for ref, role in role_by_ref.items()
            if ref in result
            and ref != ic_ref
            and not _is_ic_ref(ref)
            and role is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
            and abs(result[ref][0] - ic_x) <= 2.0 * _GRID_COL_MM
            and abs(result[ref][1] - ic_y) <= 8.0 * GRID_ROW_MM
        }
        candidates = sorted(set(signal_neighbors) | local_role_neighbors)
        input_like: list[str] = []
        handoff_like: list[str] = []
        output_like: list[str] = []
        feedback_like: list[str] = []
        halo_like: list[str] = []
        local_distances = _local_signal_distances(ic_ref, set(candidates), adjacency)

        def _local_order_key(ref: str) -> tuple[int, float, str]:
            return (local_distances.get(ref, 999), result[ref][1], ref)

        for ref in candidates:
            role = role_by_ref.get(ref)
            is_feedback = bool(annotations.get(ref) and annotations[ref].feedback)
            is_decoupling = context.decoupling_map.get(ref) == ic_ref
            is_halo = context.halo is not None and context.halo.get(ref) == ic_ref

            if is_halo:
                halo_like.append(ref)
                continue

            if is_feedback or role == BlockRole.FEEDBACK:
                feedback_like.append(ref)
                continue
            if is_decoupling or role == BlockRole.DECOUPLING:
                continue
            if role == BlockRole.INTERSTAGE:
                handoff_like.append(ref)
            elif role is not None and is_input_like_role(role):
                if ic_ref in adjacency.get(ref, set()) and any(
                    is_output_like_role(role_by_ref.get(nbr)) for nbr in adjacency.get(ref, set())
                ):
                    handoff_like.append(ref)
                else:
                    input_like.append(ref)
            elif role is not None and is_output_like_role(role):
                output_like.append(ref)

        input_like.sort(key=_local_order_key)
        handoff_like.sort(key=_local_order_key)
        output_like.sort(key=_local_order_key)
        feedback_like.sort(key=_local_order_key)
        halo_like.sort(key=_local_order_key)

        # Keep stage input parts on op-amp input side (left), slightly above
        # center to separate them from output support parts.
        target_input_x = round(ic_x - _GRID_COL_MM, 2)
        for idx, ref in enumerate(input_like):
            x, y, rot = result[ref]
            offset = idx - (len(input_like) - 1) / 2
            target_y = round(ic_y - 1.5 * GRID_ROW_MM + offset * GRID_ROW_MM, 2)
            result[ref] = (min(round(x, 2), target_input_x), target_y, rot)

        # Keep bridge parts that hand off into a later output stage close to the
        # op-amp on the right so the interstage coupling does not split apart.
        target_handoff_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(handoff_like):
            x, _y, rot = result[ref]
            offset = idx - (len(handoff_like) - 1) / 2
            target_y = round(ic_y + offset * GRID_ROW_MM, 2)
            result[ref] = (max(round(x, 2), target_handoff_x), target_y, rot)

        # Keep stage output parts on op-amp output side (right), slightly below
        # center to avoid mixing with input/support clusters.
        target_output_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(output_like):
            x, y, rot = result[ref]
            offset = idx - (len(output_like) - 1) / 2
            target_y = round(ic_y + 1.5 * GRID_ROW_MM + offset * GRID_ROW_MM, 2)
            result[ref] = (max(round(x, 2), target_output_x), target_y, rot)

        # Keep decoupling caps above the op-amp (power-pin vicinity).
        dec_refs = sorted(
            ref
            for ref, anchor in context.decoupling_map.items()
            if anchor == ic_ref and ref in result
        )
        reserved_decoupling_y: set[float] = set()
        for idx, dec_ref in enumerate(dec_refs):
            _x, _y, dec_rot = result[dec_ref]
            dec_y = round(ic_y - (idx + 1) * GRID_ROW_MM, 2)
            dec_x = round(ic_x, 2)
            if idx >= 2:
                side_step = idx - 1
                side_sign = -1 if idx % 2 == 0 else 1
                dec_x = round(ic_x + side_sign * side_step * _GRID_COL_MM, 2)
            result[dec_ref] = (dec_x, dec_y, dec_rot)
            reserved_decoupling_y.add(dec_y)

        # Keep halo members close to the op-amp but one lane off the body
        # column so coupling/output support does not collapse back onto the IC.
        target_halo_left_x = round(ic_x - _GRID_COL_MM, 2)
        target_halo_right_x = round(ic_x + _GRID_COL_MM, 2)
        for idx, ref in enumerate(halo_like):
            x, _y, rot = result[ref]
            role = role_by_ref.get(ref)
            target_x = target_halo_right_x
            if x < ic_x - 1.0:
                target_x = target_halo_left_x
            elif x > ic_x + 1.0:
                target_x = target_halo_right_x
            elif role != BlockRole.FEEDBACK and not is_output_like_role(role):
                target_x = target_halo_left_x if idx % 2 == 0 else target_halo_right_x

            offset = idx - (len(halo_like) - 1) / 2
            halo_y = round(ic_y + offset * GRID_ROW_MM, 2)
            while halo_y in reserved_decoupling_y:
                halo_y = round(halo_y + GRID_ROW_MM, 2)
            result[ref] = (target_x, halo_y, rot)

        # Keep feedback parts near the op-amp but in a distinct band below
        # the IC centerline, and avoid decoupling slots.
        for idx, ref in enumerate(feedback_like):
            _x, _y, rot = result[ref]
            level = idx + 1
            fb_y = round(ic_y + level * GRID_ROW_MM, 2)
            while fb_y in reserved_decoupling_y:
                level += 1
                fb_y = round(ic_y + level * GRID_ROW_MM, 2)
            fb_x = round(ic_x, 2)
            if idx >= 2:
                side_step = idx - 1
                side_sign = -1 if idx % 2 == 0 else 1
                fb_x = round(ic_x + side_sign * side_step * _GRID_COL_MM, 2)
            result[ref] = (fb_x, fb_y, rot)

    return result


def _find_input_stage_members(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    adjacency: Mapping[str, set[str]],
    *,
    ic_x: float,
    ic_y: float,
) -> tuple[list[str], list[str], set[str]]:
    """Return ``(input_refs, preconditioning_refs, stage_ref_set)`` for Phase 7.1."""
    from ..block_detection import BlockRole  # noqa: PLC0415

    input_connectors = sorted(
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.INPUT and _is_connector_ref(ref)
    )
    if not input_connectors:
        input_connectors = sorted(
            ref for ref, role in role_by_ref.items() if ref in positions and role == BlockRole.INPUT
        )
    if not input_connectors:
        return [], [], set()

    stage_refs: set[str] = set(input_connectors)
    for start in input_connectors:
        frontier: set[str] = {start}
        seen: set[str] = {start}
        for _ in range(2):
            next_frontier: set[str] = set()
            for ref in frontier:
                for nbr in adjacency.get(ref, set()):
                    if nbr in seen or nbr not in positions:
                        continue
                    seen.add(nbr)
                    role = role_by_ref.get(nbr)
                    if role is not None and is_input_like_role(role):
                        stage_refs.add(nbr)
                        next_frontier.add(nbr)
            frontier = next_frontier
            if not frontier:
                break

    for ref, role in role_by_ref.items():
        if ref not in positions or role is None or not is_input_like_role(role):
            continue
        x, y, _ = positions[ref]
        if x <= ic_x and abs(y - ic_y) <= 6.0 * GRID_ROW_MM:
            stage_refs.add(ref)

    input_refs = sorted(
        [ref for ref in stage_refs if role_by_ref.get(ref) == BlockRole.INPUT],
        key=lambda ref: positions[ref][1],
    )
    pre_refs = sorted(
        [ref for ref in stage_refs if role_by_ref.get(ref) == BlockRole.PRECONDITIONING],
        key=lambda ref: positions[ref][1],
    )
    return input_refs, pre_refs, stage_refs


def _input_stage_connector_distances(
    input_refs: list[str],
    stage_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest input-connector hop distance for refs within the input stage."""
    if not input_refs or not stage_refs:
        return {}

    stage_distance: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque((ref, 0) for ref in input_refs)
    seen = set(input_refs)
    while frontier:
        ref, dist = frontier.popleft()
        stage_distance[ref] = dist
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in stage_refs:
                continue
            seen.add(nbr)
            frontier.append((nbr, dist + 1))
    return stage_distance


def _place_input_stage_lane(
    positions: dict[str, tuple[float, float, float | None]],
    input_refs: list[str],
    pre_refs: list[str],
    *,
    anchor: tuple[float, float],
    stage_distance: Mapping[str, int] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Place input and preconditioning refs into compact left-side columns."""
    ic_x, ic_y = anchor
    result = dict(positions)
    connector_x = round(ic_x - 3.0 * _GRID_COL_MM, 2)
    precond_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    precond_inner_x = round(ic_x - _GRID_COL_MM, 2)

    for idx, ref in enumerate(input_refs):
        x, _y, rot = result[ref]
        offset = idx - (len(input_refs) - 1) / 2
        target_y = round(ic_y + offset * GRID_ROW_MM, 2)
        result[ref] = (min(round(x, 2), connector_x), target_y, rot)

    for idx, ref in enumerate(pre_refs):
        x, _y, rot = result[ref]
        offset = idx - (len(pre_refs) - 1) / 2
        target_y = round(ic_y + offset * GRID_ROW_MM, 2)
        target_x = precond_x
        if len(pre_refs) >= 3 and (stage_distance or {}).get(ref, 0) >= 2:
            target_x = precond_inner_x
        result[ref] = (round(target_x, 2), target_y, rot)

    return result


def _evict_input_lane_intruders(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    stage_refs: set[str],
    *,
    precond_x: float,
    ic_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep unrelated roles out of the input lane."""
    result = dict(positions)
    for ref, role in role_by_ref.items():
        if ref not in result or ref in stage_refs:
            continue
        x, y, rot = result[ref]
        if x >= precond_x:
            continue
        if role is not None and (is_output_like_role(role) or is_core_like_role(role)):
            result[ref] = (precond_x, y, rot)
        elif role is not None and is_power_like_role(role):
            top_y = round(ic_y - 5.0 * GRID_ROW_MM, 2)
            result[ref] = (x, min(y, top_y), rot)
    return result


def _snap_input_stage_cohesion(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep the input stage coherent and clearly left-bounded (Phase 7.1)."""
    if not positions or block_layout is None:
        return positions

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    ic_refs = [ref for ref in positions if _is_ic_ref(ref)]
    if not ic_refs:
        return positions

    anchor_ic = max(ic_refs, key=lambda ref: positions[ref][0])
    ic_x, ic_y, _ = positions[anchor_ic]
    adjacency = _build_signal_adjacency(ir)

    input_refs, pre_refs, stage_refs = _find_input_stage_members(
        positions,
        role_by_ref,
        adjacency,
        ic_x=ic_x,
        ic_y=ic_y,
    )
    if not input_refs and not pre_refs:
        return positions

    result = _place_input_stage_lane(
        positions,
        input_refs,
        pre_refs,
        anchor=(ic_x, ic_y),
        stage_distance=_input_stage_connector_distances(input_refs, stage_refs, adjacency),
    )
    precond_x = round(ic_x - 2.0 * _GRID_COL_MM, 2)
    return _evict_input_lane_intruders(
        result,
        role_by_ref,
        stage_refs,
        precond_x=precond_x,
        ic_y=ic_y,
    )


def _find_output_stage_members(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    adjacency: Mapping[str, set[str]],
    *,
    ic_x: float,
    ic_y: float,
) -> tuple[list[str], list[str], set[str]]:
    """Return ``(output_connectors, output_support, stage_ref_set)`` for Phase 7.2."""
    from ..block_detection import BlockRole  # noqa: PLC0415

    def _is_output_stage_role(role: BlockRole | None) -> bool:
        return role == BlockRole.INTERSTAGE or is_output_like_role(role)

    output_connectors = sorted(
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.OUTPUT and _is_connector_ref(ref)
    )
    # Require an explicit output connector for this pass; without one, we can
    # accidentally pull unrelated output-tagged parts into the terminal lane.
    if not output_connectors:
        return [], [], set()

    # Seed the stage with explicit interstage handoff parts because the IR
    # adjacency still uses unsplit IC refs while the placed schematic uses
    # split-unit refs (for example U1A/U1B).
    stage_refs: set[str] = {
        ref
        for ref, role in role_by_ref.items()
        if ref in positions and role == BlockRole.INTERSTAGE
    }
    stage_refs.update(output_connectors)
    for start in output_connectors:
        frontier: set[str] = {start}
        seen: set[str] = {start}
        for _ in range(2):
            next_frontier: set[str] = set()
            for ref in frontier:
                for nbr in adjacency.get(ref, set()):
                    if nbr in seen or nbr not in positions:
                        continue
                    seen.add(nbr)
                    role = role_by_ref.get(nbr)
                    if _is_output_stage_role(role):
                        stage_refs.add(nbr)
                        next_frontier.add(nbr)
            frontier = next_frontier
            if not frontier:
                break

    for ref, role in role_by_ref.items():
        if ref not in positions or not _is_output_stage_role(role):
            continue
        x, y, _ = positions[ref]
        if x >= ic_x and abs(y - ic_y) <= 6.0 * GRID_ROW_MM:
            stage_refs.add(ref)

    output_connectors_sorted = sorted(
        [
            ref
            for ref in stage_refs
            if role_by_ref.get(ref) == BlockRole.OUTPUT and _is_connector_ref(ref)
        ],
        key=lambda ref: positions[ref][1],
    )
    output_support = sorted(
        [ref for ref in stage_refs if _is_output_stage_role(role_by_ref.get(ref))],
        key=lambda ref: positions[ref][1],
    )
    output_support = [ref for ref in output_support if ref not in output_connectors_sorted]
    return output_connectors_sorted, output_support, stage_refs


def _output_stage_connector_distances(
    output_connectors: list[str],
    stage_refs: set[str],
    adjacency: Mapping[str, set[str]],
) -> dict[str, int]:
    """Return shortest connector-hop distance for refs within the output stage."""
    if not output_connectors or not stage_refs:
        return {}

    stage_distance: dict[str, int] = {}
    frontier: deque[tuple[str, int]] = deque((ref, 0) for ref in output_connectors)
    seen = set(output_connectors)
    while frontier:
        ref, dist = frontier.popleft()
        stage_distance[ref] = dist
        for nbr in adjacency.get(ref, set()):
            if nbr in seen or nbr not in stage_refs:
                continue
            seen.add(nbr)
            frontier.append((nbr, dist + 1))
    return stage_distance


def _place_output_stage_lane(
    positions: dict[str, tuple[float, float, float | None]],
    output_connectors: list[str],
    output_support: list[str],
    *,
    anchor: tuple[float, float],
    stage_distance: Mapping[str, int] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Place output connector/support refs into right-side columns."""
    ic_x, ic_y = anchor
    result = dict(positions)
    connector_x = round(ic_x + 3.0 * _GRID_COL_MM + _OUTPUT_CONNECTOR_CLEARANCE_MM, 2)
    support_x = round(ic_x + 2.0 * _GRID_COL_MM, 2)
    support_inner_x = round(ic_x + _GRID_COL_MM, 2)

    output_lane_y = ic_y + GRID_ROW_MM

    for idx, ref in enumerate(output_connectors):
        x, _y, rot = result[ref]
        offset = idx - (len(output_connectors) - 1) / 2
        target_y = round(output_lane_y + offset * GRID_ROW_MM, 2)
        result[ref] = (max(round(x, 2), connector_x), target_y, rot)

    for idx, ref in enumerate(output_support):
        x, _y, rot = result[ref]
        offset = idx - (len(output_support) - 1) / 2
        target_y = round(output_lane_y + offset * GRID_ROW_MM, 2)
        target_x = support_x
        if len(output_support) >= 3 and (stage_distance or {}).get(ref, 0) >= 2:
            target_x = support_inner_x
        result[ref] = (max(round(x, 2), target_x), target_y, rot)

    return result


def _align_output_connectors_without_ic(
    positions: dict[str, tuple[float, float, float | None]],
    output_connectors: list[str],
    output_support: list[str],
) -> dict[str, tuple[float, float, float | None]]:
    """Align output connectors to nearby output support when no IC anchor exists.

    Some simple output-stage circuits consist only of passives between stage
    nets and output connectors. In that case the usual IC-anchored lane logic
    cannot run, but the output connector row should still line up with the
    output support row instead of floating above it.
    """
    if not output_connectors:
        return dict(positions)

    result = dict(positions)
    connector_lane_x: float | None = None
    if output_support:
        support_rows = [result[ref][1] for ref in output_support]
        support_xs = [result[ref][0] for ref in output_support]
        connector_lane_x = round(max(support_xs) + _GRID_COL_MM, 2)
        if len(support_rows) == len(output_connectors):
            target_rows = support_rows
        else:
            center_y = sum(support_rows) / len(support_rows)
            target_rows = [
                round(center_y + (index - (len(output_connectors) - 1) / 2) * GRID_ROW_MM, 2)
                for index in range(len(output_connectors))
            ]
    else:
        connector_rows = [result[ref][1] for ref in output_connectors]
        center_y = sum(connector_rows) / len(connector_rows)
        target_rows = [
            round(center_y + (index - (len(output_connectors) - 1) / 2) * GRID_ROW_MM, 2)
            for index in range(len(output_connectors))
        ]

    for ref, target_y in zip(output_connectors, target_rows, strict=False):
        x, _y, rot = result[ref]
        target_x = x if connector_lane_x is None else max(round(x, 2), connector_lane_x)
        result[ref] = (round(target_x, 2), target_y, rot)

    return result


def _evict_output_lane_intruders(
    positions: dict[str, tuple[float, float, float | None]],
    role_by_ref: Mapping[str, BlockRole],
    stage_refs: set[str],
    *,
    support_x: float,
    ic_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep unrelated roles out of the output lane."""
    result = dict(positions)
    for ref, role in role_by_ref.items():
        if ref not in result or ref in stage_refs:
            continue
        x, y, rot = result[ref]
        if x <= support_x:
            continue
        if role is not None and (is_input_like_role(role) or is_core_like_role(role)):
            result[ref] = (support_x, y, rot)
        elif role is not None and is_power_like_role(role):
            bottom_y = round(ic_y + 5.0 * GRID_ROW_MM, 2)
            result[ref] = (x, max(y, bottom_y), rot)
    return result


def _snap_output_stage_cohesion(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    block_layout: BlockLayout | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Keep the output stage coherent and clearly right-bounded (Phase 7.2)."""
    if not positions or block_layout is None:
        return positions

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    ic_refs = [ref for ref in positions if _is_ic_ref(ref)]
    if not ic_refs:
        output_connectors = sorted(
            ref
            for ref, role in role_by_ref.items()
            if ref in positions and role == BlockRole.OUTPUT and _is_connector_ref(ref)
        )
        output_support = sorted(
            ref
            for ref, role in role_by_ref.items()
            if ref in positions
            and (role == BlockRole.INTERSTAGE or is_output_like_role(role))
            and ref not in output_connectors
        )
        return _align_output_connectors_without_ic(positions, output_connectors, output_support)

    anchor_ic = max(ic_refs, key=lambda ref: positions[ref][0])
    ic_x, ic_y, _ = positions[anchor_ic]
    adjacency = _build_signal_adjacency(ir)

    output_connectors, output_support, stage_refs = _find_output_stage_members(
        positions,
        role_by_ref,
        adjacency,
        ic_x=ic_x,
        ic_y=ic_y,
    )
    if not output_connectors and not output_support:
        return positions

    result = _place_output_stage_lane(
        positions,
        output_connectors,
        output_support,
        anchor=(ic_x, ic_y),
        stage_distance=_output_stage_connector_distances(output_connectors, stage_refs, adjacency),
    )
    support_x = round(ic_x + 2.0 * _GRID_COL_MM, 2)
    return _evict_output_lane_intruders(
        result,
        role_by_ref,
        stage_refs,
        support_x=support_x,
        ic_y=ic_y,
    )


def _snap_block_zones(
    positions: dict[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Bias component positions toward their designated functional block zones.

    Applies gentle nudges to components based on their BlockRole assignment:

    * **POWER_ENTRY / DECOUPLING** → bias toward top (y closer to origin_y)
    * **INPUT / PRECONDITIONING** → bias toward left (x closer to origin_x)
    * **OUTPUT** → bias toward right (x closer to page_max_x)
    * **OPAMP_CORE / FEEDBACK** → no adjustment (Graphviz center is fine)

    This pass runs after power-symbol and connector snaps but before stereo
    split and compaction so that block zones influence the initial layout
    without conflicting with later hard constraints.

    Parameters
    ----------
    positions:
        KiCad mm positions from the previous snap pass.
    block_layout:
        Functional block classification from
        :func:`~kicad_pcb.block_detection.classify_circuit`.
    origin_x, origin_y:
        Page margins (left, top).
    page_max_x:
        Right page boundary.
    """
    result = dict(positions)

    # Define block zones with stronger separation (Phase 3.1):
    # * Left zone: origin_x to ~100mm — INPUT / PRECONDITIONING
    # * Center zone: ~100mm to ~170mm — OPAMP_CORE / FEEDBACK
    # * Right zone: ~170mm to page_max_x — OUTPUT
    # * Top zone: origin_y to ~50mm — POWER_ENTRY / DECOUPLING
    LEFT_ZONE_MAX_X = origin_x + 90.0  # mm — INPUT must stay left of this
    RIGHT_ZONE_MIN_X = page_max_x - 117.0  # mm — OUTPUT must stay right of this
    TOP_ZONE_MAX_Y = origin_y + 50.0  # mm — POWER should stay above this

    # Minimum positions to enforce strong separation (Phase 3.1)
    OUTPUT_MIN_X = origin_x + 140.0  # mm — OUTPUT must be at least this far right
    INPUT_MAX_X = origin_x + 100.0  # mm — INPUT must not exceed this far right

    for ref, assignment in block_layout.assignments.items():
        if ref not in result:
            continue
        if ref.startswith("#"):
            continue

        x, y, rot = result[ref]
        role = assignment.role

        # Bias power and decoupling components toward the top
        if is_power_like_role(role):
            if y > TOP_ZONE_MAX_Y + 20.0:
                # Pull up by 1 grid row (7.62mm) if way too low
                new_y = round(y - GRID_ROW_MM, 2)
                _log.debug(
                    "block zone snap: %r (%s) too low (y=%.2f), nudging to %.2f",
                    ref,
                    role.value,
                    y,
                    new_y,
                )
                result[ref] = (x, new_y, rot)

        # Strongly bias input/preconditioning toward left (Phase 3.1 fix)
        elif is_input_like_role(role):
            if x > LEFT_ZONE_MAX_X:
                # Pull left aggressively; enforce INPUT_MAX_X hard limit
                new_x = round(max(origin_x + 20.0, min(INPUT_MAX_X, x - 50.8)), 2)
                _log.debug(
                    "block zone snap: %r (%s) too far right (x=%.2f), pulling to %.2f",
                    ref,
                    role.value,
                    x,
                    new_x,
                )
                result[ref] = (new_x, y, rot)  # FIX: was (x, y, rot) — critical bug!

        # Strongly bias output toward right (Phase 3.1 enhancement)
        elif is_output_like_role(role) and x < RIGHT_ZONE_MIN_X:
            # Pull right aggressively; enforce OUTPUT_MIN_X hard limit
            # Use large 80mm nudge to overcome initial clustering
            new_x = round(max(OUTPUT_MIN_X, x + 80.0), 2)
            new_x = min(page_max_x - 20.0, new_x)  # Stay within page bounds
            _log.debug(
                "block zone snap: %r (%s) too far left (x=%.2f), pulling to %.2f",
                ref,
                role.value,
                x,
                new_x,
            )
            result[ref] = (new_x, y, rot)

        # OPAMP_CORE and FEEDBACK: no adjustment (center is fine)

    return result


def _apply_density_spreading(  # noqa: PLR0912, PLR0915
    positions: dict[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    radius_mm: float = 30.0,
    threshold: int = 5,
) -> dict[str, tuple[float, float, float | None]]:
    """Push apart symbols in dense clusters to reduce local crowding (Phase 2.3).

    Identifies spatial hotspots where ≥ *threshold* symbols lie within
    *radius_mm* of each other, then distributes them along the y-axis to
    improve readability.  When *block_layout* is supplied, spreading respects
    functional block boundaries so components don't migrate across blocks.

    This pass runs after :func:`_snap_block_zones` (so block zones are already
    set) but before :func:`_apply_stereo_split` and :func:`_compact_y_gap` (so
    spreading doesn't conflict with those specialized layout adjustments).

    Parameters
    ----------
    positions:
        KiCad mm positions from the previous snap pass.
    block_layout:
        Optional functional block classification from
        :func:`~kicad_pcb.block_detection.classify_circuit`.  When supplied,
        spreading only affects symbols within the same block role.
    radius_mm:
        Neighbor search radius (default: 30mm, ~4 KiCad grid cells).
    threshold:
        Minimum neighbor count to qualify as a dense cluster (default: 5).

    Returns
    -------
    dict[str, tuple[float, float, float | None]]:
        Updated positions with dense clusters spread vertically.
    """
    result = dict(positions)
    refs = list(positions.keys())

    # Map refs to positions for distance computation
    pos_map = {ref: (x, y) for ref, (x, y, _) in positions.items()}

    # Compute local density: count neighbors within radius for each symbol
    density: dict[str, int] = {}
    radius_sq = radius_mm**2
    for ref_i in refs:
        if ref_i.startswith("#"):
            continue
        if ref_i not in pos_map:
            continue
        x_i, y_i = pos_map[ref_i]
        neighbors = 0
        for ref_j in refs:
            if ref_j.startswith("#"):
                continue
            if ref_i == ref_j or ref_j not in pos_map:
                continue
            x_j, y_j = pos_map[ref_j]
            dist_sq = (x_i - x_j) ** 2 + (y_i - y_j) ** 2
            if dist_sq <= radius_sq:
                neighbors += 1
        density[ref_i] = neighbors

    # Identify dense cluster centers (symbols with ≥ threshold neighbors)
    dense_refs = {ref for ref, count in density.items() if count >= threshold}

    if not dense_refs:
        return result  # No dense clusters; skip spreading

    # Group dense refs by block role (if layout provided) and x-column
    # so spreading only affects symbols in the same functional area
    if block_layout:
        groups: dict[tuple[str, float], list[str]] = defaultdict(list)
        for ref in dense_refs:
            if ref not in block_layout.assignments:
                continue
            role = block_layout.assignments[ref].role.value
            x, _, _ = result[ref]
            # Round x to ~25mm columns to group symbols in same vertical column
            col_x = round(x / 25.4) * 25.4
            groups[(role, col_x)].append(ref)
    else:
        # No block layout: group only by x-column
        groups = defaultdict(list)
        for ref in dense_refs:
            x, _, _ = result[ref]
            col_x = round(x / 25.4) * 25.4
            groups[("ALL", col_x)].append(ref)

    # Spread each group vertically along y-axis
    for (role_or_all, col_x), group_refs in groups.items():
        if len(group_refs) < 2:
            continue  # Single symbol; no spreading needed

        # Sort by current y position
        sorted_refs = sorted(group_refs, key=lambda r: result[r][1])

        # Compute target y positions with minimum spacing of 1 grid row
        min_spacing = GRID_ROW_MM  # 7.62mm

        y_positions: list[float] = []
        current_y = result[sorted_refs[0]][1]  # Start from first symbol's y
        for ref in sorted_refs:
            _, old_y, _ = result[ref]
            # Enforce minimum spacing from previous symbol
            if y_positions and current_y < y_positions[-1] + min_spacing:
                current_y = round(y_positions[-1] + min_spacing, 2)
            else:
                current_y = round(old_y, 2)
            y_positions.append(current_y)
            current_y += min_spacing  # Advance for next symbol

        # Apply new y positions (keep x and rotation unchanged)
        for i, ref in enumerate(sorted_refs):
            x, old_y, rot = result[ref]
            new_y = y_positions[i]
            if abs(new_y - old_y) > 0.1:  # Only log when meaningful change
                _log.debug(
                    "density spread: %r (%s col=%.1f) y %.2f → %.2f (neighbors=%d)",
                    ref,
                    role_or_all,
                    col_x,
                    old_y,
                    new_y,
                    density[ref],
                )
                result[ref] = (x, new_y, rot)

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
    caps_by_ic: dict[str, list[str]] = defaultdict(list)
    for cap_ref, ic_ref in decoupling_map.items():
        if cap_ref in result and ic_ref in result:
            caps_by_ic[ic_ref].append(cap_ref)

    for ic_ref, cap_refs in caps_by_ic.items():
        ic_x, ic_y, _ = result[ic_ref]
        for idx, cap_ref in enumerate(sorted(cap_refs)):
            cap_x = round(ic_x, 2)
            if idx >= 2:
                side_step = idx - 1
                side_sign = -1 if idx % 2 == 0 else 1
                cap_x = round(ic_x + side_sign * side_step * _GRID_COL_MM, 2)
            cap_y = round(ic_y - (idx + 1) * GRID_ROW_MM, 2)
            result[cap_ref] = (cap_x, cap_y, None)
    return result


def _center_ics_in_columns(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    halo: Mapping[str, str] | None = None,
) -> dict[str, tuple[float, float, float | None]]:
    """Re-sort each x-column so ICs sit at the vertical midpoint.

    Within each column, non-power components are split into three buckets:

    * **ic_refs** — ICs (``_is_ic_ref`` returns True).
    * **halo_other** — non-IC components that are op-amp halo members (i.e.
      present as a key in the *halo* mapping).
    * **plain_other** — everything else (passives, connectors, etc.).

    Each bucket is sorted ascending by current y-coordinate to preserve
    relative ordering within the bucket.  Refs are then interleaved as::

        plain_other[:mid] + halo_other[:mid] + ic_refs
            + halo_other[mid:] + plain_other[mid:]

    so ICs end up at the vertical centre with halo members flanking them and
    plain passives at the outer edges.  This mirrors the ordering that the
    former ``HeuristicLayoutEngine`` applied and gives schematics a more
    structured, readable appearance.

    Columns that contain no ICs are returned unchanged.  Power symbols
    (``#PWR`` / ``#FLG``) are excluded from reordering and kept at their
    original positions.

    The existing sorted y-values of each column are used as target slots —
    no new y-coordinates are introduced; only the *assignment* of refs to
    slots changes.  Run this pass **before** :func:`_post_snap_decoupling_caps`
    so that bypass caps are re-anchored to the ICs' newly-centred y values.
    """
    halo_keys: frozenset[str] = frozenset(halo) if halo else frozenset()

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        if ref.startswith("#"):  # skip #PWR / #FLG symbols
            continue
        by_x[x].append(ref)

    result = dict(positions)
    for group in by_x.values():
        if len(group) < 2:
            continue
        # Only bother reordering columns that contain at least one IC.
        if not any(_is_ic_ref(r) for r in group):
            continue

        # Sort ascending by y then ref to get stable, well-defined slots.
        group_sorted = sorted(group, key=lambda r: (positions[r][1], r))
        y_slots = [positions[r][1] for r in group_sorted]

        # Partition into buckets.
        ic_refs = [r for r in group_sorted if _is_ic_ref(r)]
        other = [r for r in group_sorted if not _is_ic_ref(r)]
        halo_other = [r for r in other if r in halo_keys]
        plain_other = [r for r in other if r not in halo_keys]

        mid_plain = len(plain_other) // 2
        mid_halo = len(halo_other) // 2
        ordered = (
            plain_other[:mid_plain]
            + halo_other[:mid_halo]
            + ic_refs
            + halo_other[mid_halo:]
            + plain_other[mid_plain:]
        )

        # Assign each ref in the interleaved order to the sorted y-slots.
        for ref, y in zip(ordered, y_slots):
            x, _old_y, rot = result[ref]
            result[ref] = (x, y, rot)

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


def _post_stereo_barycentric(  # noqa: PLR0912
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    channels: Mapping[str, str],
    *,
    passes: int = 2,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply barycentric vertical re-ordering within each stereo channel band.

    After :func:`_apply_stereo_split` compresses L/R components into the top
    and bottom page halves, wire crossings can increase within each band.  This
    pass reduces intra-channel crossings by performing a two-pass barycentric
    sweep on each band independently:

    * For each channel band (``"L"`` and ``"R"``) collect the refs and group
      them by x-column (using their snapped x-coordinate as the key).
    * Run *passes* sweeps of left→right and right→left barycentric sorting,
      where the *row ordering* of a ref within its x-column is determined by
      the average y-position of its signal-adjacent neighbours in the adjacent
      column.
    * After sorting, re-assign the original sorted y-values of each column to
      the newly ordered refs (preserving the y-spacing, only swapping *which*
      ref occupies which row).

    Refs classified as ``"mono"`` (or absent from *channels*) are not moved.

    Parameters
    ----------
    positions:
        KiCad mm positions ``{ref: (x, y, rot)}``.
    ir:
        Circuit IR used to build signal-net adjacency for weight computation.
    channels:
        ``{ref: channel}`` from
        :func:`~kicad_pcb.layout.detect_stereo_channels`.
    passes:
        Number of full L→R + R→L sweeps.  Default is 2.

    Returns
    -------
    dict[str, tuple[float, float, float | None]]
        Updated positions dict.  Only L/R-channel refs may have their y
        modified; x and rotation are always preserved.
    """
    # Fast-path: no stereo → nothing to do.
    if not any(v in ("L", "R") for v in channels.values()):
        return positions

    # Build signal adjacency from the circuit IR (power nets excluded).
    sig_adj: dict[str, set[str]] = defaultdict(set)
    for net in ir.nets:
        if _is_power_net(net.name):
            continue
        pin_refs = [p.ref for p in net.pins]
        for ri in pin_refs:
            for rj in pin_refs:
                if ri != rj:
                    sig_adj[ri].add(rj)

    result = dict(positions)

    for band_ch in ("L", "R"):
        band_refs = [r for r, ch in channels.items() if ch == band_ch]
        if not band_refs:
            continue

        # Group by x-column (exact snapped x-coordinate as key).
        by_x: dict[float, list[str]] = defaultdict(list)
        for ref in band_refs:
            by_x[result[ref][0]].append(ref)

        sorted_xs = sorted(by_x)
        if len(sorted_xs) < 2:
            # Only one column in this band — nothing to cross-sort.
            continue

        # Establish initial row ordering within each x-column: ascending y.
        for x in sorted_xs:
            by_x[x].sort(key=lambda r: result[r][1])

        # ref → x column (fixed within this band pass).
        ref_to_x: dict[str, float] = {ref: result[ref][0] for ref in band_refs}

        def _row_map_stereo() -> dict[str, int]:
            return {ref: i for x in sorted_xs for i, ref in enumerate(by_x[x])}

        def _avg_nbr_row_stereo(
            ref: str,
            target_x: float,
            row_map: dict[str, int],
        ) -> float:
            nbrs = [r for r in sig_adj.get(ref, set()) if ref_to_x.get(r) == target_x]
            if nbrs:
                return sum(row_map[r] for r in nbrs) / len(nbrs)
            # No cross-col neighbour: use own row as neutral weight.
            return float(row_map.get(ref, 0))

        for _ in range(passes):
            # Pass 1: left → right.
            rm = _row_map_stereo()
            for i, x in enumerate(sorted_xs):
                if i == 0:
                    by_x[x].sort(key=lambda r: (0.0, r))
                    for j, ref in enumerate(by_x[x]):
                        rm[ref] = j
                else:
                    prev_x = sorted_xs[i - 1]
                    by_x[x].sort(
                        key=lambda r, _px=prev_x, _rm=rm: (  # type: ignore[misc]  # mypy cannot infer lambda default-arg types
                            _avg_nbr_row_stereo(r, _px, _rm),
                            r,
                        )
                    )
                    for j, ref in enumerate(by_x[x]):
                        rm[ref] = j

            # Pass 2: right → left.
            rm = _row_map_stereo()
            for i in range(len(sorted_xs) - 2, -1, -1):
                x = sorted_xs[i]
                next_x = sorted_xs[i + 1]
                by_x[x].sort(
                    key=lambda r, _nx=next_x, _rm=rm: (  # type: ignore[misc]  # mypy cannot infer lambda default-arg types
                        _avg_nbr_row_stereo(r, _nx, _rm),
                        r,
                    )
                )
                for j, ref in enumerate(by_x[x]):
                    rm[ref] = j

        # Re-assign y-values: extract the canonical sorted y-values for each
        # x-column and assign them to the barycentric-ordered refs.
        for x in sorted_xs:
            sorted_ys = sorted(result[r][1] for r in by_x[x])
            for ref, new_y in zip(by_x[x], sorted_ys):
                rx, _ry, rrot = result[ref]
                result[ref] = (rx, new_y, rrot)

    return result


# ---------------------------------------------------------------------------
# X-column spread  (Phase 4.3 — prevent column collapse)
# ---------------------------------------------------------------------------


def _spread_x_columns(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    max_per_column: int = 3,
    col_step_mm: float = 25.4,  # 1000 mil — exactly 20 × 1.27 mm grid steps
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
) -> dict[str, tuple[float, float, float | None]]:
    """Spread overloaded x-columns into multiple sub-columns.

    After grid-snapping, several symbols may collapse to exactly the same
    x-coordinate.  If left uncorrected, :func:`_deoverlap_positions` can only
    push them apart vertically, producing a tall and unreadable single stack.
    This pass detects *overloaded* x-columns — those containing more than
    *max_per_column* symbols — and redistributes the excess symbols into
    adjacent sub-columns spaced *col_step_mm* apart, centred on the original
    x-coordinate.

    Within each overloaded column the symbols are sorted by ascending y (then
    by ref for determinism) before partitioning.  Because the Graphviz→KiCad
    y-coordinate encodes tier depth (source tier → small y, load tier → large
    y), this sort order preserves the left-to-right tier ordering that the
    signal-flow layout algorithm establishes.

    This pass must run **before** :func:`_deoverlap_positions` so that the
    Y deoverlap benefits from the reduced per-column density.

    Parameters
    ----------
    positions:
        KiCad mm positions after all specialised snap passes (grid-snapped).
    max_per_column:
        Maximum number of symbols allowed in one x-column before spreading
        begins.  Columns with ≤ *max_per_column* symbols are left untouched.
        Default: 3.
    col_step_mm:
        Horizontal distance between adjacent sub-columns.  Should be a
        multiple of the 1.27 mm KiCad grid; the default 25.4 mm (1000 mil)
        equals exactly 20 grid steps.
    origin_x:
        Left page bound; sub-column x values are clamped to this value.
    page_max_x:
        Right page bound; sub-column x values are clamped to this value.

    Returns
    -------
    dict
        A new positions dict with overloaded columns split into sub-columns.
        Symbols in non-overloaded columns are returned unchanged.
    """
    _GRID: float = 1.27

    by_x: dict[float, list[str]] = defaultdict(list)
    for ref, (x, _y, _r) in positions.items():
        by_x[x].append(ref)

    result = dict(positions)
    for x, group in by_x.items():
        if len(group) <= max_per_column:
            continue
        # Sort by ascending y then ref name for a stable, tier-preserving order.
        group.sort(key=lambda r: (result[r][1], r))
        n_cols = math.ceil(len(group) / max_per_column)
        half = (n_cols - 1) / 2.0
        for col_idx in range(n_cols):
            offset = (col_idx - half) * col_step_mm
            raw_x = x + offset
            # Grid-snap then clamp to page bounds.
            new_x: float = round(round(raw_x / _GRID) * _GRID, 4)
            new_x = max(origin_x, min(page_max_x, new_x))
            start = col_idx * max_per_column
            end = min(start + max_per_column, len(group))
            for ref in group[start:end]:
                _cx, cy, cr = result[ref]
                result[ref] = (new_x, cy, cr)
    return result


def _apply_property_text_spacing(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    near_x_mm: float = _PROPERTY_TEXT_NEAR_X_MM,
    min_vertical_gap_mm: float = _PROPERTY_TEXT_VERTICAL_GAP_MM,
    fixed_refs: frozenset[str] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Reserve a readable vertical lane for generated Reference/Value text.

    Generated component properties now sit above and below the symbol body.
    When nearby components land in the same or an adjacent x-lane, those text
    bands compete for the same whitespace as neighboring symbols and short
    local wires. This late snap pass walks the layout top-to-bottom and
    increases the vertical gap for nearby x-lanes to at least two KiCad rows.

    Only non-power refs are moved; x and rotation are preserved.
    """
    if len(positions) < 2:
        return positions

    result = dict(positions)
    movable_refs = [ref for ref in positions if not ref.startswith("#")]
    if len(movable_refs) < 2:
        return result

    changed = True
    while changed:
        changed = False
        ordered_refs = sorted(movable_refs, key=lambda ref: (result[ref][1], result[ref][0], ref))
        for idx, upper_ref in enumerate(ordered_refs[:-1]):
            upper_x, upper_y, _upper_rot = result[upper_ref]
            for lower_ref in ordered_refs[idx + 1 :]:
                lower_x, lower_y, lower_rot = result[lower_ref]
                target_y = round(upper_y + min_vertical_gap_mm, 4)
                if lower_y + 1e-6 >= target_y:
                    break
                if abs(lower_x - upper_x) > near_x_mm:
                    continue
                if lower_ref in fixed_refs:
                    continue
                if target_y > lower_y + 1e-6:
                    result[lower_ref] = (lower_x, target_y, lower_rot)
                    changed = True

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
# Connector I/O x-bound enforcement (Rule 0)
# ---------------------------------------------------------------------------


def _enforce_connector_x_bounds(
    positions: dict[str, tuple[float, float, float | None]],
    roles: Mapping[str, str],
    *,
    origin_x: float = ORIGIN_X,
    page_max_x: float = PAGE_MAX_X,
    grid: float = 1.27,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp connector x-coordinates to their designated page region.

    Input connectors are clamped to the left 25 % of the usable page width;
    output connectors are clamped to the right 75 %-to-100 % of that width.
    Both bounds are grid-snapped to the KiCad 50-mil grid.

    This is a belt-and-suspenders guarantee that the left → right signal-flow
    convention holds even when Graphviz edge cases misplace a connector (e.g.
    when two connectors have the same hop distance to the nearest IC and the
    alphabetical tiebreak selects the wrong one as the seed).

    Only the x-coordinate is adjusted; y and rotation are preserved.
    Components absent from *positions* or with role ``"unknown"`` are skipped.

    Parameters
    ----------
    positions:
        KiCad mm positions from a previous snap pass.
    roles:
        ``{ref: "input" | "output" | "unknown"}`` from
        :func:`~kicad_pcb.tier.classify_connector_roles`.
    origin_x:
        Left edge of the usable schematic area (default :data:`ORIGIN_X`).
    page_max_x:
        Right edge of the usable schematic area (default :data:`PAGE_MAX_X`).
    grid:
        KiCad snap grid in mm (default 1.27 — 50 mil).
    """
    if not roles:
        return positions

    usable_w = page_max_x - origin_x
    # Snap boundaries to grid so clamped positions land on-grid.
    input_x_limit = round(round((origin_x + usable_w * 0.25) / grid) * grid, 4)
    output_x_limit = round(round((origin_x + usable_w * 0.75) / grid) * grid, 4)

    result = dict(positions)
    for ref, role in roles.items():
        if ref not in result:
            continue
        x, y, rot = result[ref]
        if role == "input" and x > input_x_limit:
            new_x = input_x_limit
            _log.debug("snap: clamping input connector %r x %.2f → %.2f", ref, x, new_x)
            result[ref] = (new_x, y, rot)
        elif role == "output" and x < output_x_limit:
            new_x = output_x_limit
            _log.debug("snap: clamping output connector %r x %.2f → %.2f", ref, x, new_x)
            result[ref] = (new_x, y, rot)
    return result


# ---------------------------------------------------------------------------
# Crossing remediation sweep
# ---------------------------------------------------------------------------


def _remediate_crossings(
    positions: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    max_sweeps: int = 3,
    crossing_ratio_threshold: float = 0.30,
    skip_pairs: frozenset[tuple[str, str]] = frozenset(),
) -> dict[str, tuple[float, float, float | None]]:
    """Reduce wire crossings re-introduced by snap passes via barycentric re-sort.

    Graphviz minimises edge crossings before any snap pass runs, but
    connector y-snapping, stereo splitting, IC-centring, and deoverlap can
    all re-introduce crossings.  This pass:

    1. Builds the signal adjacency graph from *ir* (power nets excluded).
    2. Computes ``ratio = crossings / total_signal_wires``.
    3. If ``ratio ≥ crossing_ratio_threshold``, sorts columns with a two-pass
       barycentric sweep and rebuilds y-slots, then goes back to step 2.
    4. Repeats up to *max_sweeps* times, breaking early when the ratio drops
       below the threshold or the budget is exhausted.
    5. Re-runs :func:`_deoverlap_positions` to fix any new grid collisions
       introduced by the reordering.

    Power symbols (``#PWR`` / ``#FLG``) are excluded from column bucketing
    and are never moved.

    Parameters
    ----------
    positions:
        KiCad mm positions ``(x, y, rot)`` for all components.
    ir:
        Circuit IR used to build the signal adjacency graph.
    max_sweeps:
        Maximum barycentric sweeps to run (default 3, matching
        ``_MAX_REMEDIATION_SWEEPS`` in ``layout.py``).
    crossing_ratio_threshold:
        Crossing ratio below which no further sweep is attempted (default 0.30).
    skip_pairs:
        Passed through to the inner :func:`_deoverlap_positions` call so that
        intentional co-locations (e.g. decoupling caps) are preserved.

    Returns
    -------
    dict[str, tuple[float, float, float | None]]
        Updated positions dict.  Input is not mutated.
    """
    sig_adj = _build_signal_adjacency(ir)
    total_sig_wires = sum(len(v) for v in sig_adj.values()) // 2
    if total_sig_wires == 0:
        return positions

    # Bucket non-power refs by integer column index.
    by_col: dict[int, list[str]] = defaultdict(list)
    for ref, (x, _y, _rot) in positions.items():
        if ref.startswith("#"):  # exclude #PWR / #FLG — already at fixed rows
            continue
        col_idx = round((x - ORIGIN_X) / _GRID_COL_MM)
        by_col[col_idx].append(ref)

    result = dict(positions)

    for sweep in range(max_sweeps):
        # count_wire_crossings takes 2-tuples; strip rotation.
        pos2: dict[str, tuple[float, float]] = {r: (x, y) for r, (x, y, _) in result.items()}
        crossing_count = _count_wire_crossings(pos2, sig_adj)
        ratio = crossing_count / total_sig_wires
        _log.debug(
            "remediate_crossings: sweep %d crossings=%d wires=%d ratio=%.2f",
            sweep + 1,
            crossing_count,
            total_sig_wires,
            ratio,
        )
        if ratio < crossing_ratio_threshold or sweep == max_sweeps - 1:
            break

        # Sort columns to reduce crossings.
        by_col = _barycentric_sort(dict(by_col), sig_adj)

        # Rebuild positions: within each column, redistribute current sorted
        # y-slots across the new barycentric ref order.  x and rotation are
        # preserved per-ref (refs within a column may have slightly different x
        # values from Graphviz; we keep each ref's own x unchanged).
        new_result = dict(result)
        for col_refs in by_col.values():
            if len(col_refs) < 2:
                continue
            y_slots = sorted(result[r][1] for r in col_refs)
            for ref, new_y in zip(col_refs, y_slots):
                x, _old_y, rot = result[ref]
                new_result[ref] = (x, new_y, rot)
        result = new_result

    # Re-run deoverlap: barycentric sort may co-locate refs within a column.
    result = _deoverlap_positions(result, skip_pairs=skip_pairs)
    return result


# ---------------------------------------------------------------------------
# Page-bounds clamp
# ---------------------------------------------------------------------------


def _clamp_to_page(
    positions: dict[str, tuple[float, float, float | None]],
    *,
    min_x: float = ORIGIN_X,
    min_y: float = ORIGIN_Y,
    max_x: float = PAGE_MAX_X,
    max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Clamp every position so it stays within the printable A4 area.

    Snap passes (connector snaps, stereo split, deoverlap, crossing
    remediation) can push individual components outside the page bounds,
    which triggers LAY004.  This pass runs last to guarantee no position
    violates the page boundary.

    Only x and y are modified; rotation is always preserved.
    """
    out: dict[str, tuple[float, float, float | None]] = {}
    for ref, (x, y, rot) in positions.items():
        cx = max(min_x, min(max_x, x))
        cy = max(min_y, min(max_y, y))
        out[ref] = (cx, cy, rot)
    return out


class _QuadrantUtilization(TypedDict):
    top_left: float
    top_right: float
    bottom_left: float
    bottom_right: float
    imbalance: float
    dense_quadrant: str
    sparse_quadrant: str


def _compute_page_quadrant_utilization(
    positions: Mapping[str, tuple[float, float, float | None]],
    *,
    origin_x: float = ORIGIN_X,
    origin_y: float = ORIGIN_Y,
    page_max_x: float = PAGE_MAX_X,
    page_max_y: float = PAGE_MAX_Y,
) -> _QuadrantUtilization:
    """Measure how components are distributed across the four page quadrants.

    Divides the page into four equal quadrants using the **page centre**
    (not the component bounding-box centre) and counts how many components
    fall in each quadrant.  The page centre is the literal midpoint of the
    usable schematic area so the measurement is stable regardless of the
    circuit's actual footprint.

    Parameters
    ----------
    positions:
        Current ``{ref: (x, y, rot)}`` position map.
    origin_x, origin_y:
        Page margin constants (default :data:`ORIGIN_X` / :data:`ORIGIN_Y`).
    page_max_x, page_max_y:
        Right and bottom page boundaries (default :data:`PAGE_MAX_X` /
        :data:`PAGE_MAX_Y`).

    Returns
    -------
    dict with keys:

    * ``top_left``, ``top_right``, ``bottom_left``, ``bottom_right`` —
      float fractions (0.0–1.0) of components in each quadrant.
    * ``imbalance`` — ``max_fraction − min_fraction`` (0 = perfectly
      balanced; 1 = all components in one quadrant).
    * ``dense_quadrant`` — name of the most-populated quadrant.
    * ``sparse_quadrant`` — name of the least-populated quadrant.
    """
    _QUADRANTS = ("top_left", "top_right", "bottom_left", "bottom_right")
    if not positions:
        return {
            "top_left": 0.0,
            "top_right": 0.0,
            "bottom_left": 0.0,
            "bottom_right": 0.0,
            "imbalance": 0.0,
            "dense_quadrant": _QUADRANTS[0],
            "sparse_quadrant": _QUADRANTS[0],
        }

    cx = (origin_x + page_max_x) / 2.0
    cy = (origin_y + page_max_y) / 2.0

    counts: dict[str, int] = {q: 0 for q in _QUADRANTS}
    for x, y, _ in positions.values():
        if x <= cx and y <= cy:
            counts["top_left"] += 1
        elif x > cx and y <= cy:
            counts["top_right"] += 1
        elif x <= cx:
            counts["bottom_left"] += 1
        else:
            counts["bottom_right"] += 1

    total = len(positions)
    fractions: dict[str, float] = {k: v / total for k, v in counts.items()}
    dense = max(fractions, key=lambda k: fractions[k])
    sparse = min(fractions, key=lambda k: fractions[k])
    return {
        "top_left": fractions["top_left"],
        "top_right": fractions["top_right"],
        "bottom_left": fractions["bottom_left"],
        "bottom_right": fractions["bottom_right"],
        "imbalance": round(fractions[dense] - fractions[sparse], 4),
        "dense_quadrant": dense,
        "sparse_quadrant": sparse,
    }


def _snap_page_balance(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> tuple[dict[str, tuple[float, float, float | None]], float]:
    """Nudge signal-path components toward the vertical page centre (Phase 8.1).

    Power-entry and decoupling components are intentionally placed near the
    top of the page (schematic convention), so they are excluded from both
    the centre-of-gravity computation and the shift.  Only signal-path
    components (INPUT, PRECONDITIONING, OPAMP_CORE, FEEDBACK, OUTPUT) are
    considered.

    A gentle proportional correction
    (:data:`_PAGE_BALANCE_CORRECTION` × detected deviation) is applied when
    the signal-circuit centre deviates from the page centre Y by more than
    :data:`_PAGE_BALANCE_DEAD_ZONE_MM`.  The pass is a no-op when
    *block_layout* is ``None`` because role information is required to
    distinguish signal refs from power refs.

    Positions are not clamped here; :func:`_clamp_to_page` handles the
    final page-boundary enforcement.

    Returns:
        Tuple of (adjusted positions dict, shift in mm). Shift is 0.0 if no
        balance correction was applied.
    """
    if not positions or block_layout is None:
        return dict(positions), 0.0

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: a.role for ref, a in block_layout.assignments.items()}
    signal_refs = [
        ref
        for ref in positions
        if (
            (role := role_by_ref.get(ref)) is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
        )
        # Never move KiCad internal power/flag symbols (#PWR*, #FLG*, #NET*
        # etc.).  _snap_power_symbols pins them to specific page rows; moving
        # them afterward would violate that invariant.  Some circuits also
        # misclassify these refs through block detection heuristics, so an
        # explicit prefix guard is more robust than relying on role alone.
        and not ref.startswith("#")
    ]
    if not signal_refs:
        return dict(positions), 0.0

    signal_ys = [positions[ref][1] for ref in signal_refs]
    circuit_center_y = sum(signal_ys) / len(signal_ys)
    page_center_y = (origin_y + page_max_y) / 2.0

    delta = page_center_y - circuit_center_y
    if abs(delta) < _PAGE_BALANCE_DEAD_ZONE_MM:
        _log.debug(
            "page balance: δy=%.2f mm < dead zone %.2f mm — skipping",
            delta,
            _PAGE_BALANCE_DEAD_ZONE_MM,
        )
        return dict(positions), 0.0

    shift = round(delta * _PAGE_BALANCE_CORRECTION, 2)
    _log.debug(
        "page balance: circuit_center_y=%.2f page_center_y=%.2f δy=%.2f shift=%.2f mm",
        circuit_center_y,
        page_center_y,
        delta,
        shift,
    )

    # Quantise the shift to the 1.27 mm KiCad 50-mil grid so all positions
    # remain grid-aligned after the balance nudge.
    _GRID_SNAP = 1.27
    shift = round(round(shift / _GRID_SNAP) * _GRID_SNAP, 4)

    # Sanity check: ensure the shift doesn't create NEW overlaps. If two refs
    # didn't previously share a y-coordinate but would after shifting, abort.
    current_ys = [round(positions[ref][1], 2) for ref in signal_refs]
    shifted_ys_check = [round(positions[ref][1] + shift, 2) for ref in signal_refs]
    current_y_set = set(current_ys)
    shifted_y_set = set(shifted_ys_check)
    # A new overlap happens if: len(shifted_y_set) < len(current_y_set).
    if len(shifted_y_set) < len(current_y_set):
        # The shift would collapse distinct y-coordinates into duplicates.
        # This is a real overlap risk — abort.
        _log.debug("page balance: shift %.2f mm would create new overlaps — skipping", shift)
        return dict(positions), 0.0

    result = dict(positions)
    for ref in signal_refs:
        x, y, rot = result[ref]
        result[ref] = (x, round(y + shift, 2), rot)
    return result, shift


def _shift_refs_y(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    delta_y: float,
) -> dict[str, tuple[float, float, float | None]]:
    """Return a copy of *positions* with the selected refs shifted by *delta_y*."""
    result = dict(positions)
    for ref in refs:
        x, y, rot = result[ref]
        result[ref] = (x, round(y + delta_y, 4), rot)
    return result


def _shift_creates_y_overlap(
    positions: Mapping[str, tuple[float, float, float | None]],
    refs: list[str],
    delta_y: float,
) -> bool:
    """Return True when shifting *refs* by *delta_y* collapses distinct y-values."""
    current_ys = [round(positions[ref][1], 2) for ref in refs]
    shifted_ys = [round(positions[ref][1] + delta_y, 2) for ref in refs]
    return len(set(shifted_ys)) < len(set(current_ys))


def _central_composition_refs(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None,
) -> tuple[list[str], list[str]]:
    """Return the signal-path refs and op-amp refs used by Phase 8.2 checks."""
    if block_layout is None:
        signal_refs = [ref for ref in positions if not ref.startswith("#")]
        return signal_refs, []

    from ..block_detection import BlockRole  # noqa: PLC0415

    role_by_ref = {ref: assignment.role for ref, assignment in block_layout.assignments.items()}
    signal_refs = [
        ref
        for ref in positions
        if (
            (role := role_by_ref.get(ref)) is not None
            and (
                is_input_like_role(role)
                or is_core_like_role(role)
                or is_output_like_role(role)
                or role == BlockRole.DECOUPLING
            )
            and not ref.startswith("#")
        )
    ]
    opamp_refs = [ref for ref in signal_refs if role_by_ref.get(ref) == BlockRole.OPAMP_CORE]
    return signal_refs, opamp_refs


def _apply_opamp_vertical_nudge(
    positions: Mapping[str, tuple[float, float, float | None]],
    signal_refs: list[str],
    opamp_refs: list[str],
    page_bounds: tuple[float, float],
) -> dict[str, tuple[float, float, float | None]]:
    """Nudge signal-path refs when the op-amp stage is too high or too low."""
    if not opamp_refs:
        return dict(positions)

    origin_y, page_max_y = page_bounds
    page_height = page_max_y - origin_y
    lower_limit_y = origin_y + _OPAMP_LOWER_LIMIT_FRACTION * page_height
    upper_limit_y = origin_y + _OPAMP_UPPER_LIMIT_FRACTION * page_height
    page_center_y = (origin_y + page_max_y) / 2.0
    opamp_avg_y = sum(positions[ref][1] for ref in opamp_refs) / len(opamp_refs)

    shift = 0.0
    limit_y = 0.0
    direction = ""
    if opamp_avg_y > lower_limit_y:
        shift = -round(round((opamp_avg_y - page_center_y) / 1.27) * 1.27, 4)
        limit_y = lower_limit_y
        direction = "low"
    elif opamp_avg_y < upper_limit_y:
        shift = round(round((page_center_y - opamp_avg_y) / 1.27) * 1.27, 4)
        limit_y = upper_limit_y
        direction = "high"

    if shift == 0.0:
        return dict(positions)

    if _shift_creates_y_overlap(positions, signal_refs, shift):
        _log.debug(
            "central composition: op-amp-%s nudge %.2f mm would create overlaps — skipping",
            direction,
            abs(shift),
        )
        return dict(positions)

    _log.debug(
        "central composition: op-amp too %s (avg_y=%.2f limit=%.2f); nudging %s %.2f mm",
        direction,
        opamp_avg_y,
        limit_y,
        "up" if shift < 0 else "down",
        abs(shift),
    )
    return _shift_refs_y(positions, signal_refs, shift)


def _snap_central_composition(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout | None = None,
    *,
    origin_y: float = ORIGIN_Y,
    page_max_y: float = PAGE_MAX_Y,
) -> dict[str, tuple[float, float, float | None]]:
    """Phase 8.2: enforce sensible vertical composition.

    Three checks are applied in order:

    1. **Title-block clearance** — Any signal-path component whose y-coordinate
       exceeds ``page_max_y - _TITLE_BLOCK_CLEARANCE_MM`` encroaches on the
       KiCad title block area.  When such encroachment is detected, all
       signal-path components are shifted upward by the minimum grid-quantized
       amount needed to bring the lowest component to the safe y boundary.

    2. **Op-amp vertical position** — If *block_layout* is provided and the
       average y-coordinate of OPAMP_CORE components falls outside the central
       band (below :data:`_OPAMP_LOWER_LIMIT_FRACTION` or above
       :data:`_OPAMP_UPPER_LIMIT_FRACTION` of the usable vertical range), a
       corrective grid-quantized nudge is applied to all signal-path
       components.  The nudge is proportional to the deviation from the page
       centre so it is gentle by default, and is skipped entirely when it
       would collapse distinct y-coordinates into duplicates.

    3. **Circuit span logging** — If the vertical span of signal-path
       components is less than :data:`_MIN_CIRCUIT_SPAN_FRACTION` × available
       height, a debug warning is emitted.  No position change is made for
       this case.

    When *block_layout* is ``None``, all non-``#`` refs are treated as
    signal-path components and the op-amp-specific check (pass 2) is skipped.
    The pass is a no-op when *positions* is empty.
    """
    if not positions:
        return dict(positions)

    _GRID_SNAP = 1.27
    signal_refs, opamp_refs = _central_composition_refs(positions, block_layout)

    if not signal_refs:
        return dict(positions)

    result = dict(positions)

    # ---- Pass 1: title-block clearance (hard constraint) ----
    safe_max_y = page_max_y - _TITLE_BLOCK_CLEARANCE_MM
    lowest_signal_y = max(result[ref][1] for ref in signal_refs)
    if lowest_signal_y > safe_max_y:
        raw_push = lowest_signal_y - safe_max_y
        # Ceil to grid so the lowest component lands *at or above* safe_max_y.
        push_up = round(math.ceil(raw_push / _GRID_SNAP) * _GRID_SNAP, 4)
        _log.debug(
            "central composition: title-block encroachment"
            " (lowest=%.2f safe=%.2f); shifting all signal refs up %.2f mm",
            lowest_signal_y,
            safe_max_y,
            push_up,
        )
        result = _shift_refs_y(result, signal_refs, -push_up)

    # ---- Pass 2: op-amp vertical position (soft nudge) ----
    result = _apply_opamp_vertical_nudge(
        result,
        signal_refs,
        opamp_refs,
        (origin_y, page_max_y),
    )

    # ---- Pass 3: circuit span diagnostic ----
    span_ys = [result[ref][1] for ref in signal_refs]
    circuit_span = max(span_ys) - min(span_ys)
    available_height = page_max_y - origin_y
    if circuit_span < _MIN_CIRCUIT_SPAN_FRACTION * available_height:
        _log.debug(
            "central composition: small vertical span %.2f mm"
            " (%.0f%% of %.0f mm available);"
            " consider spreading components vertically",
            circuit_span,
            100.0 * circuit_span / available_height,
            available_height,
        )

    return result


# ---------------------------------------------------------------------------
# Composite snap coordinator
# ---------------------------------------------------------------------------


def _apply_post_layout_snaps(  # noqa: PLR0913, PLR0915
    result: dict[str, tuple[float, float, float | None]],
    ir: CircuitIR,
    *,
    feedback_refs: set[str],
    annotations: dict[str, _ComponentAnnotation],
    channels: Mapping[str, str],
    decoupling_map: dict[str, str],
    roles: Mapping[str, str] | None = None,
    halo: Mapping[str, str] | None = None,
    block_layout: BlockLayout | None = None,
    heuristic_policy: LayoutHeuristicPolicy = DEFAULT_LAYOUT_HEURISTIC_POLICY,
    strict: bool = False,
) -> dict[str, tuple[float, float, float | None]]:
    """Apply all post-layout positional corrections in canonical order.

    The passes must run in the order shown — see the module docstring for the
    ordering rationale:

    1. :func:`snap_positions` — quantise to the KiCad 50-mil grid.
    2. :func:`_snap_power_symbols` — clamp ``#PWR``/``#FLG`` to top/bottom row.
    2b. :func:`_enforce_connector_x_bounds` — clamp input/output connectors
        to the left/right 25 % of the page (skipped when *roles* is ``None``).
    2c. :func:`_snap_connectors_to_ic_y` — pull each connector's y to the
        median y of its signal-net neighbours (Rule 2).
    3. :func:`_snap_feedback_components` — pull feedback passives above anchor
       IC (skipped when *feedback_refs* is empty).
    3b. :func:`_snap_opamp_halo` — normalize halo members into adjacent lanes
        near their anchor IC (skipped when *halo* is ``None``).
    3c. :func:`_snap_block_zones` — bias components toward their functional
        block zones (INPUT left, OUTPUT right, POWER top; skipped when
        *block_layout* is ``None``).
    3d. :func:`_apply_density_spreading` — push apart symbols in dense
        clusters (≥ 5 neighbors within 30mm) to reduce local crowding;
        respects block boundaries when *block_layout* is provided (Phase
        2.3; skipped when *block_layout* is ``None``).
    4. :func:`_apply_stereo_split` — compress L/R components into page halves
       (skipped when no L or R channel is present in *channels*).
    4b. :func:`_post_stereo_barycentric` — reduce intra-channel crossings after
       the stereo split by applying a two-pass barycentric sort within each
       channel band (R3-3; skipped when no L/R channels present).
    5. :func:`_compact_y_gap` — collapse the largest vertical gap.
    6. :func:`_center_ics_in_columns` — re-sort each column so ICs land at the
       vertical midpoint with passives above and below (always runs; no-op
       when no column contains an IC).
    7. :func:`_post_snap_decoupling_caps` — co-locate bypass caps above their
       IC (skipped when *decoupling_map* is empty).
    7b. :func:`_snap_opamp_locality` — enforce op-amp-centric local staging
        (input-side left, output-side right, feedback near op-amp, decoupling
        separated from feedback).
    7c. :func:`_snap_input_stage_cohesion` — keep input connector and
        preconditioning parts as a compact left-side stage with a short,
        readable transition into the op-amp input side.
    7d. :func:`_snap_output_stage_cohesion` — keep output connector and
        feedback parts as a compact right-side stage with a short,
        readable transition from the op-amp output side.
    7e. :func:`_snap_page_balance` — nudge signal-path components toward the
        vertical page centre when the circuit's centre of gravity deviates by
        more than :data:`_PAGE_BALANCE_DEAD_ZONE_MM`.  Power-entry and
        decoupling components are excluded (they belong at the top by
        convention).  Only a gentle proportional correction is applied so that
        the balance pass does not fight the earlier structural snap passes.
    7f. :func:`_post_snap_decoupling_caps` (if *decoupling_map* non-empty) —
        re-anchors bypass caps after the balance shift.  Because block
        detection can assign a non-DECOUPLING role to a cap (e.g. POWER_ENTRY
        when it sits on a VCC_* net), the balance pass may move only the IC
        and leave the cap behind.  A second run of this pass restores the
        cap.y = IC.y − GRID_ROW_MM invariant.
    7g. :func:`_snap_central_composition` — enforce sensible vertical
        composition: (1) shift signal-path components up when any of them
        encroaches on the KiCad title-block clearance zone at the page bottom;
        (2) nudge when the op-amp (OPAMP_CORE) average y-coordinate falls
        outside the central 60 % of the vertical range; (3) emit a debug
        warning when the circuit vertical span is very small.
    7h. :func:`_apply_property_text_spacing` — reserve extra vertical space
        for components that share the same or a nearby x-lane so visible
        ``Reference``/``Value`` text does not collapse onto nearby symbol
        bodies or short local wire corridors.
    8. :func:`_spread_x_columns` — split overloaded x-columns (> 3 symbols at
       the same x) into sub-columns spaced 25.4 mm apart so the Y deoverlap
       does not produce unreadable vertical stacks.
    9. :func:`_deoverlap_positions` — push any remaining grid collisions apart.
    10. :func:`_remediate_crossings` — measure crossing ratio; if ≥ 0.30 apply
        barycentric column-sort sweeps (up to 3) then re-run deoverlap.
    11. :func:`_clamp_to_page` — clamp every position to the A4 printable area
        (``ORIGIN_X..PAGE_MAX_X`` × ``ORIGIN_Y..PAGE_MAX_Y``); prevents LAY004.
    """
    result = snap_positions(result)
    result = _snap_power_symbols(result, ir)
    if roles:
        result = _enforce_connector_x_bounds(result, roles)
    result = _snap_connectors_to_ic_y(result, ir)
    if feedback_refs:
        result = _snap_feedback_components(result, annotations, ir, strict=strict)
    if halo:
        result = _snap_opamp_halo(result, halo)
    if block_layout:
        result = _snap_block_zones(result, block_layout)
        result = _apply_density_spreading(result, block_layout)
    if any(v in ("L", "R") for v in channels.values()):
        result = _apply_stereo_split(result, channels)
        result = _post_stereo_barycentric(result, ir, channels)
    result = _compact_y_gap(result, ir)
    result = _center_ics_in_columns(result, halo=halo)
    result = heuristic_policy.apply_decoupling_snap(result, decoupling_map)
    # Build canonical skip-pairs from the decoupling map so that intentional
    # one-grid-row cap/IC co-locations are not nudged by _deoverlap_positions.
    decouple_skip: frozenset[tuple[str, str]] = frozenset(
        (min(cap, ic), max(cap, ic)) for cap, ic in decoupling_map.items()
    )
    result = _spread_x_columns(result)
    result = _deoverlap_positions(result, skip_pairs=decouple_skip)
    result = _remediate_crossings(result, ir, skip_pairs=decouple_skip)
    # Re-apply op-amp locality after crossing remediation so op-amp neighborhoods
    # remain readable in the final coordinates.
    result = heuristic_policy.apply_opamp_locality(
        result,
        ir,
        annotations=annotations,
        context=_OpAmpLocalityContext(
            decoupling_map=decoupling_map,
            halo=halo,
            block_layout=block_layout,
        ),
    )
    result = heuristic_policy.apply_input_stage_cohesion(
        result,
        ir,
        block_layout=block_layout,
    )
    result = heuristic_policy.apply_output_stage_cohesion(
        result,
        ir,
        block_layout=block_layout,
    )
    result, page_balance_shift = _snap_page_balance(result, block_layout)
    if page_balance_shift != 0.0:
        result = heuristic_policy.apply_decoupling_snap(result, decoupling_map)
    # 7g: Phase 8.2 — central composition (title-block clearance + op-amp vertical bounds).
    result = _snap_central_composition(result, block_layout)
    protected_text_refs: frozenset[str] = frozenset()
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        protected_text_roles = {BlockRole.DECOUPLING}
        protected_text_refs = frozenset(
            ref
            for ref, assignment in block_layout.assignments.items()
            if assignment.role in protected_text_roles or is_core_like_role(assignment.role)
        )
    result = _apply_property_text_spacing(result, fixed_refs=protected_text_refs)
    # Use grid-safe max bounds so final clamped coordinates stay on the
    # 1.27 mm KiCad grid even at the right/bottom page edges.
    grid = 1.27
    grid_max_x = round(math.floor(PAGE_MAX_X / grid) * grid, 4)
    grid_max_y = round(math.floor(PAGE_MAX_Y / grid) * grid, 4)
    result = _clamp_to_page(result, max_x=grid_max_x, max_y=grid_max_y)
    late_skip_pairs = set(decouple_skip)
    if block_layout is not None:
        from ..block_detection import BlockRole  # noqa: PLC0415

        protected_by_x: dict[float, list[str]] = defaultdict(list)
        for ref, (x, _y, _rot) in result.items():
            assignment = block_layout.assignments.get(ref)
            if assignment is None or (
                assignment.role != BlockRole.DECOUPLING and not is_core_like_role(assignment.role)
            ):
                continue
            protected_by_x[x].append(ref)
        for refs in protected_by_x.values():
            if len(refs) < 2:
                continue
            refs.sort()
            for idx, left in enumerate(refs[:-1]):
                for right in refs[idx + 1 :]:
                    late_skip_pairs.add((left, right))
    # Late locality/cohesion/composition passes can still reintroduce same-column
    # collisions after the earlier deoverlap step, and final clamping can merge
    # edge-bound components back onto the same grid cell. Run one last deoverlap
    # pass on the final clamped coordinates.
    result = _deoverlap_positions(result, skip_pairs=frozenset(late_skip_pairs))
    result = _clamp_to_page(result, max_x=grid_max_x, max_y=grid_max_y)
    return result
