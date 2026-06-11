"""Constants, small helpers, and policy classes for the snap pipeline.

This module is imported by all other ``_snap_*`` sub-modules so they share a
single source of truth for page-layout constants and classification helpers.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from ..block_detection import BlockLayout
    from ..circuit_ir import CircuitIR
    from ..layout import ComponentAnnotation as _ComponentAnnotation

from ..component_types import CONNECTOR_PREFIXES as _CONNECTOR_PREFIXES_CT
from ..component_types import IC_PREFIXES as _IC_PREFIXES_CT
from ..layout import GRID_COL_MM as _GRID_COL_MM

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Connector-classification helpers
# ---------------------------------------------------------------------------


def _is_ic_ref(ref: str) -> bool:
    """Return True if *ref* looks like an IC designator (U*, IC*, OA*, OP*, etc)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _IC_PREFIXES_CT)


def _is_connector_ref(ref: str) -> bool:
    """Return True if *ref* looks like a connector designator (J*, P*, CON*, etc)."""
    r = ref.upper()
    return any(r.startswith(p) for p in _CONNECTOR_PREFIXES_CT)


def _multi_unit_base_ref(ref: str) -> str | None:
    """Return the shared base ref for a split-unit IC reference, if any."""
    match = re.match(r"^([A-Za-z]+[0-9]+)([A-Za-z]+)$", ref)
    if match is None:
        return None
    return match.group(1)


def _decoupling_family_anchor_x(
    anchor_ref: str,
    positions: Mapping[str, tuple[float, float, float | None]],
) -> float:
    """Return the x lane a decoupling bank should use for *anchor_ref*.

    For split-unit devices, local supply support reads better when the bank is
    centered on the visible family span rather than pinned to only one signal
    sibling such as ``U1A``. Single-unit anchors keep their own x lane.
    """
    anchor_position = positions.get(anchor_ref)
    if anchor_position is None:
        return 0.0

    base_ref = _multi_unit_base_ref(anchor_ref)
    if base_ref is None:
        return round(anchor_position[0], 2)

    sibling_xs = sorted(
        pos[0] for ref, pos in positions.items() if _multi_unit_base_ref(ref) == base_ref
    )
    if len(sibling_xs) < 2:
        return round(anchor_position[0], 2)

    family_center_x = (sibling_xs[0] + sibling_xs[-1]) / 2.0
    return round(family_center_x, 2)


# ---------------------------------------------------------------------------
# Page-layout constants
# ---------------------------------------------------------------------------

ORIGIN_X: float = 30.48  # mm — left margin on an A4 page
ORIGIN_Y: float = 50.80  # mm — top margin on an A4 page

# Usable schematic area on an A4 sheet (page is 297 × 210 mm).
# Leave a 10 mm gutter on the right and bottom edges.
PAGE_MAX_X: float = 287.0  # mm (297 - 10)
PAGE_MAX_Y: float = 200.0  # mm (210 - 10)

# Scale factor: mm per one "graph unit" in dot -Tplain output.
SCALE_MM_PER_GV: float = 24.0

# Vertical spacing between a decoupling capacitor and its associated IC.
# One KiCad symbol row = 300 mil = 7.62 mm (KiCad default body height).
GRID_ROW_MM: float = 7.62

# Minimum centre-to-centre distance that avoids a LAY003 overlap warning.
_STEREO_DEOVERLAP_MIN_MM: float = 10.17  # 2 × 5.08 + ε

# Generated Reference/Value properties property text spacing constants.
_PROPERTY_TEXT_NEAR_X_MM: float = 12 * 1.27
_PROPERTY_TEXT_VERTICAL_GAP_MM: float = 2 * GRID_ROW_MM

# Keep output connectors one snap step farther outward than the nominal
# connector lane so left-facing connector stubs do not fall back into the
# nearest output-support body column.
_OUTPUT_CONNECTOR_CLEARANCE_MM: float = 1.27

# Bottom inset for shared ground-family power symbols.
_POWER_BOTTOM_MARGIN_MM: float = 20.0

# Phase 8.1: page-balance pass thresholds.
_PAGE_BALANCE_DEAD_ZONE_MM: float = 1.5 * GRID_ROW_MM  # ~11.43 mm

# Fraction of the detected deviation that is corrected per pass.
_PAGE_BALANCE_CORRECTION: float = 0.85

