from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter, RunResult
from kicad_pcb.errors import KiCadError, ToolError
from kicad_pcb.lint import LintError, LintIssue
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_pcb, mutate_and_validate_sch
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr import parse
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit


MINIMAL_SCH = """\
(kicad_sch (version 20230121) (generator test)
  (lib_symbols)
  (sheet_instances (path "/" (page "1")))
)
"""

MINIMAL_PCB = """\
(kicad_pcb (version 20230121) (generator test)
)
"""

PCB_WITH_OUTLINE = """\
(kicad_pcb (version 20230121) (generator test)
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 50 0) (end 50 30) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 50 30) (end 0 30) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.05))
)
"""


@pytest.fixture
def sch_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.kicad_sch"
    f.write_text(MINIMAL_SCH)
    return f


@pytest.fixture
def pcb_file(tmp_path: Path) -> Path:
    f = tmp_path / "test.kicad_pcb"
    f.write_text(MINIMAL_PCB)
    return f


@pytest.fixture
def pcb_file_with_outline(tmp_path: Path) -> Path:
    f = tmp_path / "test.kicad_pcb"
    f.write_text(PCB_WITH_OUTLINE)
    return f


# ---------------------------------------------------------------------------
# _FakeCli — test stub for KicadCliAdapter
# ---------------------------------------------------------------------------
# Returns pre-configured (RunResult, report) pairs without invoking kicad-cli.
# Used in TestMutateSchKicadMode / TestMutatePcbKicadMode below.
# ---------------------------------------------------------------------------


class _FakeCli(KicadCliAdapter):
    """Injectable KicadCliAdapter stub for unit tests.

    Accepts predetermined ``(RunResult, dict | None)`` pairs that will be
    returned from :meth:`erc` and :meth:`drc` without spawning any subprocess
    or touching the filesystem beyond what the pipeline itself writes.
    """

    def __init__(
        self,
        *,
        erc_response: tuple[RunResult, dict | None] | None = None,
        drc_response: tuple[RunResult, dict | None] | None = None,
    ) -> None:
        super().__init__()
        self._erc: tuple[RunResult, dict | None] = erc_response or (RunResult(0, "", ""), None)
        self._drc: tuple[RunResult, dict | None] = drc_response or (RunResult(0, "", ""), None)
        self.erc_call_count = 0
        self.drc_call_count = 0

    def erc(self, _sch: Path, _report: Path) -> tuple[RunResult, dict | None]:  # noqa: ARG002
        self.erc_call_count += 1
        return self._erc

    def drc(self, _pcb: Path, _report: Path) -> tuple[RunResult, dict | None]:  # noqa: ARG002
        self.drc_call_count += 1
        return self._drc


class TestMutatePcbKicadMode:
    def test_passes_when_cli_returns_ok(self, pcb_file_with_outline: Path) -> None:
        """mode=KICAD with a passing CLI stub must commit the file."""
        cli = _FakeCli(drc_response=(RunResult(0, "", ""), None))
        mutate_and_validate_pcb(  # type: ignore[arg-type]
            pcb_file_with_outline, lambda doc: None, mode=ValidationMode.KICAD, cli=cli
        )
        assert pcb_file_with_outline.exists()
        assert cli.drc_call_count == 1

    def test_nonzero_exit_raises_tool_error(self, pcb_file_with_outline: Path) -> None:
        """mode=KICAD with a non-zero DRC exit code raises ToolError and leaves file unchanged."""
        original = pcb_file_with_outline.read_text()
        cli = _FakeCli(drc_response=(RunResult(1, "", "DRC found 3 errors"), None))
        with pytest.raises(ToolError, match="DRC"):
            mutate_and_validate_pcb(  # type: ignore[arg-type]
                pcb_file_with_outline, lambda doc: None, mode=ValidationMode.KICAD, cli=cli
            )
        assert pcb_file_with_outline.read_text() == original

    def test_violations_in_report_raise_tool_error(self, pcb_file_with_outline: Path) -> None:
        """mode=KICAD with violations in the DRC JSON report raises ToolError."""
        original = pcb_file_with_outline.read_text()
        report = {"violations": [{"type": "clearance", "description": "Clearance violation"}]}
        cli = _FakeCli(drc_response=(RunResult(0, "", ""), report))
        with pytest.raises(ToolError, match="DRC"):
            mutate_and_validate_pcb(  # type: ignore[arg-type]
                pcb_file_with_outline, lambda doc: None, mode=ValidationMode.KICAD, cli=cli
            )
        assert pcb_file_with_outline.read_text() == original

    def test_mode_lint_skips_kicad_even_with_cli(self, pcb_file_with_outline: Path) -> None:
        """mode=LINT (< KICAD) must never call the CLI adapter."""
        failing_cli = _FakeCli(drc_response=(RunResult(1, "", "should not be called"), None))
        mutate_and_validate_pcb(  # type: ignore[arg-type]
            pcb_file_with_outline, lambda doc: None, mode=ValidationMode.LINT, cli=failing_cli
        )
        assert pcb_file_with_outline.exists()
        assert failing_cli.drc_call_count == 0

    def test_mode_kicad_none_cli_skips_kicad_validation(self, pcb_file_with_outline: Path) -> None:
        """mode=KICAD with cli=None must not raise — kicad step is silently skipped."""
        mutate_and_validate_pcb(
            pcb_file_with_outline, lambda doc: None, mode=ValidationMode.KICAD, cli=None
        )
        assert pcb_file_with_outline.exists()

    def test_cleanup_non_race_error_preserves_primary_tool_error(
        self,
        pcb_file_with_outline: Path,
        monkeypatch,
    ) -> None:
        """Cleanup failures in finally must not shadow a DRC ToolError."""
        original_content = pcb_file_with_outline.read_text()
        cli = _FakeCli(drc_response=(RunResult(1, "", "DRC failed"), None))
        original_unlink = Path.unlink

        def _unlink_permission(self: Path, *, missing_ok: bool = False) -> None:
            del missing_ok
            if (
                self.parent == pcb_file_with_outline.parent
                and self != pcb_file_with_outline
                and (self.name.endswith(".kicad_pcb") or self.name.endswith(".drc.json"))
            ):
                raise PermissionError("simulated cleanup permission denied")
            return original_unlink(self)

        monkeypatch.setattr(Path, "unlink", _unlink_permission)

        with pytest.raises(ToolError, match="DRC") as exc_info:
            mutate_and_validate_pcb(
                pcb_file_with_outline,
                lambda doc: None,
                mode=ValidationMode.KICAD,
                cli=cli,  # type: ignore[arg-type]
            )

        notes = getattr(exc_info.value, "__notes__", [])
        cleanup_note = getattr(exc_info.value, "cleanup_note", "")
        assert any("Validation temp cleanup failed" in note for note in notes) or (
            "Validation temp cleanup failed" in cleanup_note
        )
        assert pcb_file_with_outline.read_text() == original_content


