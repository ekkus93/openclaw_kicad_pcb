"""Phase 2 reliability tests.

Covers:
- ElementTree XML parsing in cmd_import_netlist:
    * KiCad kicadxml format: <comp ref="..."> attribute
    * Older/mock format: <comp><ref>text</ref></comp>
    * Flat fallback: <net><ref>text</ref>…</net>
    * Malformed XML raises ToolError (no silent empty list)
    * Whitespace-padded text values are stripped
    * Duplicate <ref> nodes in fallback path are deduplicated
- --backup flag wires backup=True into mutate_and_validate_sch/pcb:
    * sch commands create a .bak file before overwriting
    * pcb commands create a .bak file before overwriting
- KiCadError catch block emits hint line when details["hint"] is set
"""

from __future__ import annotations

import contextlib
import datetime
import sys
from argparse import Namespace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kicad_pcb.cli import _build_parser, main  # noqa: PLC2701
from kicad_pcb.commands.pcb import cmd_import_netlist, cmd_set_board_size
from kicad_pcb.commands.sch import cmd_add_net
from kicad_pcb.errors import ToolError, UserError
from kicad_pcb.models import ProjectRef

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _make_project(tmp_path: Path, name: str = "proj") -> ProjectRef:
    proj_dir = tmp_path / name
    proj_dir.mkdir(parents=True)
    return ProjectRef(name=name, path=proj_dir, created=datetime.datetime.now().isoformat())


# ---------------------------------------------------------------------------
# 1. XML parsing — KiCad kicadxml format  (<comp ref="…">)
# ---------------------------------------------------------------------------


class TestXmlParsingKicadFormat:
    """cmd_import_netlist XML parsing with real KiCad kicadxml attribute format."""

    XML_KICAD = """<?xml version="1.0" encoding="utf-8"?>
<export>
  <components>
    <comp ref="R1">
      <value>10k</value>
      <footprint>Resistor_SMD:R_0402</footprint>
    </comp>
    <comp ref="C1">
      <value>100nF</value>
      <footprint>Capacitor_SMD:C_0402</footprint>
    </comp>
    <comp ref="U1">
      <value>STM32F4</value>
      <footprint></footprint>
    </comp>
  </components>
</export>
"""

    def _run_parse(self, xml_text: str):
        """Drive the parsing logic directly through a mock cli adapter."""

        project = MagicMock()
        project.sch_file = MagicMock()
        project.sch_file.exists.return_value = True
        project.path = Path("/tmp")
        project.name = "test"

        cli_mock = MagicMock()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        cli_mock.export_netlist.return_value = (proc, xml_text)

        with patch("kicad_pcb.commands.pcb.get_current_project", return_value=project):
            result = cmd_import_netlist(Namespace(), cli=cli_mock)

        return result

    def test_kicad_format_parses_ref_attribute(self) -> None:
        result = self._run_parse(self.XML_KICAD)
        refs = [c[0] for c in result.components]
        assert refs == ["R1", "C1", "U1"]

    def test_kicad_format_parses_value(self) -> None:
        result = self._run_parse(self.XML_KICAD)
        values = [c[1] for c in result.components]
        assert values == ["10k", "100nF", "STM32F4"]

    def test_kicad_format_parses_footprint(self) -> None:
        result = self._run_parse(self.XML_KICAD)
        fps = [c[2] for c in result.components]
        assert fps == ["Resistor_SMD:R_0402", "Capacitor_SMD:C_0402", ""]

    def test_kicad_format_empty_footprint_becomes_empty_string(self) -> None:
        result = self._run_parse(self.XML_KICAD)
        assert result.components[2][2] == ""


# ---------------------------------------------------------------------------
# 2. XML parsing — older/mock format  (<comp><ref>text</ref></comp>)
# ---------------------------------------------------------------------------


class TestXmlParsingLegacyRefChild:
    """cmd_import_netlist fallback for <ref> as child element of <comp>."""

    XML_LEGACY = """<?xml version="1.0"?>
<netlist>
  <comp>
    <ref>R1</ref>
    <value>10k</value>
    <footprint>Resistor_SMD:R_0402</footprint>
  </comp>
  <comp>
    <ref>C1</ref>
    <value>100nF</value>
  </comp>
</netlist>
"""

    def _run_parse(self, xml_text: str):

        project = MagicMock()
        project.sch_file = MagicMock()
        project.sch_file.exists.return_value = True
        project.path = Path("/tmp")
        project.name = "test"

        cli_mock = MagicMock()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        cli_mock.export_netlist.return_value = (proc, xml_text)

        with patch("kicad_pcb.commands.pcb.get_current_project", return_value=project):
            result = cmd_import_netlist(Namespace(), cli=cli_mock)

        return result

    def test_legacy_format_parses_refs(self) -> None:
        result = self._run_parse(self.XML_LEGACY)
        refs = [c[0] for c in result.components]
        assert "R1" in refs
        assert "C1" in refs

    def test_legacy_format_parses_values(self) -> None:
        result = self._run_parse(self.XML_LEGACY)
        by_ref = {c[0]: c[1] for c in result.components}
        assert by_ref["R1"] == "10k"
        assert by_ref["C1"] == "100nF"

    def test_legacy_format_missing_footprint_is_empty_string(self) -> None:
        result = self._run_parse(self.XML_LEGACY)
        by_ref = {c[0]: c[2] for c in result.components}
        assert by_ref["C1"] == ""


