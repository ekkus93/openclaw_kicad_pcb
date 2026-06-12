"""Phase 8 layout-balance tests — central composition (Phase 8.2)."""

from __future__ import annotations

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.graphviz_layout.snap import (
    _OPAMP_LOWER_LIMIT_FRACTION,
    _OPAMP_UPPER_LIMIT_FRACTION,
    _TITLE_BLOCK_CLEARANCE_MM,
    GRID_ROW_MM,
    ORIGIN_Y,
    PAGE_MAX_Y,
    _snap_central_composition,
)

_PAGE_HEIGHT = PAGE_MAX_Y - ORIGIN_Y  # ~149.2 mm
_SAFE_MAX_Y = PAGE_MAX_Y - _TITLE_BLOCK_CLEARANCE_MM  # = 170.0 mm
_OPAMP_LOWER_Y = ORIGIN_Y + _OPAMP_LOWER_LIMIT_FRACTION * _PAGE_HEIGHT  # ~162.7 mm
_OPAMP_UPPER_Y = ORIGIN_Y + _OPAMP_UPPER_LIMIT_FRACTION * _PAGE_HEIGHT  # ~73.2 mm
_PAGE_CY_82 = (ORIGIN_Y + PAGE_MAX_Y) / 2.0  # ~125.4 mm


def _bl_with_roles(role_map: dict[str, BlockRole]) -> BlockLayout:
    """Build a minimal BlockLayout from a {ref: role} mapping."""
    bl = BlockLayout()
    for ref, role in role_map.items():
        bl.add_assignment(ref, role)
    return bl


