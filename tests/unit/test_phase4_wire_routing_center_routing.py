"""Phase 4: wire routing, IC column centering, crossing remediation, clamping, X-spread."""

from __future__ import annotations

import math

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint import LintSeverity
from kicad_pcb.router import (
    MAX_DIRECT_WIRE_MM,
    SYMBOL_HALF_SIZE_MM,
    WireSegment,
    detect_body_crossings,
    route_nets,
)
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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestPhase8WireRouting:
    """Phase 8 — tier-distance routing threshold, 30-mm label trigger, body-crossing guard."""

    def test_cross_tier_net_gets_label_not_long_wire(self) -> None:  # spec test
        """Components at non-adjacent tiers (tier_distance > 1) must use label route."""
        ir = _make_ir(
            [("J1", "Connector"), ("U1", "Amplifier:TL071")],
            [("SKIP", [("J1", "1"), ("U1", "3")])],
        )
        tiers = {"J1": 0, "U1": 2}  # tier_distance = 2
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (200.0, 100.0, 180.0),
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, tiers=tiers)
        assert len(routing.labels) > 0, (
            "Expected per-pin net labels for non-adjacent tier net; got none"
        )
        # No wire should span the full inter-component gap.
        long_wires = [s for s in routing.wires if abs(s.x2 - s.x1) > 50 or abs(s.y2 - s.y1) > 50]
        assert not long_wires, f"Unexpectedly long wires found: {long_wires}"

    def test_route_nets_respects_tier_distance(self) -> None:  # spec test
        """Adjacent tier (distance=1) and short wire → direct; distance>1 → labels."""
        ir_adj = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R")],
            [("NET_ADJ", [("J1", "1"), ("R1", "1")])],
        )
        tiers_adj = {"J1": 0, "R1": 1}  # distance = 1
        endpoints_adj: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (48.0, 100.0, 180.0),  # ≤20 mm — within MAX_DIRECT_WIRE_MM
        }
        routing_adj = route_nets(ir=ir_adj, pin_endpoints=endpoints_adj, tiers=tiers_adj)
        assert routing_adj.labels == [], (
            f"Adjacent tier / short wire should use direct route; labels={routing_adj.labels}"
        )

        ir_far = _make_ir(
            [("J1", "Connector"), ("U1", "Amplifier:TL071")],
            [("NET_FAR", [("J1", "1"), ("U1", "3")])],
        )
        tiers_far = {"J1": 0, "U1": 2}  # distance = 2
        endpoints_far: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("U1", "3"): (90.0, 100.0, 180.0),
        }
        routing_far = route_nets(ir=ir_far, pin_endpoints=endpoints_far, tiers=tiers_far)
        assert len(routing_far.labels) == 2, (
            f"Non-adjacent tier net must have 2 per-pin labels; got {routing_far.labels}"
        )

    def test_connector_to_passive_edge_net_can_still_route_directly(self) -> None:
        """Connector-to-passive 2-pin edge links stay directly wired despite tier drift."""
        ir = _make_ir(
            [("J1", "Connector"), ("R1", "Device:R")],
            [("EDGE", [("J1", "1"), ("R1", "1")])],
        )
        tiers = {"J1": 0, "R1": 4}
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (35.56, 119.38, 0.0),
            ("R1", "1"): (116.84, 119.38, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, tiers=tiers)

        assert routing.labels == [], (
            "Short connector-to-passive edge nets should remain directly wired "
            "even when tier inference places the passive deeper in the path"
        )

    def test_long_wire_adjacent_tier_gets_label(self) -> None:
        """Adjacent tier (distance=1) but wire > MAX_DIRECT_DIST_MM → label route."""
        ir = _make_ir(
            [("R1", "Device:R"), ("R2", "Device:R")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        tiers = {"R1": 0, "R2": 1}  # distance = 1 (adjacent)
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("R2", "1"): (250.0, 100.0, 180.0),  # ≈220 mm — exceeds MAX_DIRECT_DIST_MM (200 mm)
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, tiers=tiers)
        assert len(routing.labels) == 2, (
            f"Long adjacent-tier wire should fall back to labels; got {routing.labels}"
        )

    def test_route_nets_no_tiers_falls_back_to_manhattan(self) -> None:
        """Without tiers, the legacy Manhattan-distance cap behaviour is preserved."""
        ir = _make_ir(
            [("R1", "Device:R"), ("R2", "Device:R")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("R2", "1"): (55.0, 100.0, 180.0),  # ≈19 mm — within Manhattan 120 mm cap
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)  # tiers=None
        assert routing.labels == [], (
            "Without tiers, close pins should be directly routed with no labels"
        )

    def test_no_body_crossings_after_routing(self) -> None:  # spec test
        """detect_body_crossings routes around component bounding boxes."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_mid": (100.0, 100.0, None),
        }
        # Horizontal wire from x=50 to x=150 at y=100 passes straight through R_mid.
        crossing_wire = WireSegment(50.0, 100.0, 150.0, 100.0)
        result = detect_body_crossings([crossing_wire], positions)

        # The original crossing wire must have been replaced.
        assert crossing_wire not in result, "Crossing wire should be replaced by a detour"
        # Output must have more than 1 segment (the detour adds extra segments).
        assert len(result) > 1, f"Expected multiple detour segments; got {result}"
        # No output segment should span from before the box to after the box at y≈100.
        half = SYMBOL_HALF_SIZE_MM
        bx, by = 100.0, 100.0
        for seg in result:
            if not math.isclose(seg.y1, seg.y2, abs_tol=0.5):
                continue  # skip non-horizontal segments
            if not (by - half - 1.0 <= seg.y1 <= by + half + 1.0):
                continue  # not in the y-band of the obstacle
            seg_lx = min(seg.x1, seg.x2)
            seg_rx = max(seg.x1, seg.x2)
            assert not (seg_lx < bx - half and seg_rx > bx + half), (
                f"Segment {seg} still spans R_mid bounding box"
            )

    def test_detect_body_crossings_noop_when_clear(self) -> None:
        """Wires that miss all component boxes pass through detect_body_crossings unchanged."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_far": (200.0, 200.0, None),
        }
        seg = WireSegment(10.0, 10.0, 30.0, 10.0)
        result = detect_body_crossings([seg], positions)
        assert result == [seg], "Non-crossing wire must be returned unchanged"

    def test_max_direct_wire_mm_constant_is_70(self) -> None:
        """MAX_DIRECT_WIRE_MM must be 70.0 mm to clear the wider layout scale.

        With ranksep=2.5 × SCALE_MM_PER_GV=24.0 = 60 mm between adjacent
        tiers, the threshold must exceed 60 mm so adjacent-tier components
        are still wired directly rather than routed via labels.
        """
        assert MAX_DIRECT_WIRE_MM == 70.0  # exact constant — no approx needed

    def test_symbol_half_size_mm_constant_is_5_08(self) -> None:
        """SYMBOL_HALF_SIZE_MM must be 5.08 mm (200 mil = one KiCad grid unit)."""
        assert SYMBOL_HALF_SIZE_MM == 5.08  # exact constant — no approx needed
