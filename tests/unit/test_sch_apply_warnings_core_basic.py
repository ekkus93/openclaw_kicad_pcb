"""TestPhase1WarningSuite and TestFullValidate from commands/_sch_apply and _validate."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._validate import advisory_warnings
from kicad_pcb.symbol_index import SymbolIndex

pytestmark = pytest.mark.unit

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"
_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_REVIEW_NETLIST = (
    Path(__file__).resolve().parents[2] / "docs" / "circuits" / "ne5532_headphone_amp_netlist.json"
)


def _make_ir(
    *,
    components: list[ComponentIR],
    nets: list[NetIR],
) -> CircuitIR:
    return CircuitIR(version="1", components=components, nets=nets)


def _normalize_warning_entries(
    warnings: list[dict[str, object]],
) -> list[tuple[str, tuple[tuple[str, object], ...]]]:
    normalized: list[tuple[str, tuple[tuple[str, object], ...]]] = []
    for warning in warnings:
        code = warning.get("code")
        if not isinstance(code, str):
            continue
        details_obj = warning.get("details")
        details = cast(dict[str, object], details_obj) if isinstance(details_obj, dict) else {}
        normalized.append((code, tuple(sorted(details.items()))))
    return sorted(normalized)


_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)

_skip_no_real_ne5532_fixture_symbols = pytest.mark.skipif(
    not all(
        (
            _FIXTURES_DIR / "Amplifier_Operational.kicad_sym",
            _FIXTURES_DIR / "Connector.kicad_sym",
            _FIXTURES_DIR / "Device.kicad_sym",
            _FIXTURES_DIR / "Connector_Generic.kicad_sym",
        )
    ),
    reason="Portable NE5532 regression symbol fixtures are missing",
)


class TestPhase1WarningSuite:
    @pytest.mark.parametrize(
        ("ir", "expected_codes"),
        [
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="J1", symbol="Lib:J"),
                        ComponentIR(ref="C5", symbol="Device:C"),
                        ComponentIR(ref="R1", symbol="Device:R"),
                        ComponentIR(ref="RV1", symbol="Lib:P"),
                    ],
                    nets=[
                        NetIR(
                            name="LEFT_IN",
                            pins=[
                                PinRefIR(ref="J1", pin="1"),
                                PinRefIR(ref="C5", pin="1"),
                                PinRefIR(ref="R1", pin="1"),
                            ],
                        ),
                        NetIR(
                            name="IN_L_AC",
                            pins=[
                                PinRefIR(ref="C5", pin="2"),
                                PinRefIR(ref="R1", pin="2"),
                                PinRefIR(ref="RV1", pin="1"),
                            ],
                        ),
                    ],
                ),
                {"INPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="Lib:U"),
                        ComponentIR(ref="C7", symbol="Device:C"),
                        ComponentIR(ref="R8", symbol="Device:R"),
                        ComponentIR(ref="J2", symbol="Lib:J"),
                    ],
                    nets=[
                        NetIR(
                            name="OUT_L_STAGE2_RAW",
                            pins=[
                                PinRefIR(ref="U1", pin="1"),
                                PinRefIR(ref="C7", pin="1"),
                                PinRefIR(ref="R8", pin="1"),
                            ],
                        ),
                        NetIR(
                            name="HP_L_OUT",
                            pins=[
                                PinRefIR(ref="C7", pin="2"),
                                PinRefIR(ref="R8", pin="2"),
                                PinRefIR(ref="J2", pin="1"),
                            ],
                        ),
                    ],
                ),
                {"OUTPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                _make_ir(
                    components=[ComponentIR(ref="J1", symbol="TestLib:Conn3")],
                    nets=[
                        NetIR(name="IN", pins=[PinRefIR(ref="J1", pin="1")]),
                        NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="2")]),
                    ],
                ),
                {"CONNECTOR_UNUSED_PINS_AMBIGUOUS"},
            ),
            (
                _make_ir(
                    components=[ComponentIR(ref="J1", symbol="Connector:AudioJack3")],
                    nets=[
                        NetIR(name="LEFT_IN", pins=[PinRefIR(ref="J1", pin="T")]),
                        NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="S")]),
                    ],
                ),
                set(),
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                        ComponentIR(ref="J1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                        NetIR(
                            name="U1_OUT",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="J1", pin="1")],
                        ),
                    ],
                ),
                {"OPAMP_FEEDBACK_MISSING_OR_NONLOCAL"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="C1", symbol="Device:C"),
                        ComponentIR(ref="J1", symbol="TestLib:Conn3"),
                    ],
                    nets=[
                        NetIR(name="VIN", pins=[PinRefIR(ref="U1", pin="1")]),
                        NetIR(name="U1_INV", pins=[PinRefIR(ref="U1", pin="2")]),
                        NetIR(
                            name="U1_OUT",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="C1", pin="1")],
                        ),
                        NetIR(
                            name="HP_L_OUT",
                            pins=[PinRefIR(ref="C1", pin="2"), PinRefIR(ref="J1", pin="1")],
                        ),
                    ],
                ),
                {"OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R2", symbol="Device:R"),
                        ComponentIR(ref="J1", symbol="TestLib:Conn3"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R2", pin="1")],
                        ),
                        NetIR(
                            name="U1_OUT",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R2", pin="2")],
                        ),
                    ],
                ),
                {"OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                    ],
                ),
                {"OPAMP_OUTPUT_FLOATING"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                        ComponentIR(ref="R1", symbol="TestLib:R"),
                        ComponentIR(ref="P1", symbol="TestLib:R"),
                    ],
                    nets=[
                        NetIR(
                            name="VIN",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                        ),
                        NetIR(
                            name="U1_INV",
                            pins=[PinRefIR(ref="U1", pin="2"), PinRefIR(ref="R1", pin="2")],
                        ),
                        NetIR(
                            name="VPLUS15",
                            pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="P1", pin="1")],
                        ),
                        NetIR(name="BIAS", pins=[PinRefIR(ref="P1", pin="2")]),
                    ],
                ),
                {"OPAMP_OUTPUT_SHORTED_TO_RAIL"},
            ),
            (
                _make_ir(
                    components=[
                        ComponentIR(
                            ref="U1",
                            symbol="Amplifier_Operational:NE5532",
                            value="NE5532",
                        ),
                        ComponentIR(ref="J1", symbol="Connector:AudioJack3", value="Speaker Out"),
                    ],
                    nets=[
                        NetIR(name="VIN", pins=[PinRefIR(ref="U1", pin="3")]),
                        NetIR(name="U1_INV", pins=[PinRefIR(ref="U1", pin="2")]),
                        NetIR(
                            name="SPEAKER_OUT",
                            pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J1", pin="T")],
                        ),
                        NetIR(name="VMINUS15", pins=[PinRefIR(ref="U1", pin="4")]),
                        NetIR(name="VPLUS15", pins=[PinRefIR(ref="U1", pin="8")]),
                        NetIR(name="GND", pins=[PinRefIR(ref="J1", pin="S")]),
                    ],
                ),
                {"OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE"},
            ),
        ],
        ids=[
            "input-coupling",
            "output-coupling",
            "connector-ambiguity",
            "mono-trs-policy",
            "missing-feedback",
            "output-cap-no-load-or-bleed",
            "stage-topology-likely-mistaken",
            "output-floating",
            "output-shorted-to-rail",
            "speaker-power-stage",
        ],
    )
    def test_synthetic_warning_fixtures_cover_each_phase1_family(
        self,
        ir: CircuitIR,
        expected_codes: set[str],
    ) -> None:
        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert expected_codes <= codes

    def test_output_load_warning_not_emitted_when_output_side_has_bleed_resistor(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                ComponentIR(ref="C1", symbol="Device:C"),
                ComponentIR(ref="R5", symbol="Device:R"),
                ComponentIR(ref="J1", symbol="TestLib:Conn3"),
            ],
            nets=[
                NetIR(name="VIN", pins=[PinRefIR(ref="U1", pin="1")]),
                NetIR(name="U1_INV", pins=[PinRefIR(ref="U1", pin="2")]),
                NetIR(
                    name="U1_OUT",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="C1", pin="1")],
                ),
                NetIR(
                    name="HP_L_OUT",
                    pins=[
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="R5", pin="1"),
                        PinRefIR(ref="J1", pin="1"),
                    ],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R5", pin="2"), PinRefIR(ref="J1", pin="2")]),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert "OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED" not in codes

    def test_stage_topology_warning_not_emitted_for_valid_noninverting_stage(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                ComponentIR(ref="R2", symbol="Device:R"),
                ComponentIR(ref="R3", symbol="Device:R"),
                ComponentIR(ref="J1", symbol="TestLib:Conn3"),
            ],
            nets=[
                NetIR(name="VIN", pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="J1", pin="1")]),
                NetIR(
                    name="U1_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                        PinRefIR(ref="R3", pin="1"),
                    ],
                ),
                NetIR(
                    name="U1_OUT",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R2", pin="2")],
                ),
                NetIR(name="GND", pins=[PinRefIR(ref="R3", pin="2"), PinRefIR(ref="J1", pin="2")]),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert "OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN" not in codes
