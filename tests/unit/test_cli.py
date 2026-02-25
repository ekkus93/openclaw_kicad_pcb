"""Unit tests for kicad_pcb.cli — argument parsing and dispatch surface.

Tests cover:
- 7.5a: Command argument parsing matches documented signatures (Namespace attributes)
- 7.5b: Invalid / missing args produce non-zero exit with argparse error messages
- 7.5c: --json flag is parsed globally and propagates to dispatch

All tests operate on the ArgumentParser returned by _build_parser() directly;
no subprocesses or real filesystem access required.
"""
from __future__ import annotations

import pytest
from kicad_pcb.cli import _build_parser
from kicad_pcb.commands.doctor import cmd_doctor
from kicad_pcb.commands.lint import (
    cmd_format_pcb,
    cmd_format_sch,
    cmd_lint_pcb,
    cmd_lint_sch,
    cmd_validate_pcb,
    cmd_validate_sch,
)
from kicad_pcb.commands.pcb import cmd_auto_place, cmd_auto_route, cmd_set_board_size
from kicad_pcb.commands.project import cmd_new
from kicad_pcb.commands.sch import cmd_add_component, cmd_add_net, cmd_connect

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(*argv: str) -> object:
    """Parse *argv* via _build_parser() and return the resulting Namespace."""
    return _build_parser().parse_args(list(argv))


def _assert_exits_2(*argv: str) -> None:
    """Assert that parsing *argv* causes SystemExit(2) (argparse error)."""
    with pytest.raises(SystemExit) as exc_info:
        # Suppress argparse's own stderr output during the test.
        _build_parser().parse_args(list(argv), namespace=None)
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# 7.5a — Command argument parsing matches documented signatures
# ---------------------------------------------------------------------------


