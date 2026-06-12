"""Phase 4: crossing remediation, page clamping, and X-column spread."""

from __future__ import annotations

import math

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import (
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _clamp_to_page,
    _remediate_crossings,
    _spread_x_columns,
)
from kicad_pcb.layout import (
    build_signal_adjacency,
    count_wire_crossings,
)

pytestmark = pytest.mark.unit


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return an S-expression wire snippet."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


# ---------------------------------------------------------------------------
# TestRemediateCrossings
# ---------------------------------------------------------------------------

# Grid constants matching layout.py (used to place components on the snap grid).
_COL0_X: float = 30.48  # ORIGIN_X = first column x
_COL1_X: float = 60.96  # ORIGIN_X + GRID_COL_MM = second column x
_COL2_X: float = 91.44  # ORIGIN_X + 2*GRID_COL_MM = third column x
_SLOT0_Y: float = 10.0  # top y-slot used in tests
_SLOT1_Y: float = 30.48  # bottom y-slot (≈ GRID_ROW_MM + SLOT0_Y for visual clarity)


def _crossing_ir() -> CircuitIR:
    """Build a 4-component IR whose wires form a detectable X crossing.

    The ``count_wire_crossings`` heuristic excludes wire pairs whose *left*
    endpoints share the same x-coordinate.  To produce a detectable crossing
    the two signal edges must start from **different columns**::

        col0 (x=30.48)      col1 (x=60.96)      col2 (x=91.44)
          R1 (y=10)  ─────────────────────────── R2 (y=30)   NET_A
                            R3 (y=30) ──────────── R4 (y=10)  NET_B

    Edge R1→R2 starts at x=30.48 (col0) and ends at col2.
    Edge R3→R4 starts at x=60.96 (col1) and ends at col2.

    Since left-endpoint x differs (30.48 < 60.96) and the heuristic
    checks ``yr(R2)=30 > ycr(R4)=10`` → 1 crossing detected.

    After one barycentric + y-slot sweep col2 is reordered so both wires
    become horizontal (R2 and R4 swap y-values) → 0 crossings.
    """
    components = [
        ComponentIR(ref="R1", symbol="Device:R", value="1k"),
        ComponentIR(ref="R2", symbol="Device:R", value="1k"),
        ComponentIR(ref="R3", symbol="Device:R", value="1k"),
        ComponentIR(ref="R4", symbol="Device:R", value="1k"),
    ]
    nets = [
        NetIR(
            name="NET_A",
            pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
        ),
        NetIR(
            name="NET_B",
            pins=[PinRefIR(ref="R3", pin="1"), PinRefIR(ref="R4", pin="1")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


def _crossing_positions() -> dict[str, tuple[float, float, float | None]]:
    """Return positions that produce a detectable X-crossing for *_crossing_ir()*.

    R1 at col0 → R2 at col2 (NET_A, going down-right).
    R3 at col1 → R4 at col2 (NET_B, going up-right).

    The right endpoints (R2 at y=30.48, R4 at y=10) are inverted relative to
    the left endpoints (R1 at y=10, R3 at y=30.48), satisfying the
    ``yr > ycr`` condition for the crossing heuristic.

    ``R2`` is inserted before ``R4`` so ``by_col[2] = [R2, R4]``.
    After the y-slot assignment (sorted ascending), R2 is assigned the
    smaller y-slot (10) and R4 the larger (30.48), eliminating the crossing.
    """
    return {
        "R1": (_COL0_X, _SLOT0_Y, None),  # col0, y=10 (low)
        "R3": (_COL1_X, _SLOT1_Y, None),  # col1, y=30.48 (high)
        "R2": (_COL2_X, _SLOT1_Y, None),  # col2 slot 1, inserted first → by_col[2][0]
        "R4": (_COL2_X, _SLOT0_Y, None),  # col2 slot 0, inserted second → by_col[2][1]
    }
    # Edge R1→R2: (30.48,10) → (91.44,30.48)  going down-right
    # Edge R3→R4: (60.96,30.48) → (91.44,10)  going up-right
    # xl=30.48 < xcl=60.96; yr=30.48 > ycr=10 → 1 crossing detected.


class TestRemediateCrossings:
    """Unit tests for the _remediate_crossings snap pass."""

    # ------------------------------------------------------------------
    # Happy-path: crossing is actually reduced
    # ------------------------------------------------------------------

    def test_crossing_eliminated_after_one_sweep(self) -> None:
        """A classical X-crossing between two columns must be eliminated."""
        ir = _crossing_ir()
        positions = _crossing_positions()

        sig_adj = build_signal_adjacency(ir)
        pos2_before = {r: (x, y) for r, (x, y, _) in positions.items()}
        assert count_wire_crossings(pos2_before, sig_adj) == 1, (
            "pre-condition: must have 1 crossing"
        )

        result = _remediate_crossings(positions, ir)

        pos2_after = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2_after, sig_adj) == 0, (
            f"Crossing must be eliminated.  Final positions: {result}"
        )

    def test_y_slots_are_preserved_not_created(self) -> None:
        """After remediation, y-values must all come from the original positions set."""
        positions = _crossing_positions()
        original_y_values = {y for _x, y, _rot in positions.values()}

        result = _remediate_crossings(_crossing_positions(), _crossing_ir())

        for ref, (_, y, _) in result.items():
            assert y in original_y_values, (
                f"{ref} has unexpected y={y}; allowed values={original_y_values}"
            )

    def test_x_and_rotation_are_preserved(self) -> None:
        """x-coordinates and rotations must be unchanged by remediation."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir())

        for ref, (x, _y, rot) in result.items():
            orig_x, _orig_y, orig_rot = positions[ref]
            assert x == pytest.approx(orig_x), f"{ref}: x changed {orig_x} → {x}"
            assert rot == orig_rot, f"{ref}: rotation changed {orig_rot} → {rot}"

    def test_all_refs_present_in_result(self) -> None:
        """Every ref in the input must appear in the output."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir())
        assert set(result.keys()) == set(positions.keys())

    # ------------------------------------------------------------------
    # Break condition: ratio already below threshold
    # ------------------------------------------------------------------

    def test_already_optimal_layout_unchanged(self) -> None:
        """If no crossing exists, positions must be returned unchanged."""
        ir = _crossing_ir()
        # Arrange: wires are horizontal → 0 crossings.
        # R1 (col0) → R2 (col2) at same y; R3 (col1) → R4 (col2) at same y.
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R3": (_COL1_X, _SLOT1_Y, None),
            "R2": (_COL2_X, _SLOT0_Y, None),  # same y as R1 → no crossing
            "R4": (_COL2_X, _SLOT1_Y, None),  # same y as R3 → no crossing
        }
        result = _remediate_crossings(positions, ir)
        assert result == positions, f"Optimal layout must be unchanged; got {result}"

    def test_below_threshold_returns_immediately(self) -> None:
        """A crossing ratio below threshold must not trigger any sweep."""
        ir = _crossing_ir()
        # Use a threshold of 1.0 so ANY ratio is below threshold → immediate return.
        positions = _crossing_positions()
        result = _remediate_crossings(positions, ir, crossing_ratio_threshold=1.0)

        # Immediate return means result == input (no deoverlap run either,
        # since the function returns before entering the sweep block).
        # The only transformation allowed is the identity.
        # Positions are returned as-is because ratio < threshold at sweep 0 break.
        pos2_before = {r: (x, y) for r, (x, y, _) in positions.items()}
        pos2_after = {r: (x, y) for r, (x, y, _) in result.items()}
        # The function returns in the loop body; no _deoverlap after the loop is called.
        # So result equals positions exactly.
        assert pos2_before == pos2_after, (
            f"With threshold=1.0 result must equal input; got {result}"
        )

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_zero_signal_wires_returns_unchanged(self) -> None:
        """A circuit with no signal nets must be returned without error."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1")]),
                NetIR(name="GND", pins=[PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R2": (_COL1_X, _SLOT0_Y, None),
        }
        result = _remediate_crossings(positions, ir)
        assert result == positions, "Zero-signal-wire circuit must be returned unchanged"

    def test_empty_positions_returns_empty(self) -> None:
        """Empty positions dict must return empty dict without error."""
        # CircuitIR requires ≥1 component and ≥1 net; use minimal valid IR.
        ir = _crossing_ir()
        result = _remediate_crossings({}, ir)
        assert result == {}

    def test_max_sweeps_one_skips_sorting(self) -> None:
        """max_sweeps=1 must cause an immediate exit without any column reordering.

        The break condition fires on ``sweep == max_sweeps - 1`` *before* the
        sort runs.  With max_sweeps=1, sweep=0 is already the last sweep, so
        the sort is never executed and the crossing is not improved.
        Only the trailing ``_deoverlap_positions`` call runs.
        """
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir(), max_sweeps=1)

        ir = _crossing_ir()
        sig_adj = build_signal_adjacency(ir)
        pos2 = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2, sig_adj) == 1, (
            "max_sweeps=1 skips sorting; crossing must remain"
        )

    def test_max_sweeps_two_allows_one_sort_pass(self) -> None:
        """max_sweeps=2 allows exactly one sort pass and must fix the crossing."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir(), max_sweeps=2)

        ir = _crossing_ir()
        sig_adj = build_signal_adjacency(ir)
        pos2 = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2, sig_adj) == 0, (
            "max_sweeps=2 allows one sort; crossing must be eliminated"
        )

    def test_power_symbols_excluded_and_preserved(self) -> None:
        """#PWR / #FLG refs must not be reordered and must appear in the result."""
        ir = _crossing_ir()
        positions = dict(_crossing_positions())
        positions["#PWR01"] = (_COL0_X, _SLOT0_Y - 5.0, None)
        positions["#FLG02"] = (_COL1_X, _SLOT0_Y - 5.0, None)

        result = _remediate_crossings(positions, ir)

        assert result["#PWR01"] == (_COL0_X, _SLOT0_Y - 5.0, None), "#PWR01 must be unchanged"
        assert result["#FLG02"] == (_COL1_X, _SLOT0_Y - 5.0, None), "#FLG02 must be unchanged"

    def test_input_dict_not_mutated(self) -> None:
        """The original positions dict must not be modified in-place."""
        positions = _crossing_positions()
        original = dict(positions)
        _remediate_crossings(positions, _crossing_ir())
        assert positions == original, "Input dict must not be mutated"

    def test_single_component_per_column_unchanged(self) -> None:
        """Columns with only one component cannot be reordered; positions unchanged."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="NET_A",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R2": (_COL1_X, _SLOT1_Y, None),
        }
        result = _remediate_crossings(positions, ir)
        # Each column has one component; no reordering possible.
        assert result["R1"][0] == pytest.approx(_COL0_X)
        assert result["R2"][0] == pytest.approx(_COL1_X)


# ---------------------------------------------------------------------------
# Phase 4 (readable schematics) — _clamp_to_page
# ---------------------------------------------------------------------------


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
