"""Phase 4: wire routing, IC column centering, crossing remediation,
page clamping, X-spread, label policy, label modes, and power symbol tests.
"""

from __future__ import annotations

import itertools
import math
from pathlib import Path

import pytest

from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._project import minimal_schematic_text
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.graphviz_layout.snap import (
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _apply_property_text_spacing,
    _center_ics_in_columns,
    _clamp_to_page,
    _remediate_crossings,
    _spread_x_columns,
)
from kicad_pcb.layout import (
    build_signal_adjacency,
    count_wire_crossings,
)
from kicad_pcb.lint import LintSeverity
from kicad_pcb.router import (
    LABEL_MODE_POLICIES,
    MAX_DIRECT_WIRE_MM,
    SYMBOL_HALF_SIZE_MM,
    BindMarker,
    GlobalLabelPlacement,
    LabelPolicy,
    NetRouting,
    PowerSymbolPlacement,
    WireSegment,
    detect_body_crossings,
    route_nets,
    write_routing,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first

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


# ---------------------------------------------------------------------------
# TestRemediateCrossings
# ---------------------------------------------------------------------------

# Grid constants matching layout.py (used to place components on the snap grid).
_COL0_X: float = 30.48  # ORIGIN_X = first column x
_COL1_X: float = 60.96  # ORIGIN_X + GRID_COL_MM = second column x
_COL2_X: float = 91.44  # ORIGIN_X + 2*GRID_COL_MM = third column x
_SLOT0_Y: float = 10.0  # top y-slot used in tests
_SLOT1_Y: float = 30.48  # bottom y-slot (≈ GRID_ROW_MM + SLOT0_Y for visual clarity)


def _crossing_ir() -> CircuitIR:
    """Build a 4-component IR whose wires form a detectable X crossing.

    The ``count_wire_crossings`` heuristic excludes wire pairs whose *left*
    endpoints share the same x-coordinate.  To produce a detectable crossing
    the two signal edges must start from **different columns**::

        col0 (x=30.48)      col1 (x=60.96)      col2 (x=91.44)
          R1 (y=10)  ─────────────────────────── R2 (y=30)   NET_A
                            R3 (y=30) ──────────── R4 (y=10)  NET_B

    Edge R1→R2 starts at x=30.48 (col0) and ends at col2.
    Edge R3→R4 starts at x=60.96 (col1) and ends at col2.

    Since left-endpoint x differs (30.48 < 60.96) and the heuristic
    checks ``yr(R2)=30 > ycr(R4)=10`` → 1 crossing detected.

    After one barycentric + y-slot sweep col2 is reordered so both wires
    become horizontal (R2 and R4 swap y-values) → 0 crossings.
    """
    components = [
        ComponentIR(ref="R1", symbol="Device:R", value="1k"),
        ComponentIR(ref="R2", symbol="Device:R", value="1k"),
        ComponentIR(ref="R3", symbol="Device:R", value="1k"),
        ComponentIR(ref="R4", symbol="Device:R", value="1k"),
    ]
    nets = [
        NetIR(
            name="NET_A",
            pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
        ),
        NetIR(
            name="NET_B",
            pins=[PinRefIR(ref="R3", pin="1"), PinRefIR(ref="R4", pin="1")],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


def _crossing_positions() -> dict[str, tuple[float, float, float | None]]:
    """Return positions that produce a detectable X-crossing for *_crossing_ir()*.

    R1 at col0 → R2 at col2 (NET_A, going down-right).
    R3 at col1 → R4 at col2 (NET_B, going up-right).

    The right endpoints (R2 at y=30.48, R4 at y=10) are inverted relative to
    the left endpoints (R1 at y=10, R3 at y=30.48), satisfying the
    ``yr > ycr`` condition for the crossing heuristic.

    ``R2`` is inserted before ``R4`` so ``by_col[2] = [R2, R4]``.
    After the y-slot assignment (sorted ascending), R2 is assigned the
    smaller y-slot (10) and R4 the larger (30.48), eliminating the crossing.
    """
    return {
        "R1": (_COL0_X, _SLOT0_Y, None),  # col0, y=10 (low)
        "R3": (_COL1_X, _SLOT1_Y, None),  # col1, y=30.48 (high)
        "R2": (_COL2_X, _SLOT1_Y, None),  # col2 slot 1, inserted first → by_col[2][0]
        "R4": (_COL2_X, _SLOT0_Y, None),  # col2 slot 0, inserted second → by_col[2][1]
    }
    # Edge R1→R2: (30.48,10) → (91.44,30.48)  going down-right
    # Edge R3→R4: (60.96,30.48) → (91.44,10)  going up-right
    # xl=30.48 < xcl=60.96; yr=30.48 > ycr=10 → 1 crossing detected.


class TestRemediateCrossings:
    """Unit tests for the _remediate_crossings snap pass."""

    # ------------------------------------------------------------------
    # Happy-path: crossing is actually reduced
    # ------------------------------------------------------------------

    def test_crossing_eliminated_after_one_sweep(self) -> None:
        """A classical X-crossing between two columns must be eliminated."""
        ir = _crossing_ir()
        positions = _crossing_positions()

        sig_adj = build_signal_adjacency(ir)
        pos2_before = {r: (x, y) for r, (x, y, _) in positions.items()}
        assert count_wire_crossings(pos2_before, sig_adj) == 1, (
            "pre-condition: must have 1 crossing"
        )

        result = _remediate_crossings(positions, ir)

        pos2_after = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2_after, sig_adj) == 0, (
            f"Crossing must be eliminated.  Final positions: {result}"
        )

    def test_y_slots_are_preserved_not_created(self) -> None:
        """After remediation, y-values must all come from the original positions set."""
        positions = _crossing_positions()
        original_y_values = {y for _x, y, _rot in positions.values()}

        result = _remediate_crossings(_crossing_positions(), _crossing_ir())

        for ref, (_, y, _) in result.items():
            assert y in original_y_values, (
                f"{ref} has unexpected y={y}; allowed values={original_y_values}"
            )

    def test_x_and_rotation_are_preserved(self) -> None:
        """x-coordinates and rotations must be unchanged by remediation."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir())

        for ref, (x, _y, rot) in result.items():
            orig_x, _orig_y, orig_rot = positions[ref]
            assert x == pytest.approx(orig_x), f"{ref}: x changed {orig_x} → {x}"
            assert rot == orig_rot, f"{ref}: rotation changed {orig_rot} → {rot}"

    def test_all_refs_present_in_result(self) -> None:
        """Every ref in the input must appear in the output."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir())
        assert set(result.keys()) == set(positions.keys())

    # ------------------------------------------------------------------
    # Break condition: ratio already below threshold
    # ------------------------------------------------------------------

    def test_already_optimal_layout_unchanged(self) -> None:
        """If no crossing exists, positions must be returned unchanged."""
        ir = _crossing_ir()
        # Arrange: wires are horizontal → 0 crossings.
        # R1 (col0) → R2 (col2) at same y; R3 (col1) → R4 (col2) at same y.
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R3": (_COL1_X, _SLOT1_Y, None),
            "R2": (_COL2_X, _SLOT0_Y, None),  # same y as R1 → no crossing
            "R4": (_COL2_X, _SLOT1_Y, None),  # same y as R3 → no crossing
        }
        result = _remediate_crossings(positions, ir)
        assert result == positions, f"Optimal layout must be unchanged; got {result}"

    def test_below_threshold_returns_immediately(self) -> None:
        """A crossing ratio below threshold must not trigger any sweep."""
        ir = _crossing_ir()
        # Use a threshold of 1.0 so ANY ratio is below threshold → immediate return.
        positions = _crossing_positions()
        result = _remediate_crossings(positions, ir, crossing_ratio_threshold=1.0)

        # Immediate return means result == input (no deoverlap run either,
        # since the function returns before entering the sweep block).
        # The only transformation allowed is the identity.
        # Positions are returned as-is because ratio < threshold at sweep 0 break.
        pos2_before = {r: (x, y) for r, (x, y, _) in positions.items()}
        pos2_after = {r: (x, y) for r, (x, y, _) in result.items()}
        # The function returns in the loop body; no _deoverlap after the loop is called.
        # So result equals positions exactly.
        assert pos2_before == pos2_after, (
            f"With threshold=1.0 result must equal input; got {result}"
        )

    # ------------------------------------------------------------------
    # Edge cases
    # ------------------------------------------------------------------

    def test_zero_signal_wires_returns_unchanged(self) -> None:
        """A circuit with no signal nets must be returned without error."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1")]),
                NetIR(name="GND", pins=[PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R2": (_COL1_X, _SLOT0_Y, None),
        }
        result = _remediate_crossings(positions, ir)
        assert result == positions, "Zero-signal-wire circuit must be returned unchanged"

    def test_empty_positions_returns_empty(self) -> None:
        """Empty positions dict must return empty dict without error."""
        # CircuitIR requires ≥1 component and ≥1 net; use minimal valid IR.
        ir = _crossing_ir()
        result = _remediate_crossings({}, ir)
        assert result == {}

    def test_max_sweeps_one_skips_sorting(self) -> None:
        """max_sweeps=1 must cause an immediate exit without any column reordering.

        The break condition fires on ``sweep == max_sweeps - 1`` *before* the
        sort runs.  With max_sweeps=1, sweep=0 is already the last sweep, so
        the sort is never executed and the crossing is not improved.
        Only the trailing ``_deoverlap_positions`` call runs.
        """
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir(), max_sweeps=1)

        ir = _crossing_ir()
        sig_adj = build_signal_adjacency(ir)
        pos2 = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2, sig_adj) == 1, (
            "max_sweeps=1 skips sorting; crossing must remain"
        )

    def test_max_sweeps_two_allows_one_sort_pass(self) -> None:
        """max_sweeps=2 allows exactly one sort pass and must fix the crossing."""
        positions = _crossing_positions()
        result = _remediate_crossings(positions, _crossing_ir(), max_sweeps=2)

        ir = _crossing_ir()
        sig_adj = build_signal_adjacency(ir)
        pos2 = {r: (x, y) for r, (x, y, _) in result.items()}
        assert count_wire_crossings(pos2, sig_adj) == 0, (
            "max_sweeps=2 allows one sort; crossing must be eliminated"
        )

    def test_power_symbols_excluded_and_preserved(self) -> None:
        """#PWR / #FLG refs must not be reordered and must appear in the result."""
        ir = _crossing_ir()
        positions = dict(_crossing_positions())
        positions["#PWR01"] = (_COL0_X, _SLOT0_Y - 5.0, None)
        positions["#FLG02"] = (_COL1_X, _SLOT0_Y - 5.0, None)

        result = _remediate_crossings(positions, ir)

        assert result["#PWR01"] == (_COL0_X, _SLOT0_Y - 5.0, None), "#PWR01 must be unchanged"
        assert result["#FLG02"] == (_COL1_X, _SLOT0_Y - 5.0, None), "#FLG02 must be unchanged"

    def test_input_dict_not_mutated(self) -> None:
        """The original positions dict must not be modified in-place."""
        positions = _crossing_positions()
        original = dict(positions)
        _remediate_crossings(positions, _crossing_ir())
        assert positions == original, "Input dict must not be mutated"

    def test_single_component_per_column_unchanged(self) -> None:
        """Columns with only one component cannot be reordered; positions unchanged."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[
                NetIR(
                    name="NET_A",
                    pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
                ),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (_COL0_X, _SLOT0_Y, None),
            "R2": (_COL1_X, _SLOT1_Y, None),
        }
        result = _remediate_crossings(positions, ir)
        # Each column has one component; no reordering possible.
        assert result["R1"][0] == pytest.approx(_COL0_X)
        assert result["R2"][0] == pytest.approx(_COL1_X)


# ---------------------------------------------------------------------------
# Phase 4 (readable schematics) — _clamp_to_page
# ---------------------------------------------------------------------------


class TestClampToPage:
    """Unit tests for _clamp_to_page() — final pass that prevents LAY004."""

    def test_positions_inside_bounds_unchanged(self) -> None:
        """Positions already inside the A4 area must pass through unmodified."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, 100.0, 0.0),
            "U1": (150.0, 80.0, None),
        }
        result = _clamp_to_page(positions)
        assert result["R1"] == pytest.approx((100.0, 100.0, 0.0))
        assert result["U1"][0] == pytest.approx(150.0)
        assert result["U1"][1] == pytest.approx(80.0)
        assert result["U1"][2] is None

    def test_x_beyond_max_clamped(self) -> None:
        """x > PAGE_MAX_X must be clamped to PAGE_MAX_X."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (PAGE_MAX_X + 50.0, 100.0, 0.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][0] == pytest.approx(PAGE_MAX_X)
        assert result["R1"][1] == pytest.approx(100.0)

    def test_y_beyond_max_clamped(self) -> None:
        """y > PAGE_MAX_Y must be clamped to PAGE_MAX_Y."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, PAGE_MAX_Y + 30.0, 90.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][1] == pytest.approx(PAGE_MAX_Y)
        assert result["R1"][0] == pytest.approx(100.0)

    def test_x_below_origin_clamped(self) -> None:
        """x < ORIGIN_X must be clamped to ORIGIN_X."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (ORIGIN_X - 20.0, 100.0, None),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][0] == pytest.approx(ORIGIN_X)

    def test_y_below_origin_clamped(self) -> None:
        """y < ORIGIN_Y must be clamped to ORIGIN_Y."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (100.0, ORIGIN_Y - 10.0, 0.0),
        }
        result = _clamp_to_page(positions)
        assert result["R1"][1] == pytest.approx(ORIGIN_Y)

    def test_rotation_preserved(self) -> None:
        """Rotation must be unchanged even when x or y is clamped."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (PAGE_MAX_X + 5.0, PAGE_MAX_Y + 5.0, 180.0),
        }
        result = _clamp_to_page(positions)
        assert result["U1"][2] == pytest.approx(180.0)

    def test_input_not_mutated(self) -> None:
        """The input dict must not be modified in-place."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (PAGE_MAX_X + 1.0, 100.0, 0.0),
        }
        original_x = positions["R1"][0]
        _clamp_to_page(positions)
        assert positions["R1"][0] == original_x

    def test_empty_positions_returns_empty(self) -> None:
        """Empty input must produce an empty output without error."""
        result = _clamp_to_page({})
        assert result == {}


