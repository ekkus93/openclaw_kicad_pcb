"""Re-export wrapper for all geometric snap passes.

Imports are split across focused sub-modules:
  _snap_geometry_blocks   — block zones, density, decoupling, IC centering, multi-unit
  _snap_geometry_stereo   — stereo split, barycentric sort, column spreading, text spacing
  _snap_geometry_finalize — deoverlap, gap compact, connector bounds, crossings, page balance
"""

from __future__ import annotations

from ._snap_geometry_blocks import (  # noqa: F401
    _apply_density_spreading,
    _center_ics_in_columns,
    _decoupling_rail_polarities,
    _post_snap_decoupling_caps,
    _snap_block_zones,
    _snap_multi_unit_sibling_cohesion,
)
from ._snap_geometry_finalize import (  # noqa: F401
    _clamp_to_page,
    _compact_y_gap,
    _compute_page_quadrant_utilization,
    _deoverlap_positions,
    _enforce_connector_x_bounds,
    _remediate_crossings,
    _snap_page_balance,
)
from ._snap_geometry_stereo import (  # noqa: F401
    _apply_property_text_spacing,
    _apply_stereo_split,
    _post_stereo_barycentric,
    _spread_x_columns,
)

__all__ = [
    "_apply_density_spreading",
    "_center_ics_in_columns",
    "_decoupling_rail_polarities",
    "_post_snap_decoupling_caps",
    "_snap_block_zones",
    "_snap_multi_unit_sibling_cohesion",
    "_clamp_to_page",
    "_compact_y_gap",
    "_compute_page_quadrant_utilization",
    "_deoverlap_positions",
    "_enforce_connector_x_bounds",
    "_remediate_crossings",
    "_snap_page_balance",
    "_apply_property_text_spacing",
    "_apply_stereo_split",
    "_post_stereo_barycentric",
    "_spread_x_columns",
]