class TestArgParsing:
    # -- project commands ----------------------------------------------------

    def test_new_positional(self) -> None:
        args = _parse("new", "MyProject")
        assert args.command == "new"  # type: ignore[union-attr]
        assert args.name == "MyProject"  # type: ignore[union-attr]
        assert args.func is cmd_new  # type: ignore[union-attr]

    def test_new_with_description(self) -> None:
        args = _parse("new", "MyProject", "--description", "A test board")
        assert args.description == "A test board"  # type: ignore[union-attr]

    def test_new_description_short_flag(self) -> None:
        args = _parse("new", "X", "-d", "short")
        assert args.description == "short"  # type: ignore[union-attr]

    # -- schematic mutation commands -----------------------------------------

    def test_add_component_positionals(self) -> None:
        args = _parse("add-component", "Device:R", "R1")
        assert args.lib_sym == "Device:R"  # type: ignore[union-attr]
        assert args.ref == "R1"  # type: ignore[union-attr]
        assert args.func is cmd_add_component  # type: ignore[union-attr]

    def test_add_component_optional_flags(self) -> None:
        args = _parse(
            "add-component", "Device:C", "C1",
            "--value", "100nF", "--footprint", "Cap_SMD:C_0402",
        )
        assert args.value == "100nF"  # type: ignore[union-attr]
        assert args.footprint == "Cap_SMD:C_0402"  # type: ignore[union-attr]

    def test_add_component_dry_run(self) -> None:
        args = _parse("add-component", "Device:R", "R1", "--dry-run")
        assert args.dry_run is True  # type: ignore[union-attr]

    def test_add_component_dry_run_default_false(self) -> None:
        args = _parse("add-component", "Device:R", "R1")
        assert args.dry_run is False  # type: ignore[union-attr]

    def test_add_net_name(self) -> None:
        args = _parse("add-net", "VCC")
        assert args.name == "VCC"  # type: ignore[union-attr]
        assert args.func is cmd_add_net  # type: ignore[union-attr]

    def test_add_net_with_coords(self) -> None:
        args = _parse("add-net", "GND", "--x", "100.5", "--y", "50.0")
        assert args.x == pytest.approx(100.5)  # type: ignore[union-attr]
        assert args.y == pytest.approx(50.0)  # type: ignore[union-attr]

    def test_add_net_dry_run(self) -> None:
        args = _parse("add-net", "VCC", "--dry-run")
        assert args.dry_run is True  # type: ignore[union-attr]

    def test_connect_from_to(self) -> None:
        args = _parse("connect", "--from", "10,20", "--to", "30,40")
        assert args.from_pt == "10,20"  # type: ignore[union-attr]
        assert args.to_pt == "30,40"  # type: ignore[union-attr]
        assert args.func is cmd_connect  # type: ignore[union-attr]

    def test_connect_dry_run(self) -> None:
        args = _parse("connect", "--from", "0,0", "--to", "10,0", "--dry-run")
        assert args.dry_run is True  # type: ignore[union-attr]

    # -- PCB mutation commands -----------------------------------------------

    def test_set_board_size_positional(self) -> None:
        args = _parse("set-board-size", "50x30")
        assert args.size == "50x30"  # type: ignore[union-attr]
        assert args.func is cmd_set_board_size  # type: ignore[union-attr]

    def test_set_board_size_dry_run(self) -> None:
        args = _parse("set-board-size", "100x80", "--dry-run")
        assert args.dry_run is True  # type: ignore[union-attr]

    def test_auto_place_default_spacing(self) -> None:
        args = _parse("auto-place")
        assert args.spacing == pytest.approx(10.0)  # type: ignore[union-attr]
        assert args.func is cmd_auto_place  # type: ignore[union-attr]

    def test_auto_place_custom_spacing(self) -> None:
        args = _parse("auto-place", "--spacing", "15.0")
        assert args.spacing == pytest.approx(15.0)  # type: ignore[union-attr]

    def test_auto_place_dry_run(self) -> None:
        args = _parse("auto-place", "--dry-run")
        assert args.dry_run is True  # type: ignore[union-attr]

    def test_auto_route_jar_arg(self) -> None:
        args = _parse("auto-route", "--jar", "/opt/freerouting.jar")
        assert args.jar == "/opt/freerouting.jar"  # type: ignore[union-attr]
        assert args.func is cmd_auto_route  # type: ignore[union-attr]

    def test_auto_route_jar_default_none(self) -> None:
        args = _parse("auto-route")
        assert args.jar is None  # type: ignore[union-attr]

    def test_drc_strict_flag(self) -> None:
        args = _parse("drc", "--strict")
        assert args.strict is True  # type: ignore[union-attr]

    def test_drc_strict_default_false(self) -> None:
        args = _parse("drc")
        assert args.strict is False  # type: ignore[union-attr]

    # -- lint/validate/format commands ----------------------------------------

    def test_lint_sch_path(self) -> None:
        args = _parse("lint-sch", "/tmp/board.kicad_sch")
        assert args.path == "/tmp/board.kicad_sch"  # type: ignore[union-attr]
        assert args.func is cmd_lint_sch  # type: ignore[union-attr]

    def test_lint_pcb_path(self) -> None:
        args = _parse("lint-pcb", "/tmp/board.kicad_pcb")
        assert args.path == "/tmp/board.kicad_pcb"  # type: ignore[union-attr]
        assert args.func is cmd_lint_pcb  # type: ignore[union-attr]

    def test_validate_sch_path(self) -> None:
        args = _parse("validate-sch", "/tmp/board.kicad_sch")
        assert args.path == "/tmp/board.kicad_sch"  # type: ignore[union-attr]
        assert args.func is cmd_validate_sch  # type: ignore[union-attr]

    def test_validate_pcb_path(self) -> None:
        args = _parse("validate-pcb", "/tmp/board.kicad_pcb")
        assert args.func is cmd_validate_pcb  # type: ignore[union-attr]

    def test_format_sch_path(self) -> None:
        args = _parse("format-sch", "/tmp/board.kicad_sch")
        assert args.func is cmd_format_sch  # type: ignore[union-attr]

    def test_format_pcb_path(self) -> None:
        args = _parse("format-pcb", "/tmp/board.kicad_pcb")
        assert args.func is cmd_format_pcb  # type: ignore[union-attr]

    def test_doctor_no_args(self) -> None:
        args = _parse("doctor")
        assert args.command == "doctor"  # type: ignore[union-attr]
        assert args.func is cmd_doctor  # type: ignore[union-attr]

    # -- pcbway-quote options --------------------------------------------------

    def test_pcbway_quote_defaults(self) -> None:
        args = _parse("pcbway-quote")
        assert args.quantity == 5  # type: ignore[union-attr]
        assert args.layers == 2  # type: ignore[union-attr]
        assert args.thickness == pytest.approx(1.6)  # type: ignore[union-attr]

    def test_pcbway_quote_custom(self) -> None:
        args = _parse("pcbway-quote", "-q", "10", "-l", "4", "-t", "0.8")
        assert args.quantity == 10  # type: ignore[union-attr]
        assert args.layers == 4  # type: ignore[union-attr]
        assert args.thickness == pytest.approx(0.8)  # type: ignore[union-attr]

    # -- global --json flag ---------------------------------------------------

    def test_json_flag_false_by_default(self) -> None:
        args = _parse("doctor")
        assert args.output_json is False  # type: ignore[union-attr]

    def test_json_flag_set_before_command(self) -> None:
        args = _parse("--json", "doctor")
        assert args.output_json is True  # type: ignore[union-attr]

    def test_json_flag_works_with_positional_command(self) -> None:
        args = _parse("--json", "new", "Proj")
        assert args.output_json is True  # type: ignore[union-attr]
        assert args.name == "Proj"  # type: ignore[union-attr]

    # -- no command → command is None -----------------------------------------

    def test_no_subcommand_produces_none(self) -> None:
        args = _parse()
        assert args.command is None  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# 7.5b — Invalid args produce useful messages and non-zero exit
