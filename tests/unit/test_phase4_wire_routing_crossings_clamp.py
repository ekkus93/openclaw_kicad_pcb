from __future__ import annotations

import math

import pytest

from kicad_pcb.graphviz_layout.snap import (
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _clamp_to_page,
    _spread_x_columns,
)

pytestmark = pytest.mark.unit


class TestClampToPage:
    """Unit tests for _clamp_to_page() — final pass that prevents LAY004."""

    def test_positions_inside_bounds_unchanged(self) -> None:
        """Positions already inside the A4 area must pass through unmodified."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, 100.0, 0.0),
            "U1": (150.0, 80.0, None),
        }
        result = _clamp_to_page(positions)
        assert result["R1"] == pytest.approx((100.0, 100.0, 0.0))
        assert result["U1"][0] == pytest.approx(150.0)
        assert result["U1"][1] == pytest.approx(80.0)
        assert result["U1"][2] is None

    def test_x_beyond_max_clamped(self) -> None:
        """x > PAGE_MAX_X must be clamped to PAGE_MAX_X."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (PAGE_MAX_X + 50.0, 100.0, 0.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][0] == pytest.approx(PAGE_MAX_X)
        assert result["R1"][1] == pytest.approx(100.0)

    def test_y_beyond_max_clamped(self) -> None:
        """y > PAGE_MAX_Y must be clamped to PAGE_MAX_Y."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, PAGE_MAX_Y + 30.0, 90.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][1] == pytest.approx(PAGE_MAX_Y)
        assert result["R1"][0] == pytest.approx(100.0)

    def test_x_below_origin_clamped(self) -> None:
        """x < ORIGIN_X must be clamped to ORIGIN_X."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (ORIGIN_X - 20.0, 100.0, None),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][0] == pytest.approx(ORIGIN_X)

    def test_y_below_origin_clamped(self) -> None:
        """y < ORIGIN_Y must be clamped to ORIGIN_Y."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, ORIGIN_Y - 10.0, 0.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][1] == pytest.approx(ORIGIN_Y)

    def test_rotation_preserved(self) -> None:
        """Rotation must be unchanged even when x or y is clamped."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (PAGE_MAX_X + 5.0, PAGE_MAX_Y + 5.0, 180.0),
        }
        result = _clamp_to_page(positions)
        assert result["U1"][2] == pytest.approx(180.0)

    def test_input_not_mutated(self) -> None:
        """The input dict must not be modified in-place."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (PAGE_MAX_X + 1.0, 100.0, 0.0),
        }
        original_x = positions["R1"][0]
        _clamp_to_page(positions)
        assert positions["R1"][0] == original_x

    def test_empty_positions_returns_empty(self) -> None:
        """Empty input must produce an empty output without error."""
        result = _clamp_to_page({})
        assert result == {}


# ---------------------------------------------------------------------------
# Phase 4.3 — _spread_x_columns
# ---------------------------------------------------------------------------


class TestSpreadXColumns:
    """Unit tests for _spread_x_columns() — X-spread to prevent column collapse."""

    def test_empty_returns_empty(self) -> None:
        result = _spread_x_columns({})
        assert result == {}

    def test_small_column_unchanged(self) -> None:
        """≤ max_per_column symbols at same x must not be moved."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (50.8, 63.5, None),
            "R3": (50.8, 76.2, 90.0),
        }
        result = _spread_x_columns(positions, max_per_column=3)
        assert result["R1"][0] == pytest.approx(50.8)
        assert result["R2"][0] == pytest.approx(50.8)
        assert result["R3"][0] == pytest.approx(50.8)

    def test_overloaded_column_produces_two_subcolumns(self) -> None:
        """6 symbols at the same x, max_per_column=3 → exactly 2 distinct x values."""
        x0 = 50.8  # 40 × 1.27 mm (on grid)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 7)
        }
        result = _spread_x_columns(positions, max_per_column=3, col_step_mm=25.4)
        xs = {v[0] for v in result.values()}
        # 2 sub-columns: x0 ± 12.7 mm → 38.1 and 63.5
        assert len(xs) == 2, f"Expected 2 distinct x-columns, got {sorted(xs)}"
        assert all(x >= ORIGIN_X for x in xs)
        assert all(x <= PAGE_MAX_X for x in xs)

    def test_nine_symbols_produce_three_subcolumns(self) -> None:
        """9 symbols, max_per_column=3 → exactly 3 distinct x-column positions."""
        x0 = 101.6  # 80 × 1.27 mm (on grid, comfortably away from edges)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), None) for i in range(1, 10)
        }
        result = _spread_x_columns(positions, max_per_column=3, col_step_mm=25.4)
        xs = sorted(v[0] for v in result.values())
        distinct_xs = sorted(set(xs))
        # 3 sub-columns: 101.6 ± 25.4 = {76.2, 101.6, 127.0}
        assert len(distinct_xs) == 3, f"Expected 3 distinct x-cols, got {distinct_xs}"
        assert distinct_xs == pytest.approx([76.2, 101.6, 127.0])

    def test_y_tier_order_preserved_across_subcolumns(self) -> None:
        """The lowest-y (highest-tier) symbols end up in the leftmost sub-column."""
        x0 = 101.6  # on grid
        # 4 symbols: R1=y10, R2=y20, R3=y30, R4=y40
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (x0, 10.0, 0.0),
            "R2": (x0, 20.0, 0.0),
            "R3": (x0, 30.0, 0.0),
            "R4": (x0, 40.0, 0.0),
        }
        result = _spread_x_columns(positions, max_per_column=2, col_step_mm=25.4)
        # After y-sort: R1, R2 → col_idx=0 (offset=-12.7), R3, R4 → col_idx=1 (offset=+12.7)
        x_r1, x_r2 = result["R1"][0], result["R2"][0]
        x_r3, x_r4 = result["R3"][0], result["R4"][0]
        assert x_r1 == pytest.approx(x_r2), "R1 and R2 should share the same sub-column"
        assert x_r3 == pytest.approx(x_r4), "R3 and R4 should share the same sub-column"
        assert x_r1 < x_r3, "Lower-y symbols (R1/R2) should be in leftmost sub-column"

    def test_rotation_preserved(self) -> None:
        """Rotation must not be modified by spreading."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 10.0, 45.0),
            "R2": (50.8, 20.0, 90.0),
            "R3": (50.8, 30.0, None),
            "R4": (50.8, 40.0, 180.0),
        }
        result = _spread_x_columns(positions, max_per_column=2)
        assert result["R1"][2] == pytest.approx(45.0)
        assert result["R2"][2] == pytest.approx(90.0)
        assert result["R3"][2] is None
        assert result["R4"][2] == pytest.approx(180.0)

    def test_input_not_mutated(self) -> None:
        """The input dict must not be modified in-place."""
        x0 = 101.6
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 5)
        }
        original = {ref: tuple(v) for ref, v in positions.items()}
        _spread_x_columns(positions, max_per_column=2)
        for ref, orig_val in original.items():
            assert positions[ref] == orig_val, f"{ref} was mutated"

    def test_subcolumns_clamped_to_page_bounds(self) -> None:
        """Sub-columns pushed below ORIGIN_X must be clamped to ORIGIN_X."""
        # x=ORIGIN_X with 4 symbols; left sub-column would go to ORIGIN_X - 12.7 → clamped.
        x0: float = ORIGIN_X  # 30.48 mm (on grid: 24 × 1.27)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 5)
        }
        result = _spread_x_columns(positions, max_per_column=2, col_step_mm=25.4)
        for _ref, (x, _y, _r) in result.items():
            assert x >= ORIGIN_X, f"x={x} is below ORIGIN_X={ORIGIN_X}"

    def test_symbols_in_different_columns_untouched(self) -> None:
        """Symbols in non-overloaded columns must keep their original x."""
        positions: dict[str, tuple[float, float, float | None]] = {
            # One x=50.8 column (3 symbols — exactly at limit, not overloaded)
            "R1": (50.8, 10.0, 0.0),
            "R2": (50.8, 20.0, 0.0),
            "R3": (50.8, 30.0, 0.0),
            # One separate symbol at x=120
            "U1": (120.0, 50.0, None),
        }
        result = _spread_x_columns(positions, max_per_column=3)
        assert result["R1"][0] == pytest.approx(50.8)
        assert result["R2"][0] == pytest.approx(50.8)
        assert result["R3"][0] == pytest.approx(50.8)
        assert result["U1"][0] == pytest.approx(120.0)

    def test_identical_x_produces_at_least_n_columns(self) -> None:
        """Phase 4.3 acceptance test: N identical-x symbols produce >= ceil(N/max) columns.

        This is the primary readability guard from the TODO: a set of symbols
        with identical x must produce >= N x-columns after the spread pass.
        """
        max_per_col = 3
        # 12 symbols at x=152.4 (120 × 1.27 mm) — centre of usable page width.
        x0 = 152.4
        n_sym = 12
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, n_sym + 1)
        }
        result = _spread_x_columns(positions, max_per_column=max_per_col, col_step_mm=25.4)
        distinct_x_count = len({v[0] for v in result.values()})
        expected_min = math.ceil(n_sym / max_per_col)  # ceil(12/3) = 4
        assert distinct_x_count >= expected_min, (
            f"Expected >= {expected_min} x-columns from {n_sym} identical-x symbols, "
            f"got {distinct_x_count}"
        )
