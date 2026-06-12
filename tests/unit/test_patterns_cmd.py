"""Pattern registry, result dataclasses, and command application tests."""

from __future__ import annotations

import argparse
import datetime
import json
import types
from pathlib import Path
from textwrap import dedent

import pytest

from kicad_pcb.cli import _build_parser
from kicad_pcb.commands import patterns as _cmd_patterns
from kicad_pcb.commands.patterns import cmd_apply_pattern
from kicad_pcb.config import set_current_project
from kicad_pcb.errors import UserError
from kicad_pcb.formatting import format_result, format_result_json
from kicad_pcb.models import ProjectRef
from kicad_pcb.results import ApplyPatternResult

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""


class TestApplyPatternResult:
    def test_fields_present(self) -> None:
        r = ApplyPatternResult(
            pattern="resistor-divider",
            components=("R1", "R2"),
            nets=("VIN", "VOUT", "GND"),
            dry_run=True,
        )
        assert r.pattern == "resistor-divider"
        assert r.components == ("R1", "R2")
        assert r.nets == ("VIN", "VOUT", "GND")
        assert r.dry_run is True

    def test_dry_run_false_by_default(self) -> None:
        r = ApplyPatternResult(pattern="decoupling-cap", components=("C1",), nets=("VCC",))
        assert r.dry_run is False


# ===========================================================================
# cmd_apply_pattern — integration via pipeline with temp file
# ===========================================================================


class TestCmdApplyPattern:
    """Tests that exercise the full command path using a real temp schematic."""

    @pytest.fixture(autouse=True)  # noqa: F811
    def _no_sym_library(self, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[override]
        """Redirect symbol library to a nonexistent path for all cmd tests.

        When the library is unavailable, ``_place_component`` falls back to
        pin list ``["1", "2"]`` and embeds a minimal stub symbol so that the
        SCH009 lint check passes without a real KiCad installation.
        """
        monkeypatch.setattr(
            "kicad_pcb._lib_symbol_primitives._DEFAULT_SYMBOLS_DIR", Path("/nonexistent")
        )

    _MINIMAL_SCH = dedent("""\
        (kicad_sch (version 20230121) (generator test)
          (lib_symbols)
          (sheet_instances (path "/" (page "1")))
        )
    """)

    def _make_args(self, **kwargs) -> types.SimpleNamespace:
        """Build a minimal argparse-like namespace for cmd_apply_pattern."""
        defaults = dict(
            pattern="decoupling-cap",
            r1="R1",
            r2="R2",
            r1_value="10k",
            r2_value="10k",
            vin_net="VIN",
            vout_net="VOUT",
            r="R1",
            d="D1",
            r_value="330",
            d_value="LED",
            vcc_net="VCC",
            gnd_net="GND",
            conn="J1",
            n_pins=4,
            net_prefix="IO",
            c="C1",
            c_value="100nF",
            symbols_dir=None,
            dry_run=True,  # always dry-run in unit tests
        )
        defaults.update(kwargs)
        return types.SimpleNamespace(**defaults)

    def test_cmd_apply_pattern_decoupling_cap_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="decoupling-cap", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "decoupling-cap"
        assert "C1" in result.components
        assert "VCC" in result.nets
        assert "GND" in result.nets
        assert result.dry_run is True
        # dry-run must not modify the schematic file
        assert "Device:C" not in sch.read_text(encoding="utf-8")

    def test_cmd_apply_pattern_resistor_divider_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="resistor-divider", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "resistor-divider"
        assert "R1" in result.components
        assert "R2" in result.components

    def test_cmd_apply_pattern_led_resistor_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        # re-register project
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="led-resistor", r="R1", d="D1", dry_run=True)
        result = cmd_apply_pattern(args)

        assert result.pattern == "led-resistor"
        assert "R1" in result.components
        assert "D1" in result.components

    def test_cmd_apply_pattern_connector_breakout_dry_run(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(
            pattern="connector-breakout", conn="J2", n_pins=3, net_prefix="A", dry_run=True
        )
        result = cmd_apply_pattern(args)

        assert result.pattern == "connector-breakout"
        assert "J2" in result.components
        assert len(result.nets) == 3

    def test_unknown_pattern_raises_user_error(self, tmp_path: Path) -> None:
        sch = tmp_path / "myproject.kicad_sch"
        sch.write_text(self._MINIMAL_SCH, encoding="utf-8")
        pro = tmp_path / "myproject.kicad_pro"
        pro.write_text("{}", encoding="utf-8")
        ref = ProjectRef(
            name="myproject",
            path=tmp_path,
            created=datetime.datetime.now().isoformat(),
            description="",
        )
        set_current_project(ref)

        args = self._make_args(pattern="banana-circuit", dry_run=True)
        with pytest.raises(UserError, match="Unknown pattern"):
            cmd_apply_pattern(args)

    def test_no_project_raises_user_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_cmd_patterns, "get_current_project", lambda: None)
        args = self._make_args(pattern="decoupling-cap", dry_run=True)
        with pytest.raises(UserError, match="[Nn]o project"):
            cmd_apply_pattern(args)


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
