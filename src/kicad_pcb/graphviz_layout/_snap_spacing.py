"""Block spacing and transition subbands: major block spacing, interstage, passives, power.

Implementation is split across focused sub-modules:

- :mod:`._snap_spacing_block`    — major block spacing, transition subbands, interstage
- :mod:`._snap_spacing_passives` — core bridge passives, local shunts, power cohesion
"""

from __future__ import annotations

from ._snap_spacing_block import (  # noqa: F401
    _major_block_spacing_groups,
    _snap_core_anchored_major_block_spacing,
    _snap_interstage_handoff_between_stages,
    _snap_major_block_spacing,
    _snap_output_transition_subbands,
    _transition_subband_groups,
)
from ._snap_spacing_passives import (  # noqa: F401
    _snap_core_local_shunts,
    _snap_core_to_output_bridge_passives,
    _snap_power_block_cohesion,
)
