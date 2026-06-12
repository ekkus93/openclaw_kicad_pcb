"""Phase 4: decoupling cap detection — advanced rail refinement and edge cases."""

from __future__ import annotations

import pytest

import kicad_pcb.graphviz_layout as _gv_mod
from kicad_pcb.block_detection import classify_circuit
from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR

pytestmark = pytest.mark.unit


class TestFindDecouplingCaps_Advanced:
    """Advanced decoupling cap detection: rail refinement and edge cases."""

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
