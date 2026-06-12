"""Phase 4 snap: IC column centering tests."""

from __future__ import annotations

import pytest

from kicad_pcb.graphviz_layout.snap import (
    _center_ics_in_columns,
)

pytestmark = pytest.mark.unit


class TestCenterICsInColumns:
    """Unit tests for the _center_ics_in_columns snap pass."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pos(
        entries: dict[str, tuple[float, float]],
    ) -> dict[str, tuple[float, float, float | None]]:
        """Build a positions dict from {ref: (x, y)} with rot=None."""
        return {ref: (x, y, None) for ref, (x, y) in entries.items()}

    @staticmethod
    def _y_order(
        positions: dict[str, tuple[float, float, float | None]], column_x: float
    ) -> list[str]:
        """Return refs in a column (given x) sorted by ascending y."""
        return sorted(
            [r for r, (x, _y, _rot) in positions.items() if x == column_x],
            key=lambda r: positions[r][1],
        )

    # ------------------------------------------------------------------
    # Core ordering tests
    # ------------------------------------------------------------------

    def test_ic_lands_at_middle_of_three_ref_column(self) -> None:
        """Single IC in a 3-ref column must occupy the middle y-slot."""
        # x=10: three refs at y=10, 20, 30 — U1 is the IC
        positions = self._pos({"R1": (10.0, 10.0), "U1": (10.0, 20.0), "R2": (10.0, 30.0)})
        result = _center_ics_in_columns(positions)

        ordered = self._y_order(result, 10.0)
        ic_idx = ordered.index("U1")
        assert ic_idx == 1, f"IC should be at index 1 (middle), got {ic_idx}"

    def test_ic_lands_at_middle_of_five_ref_column(self) -> None:
        """Single IC in a 5-ref column must be at index 2 (middle)."""
        positions = self._pos(
            {
                "R1": (10.0, 10.0),
                "R2": (10.0, 20.0),
                "U1": (10.0, 30.0),
                "R3": (10.0, 40.0),
                "R4": (10.0, 50.0),
            }
        )
        result = _center_ics_in_columns(positions)
        ordered = self._y_order(result, 10.0)
        ic_idx = ordered.index("U1")
        assert ic_idx == 2, f"IC should be at index 2 (middle of 5), got {ic_idx}"

    def test_two_ics_in_four_ref_column_land_in_middle_pair(self) -> None:
        """Two ICs in a 4-ref column must occupy the two middle y-slots."""
        positions = self._pos(
            {
                "R1": (10.0, 10.0),
                "U1": (10.0, 20.0),
                "U2": (10.0, 30.0),
                "R2": (10.0, 40.0),
            }
        )
        result = _center_ics_in_columns(positions)
        ordered = self._y_order(result, 10.0)
        ic_indices = {ordered.index("U1"), ordered.index("U2")}
        assert ic_indices == {1, 2}, f"Both ICs should be at indices 1,2; got {ic_indices}"

    def test_column_with_only_passives_is_unchanged(self) -> None:
        """A column with no ICs must be returned with original positions."""
        positions = self._pos({"R1": (10.0, 10.0), "C1": (10.0, 20.0), "R2": (10.0, 30.0)})
        result = _center_ics_in_columns(positions)
        assert result == positions, "All-passive column must be unchanged"

    def test_halo_members_flank_ic(self) -> None:
        """Halo members must appear immediately adjacent to the IC."""
        # 4-ref column: plain_other=[R1,R2], halo_other=[C_fb], ic_refs=[U1]
        # Expected order: R1, C_fb, U1, R2  (or R2, C_fb, U1, R1)
        # plain_other[:1] + halo_other[:0 since mid=0] + [U1] + halo_other[0:] + plain_other[1:]
        # With 1 halo member: mid_halo=0
        # ordered = plain_other[:1] + [] + [U1] + [C_fb] + plain_other[1:]
        # = [R1, U1, C_fb, R2]
        positions = self._pos(
            {
                "R1": (10.0, 10.0),
                "C_fb": (10.0, 20.0),
                "U1": (10.0, 30.0),
                "R2": (10.0, 40.0),
            }
        )
        halo = {"C_fb": "U1"}
        result = _center_ics_in_columns(positions, halo=halo)
        ordered = self._y_order(result, 10.0)
        u1_idx = ordered.index("U1")
        cfb_idx = ordered.index("C_fb")
        assert abs(u1_idx - cfb_idx) == 1, (
            f"Halo member C_fb should be adjacent to U1; got order {ordered}"
        )

    # ------------------------------------------------------------------
    # Edge / boundary cases
    # ------------------------------------------------------------------

    def test_empty_positions_returns_empty(self) -> None:
        """Empty input must return empty dict without error."""
        result = _center_ics_in_columns({})
        assert result == {}

    def test_single_component_column_unchanged(self) -> None:
        """A single-component column (IC or passive) must be returned unchanged."""
        positions = self._pos({"U1": (10.0, 10.0)})
        result = _center_ics_in_columns(positions)
        assert result == positions

    def test_power_symbols_excluded_from_reordering(self) -> None:
        """#PWR and #FLG symbols must not participate in column reordering."""
        positions = self._pos(
            {
                "#PWR01": (10.0, 5.0),
                "R1": (10.0, 10.0),
                "U1": (10.0, 20.0),
                "R2": (10.0, 30.0),
            }
        )
        result = _center_ics_in_columns(positions)
        # Power symbol must stay at its original position.
        assert result["#PWR01"] == (10.0, 5.0, None)
        # Regular components still get reordered.
        ordered = self._y_order(result, 10.0)
        # U1 should be in the middle of the 3 regular refs (indices 1 out of 0,1,2)
        regular = [r for r in ordered if not r.startswith("#")]
        assert regular.index("U1") == 1, f"IC not centred: {regular}"

    def test_input_dict_not_mutated(self) -> None:
        """The original positions dict must not be modified in-place."""
        positions = self._pos({"R1": (10.0, 10.0), "U1": (10.0, 20.0), "R2": (10.0, 30.0)})
        original = dict(positions)
        _center_ics_in_columns(positions)
        assert positions == original

    def test_multiple_columns_only_reorders_ic_columns(self) -> None:
        """Columns without ICs must be unchanged; IC columns must be centred."""
        positions = self._pos(
            {
                # Column x=10: has IC — should reorder
                "R1": (10.0, 10.0),
                "U1": (10.0, 20.0),
                "R2": (10.0, 30.0),
                # Column x=50: no ICs — must be unchanged
                "C1": (50.0, 10.0),
                "C2": (50.0, 20.0),
            }
        )
        result = _center_ics_in_columns(positions)
        # Passive-only column at x=50 must be untouched.
        assert result["C1"] == (50.0, 10.0, None)
        assert result["C2"] == (50.0, 20.0, None)
        # IC column at x=10: U1 must be at the middle y-slot.
        ordered_10 = self._y_order(result, 10.0)
        assert ordered_10.index("U1") == 1

    def test_x_and_rotation_are_preserved(self) -> None:
        """x-coordinate and rotation must not be changed by this pass."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (10.0, 10.0, 90.0),
            "U1": (10.0, 20.0, 0.0),
            "R2": (10.0, 30.0, 270.0),
        }
        result = _center_ics_in_columns(positions)
        for ref, (x, _y, rot) in result.items():
            assert x == 10.0, f"{ref}: x changed"
            orig_rot = positions[ref][2]
            assert rot == orig_rot, f"{ref}: rotation changed from {orig_rot} to {rot}"

    def test_no_halo_kwarg_behaves_identically_to_none(self) -> None:
        """Calling without halo= must give the same result as halo=None."""
        positions = self._pos({"R1": (10.0, 10.0), "U1": (10.0, 20.0), "R2": (10.0, 30.0)})
        result_no_kw = _center_ics_in_columns(positions)
        result_none = _center_ics_in_columns(positions, halo=None)
        assert result_no_kw == result_none
