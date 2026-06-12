"""Phase 8 layout: major signal axis, block spacing, power cohesion, and integration tests."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.graphviz_layout.snap import (
    _MAJOR_BLOCK_MAX_GAP_MM,
    _MAJOR_BLOCK_MIN_GAP_MM,
    _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
    _POWER_BLOCK_MAX_X_OFFSET_MM,
    _snap_major_block_spacing,
    _snap_major_signal_axis,
    _snap_output_transition_subbands,
    _snap_power_block_cohesion,
)


def _bl_with_roles(role_map: dict[str, BlockRole]) -> BlockLayout:
    """Build a minimal BlockLayout from a {ref: role} mapping."""
    bl = BlockLayout()
    for ref, role in role_map.items():
        bl.add_assignment(ref, role)
    return bl


def _major_block_span_gaps(
    positions: Mapping[str, tuple[float, float, float | None]],
    block_layout: BlockLayout,
) -> list[float]:
    """Return adjacent major input/core/output block span gaps."""

    groups: list[list[str]] = []
    input_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions and assignment.role in {BlockRole.INPUT, BlockRole.PRECONDITIONING}
    )
    core_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions
        and assignment.role in {BlockRole.OPAMP_CORE, BlockRole.INTERSTAGE, BlockRole.BUFFER_STAGE}
    )
    output_group = sorted(
        ref
        for ref, assignment in block_layout.assignments.items()
        if ref in positions and assignment.role in {BlockRole.OUTPUT, BlockRole.OUTPUT_CONDITIONING}
    )
    groups.extend(group for group in (input_group, core_group, output_group) if group)

    gaps: list[float] = []
    for left_group, right_group in zip(groups, groups[1:], strict=False):
        left_max_x = max(positions[ref][0] for ref in left_group)
        right_min_x = min(positions[ref][0] for ref in right_group)
        gaps.append(round(right_min_x - left_max_x, 2))
    return gaps


# ---------------------------------------------------------------------------
# _snap_major_signal_axis — Phase 8.4
# ---------------------------------------------------------------------------


class TestSnapMajorSignalAxis:
    def test_aligns_input_output_representatives_to_fixed_core_axis(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "R1": BlockRole.PRECONDITIONING,
                "U1": BlockRole.OPAMP_CORE,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
                "RFB": BlockRole.FEEDBACK,
                "CDEC": BlockRole.DECOUPLING,
            }
        )
        positions = {
            "J1": (40.0, 118.11, None),
            "R1": (90.0, 116.84, None),
            "U1": (150.0, 106.68, None),
            "R7": (220.0, 101.95, None),
            "J4": (260.0, 102.60, None),
            "RFB": (165.0, 88.90, None),
            "CDEC": (150.0, 78.74, None),
        }

        result = _snap_major_signal_axis(positions, bl)

        expected_axis = round(
            round(positions["U1"][1] / _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM)
            * _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
            2,
        )
        for ref in ("J1", "J4"):
            assert result[ref][1] == expected_axis
        assert result["U1"] == positions["U1"], "Core anchor must stay fixed"
        assert result["R1"] == positions["R1"], "Input support lane must stay untouched"
        assert result["R7"] == positions["R7"], "Output support lane must stay untouched"
        assert result["RFB"] == positions["RFB"], "Feedback lane must stay untouched"
        assert result["CDEC"] == positions["CDEC"], "Decoupling lane must stay untouched"

    def test_falls_back_to_connector_axis_when_no_core_exists(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "R1": BlockRole.PRECONDITIONING,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "J1": (40.0, 118.11, None),
            "R1": (90.0, 116.84, None),
            "R7": (220.0, 101.95, None),
            "J4": (260.0, 102.60, None),
        }

        result = _snap_major_signal_axis(positions, bl)

        connector_axis = round(
            round(
                ((positions["J1"][1] + positions["J4"][1]) / 2.0)
                / _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM
            )
            * _MAJOR_SIGNAL_AXIS_GROUP_SPACING_MM,
            2,
        )
        for ref in ("J1", "J4"):
            assert result[ref][1] == connector_axis
        assert result["R1"] == positions["R1"]
        assert result["R7"] == positions["R7"]


# ---------------------------------------------------------------------------
# _snap_major_block_spacing — Phase 8.5
# ---------------------------------------------------------------------------


class TestSnapMajorBlockSpacingCoreAnchored:
    def test_normalizes_outer_block_gaps_around_fixed_core(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "U1": BlockRole.OPAMP_CORE,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "J1": (40.0, 120.0, None),
            "U1": (130.0, 120.0, None),
            "R7": (240.0, 120.0, None),
            "J4": (270.0, 120.0, None),
        }

        result = _snap_major_block_spacing(positions, bl)

        assert result["U1"] == positions["U1"], "Core anchor must stay fixed"
        left_gap = round(result["U1"][0] - result["J1"][0], 2)
        right_gap = round(result["R7"][0] - result["U1"][0], 2)
        assert _MAJOR_BLOCK_MIN_GAP_MM - 0.2 <= left_gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.2
        assert _MAJOR_BLOCK_MIN_GAP_MM - 0.2 <= right_gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.2
        assert round(result["J4"][0] - result["R7"][0], 2) == 30.0


class TestSnapOutputTransitionSubbands:
    def test_orders_explicit_transition_roles_into_distinct_x_bands(self) -> None:
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "C6": BlockRole.INTERSTAGE,
                "UB": BlockRole.BUFFER_STAGE,
                "R6": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "U1": (130.0, 120.0, None),
            "C6": (170.0, 120.0, None),
            "UB": (142.0, 120.0, None),
            "R6": (180.0, 120.0, None),
            "J4": (260.0, 120.0, None),
        }

        result = _snap_output_transition_subbands(positions, bl)

        assert result["U1"] == positions["U1"], "Core anchor must stay fixed"
        ordered_refs = ["U1", "C6", "UB", "R6", "J4"]
        for left_ref, right_ref in zip(ordered_refs, ordered_refs[1:], strict=False):
            gap = round(result[right_ref][0] - result[left_ref][0], 2)
            assert _MAJOR_BLOCK_MIN_GAP_MM - 0.2 <= gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.2

    def test_preserves_existing_compact_transition_when_already_ordered(self) -> None:
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "C6": BlockRole.INTERSTAGE,
                "RBUF": BlockRole.BUFFER_STAGE,
                "COUT": BlockRole.OUTPUT_CONDITIONING,
                "JOUT": BlockRole.OUTPUT,
            }
        )
        positions = {
            "U1": (130.0, 120.0, None),
            "C6": (160.48, 120.0, None),
            "RBUF": (190.96, 120.0, None),
            "COUT": (221.44, 120.0, None),
            "JOUT": (251.92, 120.0, None),
        }

        result = _snap_output_transition_subbands(positions, bl)

        assert result == positions


class TestSnapMajorBlockSpacing:
    def test_expands_undersized_core_adjacent_gaps_without_moving_core(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "U1": BlockRole.OPAMP_CORE,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "J1": (118.0, 120.0, None),
            "U1": (130.0, 120.0, None),
            "R7": (140.0, 120.0, None),
            "J4": (170.0, 120.0, None),
        }

        result = _snap_major_block_spacing(positions, bl)

        assert result["U1"] == positions["U1"], "Core anchor must stay fixed"
        left_gap = round(result["U1"][0] - result["J1"][0], 2)
        right_gap = round(result["R7"][0] - result["U1"][0], 2)
        assert _MAJOR_BLOCK_MIN_GAP_MM - 0.7 <= left_gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.2
        assert _MAJOR_BLOCK_MIN_GAP_MM - 0.7 <= right_gap <= _MAJOR_BLOCK_MAX_GAP_MM + 0.2

    def test_compresses_overlarge_adjacent_block_gap(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "R1": BlockRole.PRECONDITIONING,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "J1": (40.0, 120.0, None),
            "R1": (70.0, 120.0, None),
            "R7": (220.0, 120.0, None),
            "J4": (250.0, 120.0, None),
        }

        result = _snap_major_block_spacing(positions, bl)

        assert result["J1"] == positions["J1"]
        assert result["R1"] == positions["R1"]
        assert result["R7"][1] == positions["R7"][1]
        assert result["J4"][1] == positions["J4"][1]
        assert round(result["R7"][0] - result["R1"][0], 2) == pytest.approx(
            _MAJOR_BLOCK_MAX_GAP_MM,
            abs=0.2,
        )

    def test_expands_undersized_adjacent_block_gap(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "R1": BlockRole.PRECONDITIONING,
                "R7": BlockRole.OUTPUT_CONDITIONING,
                "J4": BlockRole.OUTPUT,
            }
        )
        positions = {
            "J1": (40.0, 120.0, None),
            "R1": (70.0, 120.0, None),
            "R7": (80.0, 120.0, None),
            "J4": (110.0, 120.0, None),
        }

        result = _snap_major_block_spacing(positions, bl)

        assert result["J1"] == positions["J1"]
        assert result["R1"] == positions["R1"]
        assert round(result["R7"][0] - result["R1"][0], 2) == pytest.approx(
            _MAJOR_BLOCK_MIN_GAP_MM,
            abs=0.2,
        )
        assert round(result["J4"][0] - result["R7"][0], 2) == 30.0


# ---------------------------------------------------------------------------
# _snap_power_block_cohesion — Phase 8.3
# ---------------------------------------------------------------------------


class TestSnapPowerBlockCohesion:
    def test_no_op_without_block_layout(self) -> None:
        positions = {
            "J3": (50.0, 55.0, None),
            "U1": (140.0, 120.0, None),
        }

        result = _snap_power_block_cohesion(positions, block_layout=None)

        assert result == positions

    def test_power_entry_moves_toward_core_anchor(self) -> None:
        bl = _bl_with_roles(
            {
                "J3": BlockRole.POWER_ENTRY,
                "U1": BlockRole.OPAMP_CORE,
                "CDEC": BlockRole.DECOUPLING,
            }
        )
        positions = {
            "J3": (50.0, 55.0, None),
            "U1": (150.0, 120.0, None),
            "CDEC": (150.0, 110.0, None),
        }

        result = _snap_power_block_cohesion(
            positions,
            bl,
            decoupling_map={"CDEC": "U1"},
        )

        assert result["J3"][1] == positions["J3"][1], "Power cohesion must not change y"
        assert result["J3"][0] > positions["J3"][0], "Power entry should move toward the core"
        assert abs(result["J3"][0] - result["U1"][0]) <= _POWER_BLOCK_MAX_X_OFFSET_MM + 0.01

    def test_power_entry_falls_back_to_signal_cluster_when_no_core_exists(self) -> None:
        bl = _bl_with_roles(
            {
                "J1": BlockRole.INPUT,
                "R1": BlockRole.PRECONDITIONING,
                "R7": BlockRole.OUTPUT,
                "J3": BlockRole.POWER_ENTRY,
            }
        )
        positions = {
            "J1": (40.0, 120.0, None),
            "R1": (100.0, 100.0, None),
            "R7": (220.0, 100.0, None),
            "J3": (60.0, 55.0, None),
        }

        result = _snap_power_block_cohesion(positions, bl)

        signal_anchor_x = round(round(((40.0 + 100.0 + 220.0) / 3.0) / 1.27) * 1.27, 2)
        assert result["J3"][0] > positions["J3"][0]
        assert abs(result["J3"][0] - signal_anchor_x) <= _POWER_BLOCK_MAX_X_OFFSET_MM + 0.01


# ---------------------------------------------------------------------------
# Phase 8.4 — Integration tests: composition properties of the full pipeline
# ---------------------------------------------------------------------------
