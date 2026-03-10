"""Phase 1 reliability tests.

Covers:
- Typed exception hierarchy
- _check_sexp: valid S-expression, wrong root, unbalanced parens
- _atomic_write: happy path, rejects bad content (no clobber), no temp leftovers
- check_kicad: raises ToolError when CLI not on PATH
- cmd_doctor: smoke test (always returns DoctorResult; overall_ok=False when deps missing)
- No bare `except:` in the module source
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Import the script module (kicad_pcb.py lives in kicad-pcb/scripts/).
# pytest.ini adds that directory to pythonpath, so a plain import works.
# ---------------------------------------------------------------------------
import kicad_pcb  # noqa: E402
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SCRIPT = Path(__file__).parent.parent.parent / "kicad-pcb" / "scripts" / "kicad_pcb.py"

VALID_SCH = """\
(kicad_sch (version 20230121) (generator eeschema)
  (uuid "test-uuid")
  (paper "A4")
  (lib_symbols)
  (sheet_instances
    (path "/" (page "1"))
  )
)
"""

VALID_PCB = """\
(kicad_pcb (version 20230121) (generator pcbnew)
  (general
    (thickness 1.6)
  )
  (net 0 "")
)
"""


# ---------------------------------------------------------------------------
# 1. Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    def test_kicad_error_is_runtime_error(self) -> None:
        assert issubclass(kicad_pcb.KiCadError, RuntimeError)

    def test_user_error_is_kicad_error(self) -> None:
        assert issubclass(kicad_pcb.UserError, kicad_pcb.KiCadError)

    def test_tool_error_is_kicad_error(self) -> None:
        assert issubclass(kicad_pcb.ToolError, kicad_pcb.KiCadError)

    def test_parse_error_is_kicad_error(self) -> None:
        assert issubclass(kicad_pcb.ParseError, kicad_pcb.KiCadError)

    def test_each_exception_carries_message(self) -> None:
        for cls in (kicad_pcb.UserError, kicad_pcb.ToolError, kicad_pcb.ParseError):
            exc = cls("boom")
            assert str(exc) == "boom"


# ---------------------------------------------------------------------------
# 2. _check_sexp
# ---------------------------------------------------------------------------


class TestCheckSexp:
    def test_valid_kicad_sch_passes(self) -> None:
        kicad_pcb._check_sexp(VALID_SCH, "kicad_sch")  # must not raise

    def test_valid_kicad_pcb_passes(self) -> None:
        kicad_pcb._check_sexp(VALID_PCB, "kicad_pcb")  # must not raise

    def test_unbalanced_open_raises(self) -> None:
        bad = "(kicad_sch (missing-close)\n"
        with pytest.raises(kicad_pcb.ParseError, match="Unbalanced"):
            kicad_pcb._check_sexp(bad, "kicad_sch")

    def test_unbalanced_close_raises(self) -> None:
        bad = "(kicad_sch)\n)\n"
        with pytest.raises(kicad_pcb.ParseError, match="Unbalanced"):
            kicad_pcb._check_sexp(bad, "kicad_sch")

    def test_wrong_root_raises(self) -> None:
        with pytest.raises(kicad_pcb.ParseError, match="Expected root node"):
            kicad_pcb._check_sexp(VALID_SCH, "kicad_pcb")

    def test_parens_inside_strings_ignored(self) -> None:
        # Parens inside quoted strings should not affect depth count.
        content = '(kicad_sch (property "Test(((" "val"))\n'
        kicad_pcb._check_sexp(content, "kicad_sch")  # must not raise

    def test_escaped_backslash_before_quote_not_in_string(self) -> None:
        # String "abc\\" ends at the second backslash; the following " closes,
        # then the outer parens are balanced.  The old char-scanner mis-read
        # this as a continued string, causing a false "Unbalanced" error.
        content = '(kicad_sch (property "abc\\\\" "val"))\n'
        kicad_pcb._check_sexp(content, "kicad_sch")  # must not raise

    def test_escaped_quote_inside_string_doesnt_end_it(self) -> None:
        # String "say \"hi\"" contains two escaped quotes; depth stays correct.
        content = '(kicad_sch (property "say \\"hi\\"" "ok"))\n'
        kicad_pcb._check_sexp(content, "kicad_sch")  # must not raise

    def test_parens_in_escaped_string_not_counted(self) -> None:
        # Two opening parens inside a string value must not affect depth.
        content = '(kicad_sch (net "name with (parens)") (dummy))\n'
        kicad_pcb._check_sexp(content, "kicad_sch")  # must not raise


# ---------------------------------------------------------------------------
# 2b. SUPPORTED_ROOTS constant
# ---------------------------------------------------------------------------


class TestSupportedRoots:
    def test_is_frozenset(self) -> None:
        assert isinstance(kicad_pcb.SUPPORTED_ROOTS, frozenset)

    def test_contains_expected_roots(self) -> None:
        assert "kicad_sch" in kicad_pcb.SUPPORTED_ROOTS
        assert "kicad_pcb" in kicad_pcb.SUPPORTED_ROOTS

    def test_no_unknown_roots(self) -> None:
        assert frozenset({"kicad_sch", "kicad_pcb"}) == kicad_pcb.SUPPORTED_ROOTS


# ---------------------------------------------------------------------------
# 2c. _write_temp_text
# ---------------------------------------------------------------------------


class TestWriteTempText:
    def test_creates_file_with_content(self, tmp_path: Path) -> None:
        tmp = kicad_pcb._write_temp_text(tmp_path, ".tmp", "hello\n")
        assert tmp.exists()
        assert tmp.read_text(encoding="utf-8") == "hello\n"

    def test_file_is_in_specified_directory(self, tmp_path: Path) -> None:
        tmp = kicad_pcb._write_temp_text(tmp_path, ".tmp", "x")
        assert tmp.parent == tmp_path

    def test_suffix_applied(self, tmp_path: Path) -> None:
        tmp = kicad_pcb._write_temp_text(tmp_path, ".kicad_sch.tmp", "x")
        assert tmp.name.endswith(".kicad_sch.tmp")

    def test_large_content_fully_written(self, tmp_path: Path) -> None:
        # 2 MiB of unicode content — verifies no partial-write truncation.
        large = "x" * (2 * 1024 * 1024)
        tmp = kicad_pcb._write_temp_text(tmp_path, ".tmp", large)
        assert tmp.read_text(encoding="utf-8") == large

    def test_unicode_content_roundtrip(self, tmp_path: Path) -> None:
        content = '(kicad_sch (property "\u6d4b\u8bd5" "\u4e2d\u6587"))\n'
        tmp = kicad_pcb._write_temp_text(tmp_path, ".tmp", content)
        assert tmp.read_text(encoding="utf-8") == content

    def test_no_temp_file_left_on_write_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the write itself fails the temp file must be cleaned up."""
        real_fdopen = os.fdopen

        def failing_fdopen(fd: int, mode: str = "r", **kwargs: Any) -> object:
            fobj = real_fdopen(fd, mode, **kwargs)

            class _FailOnWrite:
                def write(self, data: str) -> int:
                    raise OSError("simulated write failure")

                def flush(self) -> None: ...  # pragma: no cover

                def fileno(self) -> int:
                    return fobj.fileno()  # type: ignore[union-attr]

                def __enter__(self) -> _FailOnWrite:
                    return self

                def __exit__(self, *args: Any) -> None:
                    fobj.__exit__(*args)  # type: ignore[union-attr]

            return _FailOnWrite()

        monkeypatch.setattr("kicad_pcb.fs.os.fdopen", failing_fdopen)
        with pytest.raises(OSError, match="simulated write failure"):
            kicad_pcb._write_temp_text(tmp_path, ".tmp", "data")
        assert list(tmp_path.glob("*.tmp")) == []


