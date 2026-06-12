"""Unit tests for kicad_pcb.pipeline — kicad mode, PCB pipeline, lint error, and LAY tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb.adapters import KicadCliAdapter, RunResult
from kicad_pcb.errors import ToolError
from kicad_pcb.lint import LintError
from kicad_pcb.pcb_doc import PcbDoc
from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_pcb, mutate_and_validate_sch
from kicad_pcb.sexpr import find_all, find_first, parse
from kicad_pcb.sexpr.nodes import ListNode

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


class TestMutateSchKicadMode:
    def test_passes_when_cli_returns_ok(self, sch_file: Path) -> None:
        """mode=KICAD with a passing CLI stub must commit the file."""
        cli = _FakeCli(erc_response=(RunResult(0, "", ""), None))
        mutate_and_validate_sch(sch_file, lambda doc: None, mode=ValidationMode.KICAD, cli=cli)  # type: ignore[arg-type]
        assert sch_file.exists()
        assert cli.erc_call_count == 1

    def test_nonzero_exit_raises_tool_error(self, sch_file: Path) -> None:
        """mode=KICAD with a non-zero ERC exit code must raise ToolError and not overwrite."""
        original = sch_file.read_text()
        cli = _FakeCli(erc_response=(RunResult(1, "", "ERC found 2 errors"), None))
        with pytest.raises(ToolError, match="ERC"):
            mutate_and_validate_sch(sch_file, lambda doc: None, mode=ValidationMode.KICAD, cli=cli)  # type: ignore[arg-type]
        assert sch_file.read_text() == original

    def test_violations_in_report_raise_tool_error(self, sch_file: Path) -> None:
        """mode=KICAD with violations in the ERC JSON report raises ToolError."""
        original = sch_file.read_text()
        report = {"violations": [{"type": "pin_not_connected", "description": "Pin A unconnected"}]}
        cli = _FakeCli(erc_response=(RunResult(0, "", ""), report))
        with pytest.raises(ToolError, match="ERC"):
            mutate_and_validate_sch(sch_file, lambda doc: None, mode=ValidationMode.KICAD, cli=cli)  # type: ignore[arg-type]
        assert sch_file.read_text() == original

    def test_mode_lint_skips_kicad_even_with_cli(self, sch_file: Path) -> None:
        """mode=LINT (< KICAD) must never call the CLI adapter, even if one is provided."""
        failing_cli = _FakeCli(erc_response=(RunResult(1, "", "should not be called"), None))
        # Should write OK — CLI is not reached at LINT level.
        mutate_and_validate_sch(  # type: ignore[arg-type]
            sch_file, lambda doc: None, mode=ValidationMode.LINT, cli=failing_cli
        )
        assert sch_file.exists()
        assert failing_cli.erc_call_count == 0

    def test_mode_kicad_none_cli_skips_kicad_validation(self, sch_file: Path) -> None:
        """mode=KICAD with cli=None must not raise — kicad step is silently skipped."""
        mutate_and_validate_sch(sch_file, lambda doc: None, mode=ValidationMode.KICAD, cli=None)
        assert sch_file.exists()

    def test_cleanup_non_race_error_preserves_primary_tool_error(
        self,
        sch_file: Path,
        monkeypatch,
    ) -> None:
        """Cleanup failures in finally must not shadow an ERC ToolError."""
        original_content = sch_file.read_text()
        cli = _FakeCli(erc_response=(RunResult(1, "", "ERC failed"), None))
        original_unlink = Path.unlink

        def _unlink_permission(self: Path, *, missing_ok: bool = False) -> None:
            del missing_ok
            if (
                self.parent == sch_file.parent
                and self != sch_file
                and (self.name.endswith(".kicad_sch") or self.name.endswith(".erc.json"))
            ):
                raise PermissionError("simulated cleanup permission denied")
            return original_unlink(self)

        monkeypatch.setattr(Path, "unlink", _unlink_permission)

        with pytest.raises(ToolError, match="ERC") as exc_info:
            mutate_and_validate_sch(
                sch_file,
                lambda doc: None,
                mode=ValidationMode.KICAD,
                cli=cli,  # type: ignore[arg-type]
            )

        notes = getattr(exc_info.value, "__notes__", [])
        cleanup_note = getattr(exc_info.value, "cleanup_note", "")
        assert any("Validation temp cleanup failed" in note for note in notes) or (
            "Validation temp cleanup failed" in cleanup_note
        )
        assert sch_file.read_text() == original_content

    def test_cleanup_non_race_error_raises_when_no_primary_error(
        self,
        sch_file: Path,
        monkeypatch,
    ) -> None:
        """Without a primary validation error, non-race cleanup errors must surface."""
        original_content = sch_file.read_text()
        cli = _FakeCli(erc_response=(RunResult(0, "", ""), None))
        original_unlink = Path.unlink

        def _unlink_permission(self: Path, *, missing_ok: bool = False) -> None:
            del missing_ok
            if (
                self.parent == sch_file.parent
                and self != sch_file
                and (self.name.endswith(".kicad_sch") or self.name.endswith(".erc.json"))
            ):
                raise PermissionError("simulated cleanup permission denied")
            return original_unlink(self)

        monkeypatch.setattr(Path, "unlink", _unlink_permission)

        with pytest.raises(PermissionError, match="simulated cleanup permission denied"):
            mutate_and_validate_sch(
                sch_file,
                lambda doc: None,
                mode=ValidationMode.KICAD,
                cli=cli,  # type: ignore[arg-type]
            )

        assert sch_file.read_text() == original_content


# ---------------------------------------------------------------------------
# mutate_and_validate_sch — backup
# ---------------------------------------------------------------------------


class TestMutateSchBackup:
    def test_backup_created_when_enabled(self, sch_file: Path) -> None:
        original = sch_file.read_text()
        mutate_and_validate_sch(sch_file, lambda doc: None, backup=True)
        bak = sch_file.with_suffix(".kicad_sch.bak")
        assert bak.exists()
        assert bak.read_text() == original

    def test_no_backup_by_default(self, sch_file: Path) -> None:
        mutate_and_validate_sch(sch_file, lambda doc: None)
        bak = sch_file.with_suffix(".kicad_sch.bak")
        assert not bak.exists()


# ---------------------------------------------------------------------------
# mutate_and_validate_pcb — success
# ---------------------------------------------------------------------------


class TestMutatePcbSuccess:
    def test_identity_mutation_writes_file(self, pcb_file: Path) -> None:
        mutate_and_validate_pcb(pcb_file, lambda doc: None)
        root = parse(pcb_file.read_text())
        assert root.key == "kicad_pcb"

    def test_set_outline_persisted(self, pcb_file: Path) -> None:
        def _set_outline(doc: PcbDoc) -> None:
            doc.set_rect_outline(50.0, 30.0)

        mutate_and_validate_pcb(pcb_file, _set_outline)
        root = parse(pcb_file.read_text())
        gr_lines = find_all(root, "gr_line")
        assert len(gr_lines) == 4  # 4 sides of the rectangle


# ---------------------------------------------------------------------------
# mutate_and_validate_pcb — lint mode
# ---------------------------------------------------------------------------


class TestMutatePcbLintMode:
    def test_pcb005_warning_does_not_block_default(self, pcb_file: Path) -> None:
        """No Edge.Cuts is a WARNING — default LINT mode must not raise."""
        # pcb_file has no Edge.Cuts → PCB005 warning fires.
        # But default mode is LINT and strict=False → should write OK.
        mutate_and_validate_pcb(pcb_file, lambda doc: None)
        assert pcb_file.exists()

    def test_pcb005_warning_blocks_with_strict(self, pcb_file: Path) -> None:
        """strict=True should raise LintError for PCB005 warning."""
        with pytest.raises(LintError) as exc_info:
            mutate_and_validate_pcb(pcb_file, lambda doc: None, strict=True)
        codes = [i.code for i in exc_info.value.issues]
        assert "PCB005" in codes

    def test_original_preserved_on_lint_error(self, pcb_file: Path) -> None:
        original = pcb_file.read_text()

        def _corrupt(doc: PcbDoc) -> None:
            duplicate_uuid = parse('(uuid "dup")')
            new_items = doc.root.items + (duplicate_uuid, duplicate_uuid)
            doc.root = ListNode(new_items, doc.root.pos)

        with pytest.raises(LintError):
            mutate_and_validate_pcb(pcb_file, _corrupt, strict=False)
        # PCB002 is ERROR-level so strict is irrelevant; file must be unchanged.
        assert pcb_file.read_text() == original

    def test_move_footprint_persisted(self, tmp_path: Path) -> None:
        # Build a PCB with outline and a footprint directly.
        pcb_with_fp = """\
(kicad_pcb (version 20230121) (generator test)
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 50 0) (end 50 30) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 50 30) (end 0 30) (layer "Edge.Cuts") (width 0.05))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.05))
  (footprint "Lib:R" (at 0.0 0.0) (uuid "fp1")
    (property "Reference" "R1" (at 0 0 0))
    (property "Value" "10k" (at 0 0 0))
  )
)
"""
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text(pcb_with_fp)

        def _move(doc: PcbDoc) -> None:
            doc.move_footprint("R1", 25.0, 15.0)

        mutate_and_validate_pcb(pcb, _move)
        root = parse(pcb.read_text())
        fps = find_all(root, "footprint")
        assert len(fps) == 1
        at = find_first(fps[0], "at")
        assert at is not None
        assert at.items[1].value == "25.000"  # type: ignore[union-attr]
        assert at.items[2].value == "15.000"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# mutate_and_validate_pcb — mode=KICAD / mocked kicad-cli
# ---------------------------------------------------------------------------
