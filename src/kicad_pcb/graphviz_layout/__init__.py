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

import shutil  # noqa: F401  — kept for backwards-compatible `_gv_mod.shutil` test access

from ._gv_debug import _analyze_legacy_sds_fallback  # noqa: F401  — imported by test_phase1
from ._gv_decoupling import _refine_shared_rail_decoupling_map
from ._gv_discovery import find_dot_binary, find_dot_source
from ._gv_engine import GraphvizLayoutEngine, _graphviz_timeout_for_ir  # noqa: F401
from .cache import (
    _layout_cache_key,
    _LayoutCacheEntry,
    _load_layout_cache,
    _load_layout_cache_entry,
    _save_layout_cache,
)
from .dot_builder import (
    _assign_bfs_tiers,
    _build_dot_source,
    _compute_net_weights,
    _emit_decoupling_constraints,
    _find_decoupling_caps,
    _is_capacitor,
    _is_connector,
    _safe_id,  # noqa: F401  — called directly as _gv_mod._safe_id() in tests
)
from .snap import (
    DEFAULT_LAYOUT_HEURISTIC_POLICY,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    SCALE_MM_PER_GV,
    LayoutHeuristicPolicy,
    _apply_post_layout_snaps,
    _apply_stereo_split,
    _compact_y_gap,
    _deoverlap_positions,
    _fit_to_page,
    _parse_plain_positions,
    _post_snap_decoupling_caps,
    _post_stereo_barycentric,
    _snap_feedback_components,
    _snap_power_symbols,
    snap_positions,
)

__all__ = [
    "apply_post_layout_snaps",
    "apply_stereo_split",
    "assign_bfs_tiers",
    "build_dot_source",
    "compact_y_gap",
    "compute_net_weights",
    "DEFAULT_LAYOUT_HEURISTIC_POLICY",
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
    "LayoutCacheEntry",
    "LayoutHeuristicPolicy",
    "layout_cache_key",
    "load_layout_cache",
    "load_layout_cache_entry",
    "ORIGIN_X",
    "ORIGIN_Y",
    "PAGE_MAX_X",
    "PAGE_MAX_Y",
    "parse_plain_positions",
    "post_snap_decoupling_caps",
    "refine_shared_rail_decoupling_map",
    "save_layout_cache",
    "SCALE_MM_PER_GV",
    "snap_feedback_components",
    "snap_positions",
    "snap_power_symbols",
]

# ---------------------------------------------------------------------------
# Backwards-compatible re-exports (public names for tests and external code)
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
LayoutCacheEntry = _LayoutCacheEntry
load_layout_cache = _load_layout_cache
load_layout_cache_entry = _load_layout_cache_entry
parse_plain_positions = _parse_plain_positions
post_snap_decoupling_caps = _post_snap_decoupling_caps
post_stereo_barycentric = _post_stereo_barycentric
refine_shared_rail_decoupling_map = _refine_shared_rail_decoupling_map
save_layout_cache = _save_layout_cache
snap_feedback_components = _snap_feedback_components
snap_power_symbols = _snap_power_symbols