# ---------------------------------------------------------------------------
# 3. _atomic_write
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_writes_content(self, tmp_path: Path) -> None:
        target = tmp_path / "test.kicad_sch"
        kicad_pcb._atomic_write(target, VALID_SCH)
        assert target.read_text() == VALID_SCH

    def test_overwrites_existing(self, tmp_path: Path) -> None:
        target = tmp_path / "test.kicad_sch"
        target.write_text("old content")
        kicad_pcb._atomic_write(target, VALID_SCH)
        assert target.read_text() == VALID_SCH

    def test_with_root_check_valid(self, tmp_path: Path) -> None:
        target = tmp_path / "test.kicad_sch"
        kicad_pcb._atomic_write(target, VALID_SCH, "kicad_sch")
        assert target.read_text() == VALID_SCH

    def test_with_root_check_bad_content_no_clobber(self, tmp_path: Path) -> None:
        """Original file must remain untouched when sanity check fails."""
        target = tmp_path / "test.kicad_sch"
        target.write_text(VALID_SCH)  # original
        bad_content = "(kicad_sch (oops"  # unbalanced

        with pytest.raises(kicad_pcb.ParseError):
            kicad_pcb._atomic_write(target, bad_content, "kicad_sch")

        # Original preserved
        assert target.read_text() == VALID_SCH

    def test_no_temp_file_left_on_parse_error(self, tmp_path: Path) -> None:
        """Temp .tmp artefact must be cleaned up after a failed write."""
        target = tmp_path / "test.kicad_sch"
        with pytest.raises(kicad_pcb.ParseError):
            kicad_pcb._atomic_write(target, "(broken", "kicad_sch")

        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == [], f"Temp files left behind: {tmp_files}"

    def test_creates_parent_dirs_not_required(self, tmp_path: Path) -> None:
        """Parent directory must already exist (os.replace requirement)."""
        target = tmp_path / "out.kicad_sch"
        kicad_pcb._atomic_write(target, VALID_SCH, "kicad_sch")
        assert target.exists()

    def test_parse_error_includes_file_path(self, tmp_path: Path) -> None:
        """ParseError message must include the target file path."""
        target = tmp_path / "broken.kicad_sch"
        with pytest.raises(kicad_pcb.ParseError, match=str(target)):
            kicad_pcb._atomic_write(target, "(kicad_sch (oops", "kicad_sch")

    def test_parse_error_includes_operation_name(self, tmp_path: Path) -> None:
        """ParseError message must include the operation label when supplied."""
        target = tmp_path / "broken.kicad_sch"
        with pytest.raises(kicad_pcb.ParseError, match=r"\[add-component\]"):
            kicad_pcb._atomic_write(
                target, "(kicad_sch (oops", "kicad_sch", operation="add-component"
            )

    def test_backup_created_before_overwrite(self, tmp_path: Path) -> None:
        """With backup=True, a .bak file must appear alongside the overwritten file."""
        target = tmp_path / "test.kicad_sch"
        target.write_text(VALID_SCH)
        new_content = VALID_SCH.replace("20230121", "20231231")
        kicad_pcb._atomic_write(target, new_content, "kicad_sch", backup=True)
        bak = tmp_path / "test.kicad_sch.bak"
        assert bak.exists(), "backup file must be created"
        assert bak.read_text() == VALID_SCH, "backup must contain original content"
        assert target.read_text() == new_content, "target must hold new content"

    def test_no_backup_when_flag_false(self, tmp_path: Path) -> None:
        """Default backup=False must not create any .bak artefact."""
        target = tmp_path / "test.kicad_sch"
        target.write_text(VALID_SCH)
        kicad_pcb._atomic_write(target, VALID_SCH, "kicad_sch")
        assert not (tmp_path / "test.kicad_sch.bak").exists()


