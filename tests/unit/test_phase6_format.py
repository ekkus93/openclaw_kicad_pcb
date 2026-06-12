from __future__ import annotations

import argparse
import textwrap
from pathlib import Path

import pytest

import kicad_pcb
from kicad_pcb.commands.lint import (
    cmd_format_pcb,
    cmd_format_sch,
)
from kicad_pcb.formatting import format_result
from kicad_pcb.lint import LINT_SUGGESTIONS, LintIssue, LintSeverity
from kicad_pcb.results import FormatFileResult, LintFileResult

pytestmark = pytest.mark.unit


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
