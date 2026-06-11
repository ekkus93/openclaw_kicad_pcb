"""Phase 4 snap: affinity groups, tier assignment, fit-to-page, and power symbol snapping."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.component_types import component_type
from kicad_pcb.layout import (
    compute_affinity_groups,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.tier import (
    assign_tiers,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_WARN = LintSeverity.WARNING
_ERR = LintSeverity.ERROR


def _minimal_ir(
    *,
    refs: list[str] | None = None,
    nets: list[dict] | None = None,
) -> CircuitIR:
    """Build a minimal CircuitIR for testing.

    *refs* defaults to ["R1", "R2"].
    *nets* is a list of dicts with keys ``name`` and ``pins`` (list of
    ``{"ref": ..., "pin": ...}`` dicts).
    """
    if refs is None:
        refs = ["R1", "R2"]
    if nets is None:
        nets = [
            {"name": "NET1", "pins": [{"ref": refs[0], "pin": "1"}, {"ref": refs[1], "pin": "1"}]}
        ]

    components = [ComponentIR(ref=r, symbol="Device:R", value="1k") for r in refs]
    ir_nets = [NetIR(name=n["name"], pins=[PinRefIR(**p) for p in n["pins"]]) for n in nets]
    return CircuitIR(version="1", components=components, nets=ir_nets)


def _sch(body: str = "") -> ListNode:
    """Parse a minimal kicad_sch document with optional *body*."""
    return parse(
        "(kicad_sch (version 20230121) (generator test)\n"
        "  (lib_symbols)\n"
        f"  {body}\n"
        '  (sheet_instances (path "/" (page "1")))\n'
        ")"
    )


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return an S-expression wire snippet."""
    return f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})))"


def _symbol_at(x: float, y: float) -> str:
    """Return a minimal symbol snippet at (x, y)."""
    return (
        f'(symbol (lib_id "Device:R") (at {x} {y} 0) (uuid "00000000-0000-0000-0000-000000000001"))'
    )


def _label(name: str, x: float = 10.0, y: float = 10.0) -> str:
    return f'(label "{name}" (at {x} {y} 0))'


def _codes(issues: list) -> list[str]:
    return [i.code for i in issues]


def _sev(issues: list, code: str) -> LintSeverity | None:
    for i in issues:
        if i.code == code:
            return i.severity
    return None


def _make_ir(
    components: list[tuple[str, str]],
    nets: list[tuple[str, list[tuple[str, str]]]],
    *,
    version: str = "1",
) -> CircuitIR:
    """Minimal CircuitIR factory.

    *components* is ``[(ref, symbol), ...]``.
    *nets* is ``[(net_name, [(ref, pin), ...]), ...]``.
    """
    ir_components = [ComponentIR(ref=ref, symbol=sym) for ref, sym in components]
    ir_nets = [
        NetIR(name=name, pins=[PinRefIR(ref=r, pin=p) for r, p in pins]) for name, pins in nets
    ]
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestComputeAffinityGroups:
    """Tests for layout.compute_affinity_groups()."""

    def test_affinity_groups_returns_sorted_refs(self) -> None:
        """Tier order from BFS: J1(0), R1(1), R2(1), U1(2). R1 has higher affinity
        to tier0 (J1) than R2 does → R1 appears before R2 in tier 1."""
        ir = _affinity_ir()
        # BFS tiers: J1=0 (seed), R1=1 (via IN), R2=2 (via STAGE from R1), U1=3 (via STAGE)
        # But since we are testing compute_affinity_groups independently, we supply tiers.
        tiers = {"J1": 0, "R1": 1, "R2": 1, "U1": 2}
        groups = compute_affinity_groups(ir, tiers)
        # Tier 0: just J1.
        assert groups[0] == ["J1"]
        # Tier 1: R1 shares IN with J1 → affinity(R1, J1) > 0.
        #         R2 shares no net with J1 → affinity(R2, J1) = 0.
        #         So R1 should be first.
        assert groups[1][0] == "R1", (
            f"Expected R1 first in tier 1 (higher affinity to J1), got: {groups[1]}"
        )
        assert groups[1][1] == "R2"
        # Tier 2: just U1.
        assert groups[2] == ["U1"]

    def test_first_tier_alphabetical(self) -> None:
        """When two connectors are at tier 0, they are sorted alphabetically."""
        components = [
            ComponentIR(ref="J2", symbol="Device:Conn", value="A"),
            ComponentIR(ref="J1", symbol="Device:Conn", value="B"),
        ]
        nets = [NetIR(name="NET1", pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="J2", pin="1")])]
        ir = CircuitIR(version="1", components=components, nets=nets)
        tiers = {"J1": 0, "J2": 0}
        groups = compute_affinity_groups(ir, tiers)
        assert groups[0] == ["J1", "J2"], (
            f"Tier 0 should be alphabetical: ['J1', 'J2'], got {groups[0]}"
        )

    def test_isolated_component_gets_stable_position(self) -> None:
        """An isolated component (no signal net connections) gets tier 0 alphabetically."""
        components = [
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
        ]
        nets = [
            NetIR(name="VCC", pins=[PinRefIR(ref="C1", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="J1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        tiers = {"J1": 0, "C1": 0}
        groups = compute_affinity_groups(ir, tiers)
        assert "C1" in groups[0]
        assert "J1" in groups[0]
        # Both in tier 0 → alphabetical → C1 before J1.
        assert groups[0] == ["C1", "J1"]


class TestNetWeights:
    """Tests for _compute_net_weights() in graphviz_layout."""

    def test_single_shared_net_gets_weight_one(self) -> None:
        """A net whose endpoints share only 1 net (this one) → weight 1."""
        nets = [NetIR(name="NET1", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")])]
        weights = _gv_mod.compute_net_weights(nets)
        assert weights["NET1"] == 1, f"Expected weight 1, got {weights['NET1']}"

    def test_two_shared_nets_get_weight_five(self) -> None:
        """When R1 and R2 share 2 nets, both get weight 5 (tightly coupled)."""
        nets = [
            NetIR(name="NET1", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")]),
            NetIR(name="NET2", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="R2", pin="2")]),
        ]
        weights = _gv_mod.compute_net_weights(nets)
        assert weights["NET1"] == 5, f"NET1 expected weight 5, got {weights['NET1']}"
        assert weights["NET2"] == 5, f"NET2 expected weight 5, got {weights['NET2']}"

    def test_weight_five_in_dot_source(self) -> None:
        """DOT source must contain [weight=5] for nets whose endpoints share 2+ nets."""
        ir = _affinity_ir()
        src = _gv_mod.build_dot_source(ir)
        # STAGE and BIAS nets are shared by R2+U1 (2 nets) → should have weight=5.
        assert "[weight=5]" in src, (
            f"Expected [weight=5] in DOT source for high-affinity net pair, "
            f"but it was not found.\nSource:\n{src}"
        )

    def test_ordering_out_present_in_dot_source(self) -> None:
        """DOT graph must include ordering=out to guide vertical sequence."""
        ir = _affinity_ir()
        src = _gv_mod.build_dot_source(ir)
        assert "ordering=out" in src, (
            "ordering=out directive missing from DOT source — "
            "needed for consistent vertical ordering within tiers"
        )


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
