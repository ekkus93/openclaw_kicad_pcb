"""Phase 4: decoupling cap detection and co-location tests."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import classify_circuit
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
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

    def test_cap_with_shared_local_rail_prefers_ic_over_passive(self) -> None:
        """A local rail cap should anchor to the active stage, not the first passive on that net."""
        components = [
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="R1", symbol="Device:R", value="1k"),
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(
                name="IN_SIG",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="R1", pin="1")],
            ),
            NetIR(
                name="LOCAL_BIAS",
                pins=[
                    PinRefIR(ref="R1", pin="2"),
                    PinRefIR(ref="U1", pin="7"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="OUT_SIG",
                pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(name="GND", pins=[PinRefIR(ref="C1", pin="2")]),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            "Expected decoupling cap to prefer the active IC anchor over the upstream resistor, "
            f"got: {result}"
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

    def test_true_bypass_cap_on_active_rail_detected(self) -> None:
        """A rail-to-ground bypass cap should anchor to the active IC on that rail."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="TL071"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="In"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Out"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
        ]
        nets = [
            NetIR(
                name="IN_SIG",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U1", pin="3")],
            ),
            NetIR(
                name="OUT_SIG",
                pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="VCC",
                pins=[PinRefIR(ref="U1", pin="7"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[PinRefIR(ref="U1", pin="4"), PinRefIR(ref="C1", pin="2")],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        result = _gv_mod.find_decoupling_caps(ir)
        assert result == {"C1": "U1"}, (
            f"Expected power-only bypass cap C1 to anchor to active IC U1, got: {result}"
        )

    def test_charge_pump_power_output_caps_stay_out_of_decoupling_map(self) -> None:
        """Caps on IC power-output nets (e.g. MAX232 VS+/VS-) are not bypass decouplers."""
        components = [
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="TTL_TX"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="RS232_TX"),
            ComponentIR(ref="U2", symbol="Interface_UART:MAX232", value="MAX232"),
            ComponentIR(ref="C64", symbol="Device:C", value="1u"),
            ComponentIR(ref="C71", symbol="Device:C", value="1u"),
            ComponentIR(ref="C72", symbol="Device:C", value="1u"),
        ]
        nets = [
            NetIR(
                name="TTL_0_TX",
                pins=[PinRefIR(ref="J1", pin="1"), PinRefIR(ref="U2", pin="11")],
            ),
            NetIR(
                name="RS232_0_TX",
                pins=[PinRefIR(ref="U2", pin="14"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="+5V",
                pins=[
                    PinRefIR(ref="C64", pin="1"),
                    PinRefIR(ref="C71", pin="2"),
                    PinRefIR(ref="U2", pin="16"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C64", pin="2"),
                    PinRefIR(ref="C72", pin="1"),
                    PinRefIR(ref="U2", pin="15"),
                ],
            ),
            NetIR(
                name="Net-(U2-VS+)",
                pins=[PinRefIR(ref="C71", pin="1"), PinRefIR(ref="U2", pin="2")],
            ),
            NetIR(
                name="Net-(U2-VS-)",
                pins=[PinRefIR(ref="C72", pin="2"), PinRefIR(ref="U2", pin="6")],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)

        assert _gv_mod.find_decoupling_caps(ir) == {"C64": "U2"}

        from kicad_pcb.layout import _find_decoupling_caps_layout  # noqa: PLC0415

        assert _find_decoupling_caps_layout(ir) == {"C64": "U2"}

    def test_shared_negative_rail_refinement_prefers_device_above_decoupler(self) -> None:
        """Shared negative rails should refine to the active device above the capacitor."""
        components = [
            ComponentIR(ref="U1", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U2", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
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
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        initial_map = _gv_mod.find_decoupling_caps(ir)
        assert initial_map == {"C1": "U2"}, (
            f"Expected stable pre-refinement anchor U2, got: {initial_map}"
        )

        refined_map = _gv_mod.refine_shared_rail_decoupling_map(
            ir,
            {
                "U1": (88.9, 30.48, 0.0),
                "U2": (96.52, 83.82, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            },
            initial_map,
        )
        assert refined_map == {"C1": "U1"}, (
            "Expected the shared negative-rail decoupler to refine to the upper active device, "
            f"got: {refined_map}"
        )

    def test_true_bypass_cap_stays_out_of_cluster_power_with_block_layout(self) -> None:
        """Block-classified decouplers should not be dumped into cluster_power."""
        components = [
            ComponentIR(ref="J1", symbol="Device:Conn", value="Input"),
            ComponentIR(ref="R1", symbol="Device:R", value="10k"),
            ComponentIR(ref="U1", symbol="Device:IC", value="OpAmp"),
            ComponentIR(ref="J3", symbol="Connector:Conn_01x02", value="Power"),
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
            NetIR(
                name="VCC",
                pins=[
                    PinRefIR(ref="J3", pin="1"),
                    PinRefIR(ref="U1", pin="2"),
                    PinRefIR(ref="C1", pin="1"),
                ],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="J3", pin="2"),
                    PinRefIR(ref="U1", pin="3"),
                    PinRefIR(ref="C1", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        block_layout = classify_circuit(ir)
        src = _gv_mod.build_dot_source(
            ir,
            block_layout=block_layout,
            sds_cols={"J1": 0, "R1": 1, "U1": 2, "J3": 0, "C1": 2},
        )

        cluster_start = src.index("cluster_power")
        cluster_end = src.index("}", cluster_start)
        cluster_body = src[cluster_start:cluster_end]

        assert "J3" in cluster_body
        assert "C1" not in cluster_body, (
            "True bypass decoupling cap should stay out of cluster_power when "
            "block classification marks it as DECOUPLING"
        )

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
