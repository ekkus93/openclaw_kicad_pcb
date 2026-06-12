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

    def test_compute_symbol_positions_refines_shared_negative_rail_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should re-anchor ambiguous negative-rail decouplers after raw layout."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[
                    PinRefIR(ref="U1", pin="1"),
                    PinRefIR(ref="U2", pin="1"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U2", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U2"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        assert round(result["C1"][0], 2) == round(result["U1"][0], 2), (
            f"Expected C1 to re-anchor to U1 after shared negative-rail refinement, got: {result}"
        )

    def test_shared_negative_rail_refinement_uses_signal_siblings_when_only_power_unit_touches_rail(
        self,
    ) -> None:
        """Shared rails should refine through sibling signal units when the rail only hits U1P."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        refined_map = _gv_mod.refine_shared_rail_decoupling_map(
            ir,
            {
                "U1A": (88.9, 30.48, 0.0),
                "U1B": (96.52, 83.82, 0.0),
                "U1P": (92.71, 57.15, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            },
            {"C1": "U1B"},
        )
        assert refined_map == {"C1": "U1A"}, (
            "Expected sibling signal units to participate in shared negative-rail refinement "
            f"when only the power unit touches the rail, got: {refined_map}"
        )

    def test_compute_symbol_positions_refines_shared_negative_rail_anchor_through_power_unit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should refine split-unit shared rails even when only U1P is on the rail."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1A"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U1B"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("U1P"): (92.71, 57.15, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        family_center_x = round((result["U1A"][0] + result["U1B"][0]) / 2.0, 2)
        assert round(result["C1"][0], 2) == family_center_x, (
            "Expected the split-unit shared-rail decoupler to stay centered on the final "
            f"signal-family span after sibling refinement, got: {result}"
        )
        assert round(result["C1"][0], 2) == round(result["U1P"][0], 2), (
            "Expected the split-unit shared-rail decoupler to follow the recentered power unit "
            f"over the sibling signal span, got: {result}"
        )

    def test_dot_source_unchanged_without_decoupling_map(self) -> None:
        """build_dot_source without decoupling_map must not co-locate C1 via invisible edge."""
        ir = _decoupling_ir()
        src = _gv_mod.build_dot_source(ir)  # no decoupling_map kwarg
        # Structural tier-anchor invisible elements are always present; the test
        # ensures that no invisible *co-location* edge is added for C1 when no
        # decoupling_map is supplied.
        invis_lines = [ln for ln in src.splitlines() if "style=invis" in ln and "C1" in ln]
        assert not invis_lines, (
            "C1 should not have invisible co-location edges without decoupling_map; "
            f"found: {invis_lines}"
        )


# ---------------------------------------------------------------------------
# Phase 2 — Vertical grouping / affinity clustering (Rule §3)
# ---------------------------------------------------------------------------


def _affinity_ir() -> CircuitIR:
    """Return an IR suitable for testing affinity grouping.

    Topology (4 components, 3 tiers):
    * Tier 0: J1 (connector seed)
    * Tier 1: R1, R2
    * Tier 2: U1

    Nets:
    * IN   : J1 pin1 ← → R1 pin1  (J1 and R1 share IN)
    * STAGE: R1 pin2 ← → R2 pin1 ← → U1 pin1 (R1, R2, U1 share STAGE)
    * BIAS : R2 pin2 ← → U1 pin2  (R2 and U1 share BIAS; so R2+U1 share 2 nets)
    * GND  : J1 pin2 → power only
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="R2", symbol="Device:R", value="47k"),
        ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
    ]
    nets = [
        NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
        NetIR(
            name="STAGE",
            pins=[
                PinRefIR(ref="R1", pin="2"),
                PinRefIR(ref="R2", pin="1"),
                PinRefIR(ref="U1", pin="1"),
            ],
        ),
        NetIR(name="BIAS", pins=[PinRefIR(ref="R2", pin="2"), PinRefIR(ref="U1", pin="2")]),
        NetIR(
            name="GND",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="R1", pin="2")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)
