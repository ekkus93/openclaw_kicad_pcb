"""Phase 4 layout + wiring engine tests.

Covers:
- 4.1  LayoutEngine Protocol factory (make_layout_engine)
- 4.2  NoneLayoutEngine: returns origin for every component
- 4.4  Router 2-pin direct route: wire segments emitted, no labels
- 4.5  Router hub route (3-pin): centroid junction emitted, no labels
- 4.6  Router power-net: PowerSymbolPlacement per pin (Phase 3), no local labels
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
import itertools
import json
import math
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import kicad_pcb.graphviz_layout as _gv_mod
import pytest
from kicad_pcb.block_detection import BlockLayout, BlockRole
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._project import minimal_schematic_text
from kicad_pcb.component_types import component_type
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.graphviz_layout.snap import (
    ORIGIN_X,
    ORIGIN_Y,
    PAGE_MAX_X,
    PAGE_MAX_Y,
    _center_ics_in_columns,
    _clamp_to_page,
    _remediate_crossings,
    _spread_x_columns,
)
from kicad_pcb.layout import (
    GRID_COL_MM,
    ComponentAnnotation,
    StereoChannel,
    build_signal_adjacency,
    compute_affinity_groups,
    compute_orientations,
    count_wire_crossings,
    detect_stereo_channels,
    find_feedback_paths,
)
from kicad_pcb.layout_engine import NoneLayoutEngine, make_layout_engine
from kicad_pcb.lint import LINT_SUGGESTIONS, LintSeverity, lint_schematic_layout
from kicad_pcb.router import (
    MAX_DIRECT_WIRE_MM,
    SYMBOL_HALF_SIZE_MM,
    LabelPolicy,
    NetRouting,
    PowerSymbolPlacement,
    WireSegment,
    _hub_route,
    _is_power_net_name,
    detect_body_crossings,
    route_nets,
    write_routing,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import AtomNode, ListNode, StringNode
from kicad_pcb.sexpr.utils import find_first
from kicad_pcb.tier import IcUnitGroup, assign_ic_units_to_tiers, assign_tiers

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
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda **_kwargs: None)
        with pytest.raises(RuntimeError, match="dot.*not found"):
            make_layout_engine()

    def test_strict_raises_when_graphviz_dot_env_invalid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """strict mode raises when GRAPHVIZ_DOT is set but invalid."""
        fake_path_dot = tmp_path / "dot"
        fake_path_dot.write_text("#!/bin/sh\n")
        fake_path_dot.chmod(0o755)
        monkeypatch.setenv("GRAPHVIZ_DOT", str(tmp_path / "missing-dot"))
        monkeypatch.setattr(_gv_mod.shutil, "which", lambda _cmd: str(fake_path_dot))
        with pytest.raises(UserError, match="GRAPHVIZ_DOT") as exc_info:
            make_layout_engine(strict=True)
        assert exc_info.value.code == ErrorCode.TOOL_ERROR


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

    def test_power_net_emits_power_symbols(self) -> None:
        """Power net must produce PowerSymbolPlacements (1 per cluster, Phase 5.1)."""
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        # Phase 5.1: nearby pins (50,110) and (80,110) are 30mm apart,
        # within _POWER_CLUSTER_RADIUS_MM=40mm, so they share 1 symbol.
        assert len(routing.power_symbols) >= 1, (
            f"Expected at least 1 PowerSymbolPlacement for GND net; got {routing.power_symbols}"
        )
        assert len(routing.power_symbols) <= 2, (  # noqa: PLR2004
            f"Expected at most 2 PowerSymbolPlacements; got {routing.power_symbols}"
        )

    def test_power_symbols_net_name_matches(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert all(p.net_name == "GND" for p in routing.power_symbols)

    def test_power_net_no_global_labels(self) -> None:
        """Power nets must NOT produce GlobalLabelPlacements (Phase 3 change)."""
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.global_labels == [], (
            f"Power nets must not use global_labels after Phase 3; got {routing.global_labels}"
        )

    def test_power_net_no_local_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.labels == []

    def test_power_net_clustering_reduces_symbols(self) -> None:
        """Phase 5.1: clustered power pins share symbols, reducing clutter."""
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        # 2 pins within 40mm → 1 cluster → 1 power symbol (down from 2)
        assert len(routing.power_symbols) == 1, (
            f"Expected 1 clustered power symbol; got {len(routing.power_symbols)}"
        )
        # All pins must still have bind markers (electrical connectivity)
        assert len(routing.bind_markers) == 2, (  # noqa: PLR2004
            f"Expected 2 bind markers (one per pin); got {len(routing.bind_markers)}"
        )
        assert all(bm.net_name == "GND" for bm in routing.bind_markers)


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
        # DEFAULT_LABEL_POLICY caps global labels at max_global_labels_per_net=4
        # (Phase 2.3: label duplication limits).  Use LabelPolicy(999) to bypass.
        assert len(routing.global_labels) == 4

    def test_high_fanout_no_local_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert routing.labels == []

    def test_high_fanout_net_name_in_global_labels(self) -> None:
        ir, endpoints = self._make_ir_and_endpoints()
        routing = route_nets(ir=ir, pin_endpoints=endpoints)
        assert all(g.name == "DATABUS" for g in routing.global_labels)


class TestRouteNetsStrictMode:
    def test_strict_mode_raises_for_unknown_pin_endpoints(self) -> None:
        ir = _minimal_ir(
            refs=["R1", "R2"],
            nets=[
                {
                    "name": "SIG",
                    "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}],
                }
            ],
        )
        endpoints = {
            ("R1", "1"): (10.0, 20.0, 0.0),
        }

        with pytest.raises(UserError) as exc_info:
            route_nets(ir=ir, pin_endpoints=endpoints, strict=True)

        assert exc_info.value.code == ErrorCode.PIN_INVALID
        assert exc_info.value.details["net_name"] == "SIG"

    def test_non_strict_mode_keeps_offcanvas_fallback_for_unknown_pins(self) -> None:
        ir = _minimal_ir(
            refs=["R1", "R2"],
            nets=[
                {
                    "name": "SIG",
                    "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}],
                }
            ],
        )
        endpoints = {
            ("R1", "1"): (10.0, 20.0, 0.0),
        }

        routing = route_nets(ir=ir, pin_endpoints=endpoints)

        assert len(routing.labels) >= 1
        assert any(marker.ref == "R2" and marker.pin == "1" for marker in routing.bind_markers)


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

    def test_x_beyond_page_errors(self) -> None:
        root = _sch(_symbol_at(450.0, 50.0))
        assert "LAY004" in _codes(lint_schematic_layout(root))

    def test_y_beyond_page_errors(self) -> None:
        root = _sch(_symbol_at(100.0, 310.0))
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

    def test_save_cache_permission_error_raises(self, tmp_path: Path) -> None:
        """save_layout_cache must fail fast when the file cannot be written."""
        cache_file: Path = tmp_path / "layout.json"
        with (
            patch("pathlib.Path.write_text", side_effect=PermissionError("read-only")),
            pytest.raises(RuntimeError, match="Failed to write layout cache"),
        ):
            _gv_mod.save_layout_cache(cache_file, "k", {"R1": (1.0, 2.0, None)})

    def test_load_cache_invalid_json_raises(self, tmp_path: Path) -> None:
        """Invalid cache JSON must raise RuntimeError (no silent cache bypass)."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text("{not-json", encoding="utf-8")

        with pytest.raises(RuntimeError, match="Invalid JSON in layout cache"):
            _gv_mod.load_layout_cache(cache_file, "k")

    def test_load_cache_invalid_positions_shape_raises(self, tmp_path: Path) -> None:
        """Malformed positions payload must raise RuntimeError."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps({"version": 1, "key": "k", "positions": {"R1": "bad"}}),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="Invalid layout cache entry"):
            _gv_mod.load_layout_cache(cache_file, "k")


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

        before_json = {p.resolve() for p in Path().iterdir() if p.suffix == ".json"}
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")  # no cache_path
        engine.compute_symbol_positions(ir)

        after_json = {p.resolve() for p in Path().iterdir() if p.suffix == ".json"}
        created_json = sorted(str(p) for p in (after_json - before_json))
        assert not created_json, f"unexpected .json file created in cwd: {created_json}"


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
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda **_kwargs: "/usr/bin/dot")
        engine = make_layout_engine(seed=99)
        assert isinstance(engine, _gv_mod.GraphvizLayoutEngine)
        assert engine._seed == 99  # noqa: SLF001

    def test_make_layout_engine_forwards_cache_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """make_layout_engine passes cache_path= to GraphvizLayoutEngine."""
        cache_file: Path = tmp_path / "c.json"
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda **_kwargs: "/usr/bin/dot")
        engine = make_layout_engine(cache_path=cache_file)
        assert isinstance(engine, _gv_mod.GraphvizLayoutEngine)
        assert engine._cache_path == cache_file  # noqa: SLF001

    def test_make_layout_engine_forwards_strict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """make_layout_engine passes strict= to GraphvizLayoutEngine."""
        monkeypatch.setattr(_gv_mod, "find_dot_binary", lambda **_kwargs: "/usr/bin/dot")
        engine = make_layout_engine(strict=True)
        assert isinstance(engine, _gv_mod.GraphvizLayoutEngine)
        assert engine._strict is True  # noqa: SLF001


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

    def test_invalid_env_var_falls_back_to_path_non_strict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid GRAPHVIZ_DOT falls back to PATH in default (non-strict) mode."""
        monkeypatch.setenv("GRAPHVIZ_DOT", str(tmp_path / "missing-dot"))
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

    def test_invalid_env_var_raises_in_strict_mode(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Invalid GRAPHVIZ_DOT raises in strict mode instead of falling back."""
        monkeypatch.setenv("GRAPHVIZ_DOT", str(tmp_path / "missing-dot"))
        bundled = tmp_path / "no_bundled"
        monkeypatch.setattr(_gv_mod, "_BUNDLED_DOT_PATH", bundled)
        with pytest.raises(UserError, match="GRAPHVIZ_DOT") as exc_info:
            _gv_mod.find_dot_source(strict=True)
        assert exc_info.value.code == ErrorCode.TOOL_ERROR

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
# Phase 4.1 — Connector orientation (tier-driven) and diode explicit 0°
# ---------------------------------------------------------------------------


class TestConnectorOrientations:
    """compute_orientations uses tier info to set connector direction.

    Input connectors (tier 0) get 0° so their pins point right into the
    circuit.  Output connectors (max tier) get 180° so their pins point
    left, back toward the circuit.  Intermediate connectors (if any)
    default to 0°.  When no tiers dict is passed the old behaviour
    (always 0°) is preserved for backward compatibility.
    """

    # ------------------------------------------------------------------
    # Shared fixture: J1 → R1 → U1 → J2 linear chain
    # ------------------------------------------------------------------

    @staticmethod
    def _chain_ir_4() -> CircuitIR:
        """J1 — NET0 — R1 — NET1 — U1 — NET2 — J2."""
        return _make_ir(
            [
                ("J1", "Connector_Generic:Conn_01x02"),
                ("R1", "Device:R"),
                ("U1", "Amplifier_Operational:TL071"),
                ("J2", "Connector_Generic:Conn_01x02"),
            ],
            [
                ("NET0", [("J1", "1"), ("R1", "1")]),
                ("NET1", [("R1", "2"), ("U1", "3")]),
                ("NET2", [("U1", "6"), ("J2", "1")]),
            ],
        )

    @staticmethod
    def _tiers_4() -> dict[str, int]:
        """Tier map for the 4-component chain: J1=0, R1=1, U1=2, J2=3."""
        return {"J1": 0, "R1": 1, "U1": 2, "J2": 3}

    @staticmethod
    def _positions_4() -> dict[str, tuple[float, float]]:
        return {"J1": (0.0, 30.0), "R1": (30.0, 30.0), "U1": (60.0, 30.0), "J2": (90.0, 30.0)}

    # ------------------------------------------------------------------

    def test_input_connector_orientation_is_0(self) -> None:
        """Input connector at tier 0 must be 0° (pins point right)."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        assert result["J1"] == 0, f"Input connector J1 (tier 0) should be 0°, got {result['J1']}"

    def test_output_connector_orientation_is_180(self) -> None:
        """Output connector at max tier must be 180° (pins point left)."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        assert result["J2"] == 180, (
            f"Output connector J2 (tier 3 = max) should be 180°, got {result['J2']}"
        )

    def test_non_connector_components_unaffected_by_tiers(self) -> None:
        """Passing tiers must not change orientation of non-connector components."""
        ir = self._chain_ir_4()
        result_no_tiers = compute_orientations(ir, self._positions_4())
        result_with_tiers = compute_orientations(ir, self._positions_4(), tiers=self._tiers_4())
        for ref in ("R1", "U1"):
            assert result_no_tiers[ref] == result_with_tiers[ref], (
                f"{ref} orientation changed when tiers were added: "
                f"{result_no_tiers[ref]} → {result_with_tiers[ref]}"
            )

    def test_connector_without_tiers_defaults_to_zero(self) -> None:
        """When tiers=None (backward compat), all connectors are 0°."""
        ir = self._chain_ir_4()
        result = compute_orientations(ir, self._positions_4(), tiers=None)
        assert result["J1"] == 0, "J1 should be 0° when no tiers provided"
        assert result["J2"] == 0, "J2 should be 0° without tiers (no 180° flip)"

    def test_single_connector_circuit_stays_zero(self) -> None:
        """A circuit with only one connector (max_tier == 0) keeps 0°.

        When all connectors are at tier 0 and max_tier is 0 the output
        connector guard ``_max_tier > 0`` prevents a false 180° assignment.
        """
        ir = _make_ir(
            [("J1", "Connector_Generic:Conn_01x01"), ("R1", "Device:R")],
            [("NET", [("J1", "1"), ("R1", "1")])],
        )
        tiers = {"J1": 0, "R1": 0}
        result = compute_orientations(ir, {"J1": (0.0, 0.0), "R1": (30.0, 0.0)}, tiers=tiers)
        assert result["J1"] == 0, "Single-tier connector should never be 180°"

    def test_diode_always_zero(self) -> None:
        """Diode D* is always 0° (anode left, cathode right)."""
        ir = _make_ir(
            [("D1", "Device:D"), ("J1", "Connector_Generic:Conn_01x01")],
            [("SIGNAL", [("D1", "A"), ("J1", "1")])],
        )
        tiers = {"D1": 1, "J1": 0}
        positions = {"D1": (30.0, 30.0), "J1": (0.0, 30.0)}
        result = compute_orientations(ir, positions, tiers=tiers)
        assert result["D1"] == 0, f"Diode D1 should always be 0°, got {result['D1']}"

    def test_diode_zero_regardless_of_tiers(self) -> None:
        """Diode orientation is 0° with or without a tiers dict."""
        ir = _make_ir(
            [("D2", "Device:D_Schottky")],
            [("ANODE", [("D2", "A")])],
        )
        pos = {"D2": (30.0, 30.0)}
        assert compute_orientations(ir, pos)["D2"] == 0
        assert compute_orientations(ir, pos, tiers={"D2": 2})["D2"] == 0


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

    def test_build_dot_source_with_affinity_order_uses_specified_order(self) -> None:
        """affinity_order overrides alphabetical ordering within rank=same blocks."""
        # Three-tier circuit: J1 (tier 0, connector) → A_R and Z_R (tier 1,
        # rank=same) → J2 (tier 2, connector).  Reverse-alphabetical affinity
        # order for tier 1 should put Z_R before A_R in the DOT output.
        ir = _two_same_tier_ir()
        affinity_order = {1: ["Z_R", "A_R"]}
        src = _gv_mod.build_dot_source(ir, affinity_order=affinity_order)
        same_block_refs = _extract_rank_same_refs(src)
        assert same_block_refs == [
            "Z_R",
            "A_R",
        ], f"Expected Z_R before A_R with affinity_order; got {same_block_refs}"

    def test_build_dot_source_without_affinity_order_emits_alphabetical(self) -> None:
        """Without affinity_order the fallback sorts refs alphabetically per tier."""
        ir = _two_same_tier_ir()
        src = _gv_mod.build_dot_source(ir)
        same_block_refs = _extract_rank_same_refs(src)
        assert same_block_refs == [
            "A_R",
            "Z_R",
        ], f"Expected alphabetical A_R, Z_R without affinity_order; got {same_block_refs}"


# ---------------------------------------------------------------------------
# Helpers for TestBuildDotSourceSignalFlow (affinity_order tests)
# ---------------------------------------------------------------------------


def _two_same_tier_ir() -> CircuitIR:
    """Three-tier circuit; A_R and Z_R are both in tier 1 (rank=same).

    Topology:
      J1 → NET_A → A_R ─┐
      J1 → NET_Z → Z_R ─┴─ NET_OUT → J2
    """
    components = [
        ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
        ComponentIR(ref="A_R", symbol="Device:R", value="10k"),
        ComponentIR(ref="Z_R", symbol="Device:R", value="10k"),
        ComponentIR(ref="J2", symbol="Device:Conn", value="Output"),
    ]
    nets = [
        NetIR(
            name="NET_A",
            pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="A_R", pin="1")],
        ),
        NetIR(
            name="NET_Z",
            pins=[PinRefIR(ref="J1", pin="2"), PinRefIR(ref="Z_R", pin="1")],
        ),
        NetIR(
            name="NET_OUT",
            pins=[
                PinRefIR(ref="A_R", pin="2"),
                PinRefIR(ref="Z_R", pin="2"),
                PinRefIR(ref="J2", pin="1"),
            ],
        ),
    ]
    return CircuitIR(version="1", components=components, nets=nets)


def _extract_rank_same_refs(dot_src: str) -> list[str]:
    """Return the list of component refs inside the first rank=same subgraph block."""
    lines = dot_src.splitlines()
    same_idx: int | None = None
    for i, ln in enumerate(lines):
        if "rank=same" in ln:
            same_idx = i
            break
    assert same_idx is not None, "No rank=same block found in DOT source"
    refs: list[str] = []
    for ln in lines[same_idx + 1 :]:
        stripped = ln.strip()
        if stripped == "}":
            break
        # Exclude lines that are only directives (rank=…, etc.)
        if stripped and not stripped.startswith("rank"):
            refs.append(stripped.rstrip(";"))
    return refs


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


class TestApplyPostLayoutSnaps:
    """Tests for :func:`apply_post_layout_snaps` in graphviz_layout."""

    def _simple_ir(self) -> CircuitIR:
        """Minimal IR: one connector, one resistor, one power symbol."""
        return CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="#PWR01", symbol="power:VCC", value="VCC"),
            ],
            nets=[
                NetIR(
                    name="NET",
                    pins=[
                        PinRefIR(ref="J1", pin="1"),
                        PinRefIR(ref="R1", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="#PWR01", pin="1"),
                    ],
                ),
            ],
        )

    def test_snap_order_power_before_feedback(self) -> None:
        """Power snap must run before feedback snap.

        A ``#PWR`` VCC symbol must be clamped to ``ORIGIN_Y`` by the power
        snap pass even when a feedback ref shares the same x-column.  The
        feedback snap only moves non-``#PWR`` refs, so the power-snap result
        is preserved.
        """
        ir = self._simple_ir()
        # Place #PWR01 at a y somewhere in the middle of the page.
        positions: dict[str, tuple[float, float, float | None]] = {
            "#PWR01": (50.0, 120.0, None),
            "R1": (50.0, 100.0, None),
            "J1": (30.48, 80.0, None),
        }
        # Treat R1 as a feedback ref so the feedback pass attempts to move it.
        annotations = {"R1": ComponentAnnotation(feedback=True)}
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R1"},
            annotations=annotations,
            channels={"J1": "mono", "R1": "mono", "#PWR01": "mono"},
            decoupling_map={},
        )
        # Power snap: #PWR01 (VCC) → top row = ORIGIN_Y.
        assert result["#PWR01"][1] == pytest.approx(_gv_mod.ORIGIN_Y), (
            f"#PWR01 must be clamped to ORIGIN_Y={_gv_mod.ORIGIN_Y} by power snap, "
            f"got {result['#PWR01'][1]}"
        )

    def test_snap_skips_empty_feedback_refs(self) -> None:
        """Passing feedback_refs=set() must not raise and must return valid positions."""
        ir = self._simple_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 80.0, None),
            "R1": (50.0, 100.0, None),
        }
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={"J1": "mono", "R1": "mono"},
            decoupling_map={},
        )
        # All refs must still be present; no exception raised.
        assert set(result.keys()) == {"J1", "R1"}

    def test_snap_skips_mono_channels(self) -> None:
        """All-mono channels must not trigger a stereo split.

        With no L/R channels present, ``_apply_stereo_split`` is skipped.
        However, ``_snap_connectors_to_ic_y`` (Rule 2) still runs and snaps
        J1's y-coordinate to the median y of its signal-net neighbours (R1).
        """
        ir = self._simple_ir()
        # Place components at exact grid positions.
        positions: dict[str, tuple[float, float, float | None]] = {
            "J1": (30.48, 50.80, None),
            "R1": (50.80, 76.20, None),
        }
        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={"J1": "mono", "R1": "mono"},
            decoupling_map={},
        )
        # No stereo split — R1 (non-connector) y stays unchanged.
        assert result["R1"][1] == pytest.approx(positions["R1"][1])
        # Connector J1 is snapped to R1's y by _snap_connectors_to_ic_y (Rule 2).
        assert result["J1"][1] == pytest.approx(76.20)

    def test_feedback_falls_back_to_any_neighbor_non_strict(self) -> None:
        """Non-strict mode preserves fallback from IC/connector anchor to any neighbor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"R_FB"},
            annotations=annotations,
            channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
            decoupling_map={},
        )
        anchor_y_after_grid = round(round(80.0 / 1.27) * 1.27, 2)
        expected_y = round(anchor_y_after_grid - _gv_mod.GRID_ROW_MM, 2)
        assert result["R_FB"][1] == pytest.approx(expected_y)

    def test_feedback_fallback_raises_in_strict_mode(self) -> None:
        """Strict mode raises when a feedback component has no IC/connector anchor."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R_FB", symbol="Device:R", value="100k"),
                ComponentIR(ref="R1", symbol="Device:R", value="10k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(name="N1", pins=[PinRefIR(ref="R_FB", pin="1"), PinRefIR(ref="R1", pin="1")]),
                NetIR(name="N2", pins=[PinRefIR(ref="R_FB", pin="2"), PinRefIR(ref="R2", pin="1")]),
            ],
        )
        positions: dict[str, tuple[float, float, float | None]] = {
            "R_FB": (50.8, 120.0, None),
            "R1": (50.8, 80.0, None),
            "R2": (76.2, 100.0, None),
        }
        annotations = {"R_FB": ComponentAnnotation(feedback=True)}

        with pytest.raises(UserError, match="no IC/connector anchor") as exc_info:
            _gv_mod.apply_post_layout_snaps(
                positions,
                ir,
                feedback_refs={"R_FB"},
                annotations=annotations,
                channels={"R_FB": "mono", "R1": "mono", "R2": "mono"},
                decoupling_map={},
                strict=True,
            )
        assert exc_info.value.code == ErrorCode.IR_SEMANTIC_INVALID

    def test_opamp_local_rules_input_output_feedback_decoupling(self) -> None:
        """Phase 4.1: op-amp neighborhood should stage local roles clearly."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]
                ),
                NetIR(
                    name="FB_B", pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="RFB", pin="2")]
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="ROUT", pin="1"),
                        PinRefIR(ref="JOUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="GND", pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        # Deliberately scrambled initial placement.
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (100.0, 100.0, None),
            "RIN": (120.0, 70.0, None),
            "ROUT": (80.0, 135.0, None),
            "RFB": (145.0, 70.0, None),
            "CDEC": (70.0, 150.0, None),
            "JIN": (60.0, 70.0, None),
            "JOUT": (150.0, 135.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={
                "U1": "mono",
                "RIN": "mono",
                "ROUT": "mono",
                "RFB": "mono",
                "CDEC": "mono",
                "JIN": "mono",
                "JOUT": "mono",
            },
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rin_x, _rin_y, _ = result["RIN"]
        rout_x, _rout_y, _ = result["ROUT"]
        rfb_x, rfb_y, _ = result["RFB"]
        cdec_x, cdec_y, _ = result["CDEC"]

        assert rin_x < ux, f"Input-side component RIN should be left of U1: {result}"
        assert rout_x > ux, f"Output-side component ROUT should be right of U1: {result}"
        assert math.isclose(rfb_x, ux, abs_tol=0.01), (
            f"Feedback component RFB should stay in U1 column: {result}"
        )
        assert cdec_y < uy, f"Decoupling CDEC should be above U1 (power side): {result}"
        assert math.isclose(cdec_x, ux, abs_tol=0.01), (
            f"Decoupling CDEC should align to U1 x-column: {result}"
        )
        assert not math.isclose(rfb_y, cdec_y, abs_tol=0.01), (
            "Feedback and decoupling parts should not occupy the same y-slot"
        )

    def test_opamp_local_rules_separate_support_roles(self) -> None:
        """Phase 4.2: support roles should form distinct local clusters."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="22p"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
                ComponentIR(ref="CDEC1", symbol="Device:C", value="100n"),
                ComponentIR(ref="CDEC2", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="FB_B",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_C",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
                NetIR(
                    name="VCC",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="CDEC1", pin="1"),
                        PinRefIR(ref="CDEC2", pin="1"),
                    ],
                ),
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="CDEC1", pin="2"),
                        PinRefIR(ref="CDEC2", pin="2"),
                    ],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("CDEC1", BlockRole.DECOUPLING)
        block_layout.add_assignment("CDEC2", BlockRole.DECOUPLING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (110.0, 100.0, None),
            "CIN": (145.0, 70.0, None),
            "RIN": (150.0, 80.0, None),
            "RFB": (170.0, 65.0, None),
            "CFB": (170.0, 140.0, None),
            "ROUT": (70.0, 110.0, None),
            "COUT": (70.0, 120.0, None),
            "CDEC1": (80.0, 150.0, None),
            "CDEC2": (80.0, 160.0, None),
            "JIN": (55.0, 75.0, None),
            "JOUT": (190.0, 120.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={
                "RFB": ComponentAnnotation(feedback=True),
                "CFB": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC1": "U1", "CDEC2": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        input_refs = ["CIN", "RIN"]
        output_refs = ["ROUT", "COUT"]
        feedback_refs = ["RFB", "CFB"]
        dec_refs = ["CDEC1", "CDEC2"]

        assert all(result[r][0] < ux for r in input_refs), (
            f"Input support should be left of U1: {result}"
        )
        assert all(result[r][0] > ux for r in output_refs), (
            f"Output support should be right of U1: {result}"
        )
        assert all(math.isclose(result[r][0], ux, abs_tol=0.01) for r in feedback_refs), (
            f"Feedback support should stay in op-amp column: {result}"
        )
        assert all(math.isclose(result[r][0], ux, abs_tol=0.01) for r in dec_refs), (
            f"Decoupling support should align to op-amp column: {result}"
        )

        feedback_ys = [result[r][1] for r in feedback_refs]
        dec_ys = [result[r][1] for r in dec_refs]
        assert max(dec_ys) < min(feedback_ys), (
            "Decoupling cluster should sit above feedback cluster to avoid role mixing"
        )

        input_mean_y = sum(result[r][1] for r in input_refs) / len(input_refs)
        output_mean_y = sum(result[r][1] for r in output_refs) / len(output_refs)
        assert input_mean_y < output_mean_y, (
            f"Input and output support clusters should be vertically staged: {result}"
        )
        assert all(abs(result[r][1] - uy) <= 3.0 * _gv_mod.GRID_ROW_MM for r in feedback_refs), (
            "Feedback cluster should remain local to the op-amp body"
        )

    def test_input_stage_cohesion_left_to_right_transition(self) -> None:
        """Phase 7.1: input stage should read connector -> preconditioning -> op-amp."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 70.0, None),
            "CIN": (175.0, 110.0, None),
            "RIN": (180.0, 125.0, None),
            "U1": (190.0, 100.0, None),
            "ROUT": (120.0, 140.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        jx, jy, _ = result["JIN"]
        cin_x, cin_y, _ = result["CIN"]
        rin_x, rin_y, _ = result["RIN"]
        ux, uy, _ = result["U1"]

        assert jx < cin_x < ux, f"Input flow should be left-to-right: {result}"
        assert jx < rin_x < ux, f"Input flow should be left-to-right: {result}"
        assert abs(cin_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Input preconditioning should stay vertically close to op-amp input side"
        )
        assert abs(rin_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Input preconditioning should stay vertically close to op-amp input side"
        )
        assert ux - jx <= 3.5 * GRID_COL_MM, (
            "Input-to-op-amp transition should remain short and readable"
        )

    def test_input_stage_cohesion_avoids_unrelated_role_mixing(self) -> None:
        """Phase 7.1: unrelated support parts should not occupy the input lane."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]),
                NetIR(name="IN2", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (165.0, 100.0, None),
            "RIN": (175.0, 105.0, None),
            "U1": (190.0, 100.0, None),
            "CDEC": (150.0, 102.0, None),
            "ROUT": (130.0, 103.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        jx, _jy, _ = result["JIN"]
        rin_x, _rin_y, _ = result["RIN"]
        ux, uy, _ = result["U1"]
        cdec_x, cdec_y, _ = result["CDEC"]
        rout_x, _rout_y, _ = result["ROUT"]

        assert jx < rin_x < ux, f"Input lane should remain ordered: {result}"
        assert cdec_x >= rin_x, "Decoupling support should not intrude into the input lane"
        assert cdec_y < uy, "Decoupling support should stay on power-side (above op-amp)"
        assert rout_x >= rin_x, "Output support should not intrude into the input lane"

    def test_output_stage_cohesion_left_to_right_transition(self) -> None:
        """Phase 7.2: output stage should read op-amp -> feedback -> connector."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="10k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="10n"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="FB",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT",
                    pins=[
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="CFB", pin="2"),
                        PinRefIR(ref="COUT", pin="1"),
                    ],
                ),
                NetIR(
                    name="JOUT_NET",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (190.0, 100.0, None),
            "RFB": (170.0, 95.0, None),
            "CFB": (175.0, 105.0, None),
            "COUT": (210.0, 85.0, None),
            "JOUT": (215.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rfb_x, rfb_y, _ = result["RFB"]
        _cfb_x, cfb_y, _ = result["CFB"]
        cout_x, cout_y, _ = result["COUT"]
        jout_x, jout_y, _ = result["JOUT"]

        assert ux < cout_x < jout_x, f"Output flow should be left-to-right: {result}"
        assert rfb_x <= cout_x, "Feedback support should not overtake output stage terminal lane"
        assert abs(rfb_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Output feedback should stay vertically close to op-amp output side"
        )
        assert abs(cfb_y - uy) <= 2.0 * _gv_mod.GRID_ROW_MM, (
            "Output feedback should stay vertically close to op-amp output side"
        )
        assert jout_x - ux <= 3.5 * GRID_COL_MM, (
            "Op-amp-to-output transition should remain short and readable"
        )

    def test_output_stage_cohesion_avoids_unrelated_role_mixing(self) -> None:
        """Phase 7.2: unrelated support parts should not occupy the output lane."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="10k"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="FB",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="2")],
                ),
                NetIR(
                    name="OUT",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")],
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (160.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (200.0, 105.0, None),
            "CDEC": (205.0, 85.0, None),
            "JOUT": (212.0, 103.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        rfb_x, rfb_y, _ = result["RFB"]
        cdec_x, cdec_y, _ = result["CDEC"]
        jout_x, _jout_y, _ = result["JOUT"]
        jin_x, _jin_y, _ = result["JIN"]

        assert ux < jout_x, f"Output lane should remain ordered: {result}"
        assert rfb_x <= jout_x, "Feedback support should stay before output connector lane"
        assert jin_x <= rfb_x, "Input support should not intrude into the output lane"
        assert cdec_x <= rfb_x, "Decoupling support should not intrude into the output lane"
        assert cdec_y < uy, "Decoupling support should stay on power-side (above op-amp)"

    def test_stage_coherence_input_block_compact_and_left_bounded(self) -> None:
        """Phase 7.3: input stage should stay compact and left-bounded."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 80.0, None),
            "CIN": (178.0, 92.0, None),
            "RIN": (183.0, 108.0, None),
            "U1": (190.0, 100.0, None),
            "COUT": (210.0, 90.0, None),
            "JOUT": (218.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, _uy, _ = result["U1"]
        input_refs = ["JIN", "CIN", "RIN"]
        input_x = [result[r][0] for r in input_refs]
        input_y = [result[r][1] for r in input_refs]

        assert max(input_x) < ux, f"Input stage should remain left of op-amp: {result}"
        assert max(input_x) - min(input_x) <= 2.5 * GRID_COL_MM, (
            "Input stage should be compact in x"
        )
        assert max(input_y) - min(input_y) <= 2.5 * _gv_mod.GRID_ROW_MM, (
            "Input stage should be compact in y"
        )

    def test_stage_coherence_output_block_compact_and_right_bounded(self) -> None:
        """Phase 7.3: output stage should stay compact and right-bounded."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(name="FB", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (165.0, 100.0, None),
            "RIN": (178.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (197.0, 92.0, None),
            "COUT": (208.0, 88.0, None),
            "JOUT": (216.0, 102.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, _uy, _ = result["U1"]
        output_refs = ["COUT", "JOUT"]
        output_x = [result[r][0] for r in output_refs]
        output_y = [result[r][1] for r in output_refs]

        assert min(output_x) > ux, f"Output stage should remain right of op-amp: {result}"
        assert max(output_x) - min(output_x) <= 1.5 * GRID_COL_MM, (
            "Output stage should be compact in x"
        )
        assert max(output_y) - min(output_y) <= 2.5 * _gv_mod.GRID_ROW_MM, (
            "Output stage should be compact in y"
        )

    def test_stage_coherence_input_output_boundaries_do_not_overlap(self) -> None:
        """Phase 7.3: input and output stage boundaries should stay separated."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="COUT", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]),
                NetIR(name="IN2", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="U1", pin="3")]),
                NetIR(name="FB", pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RFB", pin="1")]),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="COUT", pin="1")]
                ),
                NetIR(
                    name="OUT2", pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (170.0, 95.0, None),
            "CIN": (178.0, 100.0, None),
            "U1": (190.0, 100.0, None),
            "RFB": (198.0, 95.0, None),
            "COUT": (208.0, 100.0, None),
            "JOUT": (216.0, 105.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB"},
            annotations={"RFB": ComponentAnnotation(feedback=True)},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        input_refs = ["JIN", "CIN"]
        output_refs = ["COUT", "JOUT"]
        input_max_x = max(result[r][0] for r in input_refs)
        output_min_x = min(result[r][0] for r in output_refs)

        assert input_max_x + GRID_COL_MM <= output_min_x, (
            f"Input/output stages should remain separated by at least one grid column: {result}"
        )

    def test_opamp_orientation_inputs_left_output_right(self) -> None:
        """Phase 4.3: op-amp orientation should have inputs left, output right."""
        from kicad_pcb.layout import compute_orientations  # noqa: PLC0415

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        positions = {"JIN": (30.0, 80.0), "U1": (90.0, 80.0), "JOUT": (150.0, 80.0)}
        orientations = compute_orientations(ir, positions)

        assert orientations["U1"] == 0, (
            "Op-amp should be at 0° orientation (inputs left, output right)"
        )

    def test_feedback_passive_vertical_near_opamp(self) -> None:
        """Phase 4.3: feedback passives in same column as op-amp prefer vertical (90°)."""
        from kicad_pcb.layout import compute_orientations  # noqa: PLC0415

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="R2", symbol="Device:R", value="10k"),
            ],
            nets=[
                NetIR(
                    name="FB",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
                NetIR(name="FB2", pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="RFB", pin="2")]),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("R2", BlockRole.PRECONDITIONING)

        # RFB positioned in same column as U1 (x within GRID_COL_MM / 2)
        # R2 positioned away from U1 column
        positions = {
            "U1": (90.0, 80.0),
            "RFB": (90.0, 60.0),
            "R2": (120.0, 80.0),
        }

        orientations = compute_orientations(ir, positions, block_layout=block_layout)

        assert orientations["RFB"] == 90, (
            "Feedback passive RFB in same column as op-amp should be vertical (90°)"
        )

    def test_input_output_passives_prefer_horizontal(self) -> None:
        """Phase 4.3: input/output stage passives prefer horizontal (0°) for left-to-right flow."""
        from kicad_pcb.layout import compute_orientations  # noqa: PLC0415

        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="OUT_A",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        # Positions arranged left-to-right (horizontal flow dominates)
        positions = {
            "JIN": (30.0, 80.0),
            "RIN": (60.0, 80.0),
            "ROUT": (120.0, 80.0),
            "JOUT": (150.0, 80.0),
        }

        orientations = compute_orientations(ir, positions, block_layout=block_layout)

        assert orientations["RIN"] == 0, (
            "Input-stage passive RIN should be horizontal (0°) for left-to-right flow"
        )
        assert orientations["ROUT"] == 0, (
            "Output-stage passive ROUT should be horizontal (0°) for left-to-right flow"
        )

    def test_opamp_neighborhood_feedback_near_opamp_not_connectors(self) -> None:
        """Phase 4.4: feedback components should be closer to op-amp than to connectors."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="RFB", symbol="Device:R", value="47k"),
                ComponentIR(ref="CFB", symbol="Device:C", value="22p"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="FB_A",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="RFB", pin="1"),
                        PinRefIR(ref="CFB", pin="1"),
                    ],
                ),
                NetIR(
                    name="FB_B",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="RFB", pin="2"),
                        PinRefIR(ref="CFB", pin="2"),
                    ],
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("RFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("CFB", BlockRole.FEEDBACK)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "RIN": (60.0, 100.0, None),
            "U1": (110.0, 100.0, None),
            "RFB": (140.0, 90.0, None),
            "CFB": (140.0, 110.0, None),
            "JOUT": (180.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs={"RFB", "CFB"},
            annotations={
                "RFB": ComponentAnnotation(feedback=True),
                "CFB": ComponentAnnotation(feedback=True),
            },
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        jin_x, jin_y, _ = result["JIN"]
        jout_x, jout_y, _ = result["JOUT"]

        for fb_ref in ["RFB", "CFB"]:
            fb_x, fb_y, _ = result[fb_ref]
            dist_to_opamp = math.sqrt((fb_x - ux) ** 2 + (fb_y - uy) ** 2)
            dist_to_jin = math.sqrt((fb_x - jin_x) ** 2 + (fb_y - jin_y) ** 2)
            dist_to_jout = math.sqrt((fb_x - jout_x) ** 2 + (fb_y - jout_y) ** 2)

            assert dist_to_opamp < dist_to_jin, (
                f"{fb_ref} should be closer to U1 than to JIN: "
                f"dist(U1)={dist_to_opamp:.2f}, dist(JIN)={dist_to_jin:.2f}"
            )
            assert dist_to_opamp < dist_to_jout, (
                f"{fb_ref} should be closer to U1 than to JOUT: "
                f"dist(U1)={dist_to_opamp:.2f}, dist(JOUT)={dist_to_jout:.2f}"
            )

    def test_opamp_neighborhood_output_parts_on_output_side(self) -> None:
        """Phase 4.4: output-side parts should be placed on the output side of op-amp."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="ROUT", symbol="Device:R", value="100"),
                ComponentIR(ref="COUT", symbol="Device:C", value="10u"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(name="IN", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="U1", pin="3")]),
                NetIR(
                    name="OUT_A", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="ROUT", pin="1")]
                ),
                NetIR(
                    name="OUT_B",
                    pins=[PinRefIR(ref="ROUT", pin="2"), PinRefIR(ref="COUT", pin="1")],
                ),
                NetIR(
                    name="OUT_C",
                    pins=[PinRefIR(ref="COUT", pin="2"), PinRefIR(ref="JOUT", pin="1")],
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("ROUT", BlockRole.OUTPUT)
        block_layout.add_assignment("COUT", BlockRole.OUTPUT)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "U1": (90.0, 100.0, None),
            "ROUT": (60.0, 95.0, None),
            "COUT": (60.0, 105.0, None),
            "JOUT": (150.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={},
            block_layout=block_layout,
        )

        ux, _uy, _ = result["U1"]
        rout_x, _rout_y, _ = result["ROUT"]
        cout_x, _cout_y, _ = result["COUT"]
        jout_x, _jout_y, _ = result["JOUT"]

        assert rout_x > ux, (
            f"Output resistor ROUT should be right of U1: ROUT.x={rout_x:.2f}, U1.x={ux:.2f}"
        )
        assert cout_x > ux, (
            f"Output cap COUT should be right of U1: COUT.x={cout_x:.2f}, U1.x={ux:.2f}"
        )
        assert jout_x > ux, (
            f"Output connector JOUT should be right of U1: JOUT.x={jout_x:.2f}, U1.x={ux:.2f}"
        )

    def test_opamp_neighborhood_decouplers_near_power_not_input(self) -> None:
        """Phase 4.4: supply decouplers should be nearer the power pins than input network."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="JIN", symbol="Connector_Generic:Conn_01x01", value="In"),
                ComponentIR(ref="RIN", symbol="Device:R", value="10k"),
                ComponentIR(ref="CIN", symbol="Device:C", value="100n"),
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
                ComponentIR(ref="CDEC", symbol="Device:C", value="100n"),
                ComponentIR(ref="JOUT", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ],
            nets=[
                NetIR(
                    name="IN_A", pins=[PinRefIR(ref="JIN", pin="1"), PinRefIR(ref="CIN", pin="1")]
                ),
                NetIR(
                    name="IN_B", pins=[PinRefIR(ref="CIN", pin="2"), PinRefIR(ref="RIN", pin="1")]
                ),
                NetIR(
                    name="IN_C", pins=[PinRefIR(ref="RIN", pin="2"), PinRefIR(ref="U1", pin="3")]
                ),
                NetIR(
                    name="OUT", pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="JOUT", pin="1")]
                ),
                NetIR(
                    name="VCC", pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="CDEC", pin="1")]
                ),
                NetIR(
                    name="GND", pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="CDEC", pin="2")]
                ),
            ],
        )

        block_layout = BlockLayout()
        block_layout.add_assignment("JIN", BlockRole.INPUT)
        block_layout.add_assignment("RIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("CIN", BlockRole.PRECONDITIONING)
        block_layout.add_assignment("U1", BlockRole.OPAMP_CORE)
        block_layout.add_assignment("CDEC", BlockRole.DECOUPLING)
        block_layout.add_assignment("JOUT", BlockRole.OUTPUT)

        positions: dict[str, tuple[float, float, float | None]] = {
            "JIN": (30.0, 100.0, None),
            "CIN": (50.0, 95.0, None),
            "RIN": (50.0, 105.0, None),
            "U1": (110.0, 100.0, None),
            "CDEC": (80.0, 140.0, None),
            "JOUT": (150.0, 100.0, None),
        }

        result = _gv_mod.apply_post_layout_snaps(
            positions,
            ir,
            feedback_refs=set(),
            annotations={},
            channels={ref: "mono" for ref in positions},
            decoupling_map={"CDEC": "U1"},
            block_layout=block_layout,
        )

        ux, uy, _ = result["U1"]
        cdec_x, cdec_y, _ = result["CDEC"]
        cin_x, cin_y, _ = result["CIN"]
        rin_x, rin_y, _ = result["RIN"]

        dist_cdec_to_opamp = math.sqrt((cdec_x - ux) ** 2 + (cdec_y - uy) ** 2)
        dist_cdec_to_cin = math.sqrt((cdec_x - cin_x) ** 2 + (cdec_y - cin_y) ** 2)
        dist_cdec_to_rin = math.sqrt((cdec_x - rin_x) ** 2 + (cdec_y - rin_y) ** 2)

        assert dist_cdec_to_opamp < dist_cdec_to_cin, (
            f"Decoupling cap CDEC should be closer to U1 than to input network CIN: "
            f"dist(U1)={dist_cdec_to_opamp:.2f}, dist(CIN)={dist_cdec_to_cin:.2f}"
        )
        assert dist_cdec_to_opamp < dist_cdec_to_rin, (
            f"Decoupling cap CDEC should be closer to U1 than to input network RIN: "
            f"dist(U1)={dist_cdec_to_opamp:.2f}, dist(RIN)={dist_cdec_to_rin:.2f}"
        )

        assert math.isclose(cdec_x, ux, abs_tol=0.01), (
            "Decoupling cap should align to op-amp column for tight power coupling"
        )


# ---------------------------------------------------------------------------
# Phase 8 — Wire routing improvements (Rule §4)
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
        routing.power_symbols.append(PowerSymbolPlacement("NOT_A_REAL_NET_XYZ", 50.0, 80.0))
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
