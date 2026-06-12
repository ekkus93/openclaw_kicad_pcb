"""Phase 1 warning suite: 555 pwm, NE5532 regression, feedback, and full-validate tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest

from kicad_pcb.circuit_ir import CircuitIR, ComponentIR, NetIR, PinRefIR
from kicad_pcb.commands._validate import advisory_warnings, full_validate
from kicad_pcb.errors import UserError
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


class TestPhase1WarningSuite_Regression:
    def test_555_warning_set_accepts_valid_pwm_topology(self) -> None:
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="Timer:NE555", value="NE555"),
                ComponentIR(ref="Q1", symbol="Transistor_FET:Q_NMOS_GSD", value="AO3400"),
                ComponentIR(ref="RV1", symbol="Device:R_Potentiometer", value="100k"),
                ComponentIR(ref="D1", symbol="Device:D", value="1N4148"),
                ComponentIR(ref="D2", symbol="Device:D", value="1N4148"),
                ComponentIR(ref="R1", symbol="Device:R", value="1k"),
                ComponentIR(ref="R2", symbol="Device:R", value="100"),
                ComponentIR(ref="R3", symbol="Device:R", value="100k"),
                ComponentIR(ref="C1", symbol="Device:C", value="22nF"),
                ComponentIR(ref="C2", symbol="Device:C", value="100nF"),
                ComponentIR(ref="C3", symbol="Device:C_Polarized", value="47uF"),
                ComponentIR(ref="C4", symbol="Device:C", value="10nF"),
                ComponentIR(ref="J1", symbol="Connector_Generic:Conn_01x02", value="LED_LOAD"),
            ],
            nets=[
                NetIR(
                    name="GND",
                    pins=[
                        PinRefIR(ref="U1", pin="1"),
                        PinRefIR(ref="Q1", pin="2"),
                        PinRefIR(ref="R3", pin="2"),
                        PinRefIR(ref="C1", pin="2"),
                        PinRefIR(ref="C2", pin="2"),
                        PinRefIR(ref="C3", pin="2"),
                        PinRefIR(ref="C4", pin="2"),
                    ],
                ),
                NetIR(
                    name="+12V",
                    pins=[
                        PinRefIR(ref="U1", pin="8"),
                        PinRefIR(ref="U1", pin="4"),
                        PinRefIR(ref="R1", pin="1"),
                        PinRefIR(ref="C2", pin="1"),
                        PinRefIR(ref="C3", pin="1"),
                        PinRefIR(ref="J1", pin="1"),
                    ],
                ),
                NetIR(
                    name="TIMING",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="U1", pin="6"),
                        PinRefIR(ref="RV1", pin="2"),
                        PinRefIR(ref="C1", pin="1"),
                    ],
                ),
                NetIR(
                    name="DISCH",
                    pins=[
                        PinRefIR(ref="U1", pin="7"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="D1", pin="1"),
                        PinRefIR(ref="D2", pin="2"),
                    ],
                ),
                NetIR(
                    name="POT_A", pins=[PinRefIR(ref="RV1", pin="1"), PinRefIR(ref="D1", pin="2")]
                ),
                NetIR(
                    name="POT_B", pins=[PinRefIR(ref="RV1", pin="3"), PinRefIR(ref="D2", pin="1")]
                ),
                NetIR(name="CTRL", pins=[PinRefIR(ref="U1", pin="5"), PinRefIR(ref="C4", pin="1")]),
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
                NetIR(
                    name="LED_NEG", pins=[PinRefIR(ref="Q1", pin="3"), PinRefIR(ref="J1", pin="2")]
                ),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }

        unexpected_codes = {
            "TIMER555_GROUND_PIN_INVALID",
            "TIMER555_VCC_PIN_INVALID",
            "TIMER555_RESET_NOT_TIED_HIGH",
            "TIMER555_TIMING_NODE_SPLIT",
            "TIMER555_TIMING_CAP_NOT_TO_GROUND",
            "TIMER555_TIMING_CAP_ACROSS_SUPPLY",
            "TIMER555_CTRL_CAP_MISSING_TO_GROUND",
            "TIMER555_CTRL_CAP_WRONG_TARGET",
            "TIMER555_STEERING_NETWORK_INVALID",
            "TIMER555_GATE_RESISTOR_MISSING",
            "TIMER555_GATE_PULLDOWN_MISSING",
            "TIMER555_GATE_PULLDOWN_TOUCHES_TIMING_NODE",
            "TIMER555_LOW_SIDE_LOAD_TOPOLOGY_INVALID",
            "TIMER555_PWM_FREQUENCY_OUT_OF_RANGE",
        }
        assert codes.isdisjoint(unexpected_codes)

    @_skip_no_real_ne5532_fixture_symbols
    def test_real_ne5532_fixture_warning_set_does_not_drift(self) -> None:
        ir = CircuitIR.load(_REAL_NE5532_REVIEW_NETLIST)
        warnings = advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))

        assert _normalize_warning_entries(warnings) == [
            (
                "HEADPHONE_OUTPUT_IMPEDANCE_HIGH",
                (
                    ("connector_output_nets", ["HP_L_OUT"]),
                    ("downstream_net", "AFTER_R6"),
                    ("output_net", "OUT_L_STAGE2_RAW"),
                    ("output_pin", "7"),
                    ("ref", "U1"),
                    ("resistor_refs", ["R6"]),
                    ("series_ohms", 47.0),
                    ("symbol", "Amplifier_Operational:NE5532"),
                ),
            ),
            (
                "SPLIT_RAIL_INTERSTAGE_AC_COUPLING_PRESENT",
                (
                    ("capacitor_refs", ["C6"]),
                    ("coupled_net", "BUF_L_IN"),
                    ("output_net", "OUT_L_STAGE1"),
                    ("output_pin", "1"),
                    ("ref", "U1"),
                    ("symbol", "Amplifier_Operational:NE5532"),
                    ("target_inputs", ["U1:5"]),
                ),
            ),
        ]

    def test_feedback_warning_not_emitted_for_local_feedback_bridge(self) -> None:
        """A direct output-to-inverting-input feedback bridge should not warn."""
        ir = _make_ir(
            components=[
                ComponentIR(ref="U1", symbol="TestLib:SingleOpAmp"),
                ComponentIR(ref="R1", symbol="TestLib:R"),
                ComponentIR(ref="R2", symbol="TestLib:R"),
            ],
            nets=[
                NetIR(
                    name="VIN",
                    pins=[PinRefIR(ref="U1", pin="1"), PinRefIR(ref="R1", pin="1")],
                ),
                NetIR(
                    name="U1_INV",
                    pins=[
                        PinRefIR(ref="U1", pin="2"),
                        PinRefIR(ref="R1", pin="2"),
                        PinRefIR(ref="R2", pin="1"),
                    ],
                ),
                NetIR(
                    name="U1_OUT",
                    pins=[PinRefIR(ref="U1", pin="3"), PinRefIR(ref="R2", pin="2")],
                ),
            ],
        )

        codes = {
            warning["code"]
            for warning in advisory_warnings(ir, SymbolIndex(symbols_dir=_FIXTURES_DIR))
        }
        assert "OPAMP_FEEDBACK_MISSING_OR_NONLOCAL" not in codes
        assert "OPAMP_OUTPUT_FLOATING" not in codes


# ---------------------------------------------------------------------------
# full_validate
# ---------------------------------------------------------------------------


class TestFullValidate:
    """full_validate(path, symbol_index) -> CircuitIR."""

    def _write_ir(self, path: Path, data: object) -> Path:
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_passes_valid_ir(self, tmp_path: Path) -> None:
        """A well-formed IR JSON passes all three layers and returns a CircuitIR."""
        data = {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "Device:R"}],
            "nets": [{"name": "N1", "pins": [{"ref": "R1", "pin": "1"}]}],
        }
        p = self._write_ir(tmp_path / "valid.json", data)
        ir = full_validate(p, SymbolIndex(symbols_dir=None))
        assert len(ir.components) == 1
        assert ir.components[0].ref == "R1"

    def test_raises_on_schema_error(self, tmp_path: Path) -> None:
        """A file missing required fields raises UserError at schema layer."""
        # Missing 'components' and 'nets' → Pydantic validation error
        data = {"version": "1"}
        p = self._write_ir(tmp_path / "schema_err.json", data)
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))

    def test_raises_on_semantic_error(self, tmp_path: Path) -> None:
        """A net referencing an undefined component ref raises UserError at layer 2."""
        data = {
            "version": "1",
            "components": [{"ref": "R1", "symbol": "Device:R"}],
            "nets": [
                {
                    "name": "N1",
                    "pins": [
                        {"ref": "R1", "pin": "1"},
                        {"ref": "UNDEFINED", "pin": "2"},  # unknown component
                    ],
                }
            ],
        }
        p = self._write_ir(tmp_path / "semantic_err.json", data)
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))

    def test_raises_on_invalid_json(self, tmp_path: Path) -> None:
        """A corrupt JSON file raises UserError (not a bare json.JSONDecodeError)."""
        p = tmp_path / "corrupt.json"
        p.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(UserError):
            full_validate(p, SymbolIndex(symbols_dir=None))
