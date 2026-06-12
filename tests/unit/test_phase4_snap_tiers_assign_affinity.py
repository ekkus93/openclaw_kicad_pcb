"""Phase 4 snap: affinity groups, tier assignment, fit-to-page, and power symbol snapping."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    compute_affinity_groups,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

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
