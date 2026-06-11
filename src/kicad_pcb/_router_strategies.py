"""Hub, spine, shared-lane, ladder, chain, and compact routing strategies.

Thin re-export wrapper. Implementation lives in focused sub-modules:
  - :mod:`._router_strats_basic`   — hub, spine, shared-lane routing primitives
  - :mod:`._router_strats_chain`   — chain routing, cost metrics, analog heuristics
  - :mod:`._router_strats_tail`    — compact output-tail routing (vertical/horizontal)
  - :mod:`._router_strats_cluster` — compact decoupling cluster routing
  - :mod:`._router_strats_ladder`  — local ladder lane planning and assignment
"""

from __future__ import annotations

from ._router_strats_basic import (  # noqa: F401
    _hub_route,
    _shared_lane_route,
    _spine_route,
)
from ._router_strats_chain import (  # noqa: F401
    _buffer_follower_feedback_route,
    _chain_route,
    _compact_aligned_chain_route,
    _is_small_analog_chain_candidate,
    _prefer_chain_route,
    _prefer_small_analog_chain_route,
    _protected_shared_lane_route,
    _route_length,
    _route_visual_cost,
)
from ._router_strats_cluster import (  # noqa: F401
    _choose_compact_ground_lane,
    _compact_local_decoupling_ground_cluster_route,
    _compact_local_decoupling_power_cluster_route,
    _compact_local_ground_cluster_route,
    _decoupling_ground_members,
)
from ._router_strats_ladder import (  # noqa: F401
    _assign_connector_entry_grouped_lanes,
    _assign_grouped_ladder_lanes,
    _build_ladder_adjacency,
    _collect_local_ladder_candidates,
    _connected_ladder_component,
    _infer_bounded_local_lane_plan,
    _lane_center,
    _plan_local_ladder_routes,
    _plan_single_grouped_ladder_lane,
)
from ._router_strats_tail import (  # noqa: F401
    _best_compact_vertical_tail_route,
    _compact_horizontal_stage_tail_route,
    _compact_vertical_tail_clearance_x,
    _compact_vertical_tail_members,
    _compact_vertical_tail_route,
    _compact_vertical_tail_tail_y,
    _should_skip_inferred_lane_plan,
)

__all__ = [
    "_assign_connector_entry_grouped_lanes",
    "_assign_grouped_ladder_lanes",
    "_best_compact_vertical_tail_route",
    "_buffer_follower_feedback_route",
    "_build_ladder_adjacency",
    "_chain_route",
    "_choose_compact_ground_lane",
    "_collect_local_ladder_candidates",
    "_compact_aligned_chain_route",
    "_compact_horizontal_stage_tail_route",
    "_compact_local_decoupling_ground_cluster_route",
    "_compact_local_decoupling_power_cluster_route",
    "_compact_local_ground_cluster_route",
    "_compact_vertical_tail_clearance_x",
    "_compact_vertical_tail_members",
    "_compact_vertical_tail_route",
    "_compact_vertical_tail_tail_y",
    "_connected_ladder_component",
    "_decoupling_ground_members",
    "_hub_route",
    "_infer_bounded_local_lane_plan",
    "_is_small_analog_chain_candidate",
    "_lane_center",
    "_plan_local_ladder_routes",
    "_plan_single_grouped_ladder_lane",
    "_prefer_chain_route",
    "_prefer_small_analog_chain_route",
    "_protected_shared_lane_route",
    "_route_length",
    "_route_visual_cost",
    "_shared_lane_route",
    "_should_skip_inferred_lane_plan",
    "_spine_route",
]
