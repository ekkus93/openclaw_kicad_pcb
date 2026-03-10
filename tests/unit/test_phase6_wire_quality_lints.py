"""Unit tests for Phase 6.2 — wire quality lints (LAY009, LAY010)."""

from __future__ import annotations

import json

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint.sch import lint_wire_quality
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse as _parse_sexpr

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCH_TMPL = "(kicad_sch {body})"


def _doc(body: str) -> SchematicDoc:
    """Build a minimal :class:`SchematicDoc` from an S-expression fragment."""
    root = _parse_sexpr(_SCH_TMPL.format(body=body))
    return SchematicDoc(root)


def _wire(x1: float, y1: float, x2: float, y2: float) -> str:
    """Return a minimal wire S-expression string."""
    return (
        f"(wire (pts (xy {x1} {y1}) (xy {x2} {y2})) "
        f'(stroke (width 0.0) (type default)) (uuid "wire-{x1}-{y1}"))'
    )


def _bind_marker(ref: str, pin: str, net: str, x: float, y: float) -> str:
    """Return a bind marker (hidden text node) for wire-to-net mapping.

    Uses the OpenClaw bind marker format: "OpenClaw:bind={json}".
    """

    binding_text = "OpenClaw:bind=" + json.dumps(
        {"ref": ref, "pin": pin, "net_name": net},
        separators=(",", ":"),
        sort_keys=True,
    )
    # Escape the binding text for S-expression (escape backslashes and quotes)
    escaped_text = binding_text.replace("\\", "\\\\").replace('"', '\\"')
    return (
        f'(text "{escaped_text}" '
        f"(at {x} {y} 0) "
        f"(effects (font (size 1.27 1.27)) hide) "
        f'(uuid "bind-{ref}-{pin}"))'
    )


# ---------------------------------------------------------------------------
# TestLAY009ExcessiveShortWireJogs — Phase 6.2 wire quality lint
# ---------------------------------------------------------------------------


