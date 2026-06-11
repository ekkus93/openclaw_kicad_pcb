"""Wire geometry, route-building, label/junction, and power-symbol helpers."""

from __future__ import annotations

from ._router_geometry_basic import (  # noqa: F401
    _coerce_pin_anchor_map,
    _horizontal_first_l_route,
    _is_connector_passive_edge,
    _l_route,
    _l_route_with_protected_points,
    _manhattan,
    _offset_point_along_angle,
    _point_on_segment,
    _resolve_pin_anchors,
    _route_candidate_key,
    _route_protected_point_score,
    _segment_label_angle,
    _snap_grid,
    _stub_end,
    _three_segment_route_via_x,
    _three_segment_route_via_y,
    _vertical_first_l_route,
    _wire_path_length,
)
from ._router_geometry_labels import (  # noqa: F401
    _append_pin_endpoint_labels,
    _best_direct_route_with_protected_points,
    _label_attachment_plan,
    _occupied_label_points,
    _occupied_wire_points,
    _safe_pin_label_anchor,
    _safe_stub_label_anchor,
)
from ._router_geometry_power import (  # noqa: F401
    _aligned_power_cluster_route_points,
    _append_direct_power_symbol,
    _fallback_power_label_position,
    _foreign_attachment_points_for_known,
    _known_pin_stub_hits_foreign_attachment,
    _power_cluster_angle,
    _power_label_angle_for_pin,
    _power_symbol_angle,
)
