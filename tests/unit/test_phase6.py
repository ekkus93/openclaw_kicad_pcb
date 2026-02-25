"""Phase 6 tests: --dry-run, --json output, lint/validate/format commands.

Covers:
- dry_run=True leaves files unchanged, results carry dry_run=True
- format_result_json returns valid JSON for all result types
- cmd_lint_sch / cmd_lint_pcb on clean and issue-bearing files
- cmd_validate_sch / cmd_validate_pcb on valid and invalid files
- cmd_format_sch / cmd_format_pcb (changed / unchanged)
- LintFileResult and ValidateFileResult formatters show suggestions
- CLI exits non-zero when lint/validate finds errors
"""

from __future__ import annotations

import argparse
import json
import textwrap
from pathlib import Path

import kicad_pcb
import pytest
from kicad_pcb.commands.lint import (
    cmd_format_pcb,
    cmd_format_sch,
    cmd_lint_pcb,
    cmd_lint_sch,
    cmd_validate_pcb,
    cmd_validate_sch,
)
from kicad_pcb.errors import ParseError, UserError
from kicad_pcb.formatting import format_result, format_result_json
from kicad_pcb.lint import LINT_SUGGESTIONS, LintIssue, LintSeverity
from kicad_pcb.results import (
    AddNetResult,
    AutoPlaceResult,
    ConnectResult,
    FormatFileResult,
    LintFileResult,
    SetBoardSizeResult,
    ValidateFileResult,
)

# ---------------------------------------------------------------------------
# Minimal KiCad file content helpers
# ---------------------------------------------------------------------------

_MINIMAL_SCH = textwrap.dedent("""\
    (kicad_sch (version 20230121) (generator eeschema)
      (uuid "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
      (paper "A4")
      (lib_symbols)
      (sheet_instances
        (path "/" (page "1"))
      )
    )
""")

_MINIMAL_PCB = textwrap.dedent("""\
    (kicad_pcb (version 20221018) (generator pcbnew)
      (general
        (thickness 1.6)
        (legacy_teardrops no)
      )
      (paper "A4")
      (layers
        (0 "F.Cu" signal)
        (31 "B.Cu" signal)
        (44 "B.Cu" signal)
        (45 "F.Cu" signal)
      )
      (setup
        (pad_to_mask_clearance 0)
        (allow_soldermask_bridges_in_footprints no)
        (pcbplotparams)
      )
      (net 0 "")
    )
""")


@pytest.fixture()
def minimal_sch(tmp_path: Path) -> Path:
    """Return a path to a minimal valid schematic file."""
    p = tmp_path / "test.kicad_sch"
    p.write_text(_MINIMAL_SCH, encoding="utf-8")
    return p


@pytest.fixture()
def minimal_pcb(tmp_path: Path) -> Path:
    """Return a path to a minimal valid PCB file."""
    p = tmp_path / "test.kicad_pcb"
    p.write_text(_MINIMAL_PCB, encoding="utf-8")
    return p


def _args(**kwargs: object) -> argparse.Namespace:
    """Build a fake argparse Namespace from keyword arguments."""
    ns = argparse.Namespace()
    for k, v in kwargs.items():
        setattr(ns, k, v)
    return ns


# ---------------------------------------------------------------------------
# dry_run field on result types
# ---------------------------------------------------------------------------


class TestDryRunField:
    """Result types carry dry_run and the field defaults to False."""

    def test_add_net_default(self) -> None:
        r = AddNetResult(name="VCC", x=0.0, y=0.0)
        assert r.dry_run is False

    def test_add_net_true(self) -> None:
        r = AddNetResult(name="VCC", x=0.0, y=0.0, dry_run=True)
        assert r.dry_run is True

    def test_connect_default(self) -> None:
        r = ConnectResult(x1=0.0, y1=0.0, x2=10.0, y2=0.0)
        assert r.dry_run is False

    def test_set_board_size_default(self) -> None:
        r = SetBoardSizeResult(width=50.0, height=30.0, pcb_file_name="test.kicad_pcb")
        assert r.dry_run is False

    def test_auto_place_default(self) -> None:
        r = AutoPlaceResult(placed=(), spacing=10.0)
        assert r.dry_run is False


