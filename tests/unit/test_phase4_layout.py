"""Phase 4 layout + wiring engine tests.

Covers:
- 4.1  LayoutEngine Protocol factory (make_layout_engine)
- 4.2  NoneLayoutEngine: returns origin for every component
- 4.3  HeuristicLayoutEngine: positions are spread out; all refs returned
- 4.4  Router 2-pin direct route: wire segments emitted, no labels
- 4.5  Router hub route (3-pin): centroid junction emitted, no labels
- 4.6  Router power-net: GlobalLabelPlacement per pin, no local labels
- 4.7  Router >6-pin non-power: GlobalLabelPlacement per pin (high-fanout)
- 4.8  _hub_route helper: centroid, junction only for ≥3 endpoints
- 4.9  LAY001 – label name appears > 3 times → WARN
- 4.10 LAY001 – label name appears ≤ 3 times → no issue
- 4.11 LAY002 – >60% stub-length wires → WARN
- 4.12 LAY002 – mostly long wires → no issue
- 4.13 LAY003 – overlapping symbols → WARN
- 4.14 LAY003 – well-spaced symbols → no issue
- 4.15 LAY004 – symbol outside A4 bounds → ERROR
- 4.16 LAY004 – symbol inside A4 bounds → no issue
- 4.17 LAY005 – 3 disconnected islands → WARN
- 4.18 LAY005 – single connected wire chain → no issue
- 4.19 LAY rules silent on non-sch root
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import kicad_pcb.graphviz_layout as _gv_mod
import pytest
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    MIN_SEPARATION_MM,
    HeuristicLayoutEngine,
    compute_affinity_groups,
    compute_orientations,
)
from kicad_pcb.layout_engine import NoneLayoutEngine, make_layout_engine
from kicad_pcb.lint import LINT_SUGGESTIONS, LintSeverity, lint_schematic_layout
from kicad_pcb.router import (
    _hub_route,
    _is_power_net_name,
    route_nets,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import AtomNode

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


def _sch(body: str = "") -> object:
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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


class TestLayoutEngineFactory:
    def test_none_engine_places_all_refs_at_origin(self) -> None:
        ir = _minimal_ir(refs=["U1", "U2", "C3"])
        engine = NoneLayoutEngine()
        positions = engine.compute_symbol_positions(ir)
        assert set(positions.keys()) == {"U1", "U2", "C3"}
        # All share the same origin position.
        xs = {pos[0] for pos in positions.values()}
        ys = {pos[1] for pos in positions.values()}
        assert len(xs) == 1 and len(ys) == 1

    def test_raises_when_dot_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """make_layout_engine() raises RuntimeError when dot is absent."""
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda: None)
        with pytest.raises(RuntimeError, match="dot.*not found"):
            make_layout_engine()


# ---------------------------------------------------------------------------
# 4.3  HeuristicLayoutEngine — positions are distinct
# ---------------------------------------------------------------------------


class TestHeuristicLayoutEngine:
    def test_all_refs_returned(self) -> None:
        ir = _minimal_ir(refs=["R1", "R2", "C1"])
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        assert set(positions.keys()) == {"R1", "R2", "C1"}

    def test_positions_are_spread_out(self) -> None:
        """Heuristic engine must not collapse all components to one point."""
        ir = _minimal_ir(
            refs=["R1", "R2", "R3"],
            nets=[
                {
                    "name": "NET1",
                    "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}],
                },
                {
                    "name": "NET2",
                    "pins": [{"ref": "R2", "pin": "2"}, {"ref": "R3", "pin": "1"}],
                },
            ],
        )
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        coords = [(positions[r][0], positions[r][1]) for r in ["R1", "R2", "R3"]]
        # At least two distinct x or y values.
        assert len({c[0] for c in coords}) > 1 or len({c[1] for c in coords}) > 1


# ---------------------------------------------------------------------------
# 4.3b HeuristicLayoutEngine — guaranteed overlap-free (LAY003 never fires)
# ---------------------------------------------------------------------------


class TestHeuristicLayoutNoOverlap:
    """Verify heuristic grid spacing prevents LAY003 for any reasonable IR.

    The heuristic engine places symbols on a GRID_COL_MM × GRID_ROW_MM grid.
    LAY003 fires only when both |Δx| and |Δy| are below 10.16 mm (2 × 5.08 mm
    half-bounding-box, see lint.py).  GRID_ROW_MM = MIN_SEPARATION_MM =
    20.32 mm > 10.16 mm, so adjacent positions within a column are always
    overlap-free.  GRID_COL_MM = 30.48 mm also exceeds the threshold.
    """

    @staticmethod
    def _symbols_body(positions: dict[str, tuple[float, float, float | None]]) -> str:
        """Build a schematic body string with one uniquely-UUID'd symbol per position."""
        parts = []
        for i, (_ref, pos) in enumerate(sorted(positions.items())):
            uuid = f"00000000-0000-0000-0000-{i:012d}"
            parts.append(f'(symbol (lib_id "Device:R") (at {pos[0]} {pos[1]} 0) (uuid "{uuid}"))')
        return "\n".join(parts)

    def test_min_separation_exceeds_lay003_threshold(self) -> None:
        """MIN_SEPARATION_MM must be strictly greater than the LAY003 overlap threshold.

        LAY003 fires when abs(dx) < 2*5.08 = 10.16 mm AND abs(dy) < 10.16 mm.
        MIN_SEPARATION_MM must exceed this to guarantee overlap-free layouts.
        """
        lay003_overlap_threshold_mm = 10.16  # 2 × _LAY_SYMBOL_HALF_SIZE_MM from lint.py
        assert lay003_overlap_threshold_mm < MIN_SEPARATION_MM, (
            f"MIN_SEPARATION_MM ({MIN_SEPARATION_MM}) must exceed "
            f"LAY003 threshold ({lay003_overlap_threshold_mm})"
        )

    def test_linear_chain_no_overlap(self) -> None:
        """15-component chain must not produce any LAY003 warnings."""
        refs = [f"R{i}" for i in range(1, 16)]
        nets = [
            {
                "name": f"N{i}",
                "pins": [{"ref": f"R{i}", "pin": "2"}, {"ref": f"R{i + 1}", "pin": "1"}],
            }
            for i in range(1, 15)
        ]
        ir = _minimal_ir(refs=refs, nets=nets)
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        root = _sch(self._symbols_body(positions))
        assert "LAY003" not in _codes(lint_schematic_layout(root))

    def test_amp_topology_no_overlap(self) -> None:
        """A 13-component amplifier IR must not produce any LAY003 warnings."""
        refs = ["J1", "R1", "R2", "R3", "R4", "R5", "C1", "C2", "C3", "C4", "U1", "U2", "R6"]
        nets = [
            {"name": "IN", "pins": [{"ref": "J1", "pin": "1"}, {"ref": "R1", "pin": "1"}]},
            {
                "name": "N1",
                "pins": [
                    {"ref": "R1", "pin": "2"},
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {
                "name": "N2",
                "pins": [
                    {"ref": "U1", "pin": "6"},
                    {"ref": "C1", "pin": "1"},
                    {"ref": "R3", "pin": "1"},
                ],
            },
            {"name": "N3", "pins": [{"ref": "R3", "pin": "2"}, {"ref": "U2", "pin": "2"}]},
            {"name": "OUT", "pins": [{"ref": "U2", "pin": "6"}, {"ref": "R4", "pin": "1"}]},
            {
                "name": "FB",
                "pins": [
                    {"ref": "R2", "pin": "2"},
                    {"ref": "R5", "pin": "1"},
                    {"ref": "U1", "pin": "3"},
                ],
            },
            {
                "name": "FB2",
                "pins": [
                    {"ref": "R6", "pin": "1"},
                    {"ref": "U2", "pin": "3"},
                    {"ref": "R4", "pin": "2"},
                ],
            },
            {"name": "C1N2", "pins": [{"ref": "C1", "pin": "2"}, {"ref": "C2", "pin": "1"}]},
            {"name": "C3N", "pins": [{"ref": "C3", "pin": "1"}, {"ref": "U1", "pin": "4"}]},
            {"name": "C4N", "pins": [{"ref": "C4", "pin": "1"}, {"ref": "U2", "pin": "4"}]},
        ]
        ir = _minimal_ir(refs=refs, nets=nets)
        positions = HeuristicLayoutEngine().compute_symbol_positions(ir)
        root = _sch(self._symbols_body(positions))
        assert "LAY003" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# 4.4  _is_power_net_name
# ---------------------------------------------------------------------------


class TestIsPowerNetName:
    @pytest.mark.parametrize(
        "name",
        ["GND", "VCC", "VDD", "VSS", "AGND", "PGND", "DGND", "VBAT", "VREF", "V+", "V-"],
    )
    def test_known_power_nets_detected(self, name: str) -> None:
        assert _is_power_net_name(name)

    @pytest.mark.parametrize("name", ["NET1", "SDA", "SCL", "DATA", "CLK"])
    def test_non_power_nets_not_detected(self, name: str) -> None:
        assert not _is_power_net_name(name)

    def test_case_insensitive(self) -> None:
        assert _is_power_net_name("gnd")
        assert _is_power_net_name("Vcc")


# ---------------------------------------------------------------------------
# 4.5  _hub_route helper
# ---------------------------------------------------------------------------


class TestHubRoute:
    def test_two_endpoints_no_junction(self) -> None:
        segs, junctions = _hub_route([(0.0, 0.0), (10.0, 0.0)])
        assert junctions == []

    def test_three_endpoints_produces_junction(self) -> None:
        endpoints = [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]
        segs, junctions = _hub_route(endpoints)
        assert len(junctions) == 1

    def test_hub_is_near_centroid(self) -> None:
        endpoints = [(0.0, 0.0), (10.0, 0.0), (5.0, 10.0)]
        _, junctions = _hub_route(endpoints)
        j = junctions[0]
        # Centroid ≈ (5, 3.33) — snapped to 1.27 mm grid
        assert abs(j.x - 5.08) < 1.5, f"hub x={j.x} not near centroid 5"
        assert abs(j.y - 3.81) < 1.5, f"hub y={j.y} not near centroid 3.33"

    def test_returns_wire_segments(self) -> None:
        segs, _ = _hub_route([(0.0, 0.0), (20.0, 0.0), (10.0, 20.0)])
        assert len(segs) >= 2  # at least one segment per spoke


# ---------------------------------------------------------------------------
# 4.6  route_nets — 2-pin direct route
# ---------------------------------------------------------------------------


class TestRouteNetsDirect:
    def _make_endpoints(self) -> dict[tuple[str, str], tuple[float, float, float]]:
        # R1 pin 1 at (50, 100, 180), R2 pin 1 at (70, 100, 0)
        return {
            ("R1", "1"): (50.0, 100.0, 180.0),
            ("R2", "1"): (70.0, 100.0, 0.0),
        }

    def test_direct_route_emits_wires(self) -> None:
        ir = _minimal_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._make_endpoints())
        assert len(routing.wires) > 0

    def test_direct_route_no_local_labels_when_close(self) -> None:
        ir = _minimal_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._make_endpoints())
        assert routing.labels == []

    def test_direct_route_no_global_labels(self) -> None:
        ir = _minimal_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._make_endpoints())
        assert routing.global_labels == []

    def test_direct_route_bind_markers_emitted(self) -> None:
        ir = _minimal_ir()
        routing = route_nets(ir=ir, pin_endpoints=self._make_endpoints())
        assert len(routing.bind_markers) == 2


