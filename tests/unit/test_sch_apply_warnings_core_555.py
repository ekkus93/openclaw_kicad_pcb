"""Phase 1 warning tests — 555, stereo, footprint warnings."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._validate import advisory_warnings
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"


def _make_ir(
    *,
    components: list[ComponentIR],
    nets: list[NetIR],
) -> CircuitIR:
    return CircuitIR(version="1", components=components, nets=nets)


class TestPhase1WarningSuite_Extended:
    def test_555_warning_set_detects_broken_pwm_topology(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="Timer:NE555", value="NE555"),
                ComponentIR(ref="Q1", symbol="Transistor_FET:Q_NMOS_GSD", value="AO3400"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="100k"),
                ComponentIR(ref="D1", symbol="Device:D", value="1N4148"),
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="100"),
                ComponentIR(ref="R3", symbol="Device:R", value="100k"),
                ComponentIR(ref="C1", symbol="Device:C", value="10uF"),
                ComponentIR(ref="C2", symbol="Device:C", value="100nF"),
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x02", value="LED_LOAD"),
            ],
            nets=[
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="Q1", pin="2"),
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="J1", pin="2"),
                    ],
                ),
                NetIR(
                    name="+12V",
                    pins=[
                        PinRefIR(ref="U1", pin="8"),
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="R1", pin="1"),
                        PinRefIR(ref="C1", pin="1"),
                        PinRefIR(ref="C2", pin="1"),
                        PinRefIR(ref="J1", pin="1"),
                    ],
                ),
                NetIR(
                    name="TRIG_ONLY",
                    pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="RV1", pin="2")],
                ),
                NetIR(
                    name="THRESH_ONLY",
                    pins=[PinRefIR(ref="U1", pin="6"), PinRefIR(ref="R3", pin="1")],
                ),
                NetIR(name="CTRL", pins=[PinRefIR(ref="U1", pin="5"), PinRefIR(ref="C2", pin="1")]),
                NetIR(
                    name="DISCH",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="D1", pin="1"),
                    ],
                ),
                NetIR(
                    name="OUT_DRV", pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R2", pin="1")]
                ),
                NetIR(
                    name="GATE",
                    pins=[
                        PinRefIR(ref="R2", pin="2"),
                        PinRefIR(ref="Q1", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(name="LED_NEG", pins=[PinRefIR(ref="Q1", pin="3")]),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }

        assert {
            "TIMER555_TIMING_NODE_SPLIT",
            "TIMER555_CTRL_CAP_WRONG_TARGET",
            "TIMER555_STEERING_NETWORK_INVALID",
            "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
            "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
        } <= codes

        severities = {
            warning["code"]: warning.get("severity")
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
            if isinstance(warning.get("code"), str)
        }
        assert severities["TIMER555_TIMING_NODE_SPLIT"] == "hard_fail"
        assert severities["TIMER555_CTRL_CAP_WRONG_TARGET"] == "hard_fail"
        assert severities["TIMER555_STEERING_NETWORK_INVALID"] == "hard_fail"
        assert severities["TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID"] == "hard_fail"
        assert severities["TIMER555_PWM_FREQUENCY_OUT_OF_RANGE"] == "warning"

    def test_stereo_trs_warning_fires_for_non_paired_tip_and_ring_nets(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="J1", symbol="Connector:AudioJack3", value="Stereo In"),
            ],
            nets=[
                NetIR(name="LEFT_IN", pins=[PinRefIR(ref="J1", pin="T")]),
                NetIR(name="AUX_SEND", pins=[PinRefIR(ref="J1", pin="R")]),
                NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="S")]),
            ],
        )

        warnings = advisory_warnings(ir)
        codes = {warning["code"] for warning in warnings}

        assert "TRS_STEREO_IMPLEMENTATION_INCOMPLETE" in codes

    def test_footprint_warnings_flag_placeholder_and_mismatch_cases(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(
                    ref="U1",
                    symbol="Timer:NE555",
                    value="NE555",
                    footprint="Timer:NE555",
                ),
                ComponentIR(
                    ref="Q1",
                    symbol="Transistor_FET:Q_NMOS_GSD",
                    value="AO3400",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                ),
                ComponentIR(
                    ref="J1",
                    symbol="Connector:AudioJack3",
                    value="Audio Out",
                    footprint="Resistor_SMD:R_0402_1005Metric",
                ),
            ],
            nets=[
                NetIR(name="GND", pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="Q1", pin="2")]),
                NetIR(name="VCC", pins=[PinRefIR(ref="U1", pin="8"), PinRefIR(ref="J1", pin="S")]),
                NetIR(name="OUT", pins=[PinRefIR(ref="Q1", pin="3"), PinRefIR(ref="J1", pin="T")]),
            ],
        )

        codes = {warning["code"] for warning in advisory_warnings(ir)}

        assert "FOOTPRINT_LOOKS_PLACEHOLDER_OR_SYMBOL_ID" in codes
        assert "FOOTPRINT_CLASS_MISMATCH" in codes

    def test_footprint_warnings_accept_expected_package_classes(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(
                    ref="U1",
                    symbol="Timer:NE555",
                    value="NE555",
                    footprint="Package_DIP:DIP-8_W7.62mm",
                ),
                ComponentIR(
                    ref="Q1",
                    symbol="Transistor_FET:Q_NMOS_GSD",
                    value="AO3400",
                    footprint="Package_TO_SOT_SMD:SOT-23",
                ),
                ComponentIR(
                    ref="RV1",
                    symbol="Device:R_Potentiometer",
                    value="10k",
                    footprint="Potentiometer_THT:Potentiometer_Bourns_3386P_Vertical",
                ),
                ComponentIR(
                    ref="J1",
                    symbol="Connector_Generic:Conn_01x02",
                    value="Load",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                ),
                ComponentIR(
                    ref="J2",
                    symbol="Connector:AudioJack3",
                    value="Audio Out",
                    footprint="Connector_Audio:Jack_3.5mm_CUI_SJ1-3523N_Horizontal",
                ),
            ],
            nets=[
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="Q1", pin="2"),
                        PinRefIR(ref="J2", pin="S"),
                    ],
                ),
                NetIR(
                    name="VCC",
                    pins=[PinRefIR(ref="U1", pin="8"), PinRefIR(ref="J1", pin="1")],
                ),
                NetIR(
                    name="GATE",
                    pins=[PinRefIR(ref="Q1", pin="1"), PinRefIR(ref="RV1", pin="2")],
                ),
                NetIR(
                    name="LOAD",
                    pins=[
                        PinRefIR(ref="Q1", pin="3"),
                        PinRefIR(ref="J1", pin="2"),
                        PinRefIR(ref="J2", pin="T"),
                    ],
                ),
            ],
        )

        footprint_codes = {
            warning["code"]
            for warning in advisory_warnings(ir)
            if str(warning["code"]).startswith("FOOTPRINT_")
        }

        assert footprint_codes == set()