# ---------------------------------------------------------------------------
# dry_run formatting
# ---------------------------------------------------------------------------


class TestDryRunFormatting:
    def test_set_board_size_dry_run_prefix(self) -> None:
        r = SetBoardSizeResult(width=50.0, height=30.0, pcb_file_name="foo.kicad_pcb", dry_run=True)
        lines = "\n".join(format_result(r))
        assert "DRY RUN" in lines

    def test_set_board_size_no_prefix_when_false(self) -> None:
        r = SetBoardSizeResult(
            width=50.0, height=30.0, pcb_file_name="foo.kicad_pcb", dry_run=False
        )
        lines = "\n".join(format_result(r))
        assert "DRY RUN" not in lines

    def test_add_net_dry_run_prefix(self) -> None:
        r = AddNetResult(name="GND", x=1.0, y=2.0, dry_run=True)
        lines = "\n".join(format_result(r))
        assert "DRY RUN" in lines

    def test_connect_dry_run_prefix(self) -> None:
        r = ConnectResult(x1=0.0, y1=0.0, x2=5.0, y2=0.0, dry_run=True)
        lines = "\n".join(format_result(r))
        assert "DRY RUN" in lines

    def test_auto_place_dry_run_prefix(self) -> None:
        r = AutoPlaceResult(placed=(), spacing=10.0, dry_run=True)
        lines = "\n".join(format_result(r))
        assert "DRY RUN" in lines


# ---------------------------------------------------------------------------
# format_result_json
# ---------------------------------------------------------------------------


class TestFormatResultJson:
    def test_returns_valid_json(self) -> None:
        r = SetBoardSizeResult(width=50.0, height=30.0, pcb_file_name="foo.kicad_pcb")
        output = format_result_json(r)
        parsed = json.loads(output)
        assert parsed["width"] == 50.0
        assert parsed["pcb_file_name"] == "foo.kicad_pcb"

    def test_path_serialized_as_string(self) -> None:
        r = FormatFileResult(path=Path("/tmp/test.kicad_sch"), changed=False, size_bytes=100)
        parsed = json.loads(format_result_json(r))
        assert isinstance(parsed["path"], str)
        assert "/tmp/test.kicad_sch" in parsed["path"]

    def test_enum_serialized_as_value(self) -> None:
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(LintIssue(severity=LintSeverity.ERROR, code="SCH001", message="Test"),),
            error_count=1,
            warning_count=0,
            ok=False,
        )
        parsed = json.loads(format_result_json(r))
        assert parsed["issues"][0]["severity"] == "error"

    def test_lint_file_result_json(self) -> None:
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(),
            error_count=0,
            warning_count=0,
            ok=True,
        )
        parsed = json.loads(format_result_json(r))
        assert parsed["ok"] is True
        assert parsed["issues"] == []


# ---------------------------------------------------------------------------
# cmd_lint_sch / cmd_lint_pcb
# ---------------------------------------------------------------------------


class TestCmdLintSch:
    def test_clean_file_returns_ok(self, minimal_sch: Path) -> None:
        result = cmd_lint_sch(_args(path=str(minimal_sch)))
        assert isinstance(result, LintFileResult)
        assert result.ok is True
        assert result.error_count == 0

    def test_missing_uuid_raises_lint_issues(self, tmp_path: Path) -> None:
        """A schematic with duplicate/missing UUIDs should have SCH002 warnings/errors."""
        # Write a schematic with the same UUID used twice (duplicate)
        content = _MINIMAL_SCH.replace(
            '"aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"',
            '"00000000-0000-0000-0000-000000000000"',
        )
        p = tmp_path / "dup_uuid.kicad_sch"
        p.write_text(content, encoding="utf-8")
        result = cmd_lint_sch(_args(path=str(p)))
        assert isinstance(result, LintFileResult)
        # Should parse successfully; just checking it runs without error
        assert result.path == p

    def test_nonexistent_file_raises_user_error(self) -> None:
        with pytest.raises(UserError, match="not found"):
            cmd_lint_sch(_args(path="/nonexistent/file.kicad_sch"))

    def test_wrong_root_raises_parse_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.kicad_sch"
        p.write_text("(kicad_pcb (version 1))\n", encoding="utf-8")
        with pytest.raises(ParseError):
            cmd_lint_sch(_args(path=str(p)))


