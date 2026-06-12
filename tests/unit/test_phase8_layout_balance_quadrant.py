"""Unit tests for Phase 8.1 and 8.2 — page composition heuristics.

Tests cover:
  - _compute_page_quadrant_utilization: four-quadrant page-balance metrics
  - _snap_page_balance: vertical re-centering pass for signal-path components
  - _snap_central_composition: title-block clearance + op-amp vertical bounds
"""

from __future__ import annotations

from collections.abc import Mapping

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.graphviz_layout.snap import (
    _OPAMP_LOWER_LIMIT_FRACTION,
    _OPAMP_UPPER_LIMIT_FRACTION,
    _PAGE_BALANCE_CORRECTION,
    _PAGE_BALANCE_DEAD_ZONE_MM,
    _TITLE_BLOCK_CLEARANCE_MM,
    GRID_ROW_MM,
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _compute_page_quadrant_utilization,
    _snap_page_balance,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PAGE_CX = (ORIGIN_X + PAGE_MAX_X) / 2.0  # ~158.74 mm
_PAGE_CY = (ORIGIN_Y + PAGE_MAX_Y) / 2.0  # ~125.40 mm


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
# _compute_page_quadrant_utilization
# ---------------------------------------------------------------------------


class TestComputePageQuadrantUtilization:
    def test_empty_positions_returns_zero_fractions(self) -> None:
        result = _compute_page_quadrant_utilization({})
        for q in ("top_left", "top_right", "bottom_left", "bottom_right"):
            assert result[q] == 0.0
        assert result["imbalance"] == 0.0

    def test_all_in_top_left_quadrant(self) -> None:
        """Components placed in the top-left quadrant should yield fraction 1.0."""
        # Place well inside top-left (x < _PAGE_CX, y < _PAGE_CY)
        positions = {
            "A": (ORIGIN_X + 10.0, ORIGIN_Y + 10.0, None),
            "B": (ORIGIN_X + 20.0, ORIGIN_Y + 20.0, None),
            "C": (ORIGIN_X + 30.0, ORIGIN_Y + 30.0, None),
        }
        result = _compute_page_quadrant_utilization(positions)
        assert result["top_left"] == 1.0
        assert result["top_right"] == 0.0
        assert result["bottom_left"] == 0.0
        assert result["bottom_right"] == 0.0
        assert result["imbalance"] == 1.0
        assert result["dense_quadrant"] == "top_left"

    def test_balanced_one_per_quadrant(self) -> None:
        """One component per quadrant yields equal fractions and zero imbalance."""
        cx, cy = _PAGE_CX, _PAGE_CY
        positions = {
            "TL": (cx - 20.0, cy - 20.0, None),
            "TR": (cx + 20.0, cy - 20.0, None),
            "BL": (cx - 20.0, cy + 20.0, None),
            "BR": (cx + 20.0, cy + 20.0, None),
        }
        result = _compute_page_quadrant_utilization(positions)
        for q in ("top_left", "top_right", "bottom_left", "bottom_right"):
            assert result[q] == 0.25
        assert result["imbalance"] == 0.0

    def test_detects_top_heavy_circuit(self) -> None:
        """Circuit clustered in the top half should report high top-half fractions."""
        cy = _PAGE_CY
        positions = {
            f"T{i}": (_PAGE_CX + (i - 4) * 20.0, cy - 30.0 - i * 5.0, None) for i in range(8)
        }
        positions["B1"] = (_PAGE_CX - 10.0, cy + 20.0, None)
        positions["B2"] = (_PAGE_CX + 10.0, cy + 20.0, None)
        result = _compute_page_quadrant_utilization(positions)
        top_total = result["top_left"] + result["top_right"]  # type: ignore[operator]
        bottom_total = result["bottom_left"] + result["bottom_right"]  # type: ignore[operator]
        assert top_total > bottom_total, "Expected top-heavy distribution"
        assert result["imbalance"] > 0.3, "Expected significant imbalance"

    def test_uses_page_center_not_bounding_box(self) -> None:
        """The quadrant split should use the page centre, not the component bounding box."""
        # All components are in the top-left *page* quadrant even though they span
        # a wide x range, because all y values are well above the page centre.
        positions = {
            "A": (40.0, 60.0, None),  # x < page_cx, y < page_cy  → top_left
            "B": (200.0, 60.0, None),  # x > page_cx, y < page_cy  → top_right
        }
        result = _compute_page_quadrant_utilization(positions)
        assert result["top_left"] == 0.5
        assert result["top_right"] == 0.5
        assert result["bottom_left"] == 0.0
        assert result["bottom_right"] == 0.0

    def test_imbalance_is_max_minus_min(self) -> None:
        """Imbalance should equal the largest fraction minus the smallest."""
        cx, cy = _PAGE_CX, _PAGE_CY
        positions = {
            "TL1": (cx - 10, cy - 10, None),
            "TL2": (cx - 20, cy - 20, None),
            "TL3": (cx - 30, cy - 30, None),
            "TR": (cx + 10, cy - 10, None),
        }
        result = _compute_page_quadrant_utilization(positions)
        fracs = [
            result["top_left"],
            result["top_right"],
            result["bottom_left"],
            result["bottom_right"],
        ]
        expected_imbalance = round(max(fracs) - min(fracs), 4)
        assert result["imbalance"] == expected_imbalance


# ---------------------------------------------------------------------------
# _snap_page_balance
# ---------------------------------------------------------------------------


class TestSnapPageBalance:
    def test_no_op_without_block_layout(self) -> None:
        """When block_layout is None the function returns positions unchanged."""
        positions = {
            "U1": (110.0, ORIGIN_Y + 10.0, None),
            "R1": (90.0, ORIGIN_Y + 15.0, None),
        }
        before = dict(positions)
        result, shift = _snap_page_balance(positions, block_layout=None)
        assert result == before and shift == 0.0

    def test_no_op_when_already_centered(self) -> None:
        """Signal circuit near the page centre Y should not be shifted."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
                "J1": BlockRole.INPUT,
            }
        )
        # Place all near page_center_y
        cy = _PAGE_CY
        positions = {
            "U1": (110.0, cy, None),
            "R1": (100.0, cy - GRID_ROW_MM, None),
            "J1": (70.0, cy + GRID_ROW_MM, None),
        }
        before = dict(positions)
        result, shift = _snap_page_balance(positions, block_layout=bl)
        assert result == before and shift == 0.0, (
            "Circuit centred near page_center_y should not be nudged"
        )

    def test_shifts_top_heavy_signal_circuit_downward(self) -> None:
        """Signal circuit far above page centre should be nudged down."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
                "J1": BlockRole.INPUT,
                "R2": BlockRole.OUTPUT,
            }
        )
        # Cluster signal refs near the top margin (far above page centre)
        top_y = ORIGIN_Y + 10.0
        positions = {
            "U1": (110.0, top_y, None),
            "R1": (110.0, top_y + GRID_ROW_MM, None),
            "J1": (70.0, top_y + GRID_ROW_MM, None),
            "R2": (150.0, top_y, None),
        }
        result, _ = _snap_page_balance(positions, block_layout=bl)
        # All signal refs should have moved down (y increased)
        for ref in ("U1", "R1", "J1", "R2"):
            assert result[ref][1] > positions[ref][1], (
                f"{ref} should move down; "
                f"before={positions[ref][1]:.2f}, after={result[ref][1]:.2f}"
            )

    def test_shifts_bottom_heavy_signal_circuit_upward(self) -> None:
        """Signal circuit far below page centre should be nudged up."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.PRECONDITIONING,
                "J1": BlockRole.OUTPUT,
            }
        )
        # Place near bottom of page
        bottom_y = PAGE_MAX_Y - 10.0
        positions = {
            "U1": (110.0, bottom_y, None),
            "R1": (90.0, bottom_y - GRID_ROW_MM, None),
            "J1": (150.0, bottom_y, None),
        }
        result, _ = _snap_page_balance(positions, block_layout=bl)
        for ref in ("U1", "R1", "J1"):
            assert result[ref][1] < positions[ref][1], (
                f"{ref} should move up; before={positions[ref][1]:.2f}, after={result[ref][1]:.2f}"
            )

    def test_power_decoupling_components_not_moved(self) -> None:
        """POWER_ENTRY components must not be shifted; DECOUPLING caps should move with their IC."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "CDEC1": BlockRole.DECOUPLING,
                "CDEC2": BlockRole.DECOUPLING,
                "VCC": BlockRole.POWER_ENTRY,
                "J1": BlockRole.INPUT,
            }
        )
        # Signal at top, power also at top
        top_y = ORIGIN_Y + 10.0
        positions = {
            "U1": (110.0, top_y, None),
            "CDEC1": (110.0, top_y - GRID_ROW_MM, None),
            "CDEC2": (110.0, top_y - 2 * GRID_ROW_MM, None),
            "VCC": (110.0, ORIGIN_Y, None),
            "J1": (70.0, top_y, None),
        }
        result, _ = _snap_page_balance(positions, block_layout=bl)

        # POWER_ENTRY should be unchanged (excluded from signal roles)
        assert result["VCC"] == positions["VCC"], (
            "VCC (POWER_ENTRY) should not be moved by page balance"
        )

        # U1 and J1 should have moved down (circuit is top-heavy)
        assert result["U1"][1] > positions["U1"][1]
        assert result["J1"][1] > positions["J1"][1]

        # DECOUPLING caps should move WITH the op-amp to preserve relative positioning
        assert result["CDEC1"][1] > positions["CDEC1"][1], (
            "CDEC1 (DECOUPLING) should move with op-amp to preserve cap/IC offset"
        )
        assert result["CDEC2"][1] > positions["CDEC2"][1], (
            "CDEC2 (DECOUPLING) should move with op-amp to preserve cap/IC offset"
        )

    def test_shift_magnitude_uses_correction_factor(self) -> None:
        """The shift applied should be proportional to the deviation × correction factor,
        rounded to the nearest 1.27 mm KiCad grid step."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
            }
        )
        # Place signal refs at a known y so we can compute expected shift
        signal_y = ORIGIN_Y + 15.0  # well above page centre
        positions = {
            "U1": (110.0, signal_y, None),
            "R1": (110.0, signal_y, None),
        }
        result, shift = _snap_page_balance(positions, block_layout=bl)

        circuit_center_y = signal_y
        page_center_y = _PAGE_CY
        delta = page_center_y - circuit_center_y
        raw_shift = delta * _PAGE_BALANCE_CORRECTION
        _grid = 1.27
        expected_shift = round(round(raw_shift / _grid) * _grid, 4)

        actual_shift = shift
        assert abs(actual_shift - expected_shift) < 0.02, (
            f"Expected shift ≈{expected_shift:.4f}, got {actual_shift:.4f}"
        )

    def test_dead_zone_prevents_micro_shifts(self) -> None:
        """Deviation smaller than _PAGE_BALANCE_DEAD_ZONE_MM should not trigger a shift."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
            }
        )
        # Place just within dead zone (0.5 mm inside)
        circuit_y = _PAGE_CY + (_PAGE_BALANCE_DEAD_ZONE_MM * 0.8)
        positions = {"U1": (110.0, circuit_y, None)}
        before = dict(positions)
        result, shift = _snap_page_balance(positions, block_layout=bl)
        assert result == before and shift == 0.0, "Small deviation should not trigger a shift"

    def test_x_coordinates_unchanged(self) -> None:
        """The balance pass must only shift y; x values must not change."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "J1": BlockRole.INPUT,
                "J2": BlockRole.OUTPUT,
            }
        )
        top_y = ORIGIN_Y + 8.0
        positions = {
            "U1": (110.0, top_y, None),
            "J1": (70.0, top_y, None),
            "J2": (160.0, top_y, None),
        }
        result, _ = _snap_page_balance(positions, block_layout=bl)
        for ref in positions:
            assert result[ref][0] == positions[ref][0], (
                f"x of {ref} should be unchanged by page balance"
            )


# ---------------------------------------------------------------------------
# _snap_central_composition — Phase 8.2
# ---------------------------------------------------------------------------

_PAGE_HEIGHT = PAGE_MAX_Y - ORIGIN_Y  # ~149.2 mm
_SAFE_MAX_Y = PAGE_MAX_Y - _TITLE_BLOCK_CLEARANCE_MM  # = 170.0 mm
_OPAMP_LOWER_Y = ORIGIN_Y + _OPAMP_LOWER_LIMIT_FRACTION * _PAGE_HEIGHT  # ~162.7 mm
_OPAMP_UPPER_Y = ORIGIN_Y + _OPAMP_UPPER_LIMIT_FRACTION * _PAGE_HEIGHT  # ~73.2 mm
_PAGE_CY_82 = (ORIGIN_Y + PAGE_MAX_Y) / 2.0  # ~125.4 mm
