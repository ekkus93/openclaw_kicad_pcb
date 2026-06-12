"""Pattern command — CLI arg parsing and output formatter tests."""

from __future__ import annotations

import argparse
import json

import pytest

from kicad_pcb.cli import _build_parser
from kicad_pcb.commands.patterns import cmd_apply_pattern
from kicad_pcb.formatting import format_result, format_result_json
from kicad_pcb.results import ApplyPatternResult

pytestmark = pytest.mark.unit


# ===========================================================================
# CLI arg parsing for apply-pattern
# ===========================================================================


class TestCliArgParsing:
    def _parse(self, argv: list[str]) -> argparse.Namespace:
        return _build_parser().parse_args(argv)

    def test_apply_pattern_subcommand_registered(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.command == "apply-pattern"

    def test_default_pattern_args_for_decoupling_cap(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.pattern == "decoupling-cap"
        assert ns.c == "C1"
        assert ns.c_value == "100nF"
        assert ns.vcc_net == "VCC"
        assert ns.gnd_net == "GND"

    def test_resistor_divider_custom_args(self) -> None:
        ns = self._parse(
            [
                "apply-pattern",
                "--pattern",
                "resistor-divider",
                "--r1",
                "RA1",
                "--r2",
                "RA2",
                "--r1-value",
                "47k",
                "--r2-value",
                "22k",
                "--vin-net",
                "PWR5V",
                "--vout-net",
                "TAP",
            ]
        )
        assert ns.pattern == "resistor-divider"
        assert ns.r1 == "RA1"
        assert ns.r2 == "RA2"
        assert ns.r1_value == "47k"
        assert ns.r2_value == "22k"
        assert ns.vin_net == "PWR5V"
        assert ns.vout_net == "TAP"

    def test_led_resistor_custom_args(self) -> None:
        ns = self._parse(
            [
                "apply-pattern",
                "--pattern",
                "led-resistor",
                "--r",
                "R10",
                "--d",
                "D10",
                "--r-value",
                "470",
                "--d-value",
                "LED_GREEN",
                "--vcc-net",
                "3V3",
                "--gnd-net",
                "DGND",
            ]
        )
        assert ns.r == "R10"
        assert ns.d == "D10"
        assert ns.r_value == "470"
        assert ns.d_value == "LED_GREEN"
        assert ns.vcc_net == "3V3"

    def test_connector_breakout_custom_args(self) -> None:
        ns = self._parse(
            [
                "apply-pattern",
                "--pattern",
                "connector-breakout",
                "--conn",
                "P2",
                "--n-pins",
                "8",
                "--net-prefix",
                "GPIO",
            ]
        )
        assert ns.conn == "P2"
        assert ns.n_pins == 8
        assert ns.net_prefix == "GPIO"

    def test_dry_run_flag(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap", "--dry-run"])
        assert ns.dry_run is True

    def test_symbols_dir_flag(self) -> None:
        ns = self._parse(
            [
                "apply-pattern",
                "--pattern",
                "decoupling-cap",
                "--symbols-dir",
                "/custom/kicad/symbols",
            ]
        )
        assert ns.symbols_dir == "/custom/kicad/symbols"

    def test_invalid_pattern_name_rejected(self) -> None:
        with pytest.raises(SystemExit):
            self._parse(["apply-pattern", "--pattern", "banana-circuit"])

    def test_func_is_cmd_apply_pattern(self) -> None:
        ns = self._parse(["apply-pattern", "--pattern", "decoupling-cap"])
        assert ns.func is cmd_apply_pattern


# ===========================================================================
# Formatter
# ===========================================================================


class TestApplyPatternFormatter:
    def test_success_output_mentions_pattern_name(self) -> None:
        r = ApplyPatternResult(
            pattern="decoupling-cap",
            components=("C1",),
            nets=("VCC", "GND"),
        )
        lines = format_result(r)
        combined = " ".join(lines)
        assert "decoupling-cap" in combined

    def test_success_output_lists_components(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
        )
        lines = format_result(r)
        combined = " ".join(lines)
        assert "R1" in combined
        assert "R2" in combined

    def test_dry_run_prefix_present(self) -> None:
        r = ApplyPatternResult(
            pattern="led-resistor",
            components=("R1", "D1"),
            nets=("VCC", "GND"),
            dry_run=True,
        )
        assert any("DRY RUN" in line for line in format_result(r))

    def test_no_dry_run_prefix_when_not_dry(self) -> None:
        r = ApplyPatternResult(
            pattern="led-resistor",
            components=("R1", "D1"),
            nets=("VCC", "GND"),
            dry_run=False,
        )
        assert not any("DRY RUN" in line for line in format_result(r))

    def test_net_names_listed(self) -> None:
        r = ApplyPatternResult(
            pattern="decoupling-cap",
            components=("C1",),
            nets=("3V3", "AGND"),
        )
        combined = " ".join(format_result(r))
        assert "3V3" in combined
        assert "AGND" in combined

    def test_json_serialisable(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
        )
        text = format_result_json(r)
        obj = json.loads(text)
        assert obj["pattern"] == "resistor-divider"
        assert obj["components"] == ["R1", "R2"]
