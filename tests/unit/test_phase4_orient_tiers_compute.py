"""Phase 4: component orientations, tier assignment, and dot source signal flow."""

from __future__ import annotations

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.layout import (
    compute_orientations,
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


# ---------------------------------------------------------------------------
# 4.1 / 4.2  LayoutEngine factory — NoneLayoutEngine
# ---------------------------------------------------------------------------


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