# ---------------------------------------------------------------------------
# Phase 4.3 — _spread_x_columns
# ---------------------------------------------------------------------------


class TestSpreadXColumns:
    """Unit tests for _spread_x_columns() — X-spread to prevent column collapse."""

    def test_empty_returns_empty(self) -> None:
        result = _spread_x_columns({})
        assert result == {}

    def test_small_column_unchanged(self) -> None:
        """≤ max_per_column symbols at same x must not be moved."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (50.8, 63.5, None),
            "R3": (50.8, 76.2, 90.0),
        }
        result = _spread_x_columns(positions, max_per_column=3)
        assert result["R1"][0] == pytest.approx(50.8)
        assert result["R2"][0] == pytest.approx(50.8)
        assert result["R3"][0] == pytest.approx(50.8)

    def test_overloaded_column_produces_two_subcolumns(self) -> None:
        """6 symbols at the same x, max_per_column=3 → exactly 2 distinct x values."""
        x0 = 50.8  # 40 × 1.27 mm (on grid)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 7)
        }
        result = _spread_x_columns(positions, max_per_column=3, col_step_mm=25.4)
        xs = {v[0] for v in result.values()}
        # 2 sub-columns: x0 ± 12.7 mm → 38.1 and 63.5
        assert len(xs) == 2, f"Expected 2 distinct x-columns, got {sorted(xs)}"
        assert all(x >= ORIGIN_X for x in xs)
        assert all(x <= PAGE_MAX_X for x in xs)

    def test_nine_symbols_produce_three_subcolumns(self) -> None:
        """9 symbols, max_per_column=3 → exactly 3 distinct x-column positions."""
        x0 = 101.6  # 80 × 1.27 mm (on grid, comfortably away from edges)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), None) for i in range(1, 10)
        }
        result = _spread_x_columns(positions, max_per_column=3, col_step_mm=25.4)
        xs = sorted(v[0] for v in result.values())
        distinct_xs = sorted(set(xs))
        # 3 sub-columns: 101.6 ± 25.4 = {76.2, 101.6, 127.0}
        assert len(distinct_xs) == 3, f"Expected 3 distinct x-cols, got {distinct_xs}"
        assert distinct_xs == pytest.approx([76.2, 101.6, 127.0])

    def test_y_tier_order_preserved_across_subcolumns(self) -> None:
        """The lowest-y (highest-tier) symbols end up in the leftmost sub-column."""
        x0 = 101.6  # on grid
        # 4 symbols: R1=y10, R2=y20, R3=y30, R4=y40
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (x0, 10.0, 0.0),
            "R2": (x0, 20.0, 0.0),
            "R3": (x0, 30.0, 0.0),
            "R4": (x0, 40.0, 0.0),
        }
        result = _spread_x_columns(positions, max_per_column=2, col_step_mm=25.4)
        # After y-sort: R1, R2 → col_idx=0 (offset=-12.7), R3, R4 → col_idx=1 (offset=+12.7)
        x_r1, x_r2 = result["R1"][0], result["R2"][0]
        x_r3, x_r4 = result["R3"][0], result["R4"][0]
        assert x_r1 == pytest.approx(x_r2), "R1 and R2 should share the same sub-column"
        assert x_r3 == pytest.approx(x_r4), "R3 and R4 should share the same sub-column"
        assert x_r1 < x_r3, "Lower-y symbols (R1/R2) should be in leftmost sub-column"

    def test_rotation_preserved(self) -> None:
        """Rotation must not be modified by spreading."""
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 10.0, 45.0),
            "R2": (50.8, 20.0, 90.0),
            "R3": (50.8, 30.0, None),
            "R4": (50.8, 40.0, 180.0),
        }
        result = _spread_x_columns(positions, max_per_column=2)
        assert result["R1"][2] == pytest.approx(45.0)
        assert result["R2"][2] == pytest.approx(90.0)
        assert result["R3"][2] is None
        assert result["R4"][2] == pytest.approx(180.0)

    def test_input_not_mutated(self) -> None:
        """The input dict must not be modified in-place."""
        x0 = 101.6
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 5)
        }
        original = {ref: tuple(v) for ref, v in positions.items()}
        _spread_x_columns(positions, max_per_column=2)
        for ref, orig_val in original.items():
            assert positions[ref] == orig_val, f"{ref} was mutated"

    def test_subcolumns_clamped_to_page_bounds(self) -> None:
        """Sub-columns pushed below ORIGIN_X must be clamped to ORIGIN_X."""
        # x=ORIGIN_X with 4 symbols; left sub-column would go to ORIGIN_X - 12.7 → clamped.
        x0: float = ORIGIN_X  # 30.48 mm (on grid: 24 × 1.27)
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, 5)
        }
        result = _spread_x_columns(positions, max_per_column=2, col_step_mm=25.4)
        for _ref, (x, _y, _r) in result.items():
            assert x >= ORIGIN_X, f"x={x} is below ORIGIN_X={ORIGIN_X}"

    def test_symbols_in_different_columns_untouched(self) -> None:
        """Symbols in non-overloaded columns must keep their original x."""
        positions: dict[str, tuple[float, float, float | None]] = {
            # One x=50.8 column (3 symbols — exactly at limit, not overloaded)
            "R1": (50.8, 10.0, 0.0),
            "R2": (50.8, 20.0, 0.0),
            "R3": (50.8, 30.0, 0.0),
            # One separate symbol at x=120
            "U1": (120.0, 50.0, None),
        }
        result = _spread_x_columns(positions, max_per_column=3)
        assert result["R1"][0] == pytest.approx(50.8)
        assert result["R2"][0] == pytest.approx(50.8)
        assert result["R3"][0] == pytest.approx(50.8)
        assert result["U1"][0] == pytest.approx(120.0)

    def test_identical_x_produces_at_least_n_columns(self) -> None:
        """Phase 4.3 acceptance test: N identical-x symbols produce >= ceil(N/max) columns.

        This is the primary readability guard from the TODO: a set of symbols
        with identical x must produce >= N x-columns after the spread pass.
        """
        max_per_col = 3
        # 12 symbols at x=152.4 (120 × 1.27 mm) — centre of usable page width.
        x0 = 152.4
        n_sym = 12
        positions: dict[str, tuple[float, float, float | None]] = {
            f"R{i}": (x0, float(i * 10), 0.0) for i in range(1, n_sym + 1)
        }
        result = _spread_x_columns(positions, max_per_column=max_per_col, col_step_mm=25.4)
        distinct_x_count = len({v[0] for v in result.values()})
        expected_min = math.ceil(n_sym / max_per_col)  # ceil(12/3) = 4
        assert distinct_x_count >= expected_min, (
            f"Expected >= {expected_min} x-columns from {n_sym} identical-x symbols, "
            f"got {distinct_x_count}"
        )


class TestPropertyTextSpacing:
    """Unit tests for the late property-text spacing pass."""

    def test_pushes_nearby_x_lanes_apart(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (63.5, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result["R1"] == positions["R1"]
        assert result["R2"][1] == pytest.approx(66.04)

    def test_leaves_distant_x_lanes_unchanged(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "R2": (114.3, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result == positions

    def test_skips_power_refs(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (50.8, 50.8, 0.0),
            "#PWR01": (50.8, 58.42, 0.0),
        }

        result = _apply_property_text_spacing(positions)

        assert result == positions

    def test_keeps_fixed_refs_stationary(self) -> None:
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (50.8, 50.8, 0.0),
            "R1": (63.5, 60.96, 0.0),
        }

        result = _apply_property_text_spacing(positions, fixed_refs=frozenset({"R1"}))

        assert result == positions


# ---------------------------------------------------------------------------
# Phase 2.3 — Label duplication limits (LabelPolicy)
# ---------------------------------------------------------------------------


class TestLabelPolicy:
    """Unit tests for :class:`LabelPolicy` and the ``policy`` param of :func:`route_nets`.

    Routing path notes used by fixture design
    -----------------------------------------
    * **Hub** fires when ``3 ≤ len(known) ≤ 6`` **and** ``not unknown``.  A net
      with any unknown pins bypasses hub → falls to label-fallback.
    * **High-degree** fires when ``len(known) > 6`` (regardless of unknown).
    * **Label-fallback**: emits one :class:`NetLabel` per known pin (capped by
      ``policy.max_labels_per_net``) and one per unknown pin (always).
    """

    # ------------------------------------------------------------------
    # Label-fallback (known-pin cap)
    # ------------------------------------------------------------------

    def test_default_policy_caps_known_pin_labels_at_two(self) -> None:
        """Label-fallback: 4 known + 1 unknown → default policy caps known at 2.

        Without the policy gate the loop would emit 4 known labels + 1 unknown = 5.
        With ``DEFAULT_LABEL_POLICY`` (max=2) it should emit 2 known + 1 unknown = 3.
        """
        # 4 known (R1–R4) + 1 unknown (R5 absent from pin_endpoints).
        # Hub is bypassed because ``unknown`` is non-empty.
        # High-degree is bypassed because len(known)=4 ≤ 6.
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        # Default policy: 2 known labels + 1 unknown label = 3 total.
        assert len(routing.labels) == 3, (
            f"Expected 3 labels (2 capped known + 1 unknown); got {routing.labels}"
        )

    def test_custom_policy_max_one_known_label(self) -> None:
        """policy(max_labels_per_net=1): 4 known + 1 unknown → 1 known + 1 unknown = 2."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        policy = LabelPolicy(max_labels_per_net=1)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.labels) == 2, (
            f"Expected 2 labels (1 capped known + 1 unknown); got {routing.labels}"
        )

    def test_unlimited_policy_emits_all_labels(self) -> None:
        """policy(max_labels_per_net=999): all 4 known + 1 unknown = 5 labels emitted."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }  # R5 absent → unknown
        policy = LabelPolicy(max_labels_per_net=999)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.labels) == 5, (
            f"Expected 5 labels (4 known + 1 unknown, unlimited); got {routing.labels}"
        )

    def test_two_pin_label_fallback_unaffected_by_default_policy(self) -> None:
        """2 far-apart known pins → 2 labels; default max=2 does not reduce this."""
        ir = _make_ir(
            [("R1", "Device:R"), ("R2", "Device:R")],
            [("NET1", [("R1", "1"), ("R2", "1")])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "1"): (30.0, 100.0, 0.0),
            ("R2", "1"): (250.0, 100.0, 180.0),  # 220 mm > MAX_DIRECT_DIST_MM (200 mm)
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        assert len(routing.labels) == 2, (
            f"Default policy should leave 2-pin fallback unchanged; got {routing.labels}"
        )

    # ------------------------------------------------------------------
    # High-degree global-label cap
    # ------------------------------------------------------------------

    def test_high_degree_global_labels_capped_at_default_four(self) -> None:
        """8-pin non-power net (degree>6 → global-label path): default policy caps at 4."""
        # 8 components, all in pin_endpoints (all known).  Non-power name → global labels.
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)
        }
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)
        # Default policy: max_global_labels_per_net=4.
        assert len(routing.global_labels) == 4, (
            f"Expected 4 global labels (default cap); got {routing.global_labels}"
        )

    def test_high_degree_custom_global_label_policy(self) -> None:
        """policy(max_global_labels_per_net=2): 8-pin net emits only 2 GlobalLabelPlacements."""
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)
        }
        policy = LabelPolicy(max_global_labels_per_net=2)
        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, policy=policy)
        assert len(routing.global_labels) == 2, (
            f"Expected 2 global labels (custom cap=2); got {routing.global_labels}"
        )


class TestStructuralRoutingClassification:
    def test_feedback_role_overrides_neutral_net_name(self) -> None:
        ir = _make_ir(
            [("U1", "Amplifier_Operational:OpAmp_Dual_Generic"), ("R2", "Device:R")],
            [("N001", [("U1", "1"), ("R2", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("R2", "1"): (60.0, 40.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, block_layout=block_layout)

        assert routing.route_decisions[0].classification == "feedback"

    def test_interstage_roles_override_neutral_net_name(self) -> None:
        ir = _make_ir(
            [
                ("U1", "Amplifier_Operational:OpAmp_Dual_Generic"),
                ("C6", "Device:C"),
                ("R5", "Device:R"),
            ],
            [("N002", [("U1", "1"), ("C6", "1"), ("R5", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("C6", "1"): (60.0, 30.0, 270.0),
            ("R5", "1"): (60.0, 50.0, 90.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints, block_layout=block_layout)

        assert routing.route_decisions[0].classification == "signal_chain"

    def test_name_fallback_still_applies_without_block_layout(self) -> None:
        ir = _make_ir(
            [("U1", "Amplifier_Operational:OpAmp_Dual_Generic"), ("R2", "Device:R")],
            [("U1A_INV", [("U1", "1"), ("R2", "1")])],
        )
        pin_endpoints = {
            ("U1", "1"): (40.0, 40.0, 180.0),
            ("R2", "1"): (60.0, 40.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)

        assert routing.route_decisions[0].classification == "feedback"


class TestStructuralLabelPriority:
    def test_structural_roles_prioritize_visible_local_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R4", BlockRole.OUTPUT)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
        )

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {30.48, 39.37}

    def test_structural_roles_prioritize_visible_global_labels(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 9)],
            [("N_BUS", [(f"R{i}", "1") for i in range(1, 9)])],
        )
        pin_endpoints = {(f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 9)}
        block_layout = BlockLayout()
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R2", BlockRole.BUFFER_STAGE)
        block_layout.add_assignment("R3", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.OUTPUT)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.DECOUPLING)
        block_layout.add_assignment("R8", BlockRole.POWER_ENTRY)
        policy = LabelPolicy(max_global_labels_per_net=2)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=policy,
        )

        visible_x = {round(label.x, 2) for label in routing.global_labels if label.name == "N_BUS"}
        assert visible_x == {39.37, 49.53}

    def test_label_caps_preserve_existing_order_without_structural_context(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("N_STAGE", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints = {
            ("R1", "1"): (10.0, 0.0, 0.0),
            ("R2", "1"): (20.0, 0.0, 0.0),
            ("R3", "1"): (30.0, 0.0, 0.0),
            ("R4", "1"): (40.0, 0.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=pin_endpoints)

        visible_x = {round(label.x, 2) for label in routing.labels if label.x > 0.0}
        assert visible_x == {10.16, 20.32}


class TestLabelModes:
    def test_debug_mode_expands_label_fallback_caps(self) -> None:
        ir = _make_ir(
            [(f"R{i}", "Device:R") for i in range(1, 6)],
            [("SIG", [(f"R{i}", "1") for i in range(1, 6)])],
        )
        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (f"R{i}", "1"): (float(i * 10), 0.0, 0.0) for i in range(1, 5)
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            policy=LABEL_MODE_POLICIES["debug"],
        )

        assert len(routing.labels) == 5

    def test_important_mode_promotes_one_direct_signal_chain_label(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert len(routing.labels) == 1
        label = routing.labels[0]
        assert (label.name, round(label.x, 2), round(label.y, 2), label.angle) == (
            "LEFT_IN",
            24.92,
            100.0,
            180,
        )

    def test_minimal_mode_keeps_direct_signal_chain_net_label_free(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
            ],
            [("LEFT_IN", [("J1", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (30.0, 100.0, 0.0),
            ("R1", "1"): (70.0, 100.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["minimal"],
        )

        assert routing.route_decisions[0].strategy == "direct"
        assert routing.labels == []

    def test_important_mode_still_promotes_label_on_multi_pin_stage_seam(self) -> None:
        ir = _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("C5", "Device:C"),
                ("R1", "Device:R"),
            ],
            [("IN_L_AC", [("J1", "1"), ("C5", "1"), ("R1", "1")])],
        )
        pin_endpoints = {
            ("J1", "1"): (20.0, 100.0, 0.0),
            ("C5", "1"): (60.0, 90.0, 180.0),
            ("R1", "1"): (60.0, 110.0, 180.0),
        }
        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R1", BlockRole.PRECONDITIONING)

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        assert routing.route_decisions[0].strategy != "direct"
        assert [label.name for label in routing.labels] == ["IN_L_AC"]

    def test_important_mode_promotes_only_explicit_stage_seam_nets(self) -> None:
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="C5", symbol="Device:C", value="10u"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="10k"),
                ComponentIR(ref="R4", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:NE5532", value="NE5532"),
                ComponentIR(ref="R2", symbol="Device:R", value="22k"),
                ComponentIR(ref="R3", symbol="Device:R", value="22k"),
                ComponentIR(ref="C6", symbol="Device:C", value="10u"),
                ComponentIR(ref="R5", symbol="Device:R", value="22k"),
                ComponentIR(ref="R6", symbol="Device:R", value="100"),
                ComponentIR(ref="C7", symbol="Device:C", value="220u"),
                ComponentIR(ref="R7", symbol="Device:R", value="10k"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="LEFT_IN",
                    pins=[
                        PinRefIR(ref="J1", pin="1"),
                        PinRefIR(ref="C5", pin="1"),
                        PinRefIR(ref="R1", pin="1"),
                    ],
                ),
                NetIR(
                    name="IN_L_AC",
                    pins=[
                        PinRefIR(ref="C5", pin="2"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="RV1", pin="1"),
                    ],
                ),
                NetIR(
                    name="VOL_L_OUT",
                    pins=[
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="U1", pin="3"),
                        PinRefIR(ref="R4", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE1",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="C6", pin="1"),
                    ],
                ),
                NetIR(
                    name="BUF_L_IN",
                    pins=[
                        PinRefIR(ref="C6", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="U1", pin="5"),
                    ],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C7", pin="2"),
                        PinRefIR(ref="R7", pin="1"),
                        PinRefIR(ref="J2", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_L_STAGE2_RAW",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="R6", pin="1"),
                    ],
                ),
                NetIR(
                    name="AFTER_R6",
                    pins=[PinRefIR(ref="R6", pin="2"), PinRefIR(ref="C7", pin="1")],
                ),
                NetIR(
                    name="U1A_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("J1", BlockRole.INPUT)
        block_layout.add_assignment("C5", BlockRole.INPUT)
        block_layout.add_assignment("R1", BlockRole.INPUT)
        block_layout.add_assignment("RV1", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("R4", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("R2", BlockRole.FEEDBACK)
        block_layout.add_assignment("R3", BlockRole.FEEDBACK)
        block_layout.add_assignment("C6", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R5", BlockRole.INTERSTAGE)
        block_layout.add_assignment("R6", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("C7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("R7", BlockRole.OUTPUT_CONDITIONING)
        block_layout.add_assignment("J2", BlockRole.OUTPUT)

        pin_endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("J1", "1"): (20.0, 80.0, 0.0),
            ("C5", "1"): (40.0, 72.0, 180.0),
            ("R1", "1"): (40.0, 88.0, 180.0),
            ("C5", "2"): (60.0, 72.0, 0.0),
            ("R1", "2"): (60.0, 88.0, 0.0),
            ("RV1", "1"): (80.0, 80.0, 180.0),
            ("RV1", "2"): (100.0, 80.0, 0.0),
            ("U1", "3"): (120.0, 72.0, 180.0),
            ("R4", "1"): (120.0, 88.0, 180.0),
            ("U1", "1"): (140.0, 80.0, 0.0),
            ("R2", "1"): (160.0, 72.0, 180.0),
            ("C6", "1"): (160.0, 88.0, 180.0),
            ("C6", "2"): (180.0, 72.0, 0.0),
            ("R5", "1"): (180.0, 88.0, 0.0),
            ("U1", "5"): (200.0, 80.0, 180.0),
            ("C7", "2"): (220.0, 72.0, 0.0),
            ("R7", "1"): (220.0, 88.0, 0.0),
            ("J2", "1"): (240.0, 80.0, 180.0),
            ("U1", "7"): (260.0, 72.0, 0.0),
            ("U1", "6"): (260.0, 88.0, 0.0),
            ("R6", "1"): (280.0, 80.0, 180.0),
            ("R6", "2"): (300.0, 72.0, 0.0),
            ("C7", "1"): (300.0, 88.0, 180.0),
            ("U1", "2"): (320.0, 80.0, 0.0),
            ("R2", "2"): (340.0, 72.0, 180.0),
            ("R3", "1"): (340.0, 88.0, 180.0),
        }

        routing = route_nets(
            ir=ir,
            pin_endpoints=pin_endpoints,
            block_layout=block_layout,
            policy=LABEL_MODE_POLICIES["always-show-important-labels"],
        )

        label_names = {label.name for label in routing.labels}

        assert {
            "LEFT_IN",
            "IN_L_AC",
            "VOL_L_OUT",
            "OUT_L_STAGE1",
            "BUF_L_IN",
            "HP_L_OUT",
        } <= label_names
        assert {"OUT_L_STAGE2_RAW", "AFTER_R6", "U1A_INV"}.isdisjoint(label_names)


# ---------------------------------------------------------------------------
# Phase 3 — Power net strategy: PowerSymbolPlacement + write_routing
# ---------------------------------------------------------------------------


_UUID_COUNTER = itertools.count(1)


def _next_test_uuid() -> str:
    return f"test-uuid-{next(_UUID_COUNTER):04d}"


def _make_sch_doc() -> SchematicDoc:
    """Return a fresh minimal :class:`SchematicDoc` for write_routing tests."""
    root = parse(minimal_schematic_text())
    assert isinstance(root, ListNode)
    return SchematicDoc(root)


class TestPhase3PowerSymbols:
    """Phase 3 — power net strategy: PowerSymbolPlacement and write_routing integration.

    Route-nets tests confirm that power nets produce :class:`PowerSymbolPlacement`
    objects instead of :class:`GlobalLabelPlacement`.  Write-routing tests
    confirm that :func:`write_routing` embeds the ``power:`` lib definition and
    places a proper ``(symbol ...)`` instance, with a fallback to
    ``(global_label ...)`` when the library symbol is not found.
    """

    # ------------------------------------------------------------------
    # PowerSymbolPlacement dataclass
    # ------------------------------------------------------------------

    def test_power_symbol_placement_defaults(self) -> None:
        """PowerSymbolPlacement has zero default angle and exposes net_name."""
        ps = PowerSymbolPlacement("GND", 10.0, 20.0)
        assert ps.net_name == "GND"
        assert ps.x == 10.0
        assert ps.y == 20.0
        assert ps.angle == 0

    def test_power_symbol_placement_custom_angle(self) -> None:
        ps = PowerSymbolPlacement("VCC", 0.0, 0.0, angle=90)
        assert ps.angle == 90

    # ------------------------------------------------------------------
    # write_routing — power symbol embedding (requires system KiCad libs)
    # ------------------------------------------------------------------

    def test_write_routing_embeds_power_lib_symbol(self) -> None:
        """write_routing embeds ``power:GND`` in lib_symbols and places instance."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("GND", 50.0, 80.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)

        # lib_symbols section must contain the "power:GND" definition.
        lib_sym_section = find_first(doc.root, "lib_symbols")
        assert lib_sym_section is not None, "lib_symbols section missing after write_routing"
        embedded_ids = [
            item.items[1].value
            for item in lib_sym_section.items
            if isinstance(item, ListNode)
            and len(item.items) >= 2
            and isinstance(item.items[1], StringNode)
        ]
        assert "power:GND" in embedded_ids, (
            f"power:GND not embedded in lib_symbols; found: {embedded_ids}"
        )
        assert stats["power_symbols"] == 1
        assert stats["global_labels"] == 0

    def test_write_routing_places_power_symbol_instance(self) -> None:
        """write_routing places a \"symbol\" node with lib_id \"power:GND\" in the schematic."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("GND", 30.0, 40.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)

        # A symbol instance with lib_id "power:GND" must appear in the schematic.
        placed = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "symbol"
            and any(
                isinstance(c, ListNode)
                and c.key == "lib_id"
                and len(c.items) >= 2
                and isinstance(c.items[1], StringNode)
                and c.items[1].value == "power:GND"
                for c in item.items
            )
        ]
        assert len(placed) == 1, f"Expected 1 placed power:GND instance; got {len(placed)}"

    def test_write_routing_power_symbol_fallback_to_global_label(self, tmp_path: Path) -> None:
        """Fallback to global_label when power symbol is absent from the library."""
        doc = _make_sch_doc()
        routing = NetRouting()
        # Use a net name that cannot exist in the power library.
        routing.power_symbols.append(PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0, 0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        # Point symbols_dir to an empty temp dir → no power.kicad_sym available.
        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
            symbols_dir=tmp_path,
        )
        assert stats["power_symbols"] == 0, "Expected 0 successful power symbols"
        assert stats["global_labels"] == 1, "Expected global_label fallback"
        assert stats["wires"] == 1, "Expected one orthogonal jog to the fallback label"

        placed_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode) and item.key == "global_label"
        ]
        assert len(placed_labels) == 1
        label_at = find_first(placed_labels[0], "at")
        assert label_at is not None
        label_x = label_at.items[1].value  # type: ignore[union-attr]
        label_y = label_at.items[2].value  # type: ignore[union-attr]
        assert (label_x, label_y) != ("50.00", "80.00")

        wires = [
            item for item in doc.root.items if isinstance(item, ListNode) and item.key == "wire"
        ]
        assert len(wires) == 1
        pts = find_first(wires[0], "pts")
        assert pts is not None
        xy1, xy2 = pts.items[1], pts.items[2]
        assert isinstance(xy1, ListNode)
        assert isinstance(xy2, ListNode)
        assert xy1.items[1].value == "50.00"  # type: ignore[union-attr]
        assert xy1.items[2].value == "80.00"  # type: ignore[union-attr]
        assert xy2.items[1].value == label_x  # type: ignore[union-attr]
        assert xy2.items[2].value == label_y  # type: ignore[union-attr]

    def test_write_routing_power_symbol_fallback_avoids_occupied_anchor(
        self, tmp_path: Path
    ) -> None:
        doc = _make_sch_doc()
        routing = NetRouting(
            global_labels=[GlobalLabelPlacement("/BUS", 50.0, 73.66, 270)],
            power_symbols=[PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0, 0)],
        )
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
            symbols_dir=tmp_path,
        )

        placed_labels = [
            item
            for item in doc.root.items
            if isinstance(item, ListNode)
            and item.key == "global_label"
            and isinstance(item.items[1], StringNode)
            and item.items[1].value == "NOT_A_REAL_NET_XYZ"
        ]
        assert len(placed_labels) == 1
        label_at = find_first(placed_labels[0], "at")
        assert label_at is not None
        assert not (
            label_at.items[1].value == "50.00"  # type: ignore[union-attr]
            and label_at.items[2].value == "73.66"  # type: ignore[union-attr]
        )

    def test_write_routing_strict_raises_when_power_symbol_missing(self, tmp_path: Path) -> None:
        """Strict mode must fail fast when a power symbol cannot be resolved."""
        doc = _make_sch_doc()
        routing = NetRouting()
        routing.power_symbols.append(PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        with pytest.raises(UserError) as exc_info:
            write_routing(
                doc=doc,
                routing=routing,
                new_uuid=_next_test_uuid,
                stats=stats,
                symbols_dir=tmp_path,
                strict=True,
            )

        assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND
        assert exc_info.value.details["symbol"] == "power:NOT_A_REAL_NET_XYZ"
        assert stats["global_labels"] == 0
        assert stats["power_symbols"] == 0

    def test_write_routing_multiple_power_nets(self) -> None:
        """Multiple power symbol placements all embedded and placed correctly."""
        doc = _make_sch_doc()
        routing = NetRouting()
        for net, x in [("GND", 10.0), ("GND", 20.0), ("VCC", 30.0)]:
            routing.power_symbols.append(PowerSymbolPlacement(net, x, 50.0))
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }
        write_routing(doc=doc, routing=routing, new_uuid=_next_test_uuid, stats=stats)
        assert stats["power_symbols"] == 3
        assert stats["global_labels"] == 0

    def test_write_routing_preserves_binding_marker_refs(self) -> None:
        doc = _make_sch_doc()
        routing = NetRouting(bind_markers=[BindMarker("U1A", "1", "IN_A")])
        stats: dict[str, int] = {
            "wires": 0,
            "labels": 0,
            "global_labels": 0,
            "power_symbols": 0,
            "junctions": 0,
            "binding_markers": 0,
        }

        write_routing(
            doc=doc,
            routing=routing,
            new_uuid=_next_test_uuid,
            stats=stats,
        )

        assert stats["binding_markers"] == 1
        assert doc.extract_pin_label_bindings() == [{"ref": "U1A", "pin": "1", "net_name": "IN_A"}]
