"""Phase 4: layout engine factory, routing (direct/hub/power), LAY lint codes,
graphviz layout cache, and dot source discovery tests.
"""

from __future__ import annotations

import contextlib
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.errors import ErrorCode, UserError
from kicad_pcb.layout_engine import NoneLayoutEngine, make_layout_engine
from kicad_pcb.lint import LINT_SUGGESTIONS, LintSeverity, lint_schematic_layout
from kicad_pcb.router import (
    PinAnchor,
    _hub_route,
    _is_power_net_name,
    route_nets,
)
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import AtomNode, ListNode

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
        [
            "GND",
            "VCC",
            "VDD",
            "VSS",
            "AGND",
            "PGND",
            "DGND",
            "VBAT",
            "VREF",
            "V+",
            "V-",
            "VPLUS15",
            "VMINUS15",
            "VPOS_ANALOG",
            "VNEG_FILTERED",
            "AVEE15",
        ],
    )
    def test_known_power_nets_detected(self, name: str) -> None:
        assert _is_power_net_name(name)

    @pytest.mark.parametrize("name", ["NET1", "SDA", "SCL", "DATA", "CLK"])
    def test_non_power_nets_not_detected(self, name: str) -> None:
        assert not _is_power_net_name(name)

    def test_case_insensitive(self) -> None:
        assert _is_power_net_name("gnd")
        assert _is_power_net_name("Vcc")

    @pytest.mark.parametrize("name", ["/+3.3V@SD", "/GND@CARD", "+5V@IO", "gnd@local"])
    def test_scoped_power_nets_detected(self, name: str) -> None:
        assert _is_power_net_name(name)


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
    def test_pin_anchor_map_routes_known_pins_without_flat_endpoint_map(self) -> None:
        ir = _minimal_ir(
            refs=["R1", "R2"],
            nets=[
                {
                    "name": "SIG",
                    "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}],
                }
            ],
        )
        anchors = {
            ("R1", "1"): PinAnchor(ref="R1", pin="1", x=10.0, y=20.0, angle=0.0),
            ("R2", "1"): PinAnchor(ref="R2", pin="1", x=30.0, y=20.0, angle=180.0),
        }

        routing = route_nets(ir=ir, pin_endpoints={}, pin_anchors=anchors, strict=True)

        assert routing.wires
        assert routing.labels == []
        assert any(marker.ref == "R1" and marker.pin == "1" for marker in routing.bind_markers)
        assert any(marker.ref == "R2" and marker.pin == "1" for marker in routing.bind_markers)

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

    def test_cache_entry_roundtrip_persists_decoupling_map(self, tmp_path: Path) -> None:
        cache_file: Path = tmp_path / "layout.json"
        positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (30.0, 50.0, 0.0),
            "C1": (40.0, 60.0, 90.0),
        }
        decoupling_map = {"C1": "U1"}
        key = "deadbeef" * 8

        _gv_mod.save_layout_cache(cache_file, key, positions, decoupling_map=decoupling_map)
        loaded_entry = _gv_mod.load_layout_cache_entry(cache_file, key)

        assert loaded_entry is not None
        assert loaded_entry.positions == positions
        assert loaded_entry.decoupling_map == decoupling_map

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
            json.dumps(
                {"version": 2, "key": "k", "positions": {"R1": "bad"}, "decoupling_map": {}}
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="Invalid layout cache entry"):
            _gv_mod.load_layout_cache(cache_file, "k")

    def test_load_cache_invalid_decoupling_map_shape_raises(self, tmp_path: Path) -> None:
        """Malformed decoupling_map payload must raise RuntimeError."""
        cache_file: Path = tmp_path / "layout.json"
        cache_file.write_text(
            json.dumps(
                {
                    "version": 2,
                    "key": "k",
                    "positions": {"R1": [1.0, 2.0, None]},
                    "decoupling_map": {"C1": ["U1"]},
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(RuntimeError, match="Invalid decoupling_map entry"):
            _gv_mod.load_layout_cache_entry(cache_file, "k")


# ---------------------------------------------------------------------------
# 4.18 GraphvizLayoutEngine cache integration
# ---------------------------------------------------------------------------


class TestGraphvizLayoutEngineCache:
    """Integration tests for seed and cache on GraphvizLayoutEngine."""

    def test_cache_hit_skips_dot(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a valid cache entry exists, _run_dot must not be called."""
        ir = _simple_ir()
        positions: dict[str, tuple[float, float, float | None]] = {
            "R1": (31.0, 51.0, None),
            "C1": (41.0, 61.0, None),
        }
        cache_file: Path = tmp_path / "layout.json"
        monkeypatch.setattr(
            _gv_mod._gv_engine,
            "_load_layout_cache_entry",
            lambda *_args: _gv_mod.LayoutCacheEntry(positions=positions, decoupling_map={}),
        )

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

    def test_cache_hit_debug_dump_uses_cached_decoupling_map_without_recomputing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Cache-hit debug metadata should use the persisted refined decoupling map."""
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
                ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
                ComponentIR(ref="C1", symbol="Device:C", value="100n"),
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
                ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
            ],
            nets=[
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
            ],
        )
        cached_positions: dict[str, tuple[float, float, float | None]] = {
            "U1": (88.9, 30.48, 0.0),
            "U2": (96.52, 83.82, 0.0),
            "C1": (88.9, 10.16, 90.0),
            "J1": (30.48, 30.48, 0.0),
            "J2": (30.48, 83.82, 180.0),
        }
        cache_file = tmp_path / "layout.json"
        debug_dump = tmp_path / "layout-debug.json"

        monkeypatch.setattr(
            _gv_mod._gv_engine,
            "_load_layout_cache_entry",
            lambda *_args: _gv_mod.LayoutCacheEntry(
                positions=cached_positions,
                decoupling_map={"C1": "U1"},
            ),
        )

        def fail_if_refine_called(*_args: object, **_kwargs: object) -> dict[str, str]:
            raise AssertionError("cache hit should not recompute decoupling_map refinement")

        monkeypatch.setattr(
            _gv_mod._gv_engine, "_refine_shared_rail_decoupling_map", fail_if_refine_called
        )

        run_dot_called = False

        def fake_run_dot(self: object, dot_source: str) -> dict[str, tuple[float, float, None]]:
            nonlocal run_dot_called
            run_dot_called = True
            return {}

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)

        engine = _gv_mod.GraphvizLayoutEngine(
            dot_path="dot",
            cache_path=cache_file,
            debug_dump_path=debug_dump,
        )
        result = engine.compute_symbol_positions(ir)

        assert not run_dot_called, "_run_dot was called despite a cache hit"
        payload = json.loads(debug_dump.read_text(encoding="utf-8"))
        assert payload["cache_hit"] is True
        assert payload["decoupling_map"] == {"C1": "U1"}
        assert payload["placement_constraints"]["decoupling_map"] == {"C1": "U1"}
        assert result["C1"][0] == pytest.approx(88.9)


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


class TestGraphvizTimeoutScaling:
    def test_small_ir_keeps_base_timeout(self) -> None:
        timeout = _gv_mod._graphviz_timeout_for_ir(_simple_ir(), base_timeout=10.0)

        assert timeout == 10.0

    def test_large_ir_scales_timeout_above_base(self) -> None:
        refs = [f"R{i}" for i in range(1, 101)]
        nets = [
            {
                "name": f"N{i}",
                "pins": [
                    {"ref": refs[i - 1], "pin": "1"},
                    {"ref": refs[i], "pin": "1"},
                ],
            }
            for i in range(1, len(refs))
        ]

        timeout = _gv_mod._graphviz_timeout_for_ir(
            _minimal_ir(refs=refs, nets=nets),
            base_timeout=10.0,
        )

        assert timeout > 10.0

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
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", tmp_path / "no_dot")
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
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", bundled)

        result = _gv_mod.find_dot_source()
        assert result is not None
        path, source = result
        assert path == str(fake_dot)
        assert source == "GRAPHVIZ_DOT"

    def test_path_fallback(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Falls back to PATH when no env var set; source is 'PATH'."""
        monkeypatch.delenv("GRAPHVIZ_DOT", raising=False)
        bundled = tmp_path / "no_bundled"
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", bundled)
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
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", bundled)
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
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", bundled)
        with pytest.raises(UserError, match="GRAPHVIZ_DOT") as exc_info:
            _gv_mod.find_dot_source(strict=True)
        assert exc_info.value.code == ErrorCode.TOOL_ERROR

    def test_bundled_binary_first(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Bundled binary is used first when present; source is 'bundled'."""
        bundled = tmp_path / "dot"
        bundled.write_text("#!/bin/sh\n")
        bundled.chmod(0o755)
        monkeypatch.setattr(_gv_mod._gv_discovery, "_BUNDLED_DOT_PATH", bundled)
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