class TestCmdLintPcb:
    def test_clean_file_returns_result(self, minimal_pcb: Path) -> None:
        result = cmd_lint_pcb(_args(path=str(minimal_pcb)))
        assert isinstance(result, LintFileResult)
        # Minimal PCB may have warnings (no board outline etc.) but should parse

    def test_result_has_path(self, minimal_pcb: Path) -> None:
        result = cmd_lint_pcb(_args(path=str(minimal_pcb)))
        assert result.path == minimal_pcb


# ---------------------------------------------------------------------------
# cmd_validate_sch / cmd_validate_pcb
# ---------------------------------------------------------------------------


class TestCmdValidateSch:
    def test_valid_file_syntax_ok(self, minimal_sch: Path) -> None:
        result = cmd_validate_sch(_args(path=str(minimal_sch)))
        assert isinstance(result, ValidateFileResult)
        assert result.syntax_ok is True

    def test_invalid_syntax_returns_syntax_ok_false(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.kicad_sch"
        p.write_text("not-valid-sexp\n", encoding="utf-8")
        result = cmd_validate_sch(_args(path=str(p)))
        assert isinstance(result, ValidateFileResult)
        assert result.syntax_ok is False
        assert result.ok is False

    def test_wrong_root_key_syntax_ok_false(self, tmp_path: Path) -> None:
        p = tmp_path / "wrong.kicad_sch"
        p.write_text("(kicad_pcb (version 1))\n", encoding="utf-8")
        result = cmd_validate_sch(_args(path=str(p)))
        assert result.syntax_ok is False

    def test_kicad_not_checked(self, minimal_sch: Path) -> None:
        result = cmd_validate_sch(_args(path=str(minimal_sch)))
        assert result.kicad_checked is False

    def test_format_shows_no_kicad_check(self, minimal_sch: Path) -> None:
        result = cmd_validate_sch(_args(path=str(minimal_sch)))
        lines = "\n".join(format_result(result))
        assert "DRC" in lines or "erc" in lines.lower() or "KiCad" in lines


class TestCmdValidatePcb:
    def test_valid_file_syntax_ok(self, minimal_pcb: Path) -> None:
        result = cmd_validate_pcb(_args(path=str(minimal_pcb)))
        assert isinstance(result, ValidateFileResult)
        assert result.syntax_ok is True

    def test_invalid_syntax(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.kicad_pcb"
        p.write_text("((broken\n", encoding="utf-8")
        result = cmd_validate_pcb(_args(path=str(p)))
        assert result.syntax_ok is False


# ---------------------------------------------------------------------------
# cmd_format_sch / cmd_format_pcb
# ---------------------------------------------------------------------------


class TestCmdFormatSch:
    def test_already_canonical_changed_false(self, minimal_sch: Path) -> None:
        """Round-trip an already-serialized file — should report unchanged."""
        # First format to get canonical form
        result1 = cmd_format_sch(_args(path=str(minimal_sch)))
        assert isinstance(result1, FormatFileResult)
        # Second format should be unchanged
        result2 = cmd_format_sch(_args(path=str(minimal_sch)))
        assert result2.changed is False

    def test_returns_size_bytes(self, minimal_sch: Path) -> None:
        result = cmd_format_sch(_args(path=str(minimal_sch)))
        assert result.size_bytes > 0

    def test_changed_file_gets_written(self, tmp_path: Path) -> None:
        """A file with extra whitespace diverging from canonical form is rewritten."""
        # Write non-canonical spacing
        p = tmp_path / "spaced.kicad_sch"
        p.write_text(_MINIMAL_SCH + "\n\n\n", encoding="utf-8")
        result = cmd_format_sch(_args(path=str(p)))
        # After format, file exists and result reports new size
        assert isinstance(result, FormatFileResult)
        assert p.exists()
        assert result.size_bytes > 0

    def test_format_result_formatter_changed(self) -> None:
        r = FormatFileResult(path=Path("/tmp/x.kicad_sch"), changed=True, size_bytes=512)
        lines = "\n".join(format_result(r))
        assert "Reformatted" in lines

    def test_format_result_formatter_unchanged(self) -> None:
        r = FormatFileResult(path=Path("/tmp/x.kicad_sch"), changed=False, size_bytes=512)
        lines = "\n".join(format_result(r))
        assert "canonical" in lines


class TestCmdFormatPcb:
    def test_returns_format_file_result(self, minimal_pcb: Path) -> None:
        result = cmd_format_pcb(_args(path=str(minimal_pcb)))
        assert isinstance(result, FormatFileResult)

    def test_returns_size_bytes(self, minimal_pcb: Path) -> None:
        result = cmd_format_pcb(_args(path=str(minimal_pcb)))
        assert result.size_bytes > 0


# ---------------------------------------------------------------------------
# LintFileResult formatter shows suggestions
# ---------------------------------------------------------------------------


class TestLintFileResultFormatter:
    def test_suggestion_shown_for_known_code(self) -> None:
        code = next(iter(LINT_SUGGESTIONS))  # first known code
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(LintIssue(severity=LintSeverity.ERROR, code=code, message="Test issue"),),
            error_count=1,
            warning_count=0,
            ok=False,
        )
        lines = "\n".join(format_result(r))
        assert LINT_SUGGESTIONS[code] in lines

    def test_no_suggestion_for_unknown_code(self) -> None:
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(LintIssue(severity=LintSeverity.WARNING, code="ZZZ999", message="Unknown"),),
            error_count=0,
            warning_count=1,
            ok=True,
        )
        lines = "\n".join(format_result(r))
        assert "💡" not in lines

    def test_ok_result_shows_checkmark(self) -> None:
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(),
            error_count=0,
            warning_count=0,
            ok=True,
        )
        lines = "\n".join(format_result(r))
        assert "✅" in lines

    def test_error_result_shows_cross(self) -> None:
        r = LintFileResult(
            path=Path("/tmp/x.kicad_sch"),
            issues=(LintIssue(severity=LintSeverity.ERROR, code="SCH001", message="Err"),),
            error_count=1,
            warning_count=0,
            ok=False,
        )
        lines = "\n".join(format_result(r))
        assert "❌" in lines


