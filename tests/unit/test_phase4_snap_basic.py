"""Phase 4: affinity groups, component types, tier assignment, fit-to-page,
snap passes (power symbols, feedback), IC unit groups, and stereo split tests.
"""

from __future__ import annotations

import re

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.component_types import component_type
from kicad_pcb.layout import (
    ComponentAnnotation,
    StereoChannel,
    compute_affinity_groups,
    detect_stereo_channels,
    find_feedback_paths,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode
from kicad_pcb.tier import (
    IcUnitGroup,
    assign_ic_units_to_tiers,
    assign_tiers,
    build_ic_unit_sibling_constraints,
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


def _feedback_ir() -> CircuitIR:
    """Minimal IR containing a feedback resistor.

    Signal chain:  J1 —[NET_IN]— U1 —[NET_OUT]— J2
    Feedback loop: R_fb connects NET_OUT back to NET_IN.

    After assign_tiers the cycle is broken and R_fb ends up at tier 3
    (one step beyond its highest-tier neighbour, U1 at tier 2), which
    leaves all of R_fb's neighbours at strictly lower tiers → feedback.
    """
    return _make_ir(
        [
            ("J1", "Connector_Generic:Conn_01x01"),
            ("U1", "Amplifier_Operational:TL071"),
            ("R_fb", "Device:R"),
            ("J2", "Connector_Generic:Conn_01x01"),
        ],
        [
            # Forward path: J1 → U1 input
            ("NET_IN", [("J1", "1"), ("U1", "3"), ("R_fb", "1")]),
            # U1 output → J2, same net used by R_fb pin2 (creates cycle)
            ("NET_OUT", [("U1", "6"), ("J2", "1"), ("R_fb", "2")]),
        ],
    )


class TestFindFeedbackPaths:
    """Unit tests for :func:`find_feedback_paths`."""

    def test_find_feedback_resistor(self) -> None:
        """R_fb connecting op-amp output net to inverting-input net is feedback."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)

        # The key assertion from the spec.
        assert "R_fb" in result, "R_fb must be in annotations"
        assert result["R_fb"].feedback is True, (
            f"R_fb should be feedback=True; tiers={tiers}, R_fb tier={tiers.get('R_fb')}"
        )

    def test_series_resistor_not_feedback(self) -> None:
        """A plain series resistor between two ICs is NOT feedback."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn"), ("R1", "Device:R"), ("U1", "Amplifier:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["R1"].feedback is False, "Series resistor must NOT be feedback"

    def test_connector_never_feedback(self) -> None:
        """Connectors are excluded from feedback detection (non-passive)."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["J1"].feedback is False
        assert result["J2"].feedback is False

    def test_ic_never_feedback(self) -> None:
        """ICs are excluded from feedback detection (non-passive)."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        assert result["U1"].feedback is False

    def test_all_components_returned(self) -> None:
        """Every component in the IR has an annotation entry."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        result = find_feedback_paths(ir, tiers)
        ir_refs = {c.ref for c in ir.components}
        assert set(result.keys()) == ir_refs

    def test_empty_tiers_does_not_crash(self) -> None:
        """find_feedback_paths accepts an empty tiers dict without error.

        The topological detection algorithm does not require tiers, so
        an empty dict is a valid (if sparse) input.  R_fb is still
        detected because the shared-component criterion is tier-independent.
        """
        ir = _feedback_ir()
        result = find_feedback_paths(ir, {})
        # No TypeError / crash; R_fb is still feedback (topology-driven).
        assert "R_fb" in result
        assert result["R_fb"].feedback is True

    def test_component_annotation_dataclass(self) -> None:
        """ComponentAnnotation defaults to feedback=False."""
        a = ComponentAnnotation()
        assert a.feedback is False
        b = ComponentAnnotation(feedback=True)
        assert b.feedback is True


class TestSnapFeedbackComponents:
    """Unit tests for :func:`graphviz_layout.snap_feedback_components`."""

    def test_feedback_component_placed_above_amp(self) -> None:
        """Feedback component snaps to anchor_y - GRID_ROW_MM."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (60.0, 100.0, None),  # below U1 — should be snapped above
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)

        # R_fb should now be above whichever anchor was found (U1 or J1/J2).
        rfb_y = result["R_fb"][1]
        # Any signal-net neighbour is a valid anchor; the snap places R_fb
        # at anchor_y - GRID_ROW_MM.
        possible_anchors = {"J1": 80.0, "U1": 80.0, "J2": 80.0}
        expected_ys = {y - _gv_mod.GRID_ROW_MM for y in possible_anchors.values()}
        assert rfb_y in expected_ys, (
            f"R_fb y={rfb_y} not a valid snap target; expected one of {expected_ys}"
        )

    def test_non_feedback_component_unchanged(self) -> None:
        """Components with feedback=False are not moved."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (60.0, 100.0, None),
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        for ref in ("J1", "U1", "J2"):
            assert result[ref] == positions[ref], f"{ref} must not move"

    def test_x_coordinate_preserved_for_feedback(self) -> None:
        """snap_feedback_components only changes y; x is preserved."""
        ir = _feedback_ir()
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "U1": (90.0, 80.0, None),
            "R_fb": (62.0, 100.0, None),
            "J2": (120.0, 80.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        assert result["R_fb"][0] == pytest.approx(62.0), "x should not change"

    def test_no_feedback_components_returns_unchanged(self) -> None:
        """When no component is feedback, positions dict is returned as-is."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn"), ("R1", "Device:R"), ("U1", "Amp:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        tiers = assign_tiers(ir)
        annotations = find_feedback_paths(ir, tiers)
        # Sanity: no feedback in a pure series chain.
        assert all(not a.feedback for a in annotations.values())

        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (0.0, 50.0, None),
            "R1": (30.0, 50.0, None),
            "U1": (60.0, 50.0, None),
        }
        result = _gv_mod.snap_feedback_components(positions, annotations, ir)
        assert result == positions


