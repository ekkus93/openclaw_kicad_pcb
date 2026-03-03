"""Graphviz-based schematic layout engine for kicad_pcb.

Uses ``dot -Tplain`` to lay out a *bipartite* graph of circuit components
and nets so that signals flow left → right (``rankdir=LR``).

Graph model (4.3 — bipartite)
------------------------------
* **Component nodes** — one node per reference designator (e.g. ``R1``).
* **Net nodes** — one node per net (id ``net_<name>``).
* **Edges** — connect each component to every net it participates in.
* **Power nets** (GND, VCC, VDD, V+, V- and similar short all-caps names)
  are *excluded* from the bipartite graph to avoid creating highly-connected
  hubs.  Components connected only via power nets are placed in a dedicated
  right-hand cluster.

Coordinate mapping
------------------
``dot -Tplain`` reports positions in Graphviz "point" units (72 pt/inch),
with the origin at the lower-left and y increasing upward.  We map to KiCad
mm coordinates with y increasing downward:

    x_mm = ORIGIN_X + gv_x × SCALE_MM_PER_GV
    y_mm = ORIGIN_Y + (max_gv_y − gv_y) × SCALE_MM_PER_GV

Public API
----------
:func:`find_dot_binary`     — locate the ``dot`` executable.
:class:`GraphvizLayoutEngine` — :class:`~kicad_pcb.layout_engine.LayoutEngine`
                                implementation.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .circuit_ir import CircuitIR

from .component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from .component_types import IC_PREFIXES as _IC_PREFIXES_CT
from .component_types import is_power_net as _is_power_net
from .gv_cache import _layout_cache_key, _load_layout_cache, _save_layout_cache
from .gv_dot_builder import (
    _assign_bfs_tiers,
    _build_dot_source,
    _compute_net_weights,
    _emit_decoupling_constraints,
    _find_decoupling_caps,
    _is_capacitor,
    _is_connector,
    _safe_id,
)
from .layout import ComponentAnnotation as _ComponentAnnotation
from .layout import compute_orientations as _compute_orientations
from .layout import detect_stereo_channels as _detect_stereo_channels
from .layout import find_feedback_paths as _find_feedback_paths
from .tier import assign_ic_units_to_tiers as _assign_ic_units_to_tiers
from .tier import assign_tiers as _assign_tiers

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ORIGIN_X: float = 30.48  # mm — left margin on an A4 page
ORIGIN_Y: float = 50.80  # mm — top margin on an A4 page

# Usable schematic area on an A4 sheet (page is 297 × 210 mm).
# Leave a 10 mm gutter on the right and bottom edges.
PAGE_MAX_X: float = 287.0  # mm (297 - 10)
PAGE_MAX_Y: float = 200.0  # mm (210 - 10)

# Minimum centre-to-centre distance that avoids a LAY003 overlap warning
# (symbol bounding box = ±5.08 mm, so touching copies are 10.16 mm apart).
# Used by _apply_stereo_split to push compressed same-column components apart.
_STEREO_DEOVERLAP_MIN_MM: float = 10.17  # 2 × 5.08 + ε

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

# Maximum number of subprocess attempts (retry on transient failures).
_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Binary discovery
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Bundled binary slot
# ---------------------------------------------------------------------------

# When a ``dot`` binary is shipped alongside the package it should be placed
# at ``<package_root>/bin/dot`` (or ``bin/dot.exe`` on Windows).  The slot is
# checked *before* the environment variable and PATH lookup so the bundled
# binary is always preferred when present.
#
# Currently no binary is bundled; the constant resolves to a path that will
# not exist, so the check always falls through to the env-var / PATH steps.
_BUNDLED_DOT_PATH: Path = Path(__file__).parent / "bin" / "dot"


def find_dot_binary() -> str | None:
    """Locate the ``dot`` binary used for Graphviz layout.

    Search order:

    1. **Bundled binary** — ``<package>/bin/dot`` if present and executable.
    2. :envvar:`GRAPHVIZ_DOT` environment variable.
    3. System :data:`PATH` (``shutil.which``).

    Returns the resolved path string or ``None`` if not found.

    Use :func:`find_dot_source` to get both the path and discovery source.
    """
    # 1. Bundled binary (preferred when present).
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH)
    # 2. GRAPHVIZ_DOT environment variable.
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
        return env_val
    # 3. System PATH.
    return shutil.which("dot")


def find_dot_source() -> tuple[str, str] | None:
    """Return ``(path, source)`` for the resolved ``dot`` binary.

    *source* is one of ``"bundled"``, ``"GRAPHVIZ_DOT"``, or ``"PATH"``.
    Returns ``None`` if ``dot`` cannot be found.
    """
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH), "bundled"
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val and Path(env_val).is_file() and os.access(env_val, os.X_OK):
        return env_val, "GRAPHVIZ_DOT"
    path = shutil.which("dot")
    if path:
        return path, "PATH"
    return None


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

    Formulae (as specified in Rule \u00a78):

    * L: ``y_final = origin_y + (y_relative \u00d7 0.45)``
    * R: ``y_final = origin_y + page_height \u00d7 0.55 + (y_relative \u00d7 0.45)``
    * mono: unchanged

    where ``y_relative = y \u2212 origin_y`` and
    ``page_height = page_max_y \u2212 origin_y``.

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
      ``y = page_max_y - 20`` (bottom row, clear of the lower margin).
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
        target_y = round(page_max_y - 20.0, 2) if is_gnd else origin_y
        x, _, rot = result[ref]
        result[ref] = (x, target_y, rot)
    return result


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
    proportionally shrunk (preserving relative distances) until it fits.
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

    # --- Page-fit normalisation -------------------------------------------
    # If any position lies outside the usable area, proportionally scale the
    # whole layout down so that every position is within [origin_x..PAGE_MAX_X]
    # × [origin_y..PAGE_MAX_Y].  This keeps relative topology intact.
    max_x = max(pos[0] for pos in result.values())
    max_y = max(pos[1] for pos in result.values())
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
        result = {
            name: (
                round(origin_x + (pos[0] - origin_x) * shrink, 2),
                round(origin_y + (pos[1] - origin_y) * shrink, 2),
                None,
            )
            for name, pos in result.items()
        }

    return result


# ---------------------------------------------------------------------------
# GraphvizLayoutEngine
# ---------------------------------------------------------------------------


class GraphvizLayoutEngine:
    """Calls ``dot -Tplain`` to produce left-to-right schematic layouts.

    Parameters
    ----------
    dot_path:
        Absolute (or PATH-resolved) path to the ``dot`` executable.
    scale:
        Scale factor (mm per Graphviz point unit). Default: ``SCALE_MM_PER_GV``.
    timeout:
        Subprocess timeout in seconds. Default: 10.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        dot_path: str,
        scale: float = SCALE_MM_PER_GV,
        timeout: float = 10.0,
        seed: int = 7,
        cache_path: Path | None = None,
        tiers: dict[str, int] | None = None,
    ) -> None:
        self._dot = dot_path
        self._scale = scale
        self._timeout = timeout
        self._seed = seed
        self._cache_path = cache_path
        self._tiers = tiers

    # ----------------------------------------------------------------
    # LayoutEngine Protocol
    # ----------------------------------------------------------------

    def compute_symbol_positions(
        self, ir: CircuitIR
    ) -> dict[str, tuple[float, float, float | None]]:
        """Run ``dot`` on *ir* and return ``{ref: (x_mm, y_mm, rotation_deg)}``.

        **Rotation:** Orientation is computed via
        :func:`~kicad_pcb.layout.compute_orientations` after Graphviz layout
        and merged into each tuple.  Connectors at tier 0 get 0°; last-tier
        connectors get 180°; series passives get 0° (or 90° when y-spread
        dominates); shunt/bypass passives get 90°.

        **Snapping:** All positions are snapped to the KiCad 50-mil grid
        (1.27 mm) after the Graphviz raw output is processed.

        **Cache:** If *cache_path* was supplied and a cached result exists for
        the current circuit topology (keyed by sha256 of the DOT source), the
        cached positions are returned immediately without invoking ``dot``.
        After a fresh run the result is written back to the cache.

        **Determinism:** ``dot`` is invoked with ``-Gstart=<seed>`` (default
        7) so that repeated runs on identical input produce stable output.

        Raises :class:`RuntimeError` if ``dot`` fails or returns no positions.
        """
        refs = sorted(c.ref for c in ir.components)
        if not refs:
            return {}

        # Detect decoupling caps before building DOT source so the same map
        # can be used both for invisible-edge constraints and post-layout snap.
        decoupling_map = _find_decoupling_caps(ir)

        # Detect feedback components (passives that form back-edges).
        _tiers = self._tiers if self._tiers is not None else _assign_tiers(ir)
        annotations = _find_feedback_paths(ir, _tiers)
        feedback_refs: set[str] = {r for r, a in annotations.items() if a.feedback}

        # Detect multi-unit IC groups; extract power units for cluster_power.
        _unit_groups = _assign_ic_units_to_tiers(ir, _tiers)
        _power_unit_refs: set[str] = {
            g.power_unit for g in _unit_groups.values() if g.power_unit is not None
        }

        # Build DOT source up-front so we can derive the cache key.
        # Pass pre-computed tiers so _build_dot_source skips a redundant assign_tiers call.
        dot_source = _build_dot_source(
            ir,
            decoupling_map=decoupling_map,
            feedback_refs=feedback_refs or None,
            power_unit_refs=_power_unit_refs or None,
            tiers=_tiers,
        )
        cache_key = _layout_cache_key(dot_source)

        # --- Cache hit: return immediately without invoking dot. ---
        if self._cache_path is not None:
            cached = _load_layout_cache(self._cache_path, cache_key)
            if cached is not None:
                _log.debug("Layout cache hit (key %s…); skipping dot.", cache_key[:8])
                return cached

        try:
            positions = self._run_dot(dot_source)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Graphviz 'dot' failed: {exc}.  Command: {self._dot} -Tplain -Gstart={self._seed}"
            ) from exc

        if not positions:
            raise RuntimeError(
                "Graphviz 'dot' returned no node positions.  "
                f"Command: {self._dot} -Tplain -Gstart={self._seed}"
            )

        # Verify all refs are covered.
        missing = [r for r in refs if _safe_id(r) not in positions]
        if missing:
            raise RuntimeError(
                f"Graphviz 'dot' did not return positions for refs: {missing!r}.  "
                "Check the DOT graph for isolated nodes or unsupported syntax, "
                "or file a bug with the circuit IR."
            )

        # Re-key from safe_id → original ref
        safe_to_ref = {_safe_id(r): r for r in refs}
        result: dict[str, tuple[float, float, float | None]] = {
            safe_to_ref[sid]: pos for sid, pos in positions.items() if sid in safe_to_ref
        }

        # Post-layout: snap raw Graphviz positions to the KiCad 50-mil grid
        # before any specialised snaps run.  The specialised post-layout passes
        # below may override individual positions with off-grid values (e.g.
        # ORIGIN_Y for power rails, precise IC offsets for decoupling caps);
        # that is intentional — they take priority over the grid snap.
        result = snap_positions(result)

        # Post-layout: snap #PWR/#FLG power symbols to top or bottom page row.
        result = _snap_power_symbols(result, ir)

        # Post-layout: snap feedback components above their anchor IC/connector.
        if feedback_refs:
            result = _snap_feedback_components(result, annotations, ir)

        # Post-layout: apply stereo L/R vertical split.
        channels = _detect_stereo_channels(ir)
        if any(v in ("L", "R") for v in channels.values()):
            result = _apply_stereo_split(result, channels)

        # Post-layout: snap decoupling caps to sit directly above their IC.
        if decoupling_map:
            result = _post_snap_decoupling_caps(result, decoupling_map)

        # Compute component orientations (rotation in degrees) from signal topology
        # and merge into the result so callers receive (x, y, rotation) triples.
        _plain_positions: dict[str, tuple[float, float]] = {
            ref: (x, y) for ref, (x, y, _) in result.items()
        }
        _orientations = _compute_orientations(ir, _plain_positions, _tiers)
        result = {
            ref: (x, y, float(_orientations.get(ref, 0))) for ref, (x, y, _) in result.items()
        }

        # --- Cache write: persist for next run. ---
        if self._cache_path is not None:
            _save_layout_cache(self._cache_path, cache_key, result)
            _log.debug("Layout cache written (key %s…).", cache_key[:8])

        return result

    # ----------------------------------------------------------------
    # Private helpers
    # ----------------------------------------------------------------

    def _run_dot(self, dot_source: str) -> dict[str, tuple[float, float, float | None]]:
        """Invoke ``dot`` on *dot_source* and return parsed KiCad positions.

        Passes ``-Gstart=<seed>`` to make layout deterministic across repeated
        runs on the same input.
        """
        _log.debug("DOT source:\n%s", dot_source)

        cmd: list[str] = [self._dot, "-Tplain", f"-Gstart={self._seed}"]
        for attempt in range(_MAX_ATTEMPTS):
            try:
                result = subprocess.run(  # noqa: S603
                    cmd,
                    input=dot_source,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except (FileNotFoundError, PermissionError) as exc:
                raise RuntimeError(f"dot binary not executable: {exc}") from exc
            except subprocess.TimeoutExpired as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    _log.debug("dot timed out (attempt %d), retrying", attempt + 1)
                    continue
                raise RuntimeError(f"dot timed out after {self._timeout}s") from exc

            if result.returncode != 0:
                raise RuntimeError(
                    f"dot exited with code {result.returncode}:\n{result.stderr[:400]}"
                )
            break

        gv_positions = _parse_plain_positions(result.stdout)
        return _gv_to_kicad(
            gv_positions,
            origin_x=ORIGIN_X,
            origin_y=ORIGIN_Y,
            scale=self._scale,
        )

    @property
    def dot_path(self) -> str:
        return self._dot

    def __repr__(self) -> str:
        return f"GraphvizLayoutEngine(dot_path={self._dot!r})"


# ---------------------------------------------------------------------------
# Helpers exported for tests
# ---------------------------------------------------------------------------

__all__ = [
    "apply_stereo_split",
    "assign_bfs_tiers",
    "build_dot_source",
    "compute_net_weights",
    "emit_decoupling_constraints",
    "find_decoupling_caps",
    "find_dot_binary",
    "find_dot_source",
    "GraphvizLayoutEngine",
    "GRID_ROW_MM",
    "is_capacitor",
    "is_connector",
    "layout_cache_key",
    "load_layout_cache",
    "ORIGIN_Y",
    "PAGE_MAX_Y",
    "parse_plain_positions",
    "post_snap_decoupling_caps",
    "save_layout_cache",
    "snap_feedback_components",
    "snap_positions",
    "snap_power_symbols",
]

# Expose internals for unit tests under public names
assign_bfs_tiers = _assign_bfs_tiers
build_dot_source = _build_dot_source
compute_net_weights = _compute_net_weights
emit_decoupling_constraints = _emit_decoupling_constraints
find_decoupling_caps = _find_decoupling_caps
is_capacitor = _is_capacitor
is_connector = _is_connector
layout_cache_key = _layout_cache_key
load_layout_cache = _load_layout_cache
parse_plain_positions = _parse_plain_positions
post_snap_decoupling_caps = _post_snap_decoupling_caps
save_layout_cache = _save_layout_cache
snap_feedback_components = _snap_feedback_components
snap_power_symbols = _snap_power_symbols
apply_stereo_split = _apply_stereo_split


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