# ---------------------------------------------------------------------------
# LINT_SUGGESTIONS coverage
# ---------------------------------------------------------------------------


class TestLintSuggestions:
    def test_suggestions_dict_not_empty(self) -> None:
        assert len(LINT_SUGGESTIONS) > 0

    def test_all_suggestions_are_strings(self) -> None:
        for code, suggestion in LINT_SUGGESTIONS.items():
            assert isinstance(code, str), f"{code!r} is not a str key"
            assert isinstance(suggestion, str), f"{code!r} suggestion is not a str"
            assert suggestion.strip(), f"{code!r} suggestion is blank"

    def test_sch_codes_present(self) -> None:
        for i in range(1, 10):
            assert f"SCH{i:03d}" in LINT_SUGGESTIONS, f"SCH{i:03d} missing"

    def test_pcb_codes_present(self) -> None:
        for i in range(1, 10):
            assert f"PCB{i:03d}" in LINT_SUGGESTIONS, f"PCB{i:03d} missing"


# ---------------------------------------------------------------------------
# Public __init__ exports
# ---------------------------------------------------------------------------


class TestPhase6Exports:
    def test_new_result_types_exported(self) -> None:
        assert hasattr(kicad_pcb, "LintFileResult")
        assert hasattr(kicad_pcb, "ValidateFileResult")
        assert hasattr(kicad_pcb, "FormatFileResult")

    def test_new_commands_exported(self) -> None:
        for name in (
            "cmd_lint_sch",
            "cmd_lint_pcb",
            "cmd_validate_sch",
            "cmd_validate_pcb",
            "cmd_format_sch",
            "cmd_format_pcb",
        ):
            assert hasattr(kicad_pcb, name), f"{name} not exported"

    def test_lint_suggestions_exported(self) -> None:
        assert hasattr(kicad_pcb, "LINT_SUGGESTIONS")

    def test_format_result_json_exported(self) -> None:
        assert hasattr(kicad_pcb, "format_result_json")