class TestSnapCentralComposition:
    """Tests for Phase 8.2: _snap_central_composition()."""

    # ------------------------------------------------------------------ basic
    def test_no_op_empty_positions(self) -> None:
        """Empty dict returns empty dict."""
        result = _snap_central_composition({})
        assert result == {}

    def test_no_op_all_safe(self) -> None:
        """Circuit safely within page — no change expected."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
                "J1": BlockRole.INPUT,
            }
        )
        # Op-amp at page centre, well above title-block zone.
        cy = _PAGE_CY_82
        positions = {
            "U1": (110.0, cy, None),
            "R1": (100.0, cy - GRID_ROW_MM, None),
            "J1": (70.0, cy + GRID_ROW_MM, None),
        }
        before = dict(positions)
        result = _snap_central_composition(positions, bl)
        assert result == before, "No change expected when circuit is safely in the page centre"

    # ------------------------------------------------- title-block clearance
    def test_title_block_encroachment_shifts_signal_up(self) -> None:
        """Signal component below safe_max_y must push all signal refs up."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        # Place R1 just below the safe boundary.
        offender_y = _SAFE_MAX_Y + 5.0  # 5 mm into title-block zone
        positions = {
            "U1": (110.0, _PAGE_CY_82, None),
            "R1": (100.0, offender_y, None),
        }
        result = _snap_central_composition(positions, bl)
        # R1 must now be at or above safe_max_y.
        assert result["R1"][1] <= _SAFE_MAX_Y, (
            f"R1 y={result['R1'][1]:.2f} should be <= safe_max_y={_SAFE_MAX_Y:.2f}"
        )
        # U1 must have moved up by the same amount (all signal refs are shifted together).
        assert result["U1"][1] < positions["U1"][1], "U1 should move up with the group"

    def test_title_block_shift_is_grid_quantized(self) -> None:
        """The upward push amount must be a multiple of the 1.27 mm KiCad grid."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE})
        # Encroach by an off-grid amount (3.5 mm into zone).
        positions = {"U1": (110.0, _SAFE_MAX_Y + 3.5, None)}
        result = _snap_central_composition(positions, bl)
        # Boundary must be respected.
        assert result["U1"][1] <= _SAFE_MAX_Y, (
            f"y={result['U1'][1]:.4f} exceeds safe_max_y={_SAFE_MAX_Y:.2f}"
        )
        # The *shift* applied must be a multiple of 1.27 mm.
        grid = 1.27
        shift = round(abs(positions["U1"][1] - result["U1"][1]), 6)
        remainder = round(shift % grid, 6)
        assert remainder < 0.001 or abs(remainder - grid) < 0.001, (
            f"shift={shift:.4f} is not a multiple of 1.27 mm (remainder={remainder:.6f})"
        )

    def test_title_block_no_change_when_clear(self) -> None:
        """When all components are above safe_max_y, no title-block shift occurs."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        positions = {
            "U1": (110.0, _SAFE_MAX_Y - 10.0, None),  # safely above
            "R1": (100.0, _SAFE_MAX_Y - 5.0, None),  # still above
        }
        before = dict(positions)
        result = _snap_central_composition(positions, bl)
        assert result == before

    # ---------------------------------------------- op-amp vertical position
    def test_opamp_too_low_nudges_signal_up(self) -> None:
        """Op-amp below lower_limit_y triggers an upward nudge for all signal refs."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
                "J1": BlockRole.INPUT,
            }
        )
        # Place op-amp well below the lower limit.
        low_y = _OPAMP_LOWER_Y + 20.0
        positions = {
            "U1": (110.0, low_y, None),
            "R1": (100.0, low_y - GRID_ROW_MM, None),
            "J1": (70.0, low_y - 2 * GRID_ROW_MM, None),
        }
        result = _snap_central_composition(positions, bl)
        for ref in ("U1", "R1", "J1"):
            assert result[ref][1] < positions[ref][1], (
                f"{ref} y should decrease (nudge up); "
                f"before={positions[ref][1]:.2f} after={result[ref][1]:.2f}"
            )

    def test_opamp_too_high_nudges_signal_down(self) -> None:
        """Op-amp above upper_limit_y triggers a downward nudge for all signal refs."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "R1": BlockRole.FEEDBACK,
            }
        )
        # Place op-amp well above the upper limit.
        high_y = _OPAMP_UPPER_Y - 10.0
        positions = {
            "U1": (110.0, high_y, None),
            "R1": (100.0, high_y + GRID_ROW_MM, None),
        }
        result = _snap_central_composition(positions, bl)
        for ref in ("U1", "R1"):
            assert result[ref][1] > positions[ref][1], (
                f"{ref} y should increase (nudge down); "
                f"before={positions[ref][1]:.2f} after={result[ref][1]:.2f}"
            )

    def test_opamp_in_safe_zone_no_nudge(self) -> None:
        """Op-amp within the acceptable vertical band triggers no nudge."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        cy = _PAGE_CY_82
        positions = {
            "U1": (110.0, cy, None),
            "R1": (100.0, cy + 5.0, None),
        }
        before = dict(positions)
        result = _snap_central_composition(positions, bl)
        assert result == before

    def test_opamp_nudge_is_grid_quantized(self) -> None:
        """The op-amp nudge amount must be a multiple of the 1.27 mm KiCad grid."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        low_y = _OPAMP_LOWER_Y + 15.0
        positions = {
            "U1": (110.0, low_y, None),
            "R1": (100.0, low_y - GRID_ROW_MM, None),
        }
        result = _snap_central_composition(positions, bl)
        # The nudge shift (same for all refs) must be a multiple of 1.27 mm.
        grid = 1.27
        for ref in ("U1", "R1"):
            shift = round(abs(positions[ref][1] - result[ref][1]), 6)
            remainder = round(shift % grid, 6)
            assert remainder < 0.001 or abs(remainder - grid) < 0.001, (
                f"{ref}: shift={shift:.4f} is not a multiple of 1.27 mm"
            )

    # ------------------------------------------- power refs not moved
    def test_power_entry_refs_not_moved(self) -> None:
        """POWER_ENTRY refs are outside the signal set and must not move."""
        bl = _bl_with_roles(
            {
                "U1": BlockRole.OPAMP_CORE,
                "VCC": BlockRole.POWER_ENTRY,
                "GND": BlockRole.POWER_ENTRY,
            }
        )
        positions = {
            "U1": (110.0, _OPAMP_LOWER_Y + 20.0, None),
            "VCC": (110.0, ORIGIN_Y + 5.0, None),
            "GND": (110.0, ORIGIN_Y + 3.0, None),
        }
        result = _snap_central_composition(positions, bl)
        assert result["VCC"] == positions["VCC"], "VCC (POWER_ENTRY) must not be moved"
        assert result["GND"] == positions["GND"], "GND (POWER_ENTRY) must not be moved"

    def test_x_coords_unchanged(self) -> None:
        """Central composition must only modify y; x values must be preserved."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        low_y = _OPAMP_LOWER_Y + 15.0
        positions = {
            "U1": (110.0, low_y, None),
            "R1": (85.0, low_y - GRID_ROW_MM, None),
        }
        result = _snap_central_composition(positions, bl)
        for ref in positions:
            assert result[ref][0] == positions[ref][0], (
                f"x of {ref} must not change; before={positions[ref][0]} after={result[ref][0]}"
            )

    def test_rotation_preserved(self) -> None:
        """Rotation values must be preserved after any nudge."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE})
        positions = {"U1": (110.0, _OPAMP_LOWER_Y + 10.0, 90.0)}
        result = _snap_central_composition(positions, bl)
        assert result["U1"][2] == 90.0, "Rotation must be preserved"

    # ------------------------------------------- no block_layout fallback
    def test_no_block_layout_treats_all_non_hash_as_signal(self) -> None:
        """Without block_layout, all non-# refs are treated as signal refs."""
        # Place a non-# ref below safe_max_y — it should be pushed up.
        positions = {
            "R1": (100.0, _SAFE_MAX_Y + 10.0, None),
            "#PWR01": (50.0, ORIGIN_Y, None),  # hash ref — must not move
        }
        result = _snap_central_composition(positions, block_layout=None)
        assert result["R1"][1] <= _SAFE_MAX_Y, (
            f"R1 y={result['R1'][1]:.2f} should be <= safe_max_y={_SAFE_MAX_Y:.2f}"
        )
        assert result["#PWR01"] == positions["#PWR01"], "#PWR01 must not be moved"

    # ------------------------------------------- small span (diagnostic only)
    def test_small_vertical_span_no_position_change(self) -> None:
        """Very small vertical span should only log a warning; positions unchanged."""
        bl = _bl_with_roles({"U1": BlockRole.OPAMP_CORE, "R1": BlockRole.FEEDBACK})
        cy = _PAGE_CY_82
        # Compress to a single grid row — span well below MIN fraction.
        positions = {
            "U1": (110.0, cy, None),
            "R1": (100.0, cy + 1.27, None),
        }
        before = dict(positions)
        result = _snap_central_composition(positions, bl)
        # No structural change (op-amp is near centre, no title block issue).
        assert result == before, (
            "Small vertical span must not cause position changes — only a debug log"
        )