# Phase 8.2: central composition thresholds.
_TITLE_BLOCK_CLEARANCE_MM: float = 30.0

# Phase 8.3: keep power-entry blocks visually connected to the main circuit.
_POWER_BLOCK_MAX_X_OFFSET_MM: float = _GRID_COL_MM

# Phase 8.4: align major left-to-right signal-path refs on a shared horizontal axis.
_MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM: float = GRID_ROW_MM

# Phase 8.5: keep adjacent major blocks at a readable, consistent horizontal separation.
_MAJOR_BLOCK_MIN_GAP_MM: float = _GRID_COL_MM
_MAJOR_BLOCK_MAX_GAP_MM: float = 2.0 * _GRID_COL_MM

# Keep adjacent split-unit signal symbols within a single KiCad lane.
_MULTI_UNIT_SIGNAL_SIBLING_GAP_MM: float = _GRID_COL_MM

# Fraction of the usable vertical extent beyond which the op-amp centre is considered too low.
_OPAMP_LOWER_LIMIT_FRACTION: float = 0.75

# Fraction of the usable vertical extent below which the op-amp centre is considered too high.
_OPAMP_UPPER_LIMIT_FRACTION: float = 0.15

# If the vertical span of all signal-path components is less than this fraction
# of the available page height a debug warning is emitted.
_MIN_CIRCUIT_SPAN_FRACTION: float = 0.25


# ---------------------------------------------------------------------------
# Policy classes and context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _OpAmpLocalityContext:
    decoupling_map: Mapping[str, str]
    halo: Mapping[str, str] | None = None
    block_layout: BlockLayout | None = None


class _QuadrantUtilization(TypedDict):
    top_left: float
    top_right: float
    bottom_left: float
    bottom_right: float
    imbalance: float
    dense_quadrant: str
    sparse_quadrant: str


@dataclass(frozen=True, slots=True)
class LayoutHeuristicPolicy:
    """Policy seam for analog-specific post-layout snap passes."""

    enable_decoupling_snap: bool = True
    enable_opamp_locality: bool = True
    enable_input_stage_cohesion: bool = True
    enable_output_stage_cohesion: bool = True

    def apply_decoupling_snap(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        decoupling_map: Mapping[str, str],
        ir: CircuitIR | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply the decoupling-cap snap when enabled."""
        # Import here to avoid circular imports at module level
        from ._snap_geometry import (  # noqa: PLC0415
            _decoupling_rail_polarities,
            _post_snap_decoupling_caps,
        )

        if not self.enable_decoupling_snap or not decoupling_map:
            return dict(positions)
        rail_polarities = _decoupling_rail_polarities(ir, decoupling_map)
        return _post_snap_decoupling_caps(
            dict(positions),
            dict(decoupling_map),
            rail_polarities=rail_polarities,
        )

    def apply_opamp_locality(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        annotations: Mapping[str, _ComponentAnnotation],
        context: _OpAmpLocalityContext,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply op-amp-centric locality staging when enabled."""
        from ._snap_basic import _snap_opamp_locality  # noqa: PLC0415

        if not self.enable_opamp_locality:
            return dict(positions)
        return _snap_opamp_locality(dict(positions), ir, annotations=annotations, context=context)

    def apply_input_stage_cohesion(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        block_layout: BlockLayout | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply analog input-stage cohesion when enabled."""
        from ._snap_basic import _snap_input_stage_cohesion  # noqa: PLC0415

        if not self.enable_input_stage_cohesion:
            return dict(positions)
        return _snap_input_stage_cohesion(dict(positions), ir, block_layout=block_layout)

    def apply_output_stage_cohesion(
        self,
        positions: Mapping[str, tuple[float, float, float | None]],
        ir: CircuitIR,
        *,
        block_layout: BlockLayout | None = None,
    ) -> dict[str, tuple[float, float, float | None]]:
        """Apply analog output-stage cohesion when enabled."""
        from ._snap_basic import _snap_output_stage_cohesion  # noqa: PLC0415

        if not self.enable_output_stage_cohesion:
            return dict(positions)
        return _snap_output_stage_cohesion(dict(positions), ir, block_layout=block_layout)


DEFAULT_LAYOUT_HEURISTIC_POLICY: LayoutHeuristicPolicy = LayoutHeuristicPolicy()
