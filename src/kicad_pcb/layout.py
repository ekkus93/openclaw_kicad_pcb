"""Signal-flow schematic layout for kicad_pcb.

Computes ``{ref: (x, y)}`` placements for Circuit IR components using
connector-aware SDS or tier-derived column assignment so signals flow
left→right and connected components end up adjacent to each other.
"""

from __future__ import annotations

from ._layout_feedback import (
    ComponentAnnotation,
    StereoChannel,
    _compute_opamp_halo,
    detect_stereo_channels,
    find_feedback_paths,
)
from ._layout_graph import (
    GRID_COL_MM,
    GRID_ROW_MM,
    MAX_ROWS_PER_COL,
    MIN_SEPARATION_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_HEIGHT_MM,
    PAGE_WIDTH_MM,
    _find_decoupling_caps_layout,
    build_signal_adjacency,
)
from ._layout_orient import compute_orientations
from ._layout_placement import compute_affinity_groups, compute_signal_flow_layout
from ._layout_sds import (
    barycentric_sort,
    compute_sds_columns,
    compute_signal_distance_scores,
    count_wire_crossings,
)

__all__ = [
    "PAGE_WIDTH_MM",
    "PAGE_HEIGHT_MM",
    "GRID_COL_MM",
    "GRID_ROW_MM",
    "ORIGIN_X",
    "ORIGIN_Y",
    "MAX_ROWS_PER_COL",
    "MIN_SEPARATION_MM",
    "StereoChannel",
    "ComponentAnnotation",
    "build_signal_adjacency",
    "count_wire_crossings",
    "barycentric_sort",
    "compute_signal_distance_scores",
    "compute_sds_columns",
    "compute_signal_flow_layout",
    "compute_affinity_groups",
    "compute_orientations",
    "find_feedback_paths",
    "detect_stereo_channels",
    "_find_decoupling_caps_layout",
    "_compute_opamp_halo",
]
