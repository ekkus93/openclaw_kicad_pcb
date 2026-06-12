"""Phase 1 warning suite — real NE5532 fixture drift check."""

from __future__ import annotations

from argparse import Namespace
from typing import cast

import pytest

from kicad_pcb.commands.netlist import (
    cmd_validate_netlist,
)
from tests import NE5532_HEADPHONE_REVIEW_FIXTURE, SYMBOLS_FIXTURE_DIR

_REAL_NE5532_SYMBOLS = SYMBOLS_FIXTURE_DIR
_REAL_NE5532_REVIEW_NETLIST = NE5532_HEADPHONE_REVIEW_FIXTURE.netlist_path

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


def _normalize_warning_entries(
    warnings: tuple[dict[str, object], ...],
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


class TestPhase1WarningSuite_Drift:
    @_skip_no_real_ne5532_fixture_symbols
    def test_real_ne5532_fixture_warning_set_does_not_drift(self) -> None:
        """The real NE5532 review fixture should keep the current exact warning mix."""
        result = cmd_validate_netlist(
            Namespace(
                netlist=str(_REAL_NE5532_REVIEW_NETLIST),
                symbols_dir=str(_REAL_NE5532_SYMBOLS),
            )
        )

        assert _normalize_warning_entries(result.warnings) == [
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
