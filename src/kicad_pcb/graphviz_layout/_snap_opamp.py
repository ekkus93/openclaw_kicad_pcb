"""Op-amp locality, composition, stage input/output shaping, and block spacing.

Thin re-export wrapper. Implementation lives in focused sub-modules:
  - :mod:`._snap_composition`   — composition helpers + major signal axis
  - :mod:`._snap_opamp_stage`   — op-amp stage input shaping
  - :mod:`._snap_buffer_stage`  — buffer stage shaping
  - :mod:`._snap_spacing`       — block spacing + transitions
"""

from __future__ import annotations

from ._snap_buffer_stage import (  # noqa: F401
    _snap_buffer_stage_direct_output_support,
    _snap_buffer_stage_feedback_corridor,
    _snap_buffer_stage_input_node_shape,
    _snap_buffer_stage_output_tail_locality,
)
from ._snap_composition import (  # noqa: F401
    _snap_central_composition,
    _snap_explicit_non_inverting_feedback_nodes,
    _snap_feedback_clusters_to_shifted_cores,
    _snap_major_signal_axis,
)
from ._snap_opamp_stage import (  # noqa: F401
    _snap_opamp_stage_non_inverting_input_node_shape,
    _snap_opamp_stage_upstream_input_bundle,
)
from ._snap_spacing import (  # noqa: F401
    _snap_core_local_shunts,
    _snap_core_to_output_bridge_passives,
    _snap_interstage_handoff_between_stages,
    _snap_major_block_spacing,
    _snap_output_transition_subbands,
    _snap_power_block_cohesion,
)

__all__ = [
    "_snap_buffer_stage_direct_output_support",
    "_snap_buffer_stage_feedback_corridor",
    "_snap_buffer_stage_input_node_shape",
    "_snap_buffer_stage_output_tail_locality",
    "_snap_central_composition",
    "_snap_core_local_shunts",
    "_snap_core_to_output_bridge_passives",
    "_snap_explicit_non_inverting_feedback_nodes",
    "_snap_feedback_clusters_to_shifted_cores",
    "_snap_interstage_handoff_between_stages",
    "_snap_major_block_spacing",
    "_snap_major_signal_axis",
    "_snap_opamp_stage_non_inverting_input_node_shape",
    "_snap_opamp_stage_upstream_input_bundle",
    "_snap_output_transition_subbands",
    "_snap_power_block_cohesion",
]