# ---------------------------------------------------------------------------
# Phase 6 — Multi-unit IC handling
# ---------------------------------------------------------------------------


def _multi_unit_ir() -> CircuitIR:
    """Minimal dual-op-amp circuit with a multi-unit IC.

    Signal path: J1 --[NET_IN]--> U1A --[NET_OUT]--> J2.
    Power unit: U1B connected only to VCC and GND.
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1B", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1B", "pin": "4"}]},
            ],
        }
    )


def _multi_stage_unit_ir() -> CircuitIR:
    """Three-unit op-amp example with two signal stages and one power unit."""
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn_01x01", "value": ""},
                {"ref": "U1A", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1B", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "U1P", "symbol": "Amplifier:NE5532", "value": "NE5532"},
                {"ref": "J2", "symbol": "Connector:Conn_01x01", "value": ""},
            ],
            "nets": [
                {"name": "NET_IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "U1A", "pin": "3"}]},
                {
                    "name": "NET_STAGE",
                    "pins": [{"ref": "U1A", "pin": "1"}, {"ref": "U1B", "pin": "5"}],
                },
                {
                    "name": "NET_OUT",
                    "pins": [{"ref": "U1B", "pin": "7"}, {"ref": "J2", "pin": "1"}],
                },
                {"name": "VCC", "pins": [{"ref": "U1P", "pin": "8"}]},
                {"name": "GND", "pins": [{"ref": "U1P", "pin": "4"}]},
            ],
        }
    )


class TestIcUnitGroups:
    """Phase 6 — assign_ic_units_to_tiers and per-unit DOT placement."""

    def test_multi_unit_ref_detected(self) -> None:
        """U1A and U1B are grouped under base ref U1."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert "U1" in groups
        assert groups["U1"].units == ["U1A", "U1B"]
        assert groups["U1"].base_ref == "U1"

    def test_power_unit_detected(self) -> None:
        """U1B (only VCC/GND nets) is identified as the power unit."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        assert groups["U1"].power_unit == "U1B"

    def test_single_unit_ic_excluded(self) -> None:
        """A plain 'U1' ref (no letter suffix) produces no group entry."""
        ir = _make_ir(
            [("J1", "Connector"), ("U1", "Amp:TL071"), ("J2", "Connector")],
            [("NET_IN", [("J1", "1"), ("U1", "3")]), ("NET_OUT", [("U1", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_passive_ref_with_letter_suffix_excluded(self) -> None:
        """R1A is a passive prefix; it must not be treated as a multi-unit IC."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1A", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1A", "1"), ("J2", "1")])],
        )
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))
        assert groups == {}

    def test_ic_unit_group_dataclass_defaults(self) -> None:
        """IcUnitGroup default values are correct."""
        g = IcUnitGroup(base_ref="U2")
        assert g.units == []
        assert g.power_unit is None

    def test_multi_unit_ic_power_unit_in_power_cluster(self) -> None:  # spec test
        """DOT source places U1B (power unit) inside cluster_power subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # cluster_power must exist and contain U1B.
        assert "cluster_power" in dot
        cluster_start = dot.index("cluster_power")
        cluster_end = dot.index("}", cluster_start)
        cluster_body = dot[cluster_start:cluster_end]
        assert "U1B" in cluster_body

    def test_multi_unit_ic_signal_units_in_signal_tiers(self) -> None:  # spec test
        """DOT source places U1A (signal unit) in a rank=same tier subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        # U1A must appear in a rank=... subgraph (rank=source, rank=same, or rank=sink).
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert any("U1A" in block for block in rank_blocks), (
            f"U1A not found in any rank subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_not_in_signal_tiers(self) -> None:
        """U1B must not appear in any rank=same/source/sink subgraph."""
        ir = _multi_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        dot = _gv_mod._build_dot_source(ir, power_unit_refs=power_unit_refs)
        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1B" in block for block in rank_blocks), (
            f"U1B must not be in a tier subgraph.\nDOT:\n{dot}"
        )

    def test_power_unit_excluded_from_tiers_even_with_affinity_order(self) -> None:
        """Affinity ordering must not reinsert power units into rank subgraphs."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            tiers=tiers,
            affinity_order={0: ["J1", "U1P"], 1: ["U1A", "U1B"]},
        )

        rank_blocks = re.findall(r"\{[^{}]*rank=(?:same|source|sink)[^{}]*\}", dot, re.DOTALL)
        assert not any("U1P" in block for block in rank_blocks), (
            f"U1P leaked back into a tier subgraph via affinity ordering.\nDOT:\n{dot}"
        )

    def test_signal_sibling_constraints_skip_power_units(self) -> None:
        """Only signal units participate in sibling-order constraints."""
        ir = _multi_stage_unit_ir()
        groups = assign_ic_units_to_tiers(ir, assign_tiers(ir))

        assert groups["U1"].signal_units == ["U1A", "U1B"]
        assert build_ic_unit_sibling_constraints(groups) == [("U1A", "U1B")]

    def test_signal_sibling_constraint_emitted_in_dot(self) -> None:
        """DOT source adds an invisible U1A->U1B constraint but excludes U1P."""
        ir = _multi_stage_unit_ir()
        tiers = assign_tiers(ir)
        groups = assign_ic_units_to_tiers(ir, tiers)
        power_unit_refs = {g.power_unit for g in groups.values() if g.power_unit is not None}
        sibling_pairs = build_ic_unit_sibling_constraints(groups)

        dot = _gv_mod._build_dot_source(
            ir,
            power_unit_refs=power_unit_refs,
            unit_sibling_pairs=sibling_pairs,
            tiers=tiers,
        )

        assert "U1A -> U1B [style=invis, weight=4, constraint=false];" in dot
        assert "U1P -> U1A" not in dot
        assert "U1A -> U1P" not in dot
        assert "U1P -> U1B" not in dot
        assert "U1B -> U1P" not in dot


# ---------------------------------------------------------------------------
# Phase 7 — Stereo symmetry
# ---------------------------------------------------------------------------


def _stereo_ir() -> CircuitIR:
    """Minimal stereo headphone amp circuit with distinct L/R channel nets.

    Signal paths:
      J1 --[IN_L]--> R1 --[MID_L]--> U1 --[OUT_L]--> J2   (left channel)
      J3 --[IN_R]--> R2 --[MID_R]--> U2 --[OUT_R]--> J4   (right channel)
      J5 shared (power connector, no stereo suffix)        (mono)
    """
    return CircuitIR.model_validate(
        {
            "version": "1",
            "components": [
                {"ref": "J1", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R1", "symbol": "Device:R", "value": "10k"},
                {"ref": "U1", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J2", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J3", "symbol": "Connector:Conn", "value": ""},
                {"ref": "R2", "symbol": "Device:R", "value": "10k"},
                {"ref": "U2", "symbol": "Amplifier:TL071", "value": "TL071"},
                {"ref": "J4", "symbol": "Connector:Conn", "value": ""},
                {"ref": "J5", "symbol": "Connector:Conn", "value": ""},
            ],
            "nets": [
                {"name": "IN_L", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
                {"name": "MID_L", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "U1", "pin": "3"}]},
                {"name": "OUT_L", "pins": [{"ref": "U1", "pin": "1"}, {"ref": "J2", "pin": "1"}]},
                {"name": "IN_R", "pins": [{"ref": "J3", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
                {"name": "MID_R", "pins": [{"ref": "R2", "pin": "2"}, {"ref": "U2", "pin": "3"}]},
                {"name": "OUT_R", "pins": [{"ref": "U2", "pin": "1"}, {"ref": "J4", "pin": "1"}]},
                # J5 on a non-stereo net (mono)
                {
                    "name": "POWER",
                    "pins": [{"ref": "J5", "pin": "1"}, {"ref": "U1", "pin": "8"}],
                },
            ],
        }
    )


class TestDetectStereoChannels:
    """Phase 7 — detect_stereo_channels."""

    def test_detect_stereo_channels_from_net_suffix(self) -> None:  # spec test
        """Components on _L nets → L; on _R nets → R; mixed/none → mono."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"
        assert channels["U1"] == "L"  # only L nets in signal chain
        assert channels["J2"] == "L"
        assert channels["J3"] == "R"
        assert channels["R2"] == "R"
        assert channels["U2"] == "R"
        assert channels["J4"] == "R"

    def test_mono_component_on_mixed_nets(self) -> None:
        """A component on both _L and _R signal nets is classified as mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [
                ("NET_L", [("J1", "1"), ("R1", "1")]),
                ("NET_R", [("R1", "2"), ("J2", "1")]),
            ],
        )
        channels = detect_stereo_channels(ir)
        assert channels["R1"] == "mono"  # has both L and R nets

    def test_dash_suffix_recognised(self) -> None:
        """Nets ending in -L / -R (dash separator) are detected correctly."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R")],
            [("OUT-L", [("J1", "1"), ("R1", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert channels["J1"] == "L"
        assert channels["R1"] == "L"

    def test_all_components_present_in_result(self) -> None:
        """Every component in ir is represented in the returned dict."""
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        for comp in ir.components:
            assert comp.ref in channels

    def test_no_stereo_nets_all_mono(self) -> None:
        """With no stereo-suffix nets, every component is mono."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R"), ("J2", "Connector")],
            [("NET", [("J1", "1"), ("R1", "1"), ("J2", "1")])],
        )
        channels = detect_stereo_channels(ir)
        assert all(v == "mono" for v in channels.values())

    def test_stereo_channel_type_alias(self) -> None:
        """StereoChannel is exported from layout and is a Literal type alias."""
        # Runtime check: the values returned are valid StereoChannel literals.
        ir = _stereo_ir()
        channels = detect_stereo_channels(ir)
        valid: set[StereoChannel] = {"L", "R", "mono"}
        assert all(v in valid for v in channels.values())


class TestApplyStereoSplit:
    """Phase 7 — _apply_stereo_split post-layout y remapping."""

    # Shared page constants matching graphviz_layout defaults.
    _ORIGIN_Y: float = _gv_mod.ORIGIN_Y
    _PAGE_MAX_Y: float = _gv_mod.PAGE_MAX_Y
    _PAGE_H: float = _gv_mod.PAGE_MAX_Y - _gv_mod.ORIGIN_Y

    def _mid(self) -> float:
        return self._ORIGIN_Y + self._PAGE_H * 0.5

    def test_left_channel_above_midline(self) -> None:  # spec test
        """L-channel components land in the top half (y < midline)."""
        channels: dict[str, str] = {"J1": "L", "R1": "L"}
        # Give them y values spread across the usable page.
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, self._ORIGIN_Y + 10.0, None),
            "R1": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["J1"][1] < midline, f"J1 y={result['J1'][1]} not above midline {midline}"
        assert result["R1"][1] < midline, f"R1 y={result['R1'][1]} not above midline {midline}"

    def test_right_channel_below_midline(self) -> None:  # spec test
        """R-channel components land in the bottom half (y ≥ midline)."""
        channels: dict[str, str] = {"R2": "R", "U2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R2": (30.0, self._ORIGIN_Y + 10.0, None),
            "U2": (60.0, self._ORIGIN_Y + 60.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        midline = self._mid()
        assert result["R2"][1] >= midline, f"R2 y={result['R2'][1]} not below midline {midline}"
        assert result["U2"][1] >= midline, f"U2 y={result['U2'][1]} not below midline {midline}"

    def test_mono_component_y_unchanged(self) -> None:
        """Mono components keep their original y position."""
        channels: dict[str, str] = {"J5": "mono", "R_shared": "L"}
        original_y = self._ORIGIN_Y + 30.0
        positions: dict[str, tuple[float, float, float | None]] = {
            "J5": (10.0, original_y, None),
            "R_shared": (20.0, self._ORIGIN_Y + 20.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["J5"][1] == pytest.approx(original_y)

    def test_no_stereo_channels_returns_unchanged(self) -> None:
        """When no L/R channels exist, positions are returned as-is."""
        channels: dict[str, str] = {"J1": "mono", "R1": "mono"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.0, 80.0, None),
            "R1": (60.0, 100.0, None),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result is positions  # fast-path: same object returned

    def test_x_coordinate_preserved(self) -> None:
        """apply_stereo_split only changes y; x and rotation are preserved."""
        channels: dict[str, str] = {"R1": "L", "R2": "R"}
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (42.5, self._ORIGIN_Y + 40.0, 0.0),
            "R2": (80.0, self._ORIGIN_Y + 40.0, 90.0),
        }
        result = _gv_mod.apply_stereo_split(
            positions, channels, origin_y=self._ORIGIN_Y, page_max_y=self._PAGE_MAX_Y
        )
        assert result["R1"][0] == pytest.approx(42.5)
        assert result["R1"][2] == pytest.approx(0.0)
        assert result["R2"][0] == pytest.approx(80.0)
        assert result["R2"][2] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# Phase 5 — _apply_post_layout_snaps coordinator
# ---------------------------------------------------------------------------