class TestLAY009ExcessiveShortWireJogs:
    """Validate LAY009 lint rule for excessive short wire segments.

    LAY009 detects nets where ≥50% of wire segments are shorter than 5mm,
    indicating excessive jogs/stubs that could be simplified.
    """

    def test_no_wires_no_warning(self) -> None:
        """Empty schematic has no wires, so no LAY009 warnings."""
        doc = _doc("")
        # CircuitIR requires at least 1 component and 1 net
        net = NetIR(name="DUMMY", pins=[PinRefIR(ref="R1", pin="1")])
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R", value="1k")],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay009_issues = [i for i in issues if i.code == "LAY009"]
        assert lay009_issues == []

    def test_single_long_wire_no_warning(self) -> None:
        """Single long wire segment should not trigger LAY009."""
        # 100mm wire - well above 5mm threshold
        body = " ".join(
            [
                _wire(0.0, 0.0, 100.0, 0.0),
                _bind_marker("R1", "1", "NET1", 0.0, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(name="NET1", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")])
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay009_issues = [i for i in issues if i.code == "LAY009"]
        assert lay009_issues == []

    def test_many_short_wires_triggers_warning(self) -> None:
        """Net with ≥50% short wire segments should trigger LAY009."""
        # Create a connected path of wires starting from bind marker at (0,0)
        # 3 short wires (2mm each) + 2 long wires (20mm each) = 5 segments, 60% short
        body = " ".join(
            [
                # Start at bind marker and connect each wire
                _wire(0.0, 0.0, 2.0, 0.0),  # 2mm - connects to bind marker
                _wire(2.0, 0.0, 4.0, 0.0),  # 2mm - continues from previous
                _wire(4.0, 0.0, 6.0, 0.0),  # 2mm - continues from previous
                _wire(6.0, 0.0, 26.0, 0.0),  # 20mm - continues from previous
                _wire(26.0, 0.0, 46.0, 0.0),  # 20mm - continues from previous
                # Bind markers at wire start points (represent component pins)
                _bind_marker("R1", "1", "NET_JOGGY", 0.0, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_JOGGY", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")]
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay009_issues = [i for i in issues if i.code == "LAY009"]
        assert len(lay009_issues) > 0, "Expected LAY009 warning for ≥50% short segments"
        # Verify message mentions the net
        assert any("NET_JOGGY" in i.message for i in lay009_issues)

    def test_balanced_short_long_no_warning(self) -> None:
        """Net with <50% short segments should not trigger LAY009."""
        # Create 2 short wires and 3 long wires (40% short) → below threshold
        bind_x = 0.0
        body = " ".join(
            [
                # Short wires
                _wire(0.0, 0.0, 2.0, 0.0),  # 2mm
                _wire(10.0, 0.0, 12.0, 0.0),  # 2mm
                # Long wires
                _wire(20.0, 0.0, 40.0, 0.0),  # 20mm
                _wire(50.0, 0.0, 70.0, 0.0),  # 20mm
                _wire(80.0, 0.0, 100.0, 0.0),  # 20mm
                # Bind marker
                _bind_marker("R1", "1", "NET_OK", bind_x, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(name="NET_OK", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")])
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay009_issues = [i for i in issues if i.code == "LAY009"]
        assert lay009_issues == []

    def test_net_with_fewer_than_3_segments_not_flagged(self) -> None:
        """Nets with < 3 total segments should not be flagged even if all are short."""
        # 2 short wires (100% short but < 3 segments) → should not trigger
        bind_x = 0.0
        body = " ".join(
            [
                _wire(0.0, 0.0, 2.0, 0.0),  # 2mm
                _wire(10.0, 0.0, 12.0, 0.0),  # 2mm
                _bind_marker("R1", "1", "NET_SMALL", bind_x, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_SMALL", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")]
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay009_issues = [i for i in issues if i.code == "LAY009"]
        assert lay009_issues == []


# ---------------------------------------------------------------------------
# TestLAY010OverRoutedLocalConnection — Phase 6.2 wire quality lint
# ---------------------------------------------------------------------------


class TestLAY010OverRoutedLocalConnection:
    """Validate LAY010 lint rule for over-routed 2-pin connections.

    LAY010 detects 2-pin nets that use > 4 wire segments, indicating
    unnecessarily complex routing for a simple local connection.
    """

    def test_two_pin_with_few_segments_no_warning(self) -> None:
        """2-pin net with ≤4 segments should not trigger LAY010."""
        # 2-pin net with 3 segments (reasonable) → no warning
        bind_x1 = 0.0
        bind_x2 = 50.0
        body = " ".join(
            [
                _wire(0.0, 0.0, 5.0, 0.0),  # stub 1
                _wire(5.0, 0.0, 45.0, 0.0),  # main routing
                _wire(45.0, 0.0, 50.0, 0.0),  # stub 2
                _bind_marker("R1", "1", "NET_SIMPLE", bind_x1, 0.0),
                _bind_marker("R2", "1", "NET_SIMPLE", bind_x2, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_SIMPLE", pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")]
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay010_issues = [i for i in issues if i.code == "LAY010"]
        assert lay010_issues == []

    def test_two_pin_with_many_segments_triggers_warning(self) -> None:
        """2-pin net with >4 segments should trigger LAY010."""
        # Create a connected path of 6 wire segments (over-routed for 2-pin net)
        # Add bind markers at connection points so all segments get matched to net
        body = " ".join(
            [
                # Connected path from (0,0) to (30,0) using 6 segments
                _wire(0.0, 0.0, 5.0, 0.0),  # segment 1
                _wire(5.0, 0.0, 10.0, 0.0),  # segment 2
                _wire(10.0, 0.0, 15.0, 0.0),  # segment 3
                _wire(15.0, 0.0, 20.0, 0.0),  # segment 4
                _wire(20.0, 0.0, 25.0, 0.0),  # segment 5
                _wire(25.0, 0.0, 30.0, 0.0),  # segment 6
                # Bind markers at junction points (so all wires match to this net)
                _bind_marker("R1", "1", "NET_OVERROUTED", 0.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 5.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 10.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 15.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 20.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 25.0, 0.0),
                _bind_marker("R2", "1", "NET_OVERROUTED", 30.0, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_OVERROUTED",
            pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay010_issues = [i for i in issues if i.code == "LAY010"]
        assert len(lay010_issues) > 0, "Expected LAY010 warning for ≥5 segments on 2-pin net"
        # Verify message mentions the net
        assert any("NET_OVERROUTED" in i.message for i in lay010_issues)

    def test_power_nets_excluded(self) -> None:
        """Power nets (GND, VCC, etc.) should not trigger LAY010 even with many segments."""
        # GND net with 6 segments (but it's a power net) → no warning
        bind_x1 = 0.0
        bind_x2 = 50.0
        body = " ".join(
            [
                _wire(0.0, 0.0, 5.0, 0.0),
                _wire(5.0, 0.0, 10.0, 0.0),
                _wire(10.0, 0.0, 20.0, 0.0),
                _wire(20.0, 0.0, 30.0, 0.0),
                _wire(30.0, 0.0, 40.0, 0.0),
                _wire(40.0, 0.0, 50.0, 0.0),
                _bind_marker("C1", "1", "GND", bind_x1, 0.0),
                _bind_marker("C2", "1", "GND", bind_x2, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="1"), PinRefIR(ref="C2", pin="1")])
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="C1", symbol="Device:C", value="100nF"),
                ComponentIR(ref="C2", symbol="Device:C", value="100nF"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay010_issues = [i for i in issues if i.code == "LAY010"]
        assert lay010_issues == []

    def test_multi_pin_nets_excluded(self) -> None:
        """Nets with >2 pins should not trigger LAY010 even with many segments."""
        # 3-pin net with 6 segments (but it's not a 2-pin net) → no warning
        bind_x1 = 0.0
        bind_x2 = 50.0
        bind_x3 = 25.0
        body = " ".join(
            [
                _wire(0.0, 0.0, 5.0, 0.0),
                _wire(5.0, 0.0, 10.0, 0.0),
                _wire(10.0, 0.0, 20.0, 0.0),
                _wire(20.0, 0.0, 30.0, 0.0),
                _wire(30.0, 0.0, 40.0, 0.0),
                _wire(40.0, 0.0, 50.0, 0.0),
                _bind_marker("R1", "1", "NET_MULTI", bind_x1, 0.0),
                _bind_marker("R2", "1", "NET_MULTI", bind_x2, 0.0),
                _bind_marker("R3", "1", "NET_MULTI", bind_x3, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_MULTI",
            pins=[
                PinRefIR(ref="R1", pin="1"),
                PinRefIR(ref="R2", pin="1"),
                PinRefIR(ref="R3", pin="1"),
            ],
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
            ],
            nets=[net],
        )
        issues = lint_wire_quality(doc, ir)
        lay010_issues = [i for i in issues if i.code == "LAY010"]
        assert lay010_issues == []