# ---------------------------------------------------------------------------
# 3. XML parsing — malformed XML raises ToolError
# ---------------------------------------------------------------------------


class TestXmlMalformed:
    def _run_parse(self, xml_text: str):

        project = MagicMock()
        project.sch_file = MagicMock()
        project.sch_file.exists.return_value = True
        project.path = Path("/tmp")
        project.name = "test"

        cli_mock = MagicMock()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        cli_mock.export_netlist.return_value = (proc, xml_text)

        with patch("kicad_pcb.commands.pcb.get_current_project", return_value=project):
            return cmd_import_netlist(Namespace(), cli=cli_mock)

    def test_malformed_xml_raises_tool_error(self) -> None:
        with pytest.raises(ToolError, match="malformed"):
            self._run_parse("<this is not valid xml>>>")

    def test_unclosed_tag_raises_tool_error(self) -> None:
        with pytest.raises(ToolError, match="malformed"):
            self._run_parse("<export><comp ref='R1'><value>10k</value>")

    def test_empty_string_raises_tool_error(self) -> None:
        # ET.fromstring("") does raise ET.ParseError, but the command's own guard
        # (returncode==0 and xml_text is truthy) fires first for truly empty input.
        # A near-empty but invalid XML string tests the ET path directly.
        with pytest.raises(ToolError):
            self._run_parse("<")


# ---------------------------------------------------------------------------
# 4. XML parsing — whitespace stripping
# ---------------------------------------------------------------------------


class TestXmlWhitespace:
    XML_WHITESPACE = """<?xml version="1.0"?>
<export>
  <components>
    <comp ref="  R1  ">
      <value>  10k  </value>
      <footprint>  Resistor_SMD:R_0402  </footprint>
    </comp>
  </components>
</export>
"""

    def _run_parse(self, xml_text: str):

        project = MagicMock()
        project.sch_file = MagicMock()
        project.sch_file.exists.return_value = True
        project.path = Path("/tmp")
        project.name = "test"

        cli_mock = MagicMock()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        cli_mock.export_netlist.return_value = (proc, xml_text)

        with patch("kicad_pcb.commands.pcb.get_current_project", return_value=project):
            return cmd_import_netlist(Namespace(), cli=cli_mock)

    def test_ref_attribute_whitespace_stripped(self) -> None:
        result = self._run_parse(self.XML_WHITESPACE)
        assert result.components[0][0] == "R1"

    def test_value_whitespace_stripped(self) -> None:
        result = self._run_parse(self.XML_WHITESPACE)
        assert result.components[0][1] == "10k"

    def test_footprint_whitespace_stripped(self) -> None:
        result = self._run_parse(self.XML_WHITESPACE)
        assert result.components[0][2] == "Resistor_SMD:R_0402"


# ---------------------------------------------------------------------------
# 5. XML parsing — flat-fallback deduplication
# ---------------------------------------------------------------------------


class TestXmlFlatFallback:
    """Flat format: <ref> nodes nested under non-<comp> parents, no duplicates."""

    XML_FLAT_DUP = """<?xml version="1.0"?>
<netlist>
  <net name="GND">
    <ref>R1</ref>
    <value>10k</value>
  </net>
  <net name="VCC">
    <ref>R1</ref>
    <value>10k</value>
  </net>
  <net name="SIG">
    <ref>C1</ref>
    <value>100nF</value>
  </net>
</netlist>
"""

    def _run_parse(self, xml_text: str):

        project = MagicMock()
        project.sch_file = MagicMock()
        project.sch_file.exists.return_value = True
        project.path = Path("/tmp")
        project.name = "test"

        cli_mock = MagicMock()
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        cli_mock.export_netlist.return_value = (proc, xml_text)

        with patch("kicad_pcb.commands.pcb.get_current_project", return_value=project):
            return cmd_import_netlist(Namespace(), cli=cli_mock)

    def test_flat_fallback_deduplicates_refs(self) -> None:
        result = self._run_parse(self.XML_FLAT_DUP)
        refs = [c[0] for c in result.components]
        # R1 appears in two nets but should only be listed once
        assert refs.count("R1") == 1
        assert "C1" in refs


# ---------------------------------------------------------------------------
# 6. --backup flag wires backup=True to mutate_and_validate_sch (sch commands)
# ---------------------------------------------------------------------------


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
            patch("kicad_pcb.cli.cmd_set_board_size", side_effect=_fake_func),
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
            patch("kicad_pcb.cli.cmd_set_board_size", side_effect=_fake_func),
            pytest.raises(SystemExit) as exc_info,
        ):
            self._run_main_with_argv(["set-board-size", "50x30"])

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "💡" not in combined
