"""Re-export wrapper for all basic snap passes.

Imports are split across focused sub-modules:
  _snap_connector_power  — connector y-alignment, power symbols, feedback, opamp halo
  _snap_opamp_local      — op-amp locality helpers and neighborhood placement
  _snap_input_stage      — input stage cohesion and signal attachment
  _snap_output_stage     — output stage cohesion
"""

from __future__ import annotations

from ._snap_connector_power import (  # noqa: F401
    _resolve_feedback_anchor_y,
    _snap_connectors_to_ic_y,
    _snap_feedback_components,
    _snap_opamp_halo,
    _snap_power_symbols,
)
from ._snap_input_stage import (  # noqa: F401
    _evict_input_lane_intruders,
    _find_input_stage_members,
    _input_stage_connector_distances,
    _place_input_stage_lane,
    _snap_input_connector_signal_attachment,
    _snap_input_stage_cohesion,
)
from ._snap_opamp_local import (  # noqa: F401
    _feedback_net_membership,
    _is_output_local_loop_role,
    _local_signal_distances,
    _non_inverting_feedback_pair,
    _place_non_inverting_feedback_pair,
    _snap_opamp_locality,
)
from ._snap_output_stage import (  # noqa: F401
    _align_output_connectors_without_ic,
    _evict_output_lane_intruders,
    _find_output_stage_members,
    _output_stage_connector_distances,
    _place_output_stage_lane,
    _snap_output_stage_cohesion,
)

__all__ = [
    "_resolve_feedback_anchor_y",
    "_snap_connectors_to_ic_y",
    "_snap_feedback_components",
    "_snap_opamp_halo",
    "_snap_power_symbols",
    "_evict_input_lane_intruders",
    "_find_input_stage_members",
    "_input_stage_connector_distances",
    "_place_input_stage_lane",
    "_snap_input_connector_signal_attachment",
    "_snap_input_stage_cohesion",
    "_feedback_net_membership",
    "_is_output_local_loop_role",
    "_local_signal_distances",
    "_non_inverting_feedback_pair",
    "_place_non_inverting_feedback_pair",
    "_snap_opamp_locality",
    "_align_output_connectors_without_ic",
    "_evict_output_lane_intruders",
    "_find_output_stage_members",
    "_output_stage_connector_distances",
    "_place_output_stage_lane",
    "_snap_output_stage_cohesion",
]
