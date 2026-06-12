from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR


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


pytestmark = pytest.mark.unit


class TestDecouplingCapCoLocation_Rail:
    def test_compute_symbol_positions_refines_shared_negative_rail_anchor(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should re-anchor ambiguous negative-rail decouplers after raw layout."""
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

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U2"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        assert round(result["C1"][0], 2) == round(result["U1"][0], 2), (
            f"Expected C1 to re-anchor to U1 after shared negative-rail refinement, got: {result}"
        )

    def test_shared_negative_rail_refinement_uses_signal_siblings_when_only_power_unit_touches_rail(
        self,
    ) -> None:
        """Shared rails should refine through sibling signal units when the rail only hits U1P."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]

        ir = CircuitIR(version="1", components=components, nets=nets)
        refined_map = _gv_mod.refine_shared_rail_decoupling_map(
            ir,
            {
                "U1A": (88.9, 30.48, 0.0),
                "U1B": (96.52, 83.82, 0.0),
                "U1P": (92.71, 57.15, 0.0),
                "C1": (76.2, 76.2, 0.0),
                "J1": (30.48, 30.48, 0.0),
                "J2": (30.48, 83.82, 0.0),
            },
            {"C1": "U1B"},
        )
        assert refined_map == {"C1": "U1A"}, (
            "Expected sibling signal units to participate in shared negative-rail refinement "
            f"when only the power unit touches the rail, got: {refined_map}"
        )

    def test_compute_symbol_positions_refines_shared_negative_rail_anchor_through_power_unit(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The engine should refine split-unit shared rails even when only U1P is on the rail."""
        components = [
            ComponentIR(ref="U1A", symbol="Amplifier_Operational:TL071", value="UpperActive"),
            ComponentIR(ref="U1B", symbol="Amplifier_Operational:TL071", value="LowerActive"),
            ComponentIR(ref="U1P", symbol="Amplifier_Operational:TL071", value="PowerUnit"),
            ComponentIR(ref="C1", symbol="Device:C", value="100n"),
            ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x01", value="Signal1"),
            ComponentIR(ref="J2", symbol="Connector_Generic:Conn_01x01", value="Signal2"),
        ]
        nets = [
            NetIR(
                name="VEE",
                pins=[PinRefIR(ref="U1P", pin="1"), PinRefIR(ref="C1", pin="1")],
            ),
            NetIR(
                name="SIG_A",
                pins=[PinRefIR(ref="U1A", pin="2"), PinRefIR(ref="J1", pin="1")],
            ),
            NetIR(
                name="SIG_B",
                pins=[PinRefIR(ref="U1B", pin="2"), PinRefIR(ref="J2", pin="1")],
            ),
            NetIR(
                name="GND",
                pins=[
                    PinRefIR(ref="C1", pin="2"),
                    PinRefIR(ref="J1", pin="2"),
                    PinRefIR(ref="J2", pin="2"),
                    PinRefIR(ref="U1P", pin="2"),
                ],
            ),
        ]
        ir = CircuitIR(version="1", components=components, nets=nets)

        def fake_run_dot(
            self_engine: object, dot_source: str
        ) -> dict[str, tuple[float, float, float | None]]:
            return {
                _gv_mod._safe_id("U1A"): (88.9, 30.48, 0.0),
                _gv_mod._safe_id("U1B"): (96.52, 83.82, 0.0),
                _gv_mod._safe_id("U1P"): (92.71, 57.15, 0.0),
                _gv_mod._safe_id("C1"): (76.2, 76.2, 0.0),
                _gv_mod._safe_id("J1"): (30.48, 30.48, 0.0),
                _gv_mod._safe_id("J2"): (30.48, 83.82, 0.0),
            }

        monkeypatch.setattr(_gv_mod.GraphvizLayoutEngine, "_run_dot", fake_run_dot)
        engine = _gv_mod.GraphvizLayoutEngine(dot_path="dot")

        result = engine.compute_symbol_positions(ir)
        family_center_x = round((result["U1A"][0] + result["U1B"][0]) / 2.0, 2)
        assert round(result["C1"][0], 2) == family_center_x, (
            "Expected the split-unit shared-rail decoupler to stay centered on the final "
            f"signal-family span after sibling refinement, got: {result}"
        )
        assert round(result["C1"][0], 2) == round(result["U1P"][0], 2), (
            "Expected the split-unit shared-rail decoupler to follow the recentered power unit "
            f"over the sibling signal span, got: {result}"
        )

    def test_dot_source_unchanged_without_decoupling_map(self) -> None:
        """build_dot_source without decoupling_map must not co-locate C1 via invisible edge."""
        ir = _decoupling_ir()
        src = _gv_mod.build_dot_source(ir)  # no decoupling_map kwarg
        # Structural tier-anchor invisible elements are always present; the test
        # ensures that no invisible *co-location* edge is added for C1 when no
        # decoupling_map is supplied.
        invis_lines = [ln for ln in src.splitlines() if "style=invis" in ln and "C1" in ln]
        assert not invis_lines, (
            "C1 should not have invisible co-location edges without decoupling_map; "
            f"found: {invis_lines}"
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
