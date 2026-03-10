"""Unit tests for Phase 6.3 — local direct wiring lint (LAY011)."""

from __future__ import annotations

import json

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.lint.sch import lint_local_direct_wiring
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


def _symbol(ref: str, x: float, y: float, symbol_id: str = "Device:R") -> str:
    """Return a symbol S-expression with position."""
    return (
        f'(symbol (lib_id "{symbol_id}") (at {x} {y} 0) '
        f'(property "Reference" "{ref}" (at 0 0 0)) '
        f'(property "Value" "1k" (at 0 10 0)) '
        f'(uuid "sym-{ref}"))'
    )


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
# TestLAY011LocalDirectWiring — Phase 6.3 local direct wiring lint
# ---------------------------------------------------------------------------


class TestLAY011LocalDirectWiring:
    """Validate LAY011 lint rule for local direct wiring opportunities.

    LAY011 detects 2-pin nets where both pins are within ~150mm (roughly
    same PCB block) but are routed with 3+ segments unnecessarily,
    suggesting they should use simpler direct L-routing instead.
    """

    def test_no_wires_no_warning(self) -> None:
        """Empty schematic has no wires, so no LAY011 warnings."""
        doc = _doc("")
        # CircuitIR requires at least 1 component and 1 net
        net = NetIR(name="DUMMY", pins=[PinRefIR(ref="R1", pin="1")])
        ir = CircuitIR(
            version="1",
            components=[ComponentIR(ref="R1", symbol="Device:R", value="1k")],
            nets=[net],
        )
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert lay011_issues == []

    def test_nearby_2pin_direct_no_warning(self) -> None:
        """Nearby 2-pin connection using 2 segments should not trigger LAY011."""
        # Two components 50mm apart, single L-route (2 segments)
        # Below minimum segment threshold (3) for LAY011
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 50.0, 10.0),
                _wire(0.0, 0.0, 50.0, 0.0),  # Horizontal 50mm
                _wire(50.0, 0.0, 50.0, 10.0),  # Vertical 10mm (L-route)
                _bind_marker("R1", "1", "NET_DIRECT", 0.0, 0.0),
                _bind_marker("R2", "1", "NET_DIRECT", 50.0, 10.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_DIRECT",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert lay011_issues == [], "Direct routing (2 segments) should not trigger LAY011"

    def test_nearby_2pin_over_routed_triggers_warning(self) -> None:
        """Nearby 2-pin connection with 4+ segments should trigger LAY011."""
        # Two components 60mm apart (under 150mm threshold), routed with 4 segments
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 50.0, 10.0),
                # Over-routed path: (0,0) → (20,0) → (20,30) → (50,30) → (50,10)
                _wire(0.0, 0.0, 20.0, 0.0),  # Horizontal 20mm
                _wire(20.0, 0.0, 20.0, 30.0),  # Vertical 30mm
                _wire(20.0, 30.0, 50.0, 30.0),  # Horizontal 30mm
                _wire(50.0, 30.0, 50.0, 10.0),  # Vertical 20mm
                _bind_marker("R1", "1", "NET_OVER", 0.0, 0.0),
                _bind_marker("R2", "1", "NET_OVER", 50.0, 10.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_OVER",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert len(lay011_issues) > 0, "Over-routed nearby 2-pin should trigger LAY011"
        assert any("NET_OVER" in i.message for i in lay011_issues)
        assert any("4 wire segments" in i.message for i in lay011_issues)

    def test_distant_2pin_over_routed_no_warning(self) -> None:
        """Distant 2-pin connection should not trigger LAY011 regardless of segments."""
        # Two components 200mm apart (exceeds 150mm threshold)
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 100.0, 100.0),
                _wire(0.0, 0.0, 50.0, 0.0),
                _wire(50.0, 0.0, 50.0, 50.0),
                _wire(50.0, 50.0, 100.0, 50.0),
                _wire(100.0, 50.0, 100.0, 100.0),
                _bind_marker("R1", "1", "NET_FAR", 0.0, 0.0),
                _bind_marker("R2", "1", "NET_FAR", 100.0, 100.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_FAR",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert lay011_issues == [], "Distant 2-pin (200mm+ apart) should not trigger LAY011"

    def test_power_net_skipped(self) -> None:
        """Power nets (GND, VCC) should be skipped even if over-routed."""
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 50.0, 10.0),
                _wire(0.0, 0.0, 20.0, 0.0),
                _wire(20.0, 0.0, 20.0, 20.0),
                _wire(20.0, 20.0, 50.0, 20.0),
                _wire(50.0, 20.0, 50.0, 10.0),
                _bind_marker("R1", "1", "GND", 0.0, 0.0),
                _bind_marker("R2", "1", "GND", 50.0, 10.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="GND",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert lay011_issues == [], "Power nets should be skipped for LAY011"

    def test_3pin_net_skipped(self) -> None:
        """Multi-pin nets (not 2-pin) should be skipped."""
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 20.0, 0.0),
                _symbol("R3", 50.0, 0.0),
                _wire(0.0, 0.0, 20.0, 0.0),
                _wire(20.0, 0.0, 40.0, 0.0),
                _wire(40.0, 0.0, 50.0, 0.0),
                _bind_marker("R1", "1", "NET_3PIN", 0.0, 0.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_3PIN",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert lay011_issues == [], "Multi-pin nets should be skipped"

    def test_multiple_nearby_2pins_multiple_warnings(self) -> None:
        """Multiple over-routed 2-pin nets should each trigger LAY011."""
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 40.0, 20.0),
                _symbol("R3", 60.0, 40.0),
                _symbol("R4", 100.0, 60.0),
                # NET1: nearby 2-pin, over-routed with 3 segments
                _wire(0.0, 0.0, 10.0, 0.0),
                _wire(10.0, 0.0, 10.0, 20.0),
                _wire(10.0, 20.0, 40.0, 20.0),
                # NET2: nearby 2-pin, over-routed with 3 segments
                _wire(60.0, 40.0, 70.0, 40.0),
                _wire(70.0, 40.0, 70.0, 60.0),
                _wire(70.0, 60.0, 100.0, 60.0),
                # Markers
                _bind_marker("R1", "1", "NET1", 0.0, 0.0),
                _bind_marker("R2", "1", "NET1", 40.0, 20.0),
                _bind_marker("R3", "1", "NET2", 60.0, 40.0),
                _bind_marker("R4", "1", "NET2", 100.0, 60.0),
            ]
        )
        doc = _doc(body)
        net1 = NetIR(
            name="NET1",
            pins=[PinRefIR(ref="R1", pin="1"), PinRefIR(ref="R2", pin="1")],
        )
        net2 = NetIR(
            name="NET2",
            pins=[PinRefIR(ref="R3", pin="1"), PinRefIR(ref="R4", pin="1")],
        )
        ir = CircuitIR(
            version="1",
            components=[
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="1k"),
                ComponentIR(ref="R3", symbol="Device:R", value="1k"),
                ComponentIR(ref="R4", symbol="Device:R", value="1k"),
            ],
            nets=[net1, net2],
        )
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        assert len(lay011_issues) >= 2, "Expected at least 2 LAY011 warnings"
        messages = [i.message for i in lay011_issues if "NET1" in i.message or "NET2" in i.message]
        assert len(messages) >= 1, "Should have at least one matching NET1 or NET2 warning"

    def test_edge_case_exactly_threshold_distance(self) -> None:
        """2-pin at exactly 150mm distance (threshold) should trigger if over-routed."""
        # Components exactly 150mm apart: (0,0) and (100,50)
        # Manhattan: |100-0| + |50-0| = 150mm (exactly at threshold)
        body = " ".join(
            [
                _symbol("R1", 0.0, 0.0),
                _symbol("R2", 100.0, 50.0),
                _wire(0.0, 0.0, 30.0, 0.0),
                _wire(30.0, 0.0, 30.0, 50.0),
                _wire(30.0, 50.0, 100.0, 50.0),
                _bind_marker("R1", "1", "NET_EDGE", 0.0, 0.0),
                _bind_marker("R2", "1", "NET_EDGE", 100.0, 50.0),
            ]
        )
        doc = _doc(body)
        net = NetIR(
            name="NET_EDGE",
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
        issues = lint_local_direct_wiring(doc, ir)
        lay011_issues = [i for i in issues if i.code == "LAY011"]
        # At threshold distance with 3 segments should trigger
        assert len(lay011_issues) > 0, (
            "2-pin at exactly 150mm with 3 segments should trigger LAY011"
        )
