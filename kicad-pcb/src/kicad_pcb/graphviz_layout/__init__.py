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
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..circuit_ir import CircuitIR

from ..layout import _compute_opamp_halo as _compute_opamp_halo_layout
from ..layout import compute_affinity_groups as _compute_affinity_groups
from ..layout import compute_orientations as _compute_orientations
from ..layout import compute_sds_columns as _compute_sds_columns
from ..layout import compute_signal_distance_scores as _compute_signal_distance_scores
from ..layout import detect_stereo_channels as _detect_stereo_channels
from ..layout import find_feedback_paths as _find_feedback_paths
from ..tier import assign_ic_units_to_tiers as _assign_ic_units_to_tiers
from ..tier import assign_tiers as _assign_tiers
from ..tier import classify_connector_roles as _classify_connector_roles
from .cache import _layout_cache_key, _load_layout_cache, _save_layout_cache
from .dot_builder import (
    _assign_bfs_tiers,
    _build_dot_source,
    _compute_net_weights,
    _emit_decoupling_constraints,
    _find_decoupling_caps,
    _is_capacitor,
    _is_connector,
    _safe_id,
)
from .snap import (
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    SCALE_MM_PER_GV,
    _apply_post_layout_snaps,
    _apply_stereo_split,
    _compact_y_gap,
    _deoverlap_positions,
    _fit_to_page,
    _gv_to_kicad,
    _parse_plain_positions,
    _post_snap_decoupling_caps,
    _post_stereo_barycentric,
    _snap_feedback_components,
    _snap_power_symbols,
    snap_positions,
)

_log = logging.getLogger(__name__)

# Maximum number of subprocess attempts (retry on transient failures).
_MAX_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Binary discovery
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
        _roles = _classify_connector_roles(refs, _tiers)
        annotations = _find_feedback_paths(ir, _tiers, roles=_roles or None)
        feedback_refs: set[str] = {r for r, a in annotations.items() if a.feedback}

        # Compute op-amp halo membership for DOT rank constraints (R4-5)
        # and the post-layout snap pass (R4-4).
        halo = _compute_opamp_halo_layout(ir, annotations, _tiers)

        # R2-3: compute SDS-derived column indices to feed rank subgraphs.
        sds_scores = _compute_signal_distance_scores(ir, _roles)
        sds_cols = _compute_sds_columns(refs, sds_scores)

        # Detect multi-unit IC groups; extract power units for cluster_power.
        _unit_groups = _assign_ic_units_to_tiers(ir, _tiers)
        _power_unit_refs: set[str] = {
            g.power_unit for g in _unit_groups.values() if g.power_unit is not None
        }

        # Build DOT source up-front so we can derive the cache key.
        # Pass pre-computed tiers so _build_dot_source skips a redundant assign_tiers call.
        # Compute affinity order so _emit_tier_subgraphs emits nodes in signal-flow
        # coupling order rather than alphabetical order, giving dot a better start.
        affinity_order = _compute_affinity_groups(ir, _tiers)
        dot_source = _build_dot_source(
            ir,
            decoupling_map=decoupling_map,
            feedback_refs=feedback_refs or None,
            power_unit_refs=_power_unit_refs or None,
            tiers=_tiers,
            connector_roles=_roles or None,
            halo=halo or None,
            sds_cols=sds_cols or None,
            affinity_order=affinity_order,
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

        # Post-layout: apply all snap passes in canonical order (grid → power
        # → feedback → stereo split → decoupling caps).  See
        # gv_snap._apply_post_layout_snaps for the ordering rationale.
        channels = _detect_stereo_channels(ir)
        result = _apply_post_layout_snaps(
            result,
            ir,
            feedback_refs=feedback_refs,
            annotations=annotations,
            channels=channels,
            decoupling_map=decoupling_map,
            roles=_roles or None,
            halo=halo or None,
        )

        # Compute component orientations (rotation in degrees) from signal topology
        # and merge into the result so callers receive (x, y, rotation) triples.
        _plain_positions: dict[str, tuple[float, float]] = {
            ref: (x, y) for ref, (x, y, _) in result.items()
        }
        _orientations = _compute_orientations(ir, _plain_positions, _tiers, roles=_roles or None)
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
    "apply_post_layout_snaps",
    "apply_stereo_split",
    "assign_bfs_tiers",
    "build_dot_source",
    "compact_y_gap",
    "compute_net_weights",
    "deoverlap_positions",
    "emit_decoupling_constraints",
    "find_decoupling_caps",
    "find_dot_binary",
    "find_dot_source",
    "fit_to_page",
    "GraphvizLayoutEngine",
    "GRID_ROW_MM",
    "is_capacitor",
    "is_connector",
    "layout_cache_key",
    "load_layout_cache",
    "ORIGIN_X",
    "ORIGIN_Y",
    "PAGE_MAX_X",
    "PAGE_MAX_Y",
    "parse_plain_positions",
    "post_snap_decoupling_caps",
    "save_layout_cache",
    "snap_feedback_components",
    "snap_positions",
    "snap_power_symbols",
]

# ---------------------------------------------------------------------------
# Backwards-compatible re-exports (public names for tests and external code)
# The functions below are defined in sub-modules (gv_dot_builder, gv_snap,
# gv_cache) with a leading underscore.  The aliases here expose them as
# public names for backwards compatibility and direct test access.
# New code should import from graphviz_layout (not from the sub-modules).
# ---------------------------------------------------------------------------
apply_post_layout_snaps = _apply_post_layout_snaps
apply_stereo_split = _apply_stereo_split
assign_bfs_tiers = _assign_bfs_tiers
build_dot_source = _build_dot_source
compact_y_gap = _compact_y_gap
compute_net_weights = _compute_net_weights
deoverlap_positions = _deoverlap_positions
emit_decoupling_constraints = _emit_decoupling_constraints
find_decoupling_caps = _find_decoupling_caps
fit_to_page = _fit_to_page
is_capacitor = _is_capacitor
is_connector = _is_connector
layout_cache_key = _layout_cache_key
load_layout_cache = _load_layout_cache
parse_plain_positions = _parse_plain_positions
post_snap_decoupling_caps = _post_snap_decoupling_caps
post_stereo_barycentric = _post_stereo_barycentric
save_layout_cache = _save_layout_cache
snap_feedback_components = _snap_feedback_components
snap_power_symbols = _snap_power_symbols