# ---------------------------------------------------------------------------
# 4.7  route_nets — hub routing (3-pin net)
# ---------------------------------------------------------------------------


class TestRouteNetsHub:
    def _make_ir_and_endpoints(
        self,
    ) -> tuple[CircuitIR, dict[tuple[str, str], tuple[float, float, float]]]:
        refs = ["R1", "R2", "R3"]
        ir = _minimal_ir(
            refs=refs,
            nets=[
                {
                    "name": "NET1",
                    "pins": [
                        {"ref": "R1", "pin": "1"},
                        {"ref": "R2", "pin": "1"},
                        {"ref": "R3", "pin": "1"},
                    ],
                }
            ],
        )
        endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "1"): (10.0, 100.0, 180.0),
            ("R2", "1"): (50.0, 100.0, 0.0),
            ("R3", "1"): (30.0, 120.0, 270.0),
        }
        return ir, endpoints

    def test_hub_route_emits_junction(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert len(routing.junctions) >= 1

    def test_hub_route_no_local_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.labels == []

    def test_hub_route_no_global_labels_for_normal_net(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.global_labels == []

    def test_hub_route_bind_markers_emitted(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert len(routing.bind_markers) == 3


# ---------------------------------------------------------------------------
# 4.8  route_nets — power nets use global labels
# ---------------------------------------------------------------------------


class TestRouteNetsPower:
    def _make_ir_and_endpoints(
        self,
    ) -> tuple[CircuitIR, dict[tuple[str, str], tuple[float, float, float]]]:
        refs = ["R1", "C1"]
        ir = _minimal_ir(
            refs=refs,
            nets=[
                {
                    "name": "GND",
                    "pins": [{"ref": "R1", "pin": "2"}, {"ref": "C1", "pin": "2"}],
                }
            ],
        )
        endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            ("R1", "2"): (50.0, 110.0, 270.0),
            ("C1", "2"): (80.0, 110.0, 270.0),
        }
        return ir, endpoints

    def test_power_net_emits_global_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert len(routing.global_labels) == 2

    def test_power_global_label_names_match_net(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert all(g.name == "GND" for g in routing.global_labels)

    def test_power_net_no_local_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.labels == []

    def test_power_net_no_junctions(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.junctions == []


# ---------------------------------------------------------------------------
# 4.9  route_nets — high-fanout (>6 pins) uses global labels
# ---------------------------------------------------------------------------


class TestRouteNetsHighFanout:
    def _make_ir_and_endpoints(
        self,
    ) -> tuple[CircuitIR, dict[tuple[str, str], tuple[float, float, float]]]:
        refs = [f"R{i}" for i in range(1, 9)]
        ir = _minimal_ir(
            refs=refs,
            nets=[
                {
                    "name": "DATABUS",
                    "pins": [{"ref": r, "pin": "1"} for r in refs],
                }
            ],
        )
        endpoints: dict[tuple[str, str], tuple[float, float, float]] = {
            (r, "1"): (float(i * 10), 100.0, 0.0) for i, r in enumerate(refs)
        }
        return ir, endpoints

    def test_high_fanout_emits_global_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert len(routing.global_labels) == 8

    def test_high_fanout_no_local_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.labels == []

    def test_high_fanout_net_name_in_global_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert all(g.name == "DATABUS" for g in routing.global_labels)


# ---------------------------------------------------------------------------
# 4.10 LAY001 — net label appears more than 3 times
# ---------------------------------------------------------------------------


class TestLAY001:
    def test_no_labels_no_issue(self) -> None:
        root = _sch()
        issues = lint_schematic_layout(root)
        assert "LAY001" not in _codes(issues)

    def test_label_twice_no_issue(self) -> None:
        body = _label("SDA") + "\n" + _label("SDA", x=20.0)
        root = _sch(body)
        assert "LAY001" not in _codes(lint_schematic_layout(root))

    def test_label_three_times_no_issue(self) -> None:
        body = "\n".join(_label("SDA", x=float(i * 10)) for i in range(3))
        root = _sch(body)
        assert "LAY001" not in _codes(lint_schematic_layout(root))

    def test_label_four_times_warns(self) -> None:
        body = "\n".join(_label("SDA", x=float(i * 10)) for i in range(4))
        root = _sch(body)
        assert "LAY001" in _codes(lint_schematic_layout(root))

    def test_severity_is_warning(self) -> None:
        body = "\n".join(_label("SDA", x=float(i * 10)) for i in range(5))
        root = _sch(body)
        assert _sev(lint_schematic_layout(root), "LAY001") == _WARN

    def test_different_label_names_no_collision(self) -> None:
        body = "\n".join(_label(f"NET{i}") for i in range(5))
        root = _sch(body)
        assert "LAY001" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# 4.11 LAY002 — majority of wires are stub-length
# ---------------------------------------------------------------------------


class TestLAY002:
    def test_no_wires_no_issue(self) -> None:
        root = _sch()
        assert "LAY002" not in _codes(lint_schematic_layout(root))

    def test_mostly_stub_wires_warns(self) -> None:
        # 7 stub wires (5.08 mm) + 1 long wire — 87.5% stubs
        stubs = "\n".join(_wire(0.0, float(i), 0.0, float(i) + 5.08) for i in range(7))
        long_wire = _wire(0.0, 100.0, 0.0, 200.0)
        root = _sch(stubs + "\n" + long_wire)
        assert "LAY002" in _codes(lint_schematic_layout(root))

    def test_mostly_long_wires_no_issue(self) -> None:
        # 1 stub + 9 long wires — 10% stubs
        stub = _wire(0.0, 0.0, 0.0, 5.08)
        longs = "\n".join(_wire(0.0, float(i * 10), 0.0, float(i * 10) + 50.0) for i in range(9))
        root = _sch(stub + "\n" + longs)
        assert "LAY002" not in _codes(lint_schematic_layout(root))

    def test_severity_is_warning(self) -> None:
        stubs = "\n".join(_wire(0.0, float(i), 0.0, float(i) + 5.08) for i in range(8))
        root = _sch(stubs)
        assert _sev(lint_schematic_layout(root), "LAY002") == _WARN


# ---------------------------------------------------------------------------
# 4.12 LAY003 — overlapping symbols
# ---------------------------------------------------------------------------


class TestLAY003:
    def test_no_symbols_no_issue(self) -> None:
        root = _sch()
        assert "LAY003" not in _codes(lint_schematic_layout(root))

    def test_single_symbol_no_issue(self) -> None:
        root = _sch(_symbol_at(50.0, 50.0))
        assert "LAY003" not in _codes(lint_schematic_layout(root))

    def test_overlapping_symbols_warns(self) -> None:
        # Two symbols at the same position
        body = _symbol_at(50.0, 50.0) + "\n" + _symbol_at(50.0, 50.0)
        root = _sch(body)
        assert "LAY003" in _codes(lint_schematic_layout(root))

    def test_near_overlap_warns(self) -> None:
        # 2 mm apart — inside ±5.08 mm bounding box
        body = _symbol_at(50.0, 50.0) + "\n" + _symbol_at(52.0, 50.0)
        root = _sch(body)
        assert "LAY003" in _codes(lint_schematic_layout(root))

    def test_well_spaced_symbols_no_issue(self) -> None:
        # 50 mm apart in x — clearly outside bounding box
        body = _symbol_at(50.0, 50.0) + "\n" + _symbol_at(150.0, 50.0)
        root = _sch(body)
        assert "LAY003" not in _codes(lint_schematic_layout(root))

    def test_severity_is_warning(self) -> None:
        body = _symbol_at(50.0, 50.0) + "\n" + _symbol_at(50.0, 50.0)
        root = _sch(body)
        assert _sev(lint_schematic_layout(root), "LAY003") == _WARN


# ---------------------------------------------------------------------------
# 4.13 LAY004 — symbol outside A4 page bounds
# ---------------------------------------------------------------------------


class TestLAY004:
    def test_no_symbols_no_issue(self) -> None:
        root = _sch()
        assert "LAY004" not in _codes(lint_schematic_layout(root))

    def test_inside_bounds_no_issue(self) -> None:
        root = _sch(_symbol_at(100.0, 100.0))
        assert "LAY004" not in _codes(lint_schematic_layout(root))

    def test_negative_x_errors(self) -> None:
        root = _sch(_symbol_at(-1.0, 50.0))
        assert "LAY004" in _codes(lint_schematic_layout(root))

    def test_x_beyond_a4_errors(self) -> None:
        root = _sch(_symbol_at(300.0, 50.0))
        assert "LAY004" in _codes(lint_schematic_layout(root))

    def test_y_beyond_a4_errors(self) -> None:
        root = _sch(_symbol_at(100.0, 215.0))
        assert "LAY004" in _codes(lint_schematic_layout(root))

    def test_severity_is_error(self) -> None:
        root = _sch(_symbol_at(-10.0, -10.0))
        assert _sev(lint_schematic_layout(root), "LAY004") == _ERR


# ---------------------------------------------------------------------------
# 4.14 LAY005 — disconnected wire islands
# ---------------------------------------------------------------------------


class TestLAY005:
    def test_no_wires_no_issue(self) -> None:
        root = _sch()
        assert "LAY005" not in _codes(lint_schematic_layout(root))

    def test_single_wire_no_issue(self) -> None:
        root = _sch(_wire(0.0, 0.0, 10.0, 0.0))
        assert "LAY005" not in _codes(lint_schematic_layout(root))

    def test_connected_chain_no_issue(self) -> None:
        # Three wires forming a connected chain
        body = (
            _wire(0.0, 0.0, 10.0, 0.0)
            + "\n"
            + _wire(10.0, 0.0, 20.0, 0.0)
            + "\n"
            + _wire(20.0, 0.0, 30.0, 0.0)
        )
        root = _sch(body)
        assert "LAY005" not in _codes(lint_schematic_layout(root))

    def test_three_islands_warns(self) -> None:
        # Three completely disconnected wires (3 islands)
        body = (
            _wire(0.0, 0.0, 5.0, 0.0)
            + "\n"
            + _wire(50.0, 50.0, 60.0, 50.0)
            + "\n"
            + _wire(100.0, 100.0, 110.0, 100.0)
        )
        root = _sch(body)
        assert "LAY005" in _codes(lint_schematic_layout(root))

    def test_severity_is_warning(self) -> None:
        body = (
            _wire(0.0, 0.0, 5.0, 0.0)
            + "\n"
            + _wire(50.0, 50.0, 60.0, 50.0)
            + "\n"
            + _wire(100.0, 100.0, 110.0, 100.0)
        )
        root = _sch(body)
        assert _sev(lint_schematic_layout(root), "LAY005") == _WARN

    def test_two_islands_no_issue(self) -> None:
        # LAY_MAX_ISLANDS is 2; two disconnected wires is exactly at threshold
        body = _wire(0.0, 0.0, 5.0, 0.0) + "\n" + _wire(50.0, 50.0, 60.0, 50.0)
        root = _sch(body)
        assert "LAY005" not in _codes(lint_schematic_layout(root))


# ---------------------------------------------------------------------------
# 4.15 LAY rules silent on non-sch root
# ---------------------------------------------------------------------------


class TestLAYNonSchRoot:
    def test_pcb_root_returns_empty(self) -> None:
        root = parse("(kicad_pcb (version 1))")
        assert lint_schematic_layout(root) == []

    def test_non_list_node_returns_empty(self) -> None:
        root = AtomNode("hello")
        assert lint_schematic_layout(root) == []


# ---------------------------------------------------------------------------
# 4.16 LINT_SUGGESTIONS coverage
# ---------------------------------------------------------------------------


class TestLintSuggestionsForLAY:
    def test_all_lay_rules_have_suggestions(self) -> None:
        for code in ["LAY001", "LAY002", "LAY003", "LAY004", "LAY005"]:
            assert code in LINT_SUGGESTIONS, f"LINT_SUGGESTIONS missing {code}"
            assert LINT_SUGGESTIONS[code], f"LINT_SUGGESTIONS[{code!r}] is empty"


# ---------------------------------------------------------------------------
# 4.17 Graphviz layout cache helpers
# ---------------------------------------------------------------------------


def _simple_ir() -> CircuitIR:
    """Return a minimal two-component IR for cache tests."""
    components = [
        ComponentIR(ref="R1", symbol="Device:R", value="10k"),
        ComponentIR(ref="C1", symbol="Device:C", value="100n"),
    ]
    nets = [
        NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
        NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestGraphvizLayoutCacheHelpers:
    """Unit tests for the module-level cache helper functions."""

    def test_cache_key_is_stable(self) -> None:
        ir = _simple_ir()
        src = _gv_mod.build_dot_source(ir)
        key1 = _gv_mod.layout_cache_key(src)
        key2 = _gv_mod.layout_cache_key(src)
        assert key1 == key2
        assert len(key1) == 64  # SHA-256 hex

    def test_cache_key_changes_with_different_source(self) -> None:
        key_a = _gv_mod.layout_cache_key("digraph A {}")
        key_b = _gv_mod.layout_cache_key("digraph B {}")
        assert key_a != key_b

    def test_load_cache_miss_when_file_absent(self, tmp_path: Path) -> None:
        result = _gv_mod.load_layout_cache(tmp_path / "nonexistent.json", "anykey")
        assert result is None

    def test_cache_roundtrip(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (30.0, 50.0, None),
            "C1": (40.0, 60.0, None),
        }
        key = "deadbeef" * 8  # 64 hex chars

        _gv_mod.save_layout_cache(cache_file, key, positions)
        loaded = _gv_mod.load_layout_cache(cache_file, key)

        assert loaded is not None
        assert loaded["R1"][0] == pytest.approx(30.0)
        assert loaded["R1"][1] == pytest.approx(50.0)
        assert loaded["C1"][0] == pytest.approx(40.0)
        assert loaded["C1"][1] == pytest.approx(60.0)

    def test_load_cache_miss_on_key_mismatch(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        _gv_mod.save_layout_cache(cache_file, "key-A" * 12 + "key-", {"R1": (1.0, 2.0, None)})
        result = _gv_mod.load_layout_cache(cache_file, "key-B" * 12 + "key-")
        assert result is None

    def test_load_cache_miss_on_version_mismatch(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps({"version": 999, "key": "k", "positions": {}}), encoding="utf-8"
        )
        assert _gv_mod.load_layout_cache(cache_file, "k") is None

    def test_save_cache_silently_ignores_permission_error(self, tmp_path: Path) -> None:
        """save_layout_cache must not raise even if the file cannot be written."""
        cache_file: Path = tmp_path / "layout.json"
        with patch("pathlib.Path.write_text", side_effect=PermissionError("read-only")):
            # Should not raise.
            _gv_mod.save_layout_cache(cache_file, "k", {"R1": (1.0, 2.0, None)})


# ---------------------------------------------------------------------------
# 4.18 GraphvizLayoutEngine cache integration
# ---------------------------------------------------------------------------


class TestGraphvizLayoutEngineCache:
    """Integration tests for seed and cache on GraphvizLayoutEngine."""

    def test_cache_hit_skips_dot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a valid cache entry exists, _run_dot must not be called."""
        ir = _simple_ir()
        dot_source = _gv_mod.build_dot_source(ir)
        cache_key = _gv_mod.layout_cache_key(dot_source)
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (31.0, 51.0, None),
            "C1": (41.0, 61.0, None),
        }
        cache_file: Path = tmp_path / "layout.json"
        _gv_mod.save_layout_cache(cache_file, cache_key, positions)

        run_dot_called = False

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        result = engine.compute_symbol_positions(ir)

        assert not run_dot_called, "_run_dot was called despite a cache hit"
        assert result["R1"][0] == pytest.approx(31.0)
        assert result["C1"][1] == pytest.approx(61.0)

    def test_cache_written_after_dot_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a cache miss, the engine writes the new result to disk."""
        ir = _simple_ir()
        cache_file: Path = tmp_path / "layout.json"

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            return {"R1": (32.0, 52.0, None), "C1": (42.0, 62.0, None)}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        engine.compute_symbol_positions(ir)

        assert cache_file.exists(), "cache file was not written after dot run"

        # Second call with identical IR should hit the cache, not call dot.
        run_dot_called = False

        def fake_run_dot_2(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot_2)
        engine2 = _gv_mod.GraphvizLayoutEngine(dot_path="dot", cache_path=cache_file)
        engine2.compute_symbol_positions(ir)

        assert not run_dot_called, "second run should have been a cache hit"

    def test_no_cache_path_does_not_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without cache_path, no cache file is created."""

        ir = _simple_ir()

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            return {"R1": (1.0, 2.0, None), "C1": (3.0, 4.0, None)}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")  # no cache_path
        engine.compute_symbol_positions(ir)

        # No cache files should have appeared in cwd.
        assert not any(p.suffix == ".json" for p in Path().iterdir()), (
            "unexpected .json file created in cwd"
        )


# ---------------------------------------------------------------------------
# 4.19 Deterministic seed tests
# ---------------------------------------------------------------------------


class TestGraphvizLayoutSeed:
    """Verify -Gstart=<seed> is forwarded to dot subprocess."""

    def test_default_seed_in_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ir = _simple_ir()
        captured_cmd: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured_cmd.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="/usr/bin/dot")
        dot_source = _gv_mod.build_dot_source(ir)
        with contextlib.suppress(Exception):
            engine._run_dot(dot_source)

        assert captured_cmd, "subprocess.run was not called"
        assert "-Gstart=7" in captured_cmd[0], f"Expected '-Gstart=7' in cmd; got {captured_cmd[0]}"

    def test_custom_seed_in_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        ir = _simple_ir()
        captured_cmd: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            captured_cmd.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        engine = _gv_mod.GraphvizLayoutEngine(dot_path="/usr/bin/dot", seed=42)
        dot_source = _gv_mod.build_dot_source(ir)
        with contextlib.suppress(Exception):
            engine._run_dot(dot_source)

        assert captured_cmd, "subprocess.run was not called"
        assert "-Gstart=42" in captured_cmd[0], (
            f"Expected '-Gstart=42' in cmd; got {captured_cmd[0]}"
        )

    def test_make_layout_engine_forwards_seed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """make_layout_engine passes seed= to GraphvizLayoutEngine."""
        monkeypatch.setenv("GRAPHVIZ_DOT", "/usr/bin/dot")
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda: "/usr/bin/dot")
        engine = make_layout_engine(seed=99)
        assert isinstance(engine, _gv_mod.GraphvizLayoutEngine)
        assert engine._seed == 99  # noqa: SLF001

    def test_make_layout_engine_forwards_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """make_layout_engine passes cache_path= to GraphvizLayoutEngine."""
        cache_file: Path = tmp_path / "c.json"
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda: "/usr/bin/dot")
        engine = make_layout_engine(cache_path=cache_file)
        assert isinstance(engine, _gv_mod.GraphvizLayoutEngine)
        assert engine._cache_path == cache_file  # noqa: SLF001


# ---------------------------------------------------------------------------
# Phase 5.2 — find_dot_source (centralized binary discovery)
# ---------------------------------------------------------------------------


class TestFindDotSource:
    """Unit tests for :func:`~kicad_pcb.graphviz_layout.find_dot_source`."""

    def test_returns_none_when_no_dot(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Returns None when neither env var nor PATH provides dot."""
        monkeypatch.delenv("GRAPHVIZ_DOT", raising=False)
        monkeypatch.setattr(_gv_mod.shutil, "which", lambda _cmd: None)
        # Point bundled path to a location that doesn't exist.
        monkeypatch.setattr(_gv_mod, "_BUNDLED_DOT_PATH", tmp_path / "no_dot")
        assert _gv_mod.find_dot_source() is None

    def test_env_var_takes_precedence(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GRAPHVIZ_DOT env var is used and source is 'GRAPHVIZ_DOT'."""
        fake_dot = tmp_path / "dot"
        fake_dot.write_text("#!/bin/sh\n")
        fake_dot.chmod(0o755)
        monkeypatch.setenv("GRAPHVIZ_DOT", str(fake_dot))
        # Patch bundled path to non-existent so env var wins.
        bundled = tmp_path / "no_bundled"
        monkeypatch.setattr(_gv_mod, "_BUNDLED_DOT_PATH", bundled)

        result = _gv_mod.find_dot_source()
        assert result is not None
        path, source = result
        assert path == str(fake_dot)
        assert source == "GRAPHVIZ_DOT"

    def test_path_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to PATH when no env var set; source is 'PATH'."""
        monkeypatch.delenv("GRAPHVIZ_DOT", raising=False)
        bundled = tmp_path / "no_bundled"
        monkeypatch.setattr(_gv_mod, "_BUNDLED_DOT_PATH", bundled)
        fake_dot = tmp_path / "dot"
        fake_dot.write_text("#!/bin/sh\n")
        fake_dot.chmod(0o755)
        monkeypatch.setattr(_gv_mod.shutil, "which", lambda _cmd: str(fake_dot))

        result = _gv_mod.find_dot_source()
        assert result is not None
        path, source = result
        assert path == str(fake_dot)
        assert source == "PATH"

    def test_bundled_binary_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bundled binary is used first when present; source is 'bundled'."""
        bundled = tmp_path / "dot"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)
        monkeypatch.setattr(_gv_mod, "_BUNDLED_DOT_PATH", bundled)
        # Even if env var and PATH would return something else, bundled wins.
        monkeypatch.setenv("GRAPHVIZ_DOT", "/some/other/dot")

        result = _gv_mod.find_dot_source()
        assert result is not None
        path, source = result
        assert path == str(bundled)
        assert source == "bundled"


# ---------------------------------------------------------------------------
# Phase 4.7 — compute_orientations
# ---------------------------------------------------------------------------


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
    # CircuitIR requires at least one component and one net.  When the caller
    # provides an empty list (common in orientation tests that only care about
    # prefix-based rules), inject a harmless single-pin placeholder net so the
    # model validation passes without affecting adjacency calculations.
    if not ir_components:
        ir_components = [ComponentIR(ref="_DUMMY", symbol="_")]
    if not ir_nets:
        ir_nets = [NetIR(name="_NC", pins=[PinRefIR(ref=ir_components[0].ref, pin="1")])]
    return CircuitIR(version=version, components=ir_components, nets=ir_nets)


class TestComputeOrientations:
    """Unit tests for :func:`compute_orientations`."""

    def test_connector_always_zero(self) -> None:
        """Connector refs (J/CON/P/SJ/TJ) always get 0° regardless of neighbours."""
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x02"), ("R1", "Device:R")],
            [("SIG", [("J1", "1"), ("R1", "1")])],
        )
        positions = {"J1": (0.0, 0.0), "R1": (30.0, 0.0)}
        result = compute_orientations(ir, positions)
        assert result["J1"] == 0

    def test_opamp_always_zero(self) -> None:
        """Op-amp / IC refs always get 0°."""
        ir = _make_ir(
            [("U1", "Amplifier_Operational:TL071"), ("R1", "Device:R")],
            [("SIG", [("U1", "3"), ("R1", "1")])],
        )
        positions = {"U1": (0.0, 0.0), "R1": (0.0, 30.0)}
        result = compute_orientations(ir, positions)
        # U1 has a vertical neighbour but must stay 0° (IC rule)
        assert result["U1"] == 0

    def test_passive_horizontal_neighbours_gives_zero(self) -> None:
        """Passive with horizontally-offset neighbours → 0° (horizontal orientation)."""
        # J1 --- R1 --- R2, all in a horizontal line
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x02"), ("R1", "Device:R"), ("R2", "Device:R")],
            [
                ("A", [("J1", "1"), ("R1", "1")]),
                ("B", [("R1", "2"), ("R2", "1")]),
            ],
        )
        positions = {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "R2": (60.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0

    def test_passive_vertical_neighbours_gives_90(self) -> None:
        """Passive with vertically-offset neighbours → 90°."""
        ir = _make_ir(
            [
                ("U1", "Amplifier_Operational:TL071"),
                ("R1", "Device:R"),
                ("U2", "Amplifier_Operational:TL071"),
            ],
            [
                ("A", [("U1", "1"), ("R1", "1")]),
                ("B", [("R1", "2"), ("U2", "1")]),
            ],
        )
        # U1 above R1 above U2 — vertical arrangement
        positions = {"U1": (30.0, 0.0), "R1": (30.0, 20.0), "U2": (30.0, 40.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 90

    def test_default_zero_for_unknown_prefix(self) -> None:
        """Components with unrecognised prefix default to 0°."""
        ir = _make_ir([("XYZ1", "Some:Lib")], [])
        result = compute_orientations(ir, {"XYZ1": (0.0, 0.0)})
        assert result["XYZ1"] == 0

    def test_power_nets_excluded_from_neighbour_calc(self) -> None:
        """Power/GND connections do not influence passive rotation.

        R1 only connects to VCC and GND (power nets) — no signal neighbours
        → 0° (neither horizontal nor vertical bias).
        """
        ir = _make_ir(
            [("R1", "Device:R")],
            [
                ("VCC", [("R1", "1")]),
                ("GND", [("R1", "2")]),
            ],
        )
        positions = {"R1": (30.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0

    def test_isolated_passive_defaults_zero(self) -> None:
        """Passive with no neighbours at all gets 0°."""
        ir = _make_ir([("C1", "Device:C")], [])
        result = compute_orientations(ir, {"C1": (30.0, 30.0)})
        assert result["C1"] == 0

    def test_all_components_returned(self) -> None:
        """Every component in the IR appears in the result."""
        refs = ["J1", "U1", "R1", "C1", "XYZ1"]
        comps = [(r, "Lib:sym") for r in refs]
        ir = _make_ir(comps, [])
        positions = {r: (float(i) * 10, 0.0) for i, r in enumerate(refs)}
        result = compute_orientations(ir, positions)
        assert set(result) == set(refs)


# ---------------------------------------------------------------------------
# Phase 4 — Shunt topology orientation (topology-driven, not position-based)
# ---------------------------------------------------------------------------


class TestShuntOrientations:
    """compute_orientations detects shunt topology and forces 90° rotation.

    A passive is shunt when ≥1 pin connects to a power/ground net AND ≥1 pin
    connects to a signal net.  This covers bypass capacitors and pull-up /
    pull-down resistors.  Pure-signal passives continue to use the existing
    position-based heuristic.
    """

    def test_bypass_cap_gnd_is_90(self) -> None:
        """AC-bypass capacitor: one pin on a signal net, one pin on GND → 90°.

        This covers the classic audio-stage bypass cap (AUDIO_IN to GND) where
        one pin is in the signal path and the other drains to the ground rail.
        Note: a *power-supply* decoupling cap with VCC–GND pins has *both* pins
        on power nets and is NOT detected as shunt by this rule (see
        test_both_pins_power_only_stays_zero).
        """
        ir = _make_ir(
            [("C1", "Device:C"), ("U1", "Amplifier_Operational:TL071")],
            [
                # Signal net: shared between C1 pin 1 and the op-amp input.
                ("AUDIO_IN", [("C1", "1"), ("U1", "3")]),
                # Power net: C1 pin 2 drains to GND.
                ("GND", [("C1", "2")]),
            ],
        )
        # Positions irrelevant — shunt check fires before position heuristic.
        positions = {"C1": (50.0, 50.0), "U1": (50.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["C1"] == 90, (
            "AC-bypass cap (signal → GND) should be vertical (90°) regardless of position."
        )

    def test_pullup_resistor_vcc_is_90(self) -> None:
        """Pull-up resistor: one pin on VCC, one on a signal net → 90°."""
        ir = _make_ir(
            [("R1", "Device:R"), ("U1", "74xx:74HC74")],
            [
                ("VCC", [("R1", "1")]),
                ("nRESET", [("R1", "2"), ("U1", "4")]),
            ],
        )
        positions = {"R1": (40.0, 10.0), "U1": (60.0, 10.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 90, "Pull-up resistor (VCC → signal) should be vertical (90°)."

    def test_pulldown_resistor_gnd_is_90(self) -> None:
        """Pull-down resistor: one pin on GND, one on a signal net → 90°."""
        ir = _make_ir(
            [("R2", "Device:R"), ("U1", "74xx:74HC00")],
            [
                ("SIG", [("R2", "1"), ("U1", "1")]),
                ("GND", [("R2", "2")]),
            ],
        )
        positions = {"R2": (40.0, 20.0), "U1": (60.0, 20.0)}
        result = compute_orientations(ir, positions)
        assert result["R2"] == 90, "Pull-down resistor (signal → GND) should be vertical (90°)."

    def test_series_resistor_no_power_pin_uses_heuristic(self) -> None:
        """Series resistor with no power-pin uses the position heuristic (→ 0° when horizontal)."""
        ir = _make_ir(
            [("R1", "Device:R"), ("J1", "Connector:Conn"), ("U1", "Amplifier_Operational:TL071")],
            [
                ("IN", [("J1", "1"), ("R1", "1")]),
                ("MID", [("R1", "2"), ("U1", "3")]),
            ],
        )
        # Horizontal arrangement: position heuristic gives 0°.
        positions = {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "U1": (60.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0, (
            "Series resistor between horizontally-spaced components should stay 0°."
        )

    def test_both_pins_power_only_stays_zero(self) -> None:
        """Passive with BOTH pins on power nets (no signal pin) keeps 0°.

        This is the existing behaviour for test_power_nets_excluded_from_neighbour_calc
        and must not regress.  A resistor between VCC and GND is NOT a shunt in
        the signal-path sense.
        """
        ir = _make_ir(
            [("R1", "Device:R")],
            [
                ("VCC", [("R1", "1")]),
                ("GND", [("R1", "2")]),
            ],
        )
        positions = {"R1": (30.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["R1"] == 0, (
            "Passive with only power-net pins (no signal net) must keep 0° (no shunt detection)."
        )

    def test_shunt_fires_before_position_heuristic(self) -> None:
        """Shunt rule overrides position heuristic even when neighbours are horizontal.

        If C1 has one GND pin, it should be 90° regardless of whether the
        remaining signal-net neighbours are arranged horizontally or vertically.
        """
        ir = _make_ir(
            [
                ("C1", "Device:C"),
                ("U1", "Amplifier_Operational:TL071"),
                ("U2", "Amplifier_Operational:TL071"),
            ],
            [
                # Signal net connects C1 to two horizontally-offset op-amps.
                ("SIG", [("C1", "1"), ("U1", "6"), ("U2", "3")]),
                # Power net connects C1's second pin to GND.
                ("GND", [("C1", "2")]),
            ],
        )
        # U1 and U2 are far apart horizontally → position heuristic would give 0°.
        positions = {"C1": (50.0, 30.0), "U1": (0.0, 30.0), "U2": (100.0, 30.0)}
        result = compute_orientations(ir, positions)
        assert result["C1"] == 90, (
            "Shunt detection should override the position heuristic (GND pin present)."
        )


# ---------------------------------------------------------------------------
# Phase 0 — BFS tier assignment + directional DOT source (regression 0.3)
# ---------------------------------------------------------------------------


def _chain_ir(refs: list[str], net_names: list[str] | None = None) -> CircuitIR:
    """Build a linear chain IR: refs[0] — N0 — refs[1] — N1 — ... — refs[-1].

    Each successive pair of refs shares a signal net.  ``net_names`` may
    override the auto-generated ``NET0``, ``NET1``, … names.
    """
    if net_names is None:
        net_names = [f"NET{i}" for i in range(len(refs) - 1)]
    components = [ComponentIR(ref=r, symbol="Lib:sym", value="x") for r in refs]
    nets = [
        NetIR(
            name=net_names[i],
            pins=[PinRefIR(ref=refs[i], pin="1"), PinRefIR(ref=refs[i + 1], pin="2")],
        )
        for i in range(len(refs) - 1)
    ]
    return CircuitIR(version="1", components=components, nets=nets)


class TestAssignBfsTiers:
    """Unit tests for the BFS tier-assignment helper."""

    def test_linear_chain_connector_to_connector(self) -> None:
        """J1 → R1 → U1 → J2 should yield ascending tiers 0,1,2,3."""
        refs = ["J1", "R1", "U1", "J2"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        # J1 (connector, seed first) must be before R1 before U1 before J2.
        assert tiers["J1"] < tiers["R1"] < tiers["U1"] < tiers["J2"]

    def test_single_component_gets_tier_zero(self) -> None:
        refs = ["R1"]
        tiers = _gv_mod.assign_bfs_tiers(refs, [])
        assert tiers["R1"] == 0

    def test_isolated_component_defaults_to_zero(self) -> None:
        """A component with no signal-net connections gets tier 0."""
        refs = ["J1", "R_isolated"]
        ir = _chain_ir(["J1", "R1"])  # R_isolated not in ir nets
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert tiers.get("R_isolated", 0) == 0

    def test_no_connectors_all_refs_reachable(self) -> None:
        """When there are no connectors, BFS starts from all refs; all are assigned."""
        refs = ["R1", "R2", "R3"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert set(tiers) == set(refs)

    def test_output_connector_gets_higher_tier_than_ic(self) -> None:
        """J_IN → R1 → U1 → J_OUT: J_OUT tier must exceed U1 tier."""
        refs = ["J_IN", "R1", "U1", "J_OUT"]
        ir = _chain_ir(refs)
        signal_nets = [n for n in ir.nets if len(n.pins) >= 2]
        tiers = _gv_mod.assign_bfs_tiers(refs, signal_nets)
        assert tiers["J_OUT"] > tiers["U1"]


class TestBuildDotSourceSignalFlow:
    """Regression tests for the fixed _build_dot_source (Phase 0)."""

    def _dot(self, ir: CircuitIR) -> str:
        return _gv_mod.build_dot_source(ir)

    def test_has_directional_net_hub_edges(self) -> None:
        """DOT source for a J1→R1 chain has upstream→net AND net→downstream edges."""
        ir = _chain_ir(["J1", "R1"])
        src = self._dot(ir)
        # J1 is the alphabetically-first connector (seed tier 0).
        # R1 is tier 1.  Expected: J1 -> net_NET0; net_NET0 -> R1
        assert "J1 -> net_NET0" in src or "J1->net_NET0" in src
        assert "net_NET0 -> R1" in src or "net_NET0->R1" in src

    def test_no_edges_are_all_into_net_nodes(self) -> None:
        """In the old bipartite model every edge was comp→net with no return edges.
        After the fix, at least one net_* node must have an outgoing edge to a comp.
        """
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        # Find lines where a net_* node is the *source* of an edge.
        net_source_lines = [line for line in src.splitlines() if line.strip().startswith("net_")]
        assert net_source_lines, "No net→component edges found; old bipartite model still in use"

    def test_rank_source_subgraph_present_for_input_connector(self) -> None:
        """The first-tier group must use rank=source."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        assert "rank=source" in src

    def test_rank_sink_subgraph_present_for_output_connector(self) -> None:
        """The last-tier group must use rank=sink."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        assert "rank=sink" in src

    def test_power_only_components_in_cluster_power(self) -> None:
        """Components connected only via power nets must appear in cluster_power."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        src = self._dot(ir)
        assert "cluster_power" in src
        assert "rank=max" in src

    def test_tier_separation_via_rank_same_subgraphs(self) -> None:
        """For a 4-component chain, at least 3 separate rank subgraphs are emitted."""
        ir = _chain_ir(["J1", "R1", "U1", "J2"])
        src = self._dot(ir)
        rank_lines = [ln for ln in src.splitlines() if "rank=" in ln]
        # Expect at least rank=source, one rank=same (U1 or R1), rank=sink
        assert len(rank_lines) >= 3, (
            f"Expected ≥3 rank= lines for a 4-component chain, got {len(rank_lines)}: "
            + repr(rank_lines)
        )

    def test_ranksep_is_increased(self) -> None:
        """ranksep must be at least 1.5 to give adequate tier spacing."""
        ir = _chain_ir(["J1", "R1"])
        src = self._dot(ir)
        # e.g. "  ranksep=1.5;"
        ranksep_lines = [ln for ln in src.splitlines() if "ranksep" in ln]
        assert ranksep_lines, "ranksep directive missing from DOT source"
        val_str = ranksep_lines[0].split("=")[1].strip().rstrip(";")
        assert float(val_str) >= 1.5, f"ranksep too small: {val_str}"


# ---------------------------------------------------------------------------
# Phase 3 — Decoupling cap co-location (Rule §5)
# ---------------------------------------------------------------------------


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


class TestFindDecouplingCaps:
    """Unit tests for _find_decoupling_caps()."""

    def test_cap_with_one_signal_pin_detected(self) -> None:
        """C1 with VCC_LOCAL (signal) + GND (power) → detected as decoupling cap for U1."""
        ir = _decoupling_ir()
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            f"Expected C1 to be mapped to U1 as decoupling cap, got: {result}"
        )

    def test_true_bypass_cap_not_detected(self) -> None:
        """C1 with both VCC and GND (both power nets) → not treated as decoupling cap."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(name="VCC", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="C1", pin="1")]),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, f"Expected empty map for true bypass cap, got: {result}"

    def test_cap_with_only_connector_neighbour_not_detected(self) -> None:
        """If only connectors share C1's signal net, no IC is associated → not detected."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="In"),
            ComponentIR(ref="C1", symbol="Device:C", value="10n"),
        ]
        nets = [
            # VCC_EXT is not a power net by regex, so it's a signal net.
            NetIR(
                name="VCC_EXT",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="C1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, (
            f"Expected empty map when only connector shares signal net, got: {result}"
        )

    def test_non_capacitor_ref_not_detected(self) -> None:
        """R1 (resistor) with one signal pin and one power pin → NOT detected as decoupling."""
        components = [
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
        ]
        nets = [
            NetIR(
                name="VBIAS",
                pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="U1", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="R1", pin="2")]),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {}, f"Expected empty map for resistor (not a capacitor), got: {result}"


class TestDecouplingCapCoLocation:
    """Integration tests for decoupling cap co-location in DOT source and post-snap."""

    def test_invisible_edge_in_dot_source(self) -> None:
        """DOT source must contain an invisible edge from the decoupling cap to its IC."""
        ir = _decoupling_ir()
        decoupling_map = _gv_mod.find_decoupling_caps(ir)
        assert decoupling_map, "pre-condition: decoupling_map should not be empty"

        src = _gv_mod.build_dot_source(ir, decoupling_map=decoupling_map)
        # Expect: "  C1 -> U1 [style=invis, weight=10];"
        assert "C1 -> U1 [style=invis" in src, (
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

    def test_dot_source_unchanged_without_decoupling_map(self) -> None:
        """build_dot_source without decoupling_map must not contain invisible edges."""
        ir = _decoupling_ir()
        src = _gv_mod.build_dot_source(ir)  # no decoupling_map kwarg
        assert "style=invis" not in src, (
            "Unexpected invisible edge in DOT source when no decoupling_map was supplied"
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
