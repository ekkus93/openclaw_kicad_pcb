"""Phase 4: decoupling cap co-location tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    GRID_COL_MM,
)

pytestmark = pytest.mark.unit


def _decoupling_ir() -> CircuitIR:
    """Return an IR with C1 as a decoupling cap (one signal net, one power net).

    Topology:
    * J1 -- IN_SIG --> R1 -- OUT_SIG --> U1
    * C1: pin1 on VCC_LOCAL (signal, shared with U1), pin2 on GND (power)
    * VCC_LOCAL is NOT in the power-net pattern so it is treated as a signal net.
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
    ]
    nets = [
        NetIR(
            name="IN_SIG",
            pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
        ),
        NetIR(
            name="OUT_SIG",
            pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="1")],
        ),
        # VCC_LOCAL: shared between U1's power pin and C1's signal pin.
        NetIR(
            name="VCC_LOCAL",
            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="C1", pin="1")],
        ),
        # GND: power net — C1's second pin and J1's return.
        NetIR(
            name="GND",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="C1", pin="2")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestDecouplingCapCoLocation:
    """Integration tests for decoupling cap co-location in DOT source and post-snap."""

    def test_invisible_edge_in_dot_source(self) -> None:
        """DOT source must contain an invisible edge from the decoupling cap to its IC."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        assert decoupling_map, "pre-condition: decoupling_map should not be empty"

        src = _gv_mod.build_dot_source(ir, decoupling_map=decoupling_map)
        assert "C1 -> U1 [style=invis, weight=10, constraint=false];" in src, (
            f"invisible edge C1->U1 missing from DOT source.\nFull source:\n{src}"
        )

    def test_rank_same_subgraph_for_decoupling_pair(self) -> None:
        """DOT source must contain a rank=same subgraph grouping the IC and its bypass cap."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        src = _gv_mod.build_dot_source(ir, decoupling_map=decoupling_map)
        # Look for the rank=same block that contains both U1 and C1.
        assert "rank=same" in src, "rank=same directive missing"
        # The pair U1 + C1 must appear inside a rank=same block.
        lines = src.splitlines()
        in_same = False
        found_u1 = found_c1 = False
        for line in lines:
            stripped = line.strip()
            if stripped == "rank=same;":
                in_same = True
                found_u1 = found_c1 = False
            elif stripped == "}" and in_same:
                if found_u1 and found_c1:
                    break
                in_same = False
            elif in_same:
                if stripped == "U1;":
                    found_u1 = True
                if stripped == "C1;":
                    found_c1 = True
        assert found_u1 and found_c1, (
            "No rank=same subgraph containing both U1 and C1 found in DOT source.\n"
            f"Full source:\n{src}"
        )

    def test_rank_same_subgraph_skips_cross_tier_decoupling_pair(self) -> None:
        """Explicit tier constraints should not be contradicted by decoupling rank=same blocks."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        src = _gv_mod.build_dot_source(
            ir,
            decoupling_map=decoupling_map,
            tiers={"J1": 0, "R1": 1, "U1": 2, "C1": 0},
        )

        assert "C1 -> U1 [style=invis, weight=10, constraint=false];" in src
        assert "{\n    rank=same;\n    U1;\n    C1;\n  }" not in src

    def test_post_snap_sets_cap_x_equal_to_ic_x(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After compute_symbol_positions, decoupling cap x must equal its IC's x."""
        ir = _decoupling_ir()

        # Fake dot output: C1 at a different column than U1.
        # Keys are safe_ids (same as refs for these component names).
        fake_positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (55.0, 50.80, None),
            "U1": (80.0, 60.0, None),
            "C1": (35.0, 40.0, None),  # different x than U1 initially
        }

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return fake_positions

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")
        result = engine.compute_symbol_positions(ir)

        u1_x = result["U1"][0]
        c1_x = result["C1"][0]
        assert c1_x == pytest.approx(u1_x), (
            f"C1.x ({c1_x}) should equal U1.x ({u1_x}) after decoupling-cap snap"
        )

    def test_post_snap_sets_cap_y_above_ic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """After compute_symbol_positions, decoupling cap y = IC.y - GRID_ROW_MM."""
        ir = _decoupling_ir()

        fake_positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (55.0, 50.80, None),
            "U1": (80.0, 60.0, None),
            "C1": (35.0, 40.0, None),
        }

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return fake_positions

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")
        result = engine.compute_symbol_positions(ir)

        u1_y = result["U1"][1]
        c1_y = result["C1"][1]
        expected_y = u1_y - _gv_mod.GRID_ROW_MM
        assert c1_y == pytest.approx(expected_y), (
            f"C1.y ({c1_y}) should be U1.y - GRID_ROW_MM ({expected_y}), but got {c1_y}"
        )

    def test_post_snap_keeps_mixed_polarity_decoupling_bank_compact(self) -> None:
        """Mixed-polarity decouplers should stay in a compact symmetric bank around the IC lane."""
        positions = {
            "U1": (100.0, 100.0, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
            "C3": (50.0, 50.0, None),
            "C4": (55.0, 55.0, None),
            "C5": (60.0, 60.0, None),
            "C6": (65.0, 65.0, None),
            "C7": (70.0, 70.0, None),
            "C8": (75.0, 75.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1",
                "C2": "U1",
                "C3": "U1",
                "C4": "U1",
                "C5": "U1",
                "C6": "U1",
                "C7": "U1",
                "C8": "U1",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "positive",
                "C3": "positive",
                "C4": "positive",
                "C5": "negative",
                "C6": "negative",
                "C7": "negative",
                "C8": "negative",
            },
        )

        ux, uy, _ = result["U1"]
        positive_refs = ("C1", "C2", "C3", "C4")
        negative_refs = ("C5", "C6", "C7", "C8")
        expected_xs = {
            round(ux, 2),
            round(ux - GRID_COL_MM, 2),
            round(ux + GRID_COL_MM, 2),
        }

        positive_xs = {round(result[ref][0], 2) for ref in positive_refs}
        negative_xs = {round(result[ref][0], 2) for ref in negative_refs}

        assert positive_xs == expected_xs, (
            f"Positive overflow bank should use compact symmetric x lanes: {result}"
        )
        assert negative_xs == expected_xs, (
            f"Negative overflow bank should mirror the same compact x lanes: {result}"
        )

    def test_post_snap_adds_extra_clearance_for_second_same_lane_decoupler(self) -> None:
        """Second same-column decouplers need extra y clearance to avoid pin-stub overlap."""
        positions = {
            "U1": (100.0, 100.0, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1",
                "C2": "U1",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "positive",
            },
        )

        assert result["C1"][0] == pytest.approx(100.0)
        assert result["C1"][1] == pytest.approx(100.0 - _gv_mod.GRID_ROW_MM)
        assert result["C1"][2] is None
        assert result["C2"][0] == pytest.approx(100.0)
        assert result["C2"][1] == pytest.approx(100.0 - (4 * _gv_mod.GRID_ROW_MM))
        assert result["C2"][2] is None

    def test_post_snap_centers_split_unit_decoupling_bank_on_family_x(self) -> None:
        """Split-unit decouplers should align to the visible device family, not one sibling lane."""
        positions = {
            "U1A": (91.44, 99.06, None),
            "U1B": (121.92, 99.06, None),
            "U1P": (106.68, 129.54, None),
            "C1": (40.0, 40.0, None),
            "C2": (45.0, 45.0, None),
            "C3": (50.0, 50.0, None),
            "C4": (55.0, 55.0, None),
        }

        result = _gv_mod.post_snap_decoupling_caps(
            positions,
            {
                "C1": "U1A",
                "C2": "U1A",
                "C3": "U1A",
                "C4": "U1A",
            },
            rail_polarities={
                "C1": "positive",
                "C2": "negative",
                "C3": "positive",
                "C4": "negative",
            },
        )

        family_center_x = round((positions["U1A"][0] + positions["U1B"][0]) / 2.0, 2)
        decoupling_xs = {round(result[ref][0], 2) for ref in ("C1", "C2", "C3", "C4")}
        positive_refs = ("C1", "C3")
        negative_refs = ("C2", "C4")
        uy = positions["U1A"][1]

        assert family_center_x in decoupling_xs, (
            "Split-unit decoupling bank should keep its primary lane on the family centerline: "
            f"family_center_x={family_center_x}, decoupling_xs={sorted(decoupling_xs)}"
        )
        assert round(positions["U1A"][0], 2) not in decoupling_xs, (
            "Split-unit decoupling bank should not stay pinned to only the refined anchor lane: "
            f"anchor_x={positions['U1A'][0]:.2f}, decoupling_xs={sorted(decoupling_xs)}"
        )
        assert all(result[ref][1] < uy for ref in positive_refs), (
            f"Positive decouplers should stay above the IC row: {result}"
        )
        assert all(result[ref][1] > uy for ref in negative_refs), (
            f"Negative decouplers should stay below the IC row: {result}"
        )
        assert max(result[ref][1] for ref in positive_refs) < min(
            result[ref][1] for ref in negative_refs
        ), f"Mixed-polarity banks should stay vertically separated around the IC: {result}"