# ---------------------------------------------------------------------------


class TestInvalidArgs:
    def test_add_component_missing_both_positionals(self) -> None:
        """add-component requires LIB:SYM and REF → exit 2."""
        _assert_exits_2("add-component")

    def test_add_component_missing_ref(self) -> None:
        """add-component with only LIB:SYM but no REF → exit 2."""
        _assert_exits_2("add-component", "Device:R")

    def test_add_net_missing_name(self) -> None:
        """add-net requires NAME → exit 2."""
        _assert_exits_2("add-net")

    def test_connect_missing_from_and_to(self) -> None:
        """connect requires --from and --to → exit 2."""
        _assert_exits_2("connect")

    def test_connect_missing_to(self) -> None:
        """connect with only --from → exit 2."""
        _assert_exits_2("connect", "--from", "0,0")

    def test_connect_missing_from(self) -> None:
        """connect with only --to → exit 2."""
        _assert_exits_2("connect", "--to", "10,10")

    def test_set_board_size_missing_size(self) -> None:
        """set-board-size requires WxH positional → exit 2."""
        _assert_exits_2("set-board-size")

    def test_lint_sch_missing_path(self) -> None:
        """lint-sch requires path positional → exit 2."""
        _assert_exits_2("lint-sch")

    def test_lint_pcb_missing_path(self) -> None:
        """lint-pcb requires path positional → exit 2."""
        _assert_exits_2("lint-pcb")

    def test_validate_sch_missing_path(self) -> None:
        _assert_exits_2("validate-sch")

    def test_validate_pcb_missing_path(self) -> None:
        _assert_exits_2("validate-pcb")

    def test_format_sch_missing_path(self) -> None:
        _assert_exits_2("format-sch")

    def test_format_pcb_missing_path(self) -> None:
        _assert_exits_2("format-pcb")

    def test_new_missing_name(self) -> None:
        """new requires a NAME positional → exit 2."""
        _assert_exits_2("new")

    def test_unrecognised_flag(self) -> None:
        """Flags that don't exist produce exit 2."""
        _assert_exits_2("doctor", "--does-not-exist")

    def test_auto_place_non_numeric_spacing(self) -> None:
        """--spacing must be a float → exit 2."""
        _assert_exits_2("auto-place", "--spacing", "fast")

    def test_pcbway_quote_non_integer_quantity(self) -> None:
        """--quantity must be an int → exit 2."""
        _assert_exits_2("pcbway-quote", "-q", "lots")

    def test_invalid_option_for_known_command(self) -> None:
        """An unrecognised option for a known sub-command → exit 2."""
        _assert_exits_2("new", "Proj", "--unknown-opt")

    def test_error_message_goes_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        """argparse writes its error message to stderr."""
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["add-component"])
        captured = capsys.readouterr()
        assert "error" in captured.err.lower() or "required" in captured.err.lower()
