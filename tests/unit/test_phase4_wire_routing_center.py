"""Phase 4: wire routing, IC column centering, crossing remediation, clamping, X-spread."""

from __future__ import annotations

import math

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.graphviz_layout.snap import (
    _center_ics_in_columns,
)
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
