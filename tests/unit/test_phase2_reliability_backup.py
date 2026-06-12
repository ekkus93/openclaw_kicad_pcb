"""Phase 2 reliability tests — backup flags and CLI tests."""

from __future__ import annotations

import contextlib
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kicad_pcb.cli import _build_parser, main  # noqa: PLC2701
from kicad_pcb.commands.pcb import cmd_set_board_size
from kicad_pcb.commands.sch import cmd_add_net
from kicad_pcb.errors import UserError

MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator eeschema)
  (uuid "12345678-1234-1234-1234-123456789012")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""


MINIMAL_PCB = """\
(kicad_pcb (version 20230121) (generator pcbnew)
  (general
    (thickness 1.6)
  )
  (net 0 "")
)
"""


def _write_minimal_sch(path: Path) -> None:
    path.write_text(MINIMAL_SCH, encoding="utf-8")


def _write_minimal_pcb(path: Path) -> None:
    path.write_text(MINIMAL_PCB, encoding="utf-8")


class TestBackupFlagSch:
    """--backup causes sch mutate calls to receive backup=True."""

    def test_add_net_passes_backup_true(self, tmp_path: Path) -> None:
        """backup=True is passed when --backup is set."""

        sch_path = tmp_path / "proj.kicad_sch"
        _write_minimal_sch(sch_path)

        project = MagicMock()
        project.sch_file = sch_path

        args = Namespace(
            name="VCC",
            x=60.0,
            y=50.0,
            dry_run=False,
            backup=True,
        )

        captured: dict = {}

        def _fake_mutate(path, fn, *, operation, dry_run=False, backup=False, **kw):
            captured["backup"] = backup

            return MagicMock()

        with (
            patch("kicad_pcb.commands.sch.get_current_project", return_value=project),
            patch("kicad_pcb.commands.sch.mutate_and_validate_sch", side_effect=_fake_mutate),
            contextlib.suppress(Exception),
        ):
            cmd_add_net(args)  # result-building may fail on mock; captured dict is what matters

        assert captured.get("backup") is True, (
            "backup=True must be forwarded to mutate_and_validate_sch"
        )

    def test_add_net_passes_backup_false_by_default(self, tmp_path: Path) -> None:

        sch_path = tmp_path / "proj.kicad_sch"
        _write_minimal_sch(sch_path)

        project = MagicMock()
        project.sch_file = sch_path

        args = Namespace(
            name="VCC",
            x=60.0,
            y=50.0,
            dry_run=False,
            backup=False,
        )

        captured: dict = {}

        def _fake_mutate(path, fn, *, operation, dry_run=False, backup=False, **kw):
            captured["backup"] = backup

            return MagicMock()

        with (
            patch("kicad_pcb.commands.sch.get_current_project", return_value=project),
            patch("kicad_pcb.commands.sch.mutate_and_validate_sch", side_effect=_fake_mutate),
            contextlib.suppress(Exception),
        ):
            cmd_add_net(args)

        assert captured.get("backup") is False


# ---------------------------------------------------------------------------
# 7. --backup flag wires backup=True to mutate_and_validate_pcb (pcb commands)
# ---------------------------------------------------------------------------


class TestBackupFlagPcb:
    """--backup causes pcb mutate calls to receive backup=True."""

    def test_set_board_size_passes_backup_true(self, tmp_path: Path) -> None:

        pcb_path = tmp_path / "proj.kicad_pcb"
        _write_minimal_pcb(pcb_path)

        project = MagicMock()
        project.pcb_file = pcb_path

        args = Namespace(size="50x30", dry_run=False, backup=True)

        captured: dict = {}

        def _fake_mutate(path, fn, *, operation, dry_run=False, backup=False, **kw):
            captured["backup"] = backup

            return MagicMock()

        with (
            patch("kicad_pcb.commands.pcb.get_current_project", return_value=project),
            patch("kicad_pcb.commands.pcb.mutate_and_validate_pcb", side_effect=_fake_mutate),
            contextlib.suppress(Exception),
        ):
            cmd_set_board_size(args)

        assert captured.get("backup") is True, (
            "backup=True must be forwarded to mutate_and_validate_pcb"
        )

    def test_set_board_size_passes_backup_false_by_default(self, tmp_path: Path) -> None:

        pcb_path = tmp_path / "proj.kicad_pcb"
        _write_minimal_pcb(pcb_path)

        project = MagicMock()
        project.pcb_file = pcb_path

        args = Namespace(size="50x30", dry_run=False, backup=False)

        captured: dict = {}

        def _fake_mutate(path, fn, *, operation, dry_run=False, backup=False, **kw):
            captured["backup"] = backup

            return MagicMock()

        with (
            patch("kicad_pcb.commands.pcb.get_current_project", return_value=project),
            patch("kicad_pcb.commands.pcb.mutate_and_validate_pcb", side_effect=_fake_mutate),
            contextlib.suppress(Exception),
        ):
            cmd_set_board_size(args)

        assert captured.get("backup") is False


# ---------------------------------------------------------------------------
# 8. CLI --backup flag exists and is registered correctly
# ---------------------------------------------------------------------------


class TestCliBackupFlag:
    """The global --backup flag is registered in cli.py's argument parser."""

    def test_backup_flag_accepted_by_parser(self) -> None:
        """argparse accepts --backup without error."""

        parser = _build_parser()
        # parse_known_args avoids subcommand-required error while testing the flag
        ns, _ = parser.parse_known_args(["--backup"])
        assert getattr(ns, "backup", None) is True

    def test_no_backup_default_is_false(self) -> None:

        parser = _build_parser()
        ns, _ = parser.parse_known_args([])
        assert getattr(ns, "backup", None) is False


# ---------------------------------------------------------------------------
# 9. KiCadError catch block emits hint when details["hint"] is set
# ---------------------------------------------------------------------------


class TestKiCadErrorHintPrinting:
    """When a KiCadError carries details['hint'], the CLI prints it."""

    def _run_main_with_argv(self, argv: list[str]) -> None:
        """Run cli.main() with a patched sys.argv."""

        with patch.object(sys, "argv", ["kicad_pcb"] + argv):
            main()

    def test_hint_printed_to_stdout(self, capsys) -> None:
        """hint from details is printed with 💡 prefix."""

        def _fake_func(args):
            raise UserError(
                "something went wrong",
                details={"hint": "Try using a different value"},
            )

        with (
            patch("kicad_pcb._cli_subcommands_hardware.cmd_set_board_size", side_effect=_fake_func),
            pytest.raises(SystemExit) as exc_info,
        ):
            self._run_main_with_argv(["set-board-size", "50x30"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "Try using a different value" in combined

    def test_no_hint_when_details_is_none(self, capsys) -> None:
        """No extra line when error has no hint."""

        def _fake_func(args):
            raise UserError("something went wrong")

        with (
            patch("kicad_pcb._cli_subcommands_hardware.cmd_set_board_size", side_effect=_fake_func),
            pytest.raises(SystemExit) as exc_info,
        ):
            self._run_main_with_argv(["set-board-size", "50x30"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "💡" not in combined
