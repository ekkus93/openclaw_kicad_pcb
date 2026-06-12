"""Phase 4 snap: component type classifier and tier assignment tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.component_types import component_type
from kicad_pcb.tier import assign_tiers

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Phase 1 — Tier assignment (longest-path layering)
# ---------------------------------------------------------------------------


class TestComponentTypes:
    """Tests for component_type() classifier in component_types.py."""

    def test_connector_prefixes(self) -> None:
        """J*, CON*, P*, SJ*, TJ* refs are classified as 'connector'."""
        for ref in ("J1", "J12", "CON1", "P3", "SJ2", "TJ1"):
            assert component_type(ref) == "connector", (
                f"Expected 'connector' for {ref!r}, got {component_type(ref)!r}"
            )

    def test_ic_prefixes(self) -> None:
        """U*, IC*, OA* refs are classified as 'ic'."""
        for ref in ("U1", "U33", "IC1", "OA2"):
            assert component_type(ref) == "ic", (
                f"Expected 'ic' for {ref!r}, got {component_type(ref)!r}"
            )

    def test_passive_prefixes(self) -> None:
        """R*, C*, L*, D*, Q* refs are classified as 'passive'."""
        for ref in ("R1", "C10", "L3", "D1", "Q2"):
            assert component_type(ref) == "passive", (
                f"Expected 'passive' for {ref!r}, got {component_type(ref)!r}"
            )

    def test_misc_prefixes(self) -> None:
        """BT*, F*, S*, SW* refs are classified as 'misc'."""
        for ref in ("BT1", "F1", "S1", "SW2"):
            assert component_type(ref) == "misc", (
                f"Expected 'misc' for {ref!r}, got {component_type(ref)!r}"
            )

    def test_unknown_prefix(self) -> None:
        """Unrecognised refs return 'unknown'."""
        assert component_type("XTAL1") == "unknown"
        assert component_type("Y1") == "unknown"

    def test_case_insensitive(self) -> None:
        """component_type() is case-insensitive."""
        assert component_type("j1") == "connector"
        assert component_type("u3") == "ic"
        assert component_type("r10") == "passive"


class TestAssignTiers:
    """Tests for assign_tiers() in tier.py — longest-path layering."""

    def test_linear_chain_tiers(self) -> None:
        """J1→R1→U1→J2 linear chain must yield tiers 0, 1, 2, 3."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
            ComponentIR(ref="U1", symbol="Device:OpAmp", value="TL071"),
            ComponentIR(ref="J2", symbol="Device:Conn", value="Out"),
        ]
        nets = [
            NetIR(name="SIG_IN", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
            NetIR(name="SIG_MID", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="2")]),
            NetIR(name="SIG_OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        tiers = assign_tiers(ir)

        assert tiers["J1"] == 0, f"J1 (source connector) must be tier 0, got {tiers['J1']}"
        assert tiers["R1"] == 1, f"R1 must be tier 1, got {tiers['R1']}"
        assert tiers["U1"] == 2, f"U1 must be tier 2, got {tiers['U1']}"
        assert tiers["J2"] == 3, f"J2 (sink connector) must be tier 3, got {tiers['J2']}"

    def test_assign_tiers_breaks_cycle(self) -> None:
        """Feedback resistor must not cause an infinite loop; R_fb assigned finite tier."""
        # Circuit: J1 → R1 → U1 (signal path)
        #          U1 → R_fb → R1  (feedback from U1 output back to R1 input node)
        # The feedback creates a directed cycle: R1 → U1 → R_fb → R1.
        # assign_tiers() must break this cycle and return without hanging.
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:OpAmp", value="TL071"),
            ComponentIR(ref="R_fb", symbol="Device:R", value="100k"),
        ]
        nets = [
            NetIR(name="NET_IN", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
            NetIR(name="NET_MID", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="U1", pin="2")]),
            NetIR(
                name="NET_OUT",
                pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="R_fb", pin="2")],
            ),
            # Feedback: R_fb feeds back to the R1 side (NET_MID also has R_fb)
            NetIR(
                name="NET_FB",
                pins=[PinRefIR(ref="R_fb", pin="1"), PinRefIR(ref="R1", pin="2")],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        # Must not raise or hang.
        tiers = assign_tiers(ir)

        # Every component must have a finite, non-negative tier.
        assert all(v >= 0 for v in tiers.values()), (
            f"All tiers must be non-negative after cycle breaking; got {tiers}"
        )
        assert set(tiers.keys()) == {"J1", "R1", "U1", "R_fb"}, (
            f"All components must appear in tiers dict; got keys {set(tiers.keys())}"
        )
        # The spec requires: R_fb gets tier > U1's input tier (tier of R1/U1 side).
        # After cycle breaking, the backedge is removed and R_fb ends up downstream.
        u1_input_tier = tiers["R1"]  # R1 feeds U1 — this is U1's input tier
        assert tiers["R_fb"] > u1_input_tier, (
            f"R_fb tier ({tiers['R_fb']}) must be > U1 input tier "
            f"({u1_input_tier}) after cycle breaking"
        )

    def test_isolated_component_defaults_to_zero(self) -> None:
        """Power-only components (no signal net connections) default to tier 0."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="C_pwr", symbol="Device:C", value="100n"),
        ]
        nets = [
            # Pure power net — excluded from signal net processing.
            NetIR(name="VCC", pins=[PinRefIR(ref="C_pwr", pin="1"), PinRefIR(ref="J1", pin="2")]),
            NetIR(name="GND", pins=[PinRefIR(ref="C_pwr", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        tiers = assign_tiers(ir)

        # Power-only refs have no signal connections → default tier 0.
        assert tiers.get("C_pwr", 0) == 0, (
            f"Power-only C_pwr should be at tier 0, got {tiers.get('C_pwr')}"
        )

    def test_single_component_defaults_to_tier_zero(self) -> None:
        """A single component with only power nets returns tier 0 without crashing."""
        components = [ComponentIR(ref="J1", symbol="Device:Conn", value="In")]
        nets = [NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="2")])]
        ir = CircuitIR(version="1", components=components, nets=nets)
        tiers = assign_tiers(ir)
        assert tiers == {"J1": 0}

    def test_rank_same_subgraph_present(self) -> None:
        """DOT source for a 2-tier circuit must contain a rank=same subgraph."""
        # The linear chain J1→R1 forms two tiers; Graphviz DOT must have a
        # rank=source subgraph (tier 0) and at least one rank=same/sink block.
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
        ]
        nets = [
            NetIR(name="SNET", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        src = _gv_mod.build_dot_source(ir)

        # Must have at least one tier-grouping subgraph.
        assert "rank=source" in src or "rank=same" in src or "rank=sink" in src, (
            f"DOT source must contain at least one rank= tier subgraph.\nDOT source:\n{src}"
        )
        # Specifically the two-tier circuit must have both rank=source and rank=sink.
        assert "rank=source" in src, (
            f"Two-tier circuit must have rank=source for tier 0.\nDOT source:\n{src}"
        )
        assert "rank=sink" in src, (
            f"Two-tier circuit must have rank=sink for the last tier.\nDOT source:\n{src}"
        )


# ---------------------------------------------------------------------------
# Phase 3.2 — VCC bus / GND bus snap (#PWR / #FLG power symbols)
# ---------------------------------------------------------------------------


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
