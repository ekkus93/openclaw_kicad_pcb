"""Warning IR helpers and Phase 1 warning suite tests."""

from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

from kicad_pcb.commands.netlist import (
    cmd_validate_netlist,
)
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

_KICAD_SYSTEM_SYMBOLS = Path("/usr/share/kicad/symbols")
_REAL_NE5532_SYMBOLS = SYMBOLS_FIXTURE_DIR
_REAL_NE5532_REVIEW_NETLIST = NE5532_HEADPHONE_REVIEW_FIXTURE.netlist_path

_skip_no_system_symbols = pytest.mark.skipif(
    not (_KICAD_SYSTEM_SYMBOLS / "Amplifier_Operational.kicad_sym").exists(),
    reason="KiCad system symbol libraries not installed at /usr/share/kicad/symbols",
)

_skip_no_real_ne5532_fixture_symbols = pytest.mark.skipif(
    not all(
        (
            _REAL_NE5532_SYMBOLS / "Amplifier_Operational.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Device.kicad_sym",
            _REAL_NE5532_SYMBOLS / "Connector_Generic.kicad_sym",
        )
    ),
    reason="Portable NE5532 regression symbol fixtures are missing",
)


def _write_input_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:R", "value": "Input"},
            {"ref": "C5", "symbol": "TestLib:R", "value": "1u"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "100k"},
            {"ref": "RV1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "LEFT_IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "C5", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "IN_L_AC",
                "pins": [
                    {"ref": "C5", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                    {"ref": "RV1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "RV1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_bypass_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:R", "value": "Driver"},
            {"ref": "C7", "symbol": "TestLib:R", "value": "220u"},
            {"ref": "R8", "symbol": "TestLib:R", "value": "47"},
            {"ref": "J2", "symbol": "TestLib:R", "value": "Output"},
        ],
        "nets": [
            {
                "name": "OUT_L_STAGE2_RAW",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "C7", "pin": "1"},
                    {"ref": "R8", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C7", "pin": "2"},
                    {"ref": "R8", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_load_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Driver"},
            {"ref": "C1", "symbol": "Device:C", "value": "220u"},
            {"ref": "J2", "symbol": "TestLib:Conn3", "value": "Output"},
        ],
        "nets": [
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "C1", "pin": "1"},
                ],
            },
            {
                "name": "HP_L_OUT",
                "pins": [
                    {"ref": "C1", "pin": "2"},
                    {"ref": "J2", "pin": "1"},
                ],
            },
            {
                "name": "VIN",
                "pins": [{"ref": "U1", "pin": "1"}],
            },
            {
                "name": "U1_INV",
                "pins": [{"ref": "U1", "pin": "2"}],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_connector_ambiguity_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "Stereo-ish"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "IN",
                "pins": [
                    {"ref": "J1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "GND",
                "pins": [
                    {"ref": "J1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_feedback_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:R", "value": "Out"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_stage_topology_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R2", "symbol": "Device:R", "value": "10k"},
            {"ref": "J1", "symbol": "TestLib:Conn3", "value": "In"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "J1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R2", "pin": "1"},
                ],
            },
            {
                "name": "U1_OUT",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "R2", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_floating_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_output_short_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {"ref": "U1", "symbol": "TestLib:SingleOpAmp", "value": "Gain"},
            {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
            {"ref": "P1", "symbol": "TestLib:R", "value": "Rail"},
        ],
        "nets": [
            {
                "name": "VIN",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "R1", "pin": "1"},
                ],
            },
            {
                "name": "U1_INV",
                "pins": [
                    {"ref": "U1", "pin": "2"},
                    {"ref": "R1", "pin": "2"},
                ],
            },
            {
                "name": "VPLUS15",
                "pins": [
                    {"ref": "U1", "pin": "3"},
                    {"ref": "P1", "pin": "1"},
                ],
            },
            {
                "name": "BIAS",
                "pins": [
                    {"ref": "P1", "pin": "2"},
                ],
            },
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_speaker_driver_warning_ir(path: Path) -> None:
    payload = {
        "version": "1",
        "components": [
            {
                "ref": "U1",
                "symbol": "Amplifier_Operational:NE5532",
                "value": "NE5532",
            },
            {
                "ref": "J1",
                "symbol": "Connector:AudioJack3",
                "value": "Speaker Out",
            },
        ],
        "nets": [
            {"name": "VIN", "pins": [{"ref": "U1", "pin": "3"}]},
            {"name": "U1_INV", "pins": [{"ref": "U1", "pin": "2"}]},
            {
                "name": "SPEAKER_OUT",
                "pins": [
                    {"ref": "U1", "pin": "1"},
                    {"ref": "J1", "pin": "T"},
                ],
            },
            {"name": "VMINUS15", "pins": [{"ref": "U1", "pin": "4"}]},
            {"name": "VPLUS15", "pins": [{"ref": "U1", "pin": "8"}]},
            {"name": "GND", "pins": [{"ref": "J1", "pin": "S"}]},
        ],
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class TestPhase1WarningSuite:
    @pytest.mark.parametrize(
        ("filename", "writer", "expected_codes"),
        [
            (
                "warning_ir.json",
                _write_input_bypass_warning_ir,
                {"INPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "output_warning_ir.json",
                _write_output_bypass_warning_ir,
                {"OUTPUT_COUPLING_BYPASSED_BY_RESISTOR"},
            ),
            (
                "connector_warning_ir.json",
                _write_connector_ambiguity_ir,
                {"CONNECTOR_UNUSED_PINS_AMBIGUOUS"},
            ),
            (
                "feedback_warning_ir.json",
                _write_feedback_warning_ir,
                {"OPAMP_FEEDBACK_MISSING_OR_NONLOCAL"},
            ),
            (
                "stage_topology_warning_ir.json",
                _write_stage_topology_warning_ir,
                {"OPAMP_STAGE_TOPOLOGY_LIKELY_MISTAKEN"},
            ),
            (
                "output_load_warning_ir.json",
                _write_output_load_warning_ir,
                {"OUTPUT_CAP_NO_DEFINED_LOAD_OR_BLEED"},
            ),
            (
                "output_floating_warning_ir.json",
                _write_output_floating_warning_ir,
                {"OPAMP_OUTPUT_FLOATING"},
            ),
            (
                "output_short_warning_ir.json",
                _write_output_short_warning_ir,
                {"OPAMP_OUTPUT_SHORTED_TO_RAIL"},
            ),
            (
                "speaker_driver_warning_ir.json",
                _write_speaker_driver_warning_ir,
                {"OPAMP_PRESENTED_AS_SPEAKER_POWER_STAGE"},
            ),
        ],
        ids=[
            "input-coupling",
            "output-coupling",
            "connector-ambiguity",
            "missing-feedback",
            "stage-topology-likely-mistaken",
            "output-cap-no-load-or-bleed",
            "output-floating",
            "output-shorted-to-rail",
            "speaker-power-stage",
        ],
    )
    def test_synthetic_warning_fixtures_cover_each_phase1_family(
        self,
        tmp_path: Path,
        filename: str,
        writer,
        expected_codes: set[str],
    ) -> None:
        ir_path = tmp_path / filename
        writer(ir_path)
        fixtures_dir = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

        result = cmd_validate_netlist(
            Namespace(
                netlist=str(ir_path),
                symbols_dir=str(fixtures_dir),
            )
        )

        codes = {warning["code"] for warning in result.warnings}
        assert expected_codes <= codes