# ---------------------------------------------------------------------------
# mutate_and_validate_pcb — backup
# ---------------------------------------------------------------------------


class TestMutatePcbBackup:
    def test_backup_created_when_enabled(self, pcb_file: Path) -> None:
        original = pcb_file.read_text()
        mutate_and_validate_pcb(pcb_file, lambda doc: None, backup=True)
        bak = pcb_file.with_suffix(".kicad_pcb.bak")
        assert bak.exists()
        assert bak.read_text() == original

    def test_no_backup_by_default(self, pcb_file: Path) -> None:
        mutate_and_validate_pcb(pcb_file, lambda doc: None)
        bak = pcb_file.with_suffix(".kicad_pcb.bak")
        assert not bak.exists()


# ---------------------------------------------------------------------------
# LintError carries issues list
# ---------------------------------------------------------------------------


class TestLintError:
    def test_lint_error_has_issues(self, sch_file: Path) -> None:
        def _dup_uuid(doc: SchematicDoc) -> None:
            uuid_node = parse('(uuid "dup-x")')
            new_items = doc.root.items + (uuid_node, uuid_node)
            doc.root = ListNode(new_items, doc.root.pos)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(sch_file, _dup_uuid)

        err = exc_info.value
        assert isinstance(err.issues, list)
        assert all(isinstance(i, LintIssue) for i in err.issues)
        assert len(err.issues) >= 1

    def test_lint_error_is_kicad_error(self, sch_file: Path) -> None:
        def _dup(doc: SchematicDoc) -> None:
            uuid_node = parse('(uuid "dup-y")')
            new_items = doc.root.items + (uuid_node, uuid_node)
            doc.root = ListNode(new_items, doc.root.pos)

        with pytest.raises(KiCadError):
            mutate_and_validate_sch(sch_file, _dup)


# ---------------------------------------------------------------------------
# Phase 5 — LAY004 must block writes (transactional no-overwrite)
# ---------------------------------------------------------------------------


class TestLAY004BlocksWrite:
    """Regression: a symbol placed outside A4 bounds must raise LintError and
    must not overwrite the original file."""

    def test_lay004_raises_lint_error(self, sch_file: Path) -> None:
        """LAY004 (ERROR severity) must raise LintError under LINT mode."""

        def _place_out_of_bounds(doc: SchematicDoc) -> None:
            # Add a symbol placed at x=450 (beyond page x-bound) to trigger LAY004.
            sym_node = parse("(symbol (at 450 100 0))")
            doc.root = ListNode(doc.root.items + (sym_node,), doc.root.pos)

        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_sch(
                sch_file,
                _place_out_of_bounds,
                mode=ValidationMode.LINT,
            )

        codes = [issue.code for issue in exc_info.value.issues]
        assert "LAY004" in codes

    def test_lay004_does_not_overwrite_original(self, sch_file: Path) -> None:
        """Original file must be unchanged when LAY004 aborts the write."""
        original_content = sch_file.read_text()

        def _place_out_of_bounds(doc: SchematicDoc) -> None:
            sym_node = parse("(symbol (at 450 100 0))")
            doc.root = ListNode(doc.root.items + (sym_node,), doc.root.pos)

        with pytest.raises(LintError):
            mutate_and_validate_sch(sch_file, _place_out_of_bounds)

        assert sch_file.read_text() == original_content, (
            "File was overwritten despite LAY004 error — transactional contract violated"
        )
