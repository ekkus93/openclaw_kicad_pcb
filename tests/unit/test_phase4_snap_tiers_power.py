"""Phase 4 snap: fit-to-page normalisation and power symbol snapping tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR

pytestmark = pytest.mark.unit


def _power_ir() -> CircuitIR:
    """Minimal CircuitIR that includes #PWR VCC and GND symbols plus a connector.

    Topology:
        #PWR01 (VCC)  ─── VCC net ─── J1 pin 1
        #PWR02 (GND)  ─── GND net ─── J1 pin 2
        #FLG01 (PWR_FLAG) ─── VCC net  (shares a net so IR validates)
    """
    return CircuitIR(
        version="1",
        components=[
            ComponentIR(ref="#PWR01", symbol="power:VCC", value="VCC"),
            ComponentIR(ref="#PWR02", symbol="power:GND", value="GND"),
            ComponentIR(ref="#FLG01", symbol="power:PWR_FLAG", value="PWR_FLAG"),
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
        ],
        nets=[
            NetIR(
                name="VCC",
                pins=[
                    PinRefIR(ref="#PWR01", pin="1"),
                    PinRefIR(ref="#FLG01", pin="1"),
                    PinRefIR(ref="J1", pin="1"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[PinRefIR(ref="#PWR02", pin="1"), PinRefIR(ref="J1", pin="2")],
            ),
        ],
    )


class TestFitToPage:
    """Unit tests for _fit_to_page() (Phase 6.4 — page-fit normalisation)."""

    def _positions(
        self, refs_xy: list[tuple[str, float, float]]
    ) -> dict[str, tuple[float, float, float | None]]:
        return {ref: (x, y, None) for ref, x, y in refs_xy}

    def test_positions_within_bounds_unchanged(self) -> None:
        """Positions already within the A4 area must be returned unchanged."""
        positions = self._positions(
            [
                ("R1", _gv_mod.ORIGIN_X + 10.0, _gv_mod.ORIGIN_Y + 10.0),
                ("R2", _gv_mod.ORIGIN_X + 50.0, _gv_mod.ORIGIN_Y + 50.0),
            ]
        )
        result = _gv_mod.fit_to_page(positions)
        assert result["R1"][:2] == pytest.approx(positions["R1"][:2])
        assert result["R2"][:2] == pytest.approx(positions["R2"][:2])

    def test_fit_to_page_shrinks_oversized_layout(self) -> None:
        """Layout wider than PAGE_MAX_X must be proportionally shrunk to fit."""
        # Place one component way off to the right, far past PAGE_MAX_X.
        far_x = _gv_mod.PAGE_MAX_X + 300.0
        positions = self._positions(
            [
                ("R1", _gv_mod.ORIGIN_X, _gv_mod.ORIGIN_Y),
                ("R2", far_x, _gv_mod.ORIGIN_Y + 20.0),
            ]
        )
        result = _gv_mod.fit_to_page(positions)

        # After fitting, no x-coordinate may exceed PAGE_MAX_X.
        for ref, (x, y, _) in result.items():
            assert x <= _gv_mod.PAGE_MAX_X + 0.01, (
                f"{ref}: x={x} exceeds PAGE_MAX_X={_gv_mod.PAGE_MAX_X}"
            )
            assert y <= _gv_mod.PAGE_MAX_Y + 0.01, (
                f"{ref}: y={y} exceeds PAGE_MAX_Y={_gv_mod.PAGE_MAX_Y}"
            )

        # The leftmost component stays at ORIGIN_X (the origin is not shifted).
        assert result["R1"][0] == pytest.approx(_gv_mod.ORIGIN_X), (
            "Leftmost component x must remain at ORIGIN_X after page-fit shrink."
        )

    def test_fit_to_page_shrinks_too_tall_layout(self) -> None:
        """Layout taller than PAGE_MAX_Y must be proportionally shrunk to fit."""
        tall_y = _gv_mod.PAGE_MAX_Y + 200.0
        positions = self._positions(
            [
                ("C1", _gv_mod.ORIGIN_X + 10.0, _gv_mod.ORIGIN_Y),
                ("C2", _gv_mod.ORIGIN_X + 10.0, tall_y),
            ]
        )
        result = _gv_mod.fit_to_page(positions)
        for ref, (x, y, _) in result.items():
            assert y <= _gv_mod.PAGE_MAX_Y + 0.01, (
                f"{ref}: y={y} exceeds PAGE_MAX_Y={_gv_mod.PAGE_MAX_Y} after fit"
            )

    def test_empty_positions_returns_empty(self) -> None:
        """An empty positions dict must return an empty dict without error."""
        result = _gv_mod.fit_to_page({})
        assert result == {}

    def test_relative_distances_preserved(self) -> None:
        """After shrinking, the ratio of distances between components is unchanged."""
        # Two components, one very far to the right.
        positions = self._positions(
            [
                ("A", _gv_mod.ORIGIN_X, _gv_mod.ORIGIN_Y),
                ("B", _gv_mod.ORIGIN_X + 600.0, _gv_mod.ORIGIN_Y),
            ]
        )
        result = _gv_mod.fit_to_page(positions)
        orig_dx = positions["B"][0] - positions["A"][0]
        new_dx = result["B"][0] - result["A"][0]
        # The ratio should be constant (= avail_x / span_x)
        expected_ratio = (_gv_mod.PAGE_MAX_X - _gv_mod.ORIGIN_X) / orig_dx
        assert new_dx == pytest.approx(orig_dx * expected_ratio, rel=1e-4), (
            f"Distance ratio not preserved: orig_dx={orig_dx}, new_dx={new_dx}, "
            f"expected_ratio={expected_ratio}"
        )


class TestSnapPowerSymbols:
    """Tests for _snap_power_symbols() in graphviz_layout."""

    def _make_positions(self) -> dict[str, tuple[float, float, float | None]]:
        return {
            "#PWR01": (45.0, 100.0, None),
            "#PWR02": (45.0, 70.0, None),
            "#FLG01": (60.0, 90.0, None),
            "J1": (30.48, 80.0, None),
        }

    def test_vcc_symbol_clamped_to_top_y(self) -> None:
        """#PWR symbol with value 'VCC' must be clamped to y = ORIGIN_Y."""
        ir = _power_ir()
        result = _gv_mod.snap_power_symbols(self._make_positions(), ir)
        assert result["#PWR01"][1] == pytest.approx(_gv_mod.ORIGIN_Y), (
            f"#PWR01 (VCC) y should equal ORIGIN_Y={_gv_mod.ORIGIN_Y}, got {result['#PWR01'][1]}"
        )

    def test_gnd_symbol_clamped_to_bottom_y(self) -> None:
        """#PWR symbol with value 'GND' must be clamped to y = PAGE_MAX_Y - 20."""
        ir = _power_ir()
        result = _gv_mod.snap_power_symbols(self._make_positions(), ir)
        expected = _gv_mod.PAGE_MAX_Y - 20.0
        assert result["#PWR02"][1] == pytest.approx(expected), (
            f"#PWR02 (GND) y should equal PAGE_MAX_Y - 20 = {expected}, got {result['#PWR02'][1]}"
        )

    def test_power_flag_clamped_to_top_y(self) -> None:
        """#FLG symbol with value 'PWR_FLAG' must be clamped to y = ORIGIN_Y."""
        ir = _power_ir()
        result = _gv_mod.snap_power_symbols(self._make_positions(), ir)
        assert result["#FLG01"][1] == pytest.approx(_gv_mod.ORIGIN_Y), (
            f"#FLG01 (PWR_FLAG) y should equal ORIGIN_Y={_gv_mod.ORIGIN_Y}, "
            f"got {result['#FLG01'][1]}"
        )

    def test_non_power_ref_unchanged(self) -> None:
        """Normal component refs (e.g. J1) must not be moved by snap_power_symbols."""
        ir = _power_ir()
        positions = self._make_positions()
        result = _gv_mod.snap_power_symbols(positions, ir)
        assert result["J1"] == positions["J1"], (
            f"J1 should be unchanged, but got {result['J1']} instead of {positions['J1']}"
        )

    def test_x_coordinate_preserved(self) -> None:
        """snap_power_symbols must preserve the x-coordinate of each power symbol."""
        ir = _power_ir()
        positions = self._make_positions()
        result = _gv_mod.snap_power_symbols(positions, ir)
        assert result["#PWR01"][0] == pytest.approx(positions["#PWR01"][0])
        assert result["#PWR02"][0] == pytest.approx(positions["#PWR02"][0])
        assert result["#FLG01"][0] == pytest.approx(positions["#FLG01"][0])

    def test_agnd_variant_clamped_to_bottom(self) -> None:
        """AGND (analogue ground variant) must also be treated as GND-type."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="#PWR03", symbol="power:AGND", value="AGND"),
                ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ],
            nets=[
                NetIR(
                    name="AGND",
                    pins=[PinRefIR(ref="#PWR03", pin="1"), PinRefIR(ref="J1", pin="1")],
                )
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "#PWR03": (50.0, 80.0, None),
            "J1": (30.48, 80.0, None),
        }
        result = _gv_mod.snap_power_symbols(positions, ir)
        expected = _gv_mod.PAGE_MAX_Y - 20.0
        assert result["#PWR03"][1] == pytest.approx(expected), (
            f"AGND symbol should be at y={expected}, got {result['#PWR03'][1]}"
        )

    def test_vss_variant_clamped_to_bottom(self) -> None:
        """VSS must follow the shared ground-family row placement rule."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="#PWR04", symbol="power:VSS", value="VSS"),
                ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ],
            nets=[
                NetIR(
                    name="VSS",
                    pins=[PinRefIR(ref="#PWR04", pin="1"), PinRefIR(ref="J1", pin="1")],
                )
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "#PWR04": (50.0, 80.0, None),
            "J1": (30.48, 80.0, None),
        }

        result = _gv_mod.snap_power_symbols(positions, ir)
        expected = _gv_mod.PAGE_MAX_Y - 20.0
        assert result["#PWR04"][1] == pytest.approx(expected)

    def test_zero_volt_variant_clamped_to_bottom(self) -> None:
        """0V-labelled power symbols must also land on the bottom row."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="#PWR05", symbol="power:GND", value="0V"),
                ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ],
            nets=[
                NetIR(
                    name="0V",
                    pins=[PinRefIR(ref="#PWR05", pin="1"), PinRefIR(ref="J1", pin="1")],
                )
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "#PWR05": (50.0, 80.0, None),
            "J1": (30.48, 80.0, None),
        }

        result = _gv_mod.snap_power_symbols(positions, ir)
        expected = _gv_mod.PAGE_MAX_Y - 20.0
        assert result["#PWR05"][1] == pytest.approx(expected)

    def test_power_snap_runs_inside_compute_symbol_positions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """#PWR VCC symbol y must equal ORIGIN_Y after full compute_symbol_positions."""
        ir = _power_ir()

        # _run_dot returns positions keyed by safe_id (# → _).
        # _safe_id("#PWR01") == "_PWR01", etc.
        fake_positions: dict[str, tuple[float, float, float | None]] = {
            "_PWR01": (45.0, 100.0, None),
            "_PWR02": (45.0, 70.0, None),
            "_FLG01": (60.0, 90.0, None),
            "J1": (30.48, 80.0, None),
        }

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return fake_positions

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")
        result = engine.compute_symbol_positions(ir)

        assert result["#PWR01"][1] == pytest.approx(_gv_mod.ORIGIN_Y), (
            f"#PWR01 VCC should be at y=ORIGIN_Y={_gv_mod.ORIGIN_Y} "
            f"after compute_symbol_positions, got {result['#PWR01'][1]}"
        )
        expected_gnd_y = _gv_mod.PAGE_MAX_Y - 20.0
        assert result["#PWR02"][1] == pytest.approx(expected_gnd_y), (
            f"#PWR02 GND should be at y={expected_gnd_y} "
            f"after compute_symbol_positions, got {result['#PWR02'][1]}"
        )


# ---------------------------------------------------------------------------
# Phase 5 — Feedback network detection
# ---------------------------------------------------------------------------