# ---------------------------------------------------------------------------
# 4. check_kicad raises ToolError when CLI not found
# ---------------------------------------------------------------------------


class TestCheckKicad:
    def test_raises_tool_error_when_not_on_path(self) -> None:
        with (
            patch("shutil.which", return_value=None),
            pytest.raises(kicad_pcb.ToolError, match="KiCad CLI not found"),
        ):
            kicad_pcb.check_kicad()

    def test_does_not_raise_when_cli_found(self) -> None:
        with patch("shutil.which", return_value="/usr/bin/kicad-cli"):
            kicad_pcb.check_kicad()  # must not raise


# ---------------------------------------------------------------------------
# 5. cmd_doctor smoke test
# ---------------------------------------------------------------------------


class TestCmdDoctor:
    def test_smoke_returns_doctor_result(self, tmp_path: Path) -> None:
        """doctor always returns a DoctorResult; never raises, even with missing deps."""

        class FakeArgs:
            pass

        result = kicad_pcb.cmd_doctor(FakeArgs())
        assert isinstance(result, kicad_pcb.DoctorResult)
        assert isinstance(result.overall_ok, bool)
        assert len(result.checks) > 0

    def test_overall_ok_false_when_cli_missing(self, tmp_path: Path) -> None:
        """If kicad-cli is absent, overall_ok is False (no exception raised)."""

        class FakeArgs:
            pass

        with patch("shutil.which", return_value=None):
            result = kicad_pcb.cmd_doctor(FakeArgs())

        assert isinstance(result, kicad_pcb.DoctorResult)
        assert result.overall_ok is False
        labels = [c.label for c in result.checks]
        assert "kicad-cli" in labels
        cli_check = next(c for c in result.checks if c.label == "kicad-cli")
        assert cli_check.status == "error"


# ---------------------------------------------------------------------------
# 6. No bare `except:` in the module source
# ---------------------------------------------------------------------------


class TestNoBareExcept:
    def test_no_bare_except_in_source(self) -> None:
        """Parse the AST and assert there are no bare ExceptHandler nodes."""
        source = SCRIPT.read_text()
        tree = ast.parse(source)
        bare = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ExceptHandler) and node.type is None
        ]
        assert bare == [], f"Found {len(bare)} bare `except:` at lines: " + ", ".join(
            str(b.lineno) for b in bare
        )
