"""P5.2 dry-run diff output tests.

Verifies that :func:`~kicad_pcb.pipeline.mutate_and_validate_sch` and
:func:`~kicad_pcb.pipeline.mutate_and_validate_pcb` emit a unified diff to the
*diff_output* stream when the parameter is provided, and that the private
helper :func:`~kicad_pcb.pipeline._show_diff` behaves correctly in isolation.

Scenarios:
  - ``diff_output=None`` (default) — no diff written at all
  - noop mutator with ``diff_output`` set — produces empty diff
  - non-trivial mutator — diff contains expected ``---`` / ``+++`` markers
  - ``before`` / ``after`` path labels appear in header lines
  - diff works when ``dry_run=True`` (file unchanged on disk)
  - diff works when ``dry_run=False`` (file written; diff still emitted)
  - identical before/after produces empty output
  - changed line appears in diff body for both sch and pcb pipelines
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import pytest
from kicad_pcb.pcb_doc import PcbDoc
from kicad_pcb.pipeline import (
    ValidationMode,
    _show_diff,
    mutate_and_validate_pcb,
    mutate_and_validate_sch,
)
from kicad_pcb.sch_doc import SchematicDoc
from kicad_pcb.sexpr.builder import L, atom
from kicad_pcb.sexpr.nodes import ListNode

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "valid"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sch_copy(tmp_path: Path) -> Path:
    src = FIXTURES / "minimal.kicad_sch"
    dst = tmp_path / "test.kicad_sch"
    shutil.copy(src, dst)
    return dst


def _pcb_copy(tmp_path: Path) -> Path:
    src = FIXTURES / "minimal.kicad_pcb"
    dst = tmp_path / "test.kicad_pcb"
    shutil.copy(src, dst)
    return dst


def _buf() -> io.StringIO:
    return io.StringIO()


def _noop_sch(doc: SchematicDoc) -> None:
    pass


def _noop_pcb(doc: PcbDoc) -> None:
    pass


def _append_marker(doc: SchematicDoc | PcbDoc, key: str, value: str) -> None:
    """Append ``(key value)`` as a new child of the doc root node."""
    new_node = L(atom(key), atom(value))
    doc.root = ListNode(doc.root.items + (new_node,), doc.root.pos)


# ---------------------------------------------------------------------------
# Unit tests for _show_diff
# ---------------------------------------------------------------------------


class TestShowDiff:
    """Direct unit tests for the _show_diff helper."""

    def test_identical_content_produces_no_output(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        buf = _buf()
        _show_diff("(kicad_sch)\n", "(kicad_sch)\n", path, buf)
        assert buf.getvalue() == ""

    def test_empty_strings_produce_no_output(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        buf = _buf()
        _show_diff("", "", path, buf)
        assert buf.getvalue() == ""

    def test_changed_content_produces_diff(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        buf = _buf()
        _show_diff("(kicad_sch old)\n", "(kicad_sch new)\n", path, buf)
        diff = buf.getvalue()
        assert diff != ""
        assert "---" in diff
        assert "+++" in diff

    def test_removed_line_appears_with_minus_prefix(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        buf = _buf()
        _show_diff("keep\nremove me\n", "keep\n", path, buf)
        diff = buf.getvalue()
        assert any(line.startswith("-remove me") for line in diff.splitlines())

    def test_added_line_appears_with_plus_prefix(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        buf = _buf()
        _show_diff("keep\n", "keep\nadded line\n", path, buf)
        diff = buf.getvalue()
        assert any(line.startswith("+added line") for line in diff.splitlines())

    def test_from_label_contains_before(self, tmp_path: Path) -> None:
        path = tmp_path / "my.kicad_sch"
        buf = _buf()
        _show_diff("a\n", "b\n", path, buf)
        diff = buf.getvalue()
        assert "my.kicad_sch (before)" in diff

    def test_to_label_contains_after(self, tmp_path: Path) -> None:
        path = tmp_path / "my.kicad_sch"
        buf = _buf()
        _show_diff("a\n", "b\n", path, buf)
        diff = buf.getvalue()
        assert "my.kicad_sch (after)" in diff

    def test_path_name_not_full_path_in_header(self, tmp_path: Path) -> None:
        """Only the basename is shown — not the full absolute path."""
        path = tmp_path / "deeply" / "nested" / "file.kicad_sch"
        buf = _buf()
        _show_diff("x\n", "y\n", path, buf)
        diff = buf.getvalue()
        assert "file.kicad_sch (before)" in diff
        assert "file.kicad_sch (after)" in diff

    def test_multiple_changed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "board.kicad_sch"
        original = "line1\nline2\nline3\n"
        updated = "line1\nLINE2_CHANGED\nline3\n"
        buf = _buf()
        _show_diff(original, updated, path, buf)
        diff = buf.getvalue()
        assert "-line2" in diff
        assert "+LINE2_CHANGED" in diff


# ---------------------------------------------------------------------------
# Pipeline integration: schematic
# ---------------------------------------------------------------------------


class TestDryRunDiffSch:
    """Verify diff_output behaviour in the schematic pipeline."""

    def test_default_no_diff_output(self, tmp_path: Path) -> None:
        """Not passing diff_output → parameter defaults to None, no side-effects."""
        path = _sch_copy(tmp_path)
        # No buf — must not raise and must not write anything.
        mutate_and_validate_sch(path, _noop_sch)

    def test_noop_mutator_diff_output_no_raise(self, tmp_path: Path) -> None:
        """diff_output with a noop mutator completes without error.

        The serialiser may normalise trailing whitespace, so we only assert
        that no exception is raised and that no application-level content
        (uuid, net, symbol nodes) appears as added/removed.
        """
        path = _sch_copy(tmp_path)
        buf = _buf()
        mutate_and_validate_sch(path, _noop_sch, diff_output=buf)
        diff = buf.getvalue()
        # If a diff is emitted, it must be trivial (trailing-newline only).
        meaningful = [
            ln for ln in diff.splitlines()
            if ln.startswith(("+", "-"))
            and not ln.startswith(("++", "--"))
            and ln.strip() not in ("+)", "-)", "+", "-")
        ]
        assert not meaningful, f"unexpected content diff lines: {meaningful}"

    def test_noop_dry_run_diff_output_no_raise(self, tmp_path: Path) -> None:
        """dry_run + noop mutator + diff_output completes without error."""
        path = _sch_copy(tmp_path)
        buf = _buf()
        mutate_and_validate_sch(path, _noop_sch, dry_run=True, diff_output=buf)
        meaningful = [
            ln for ln in buf.getvalue().splitlines()
            if ln.startswith(("+", "-"))
            and not ln.startswith(("++", "--"))
            and ln.strip() not in ("+)", "-)", "+", "-")
        ]
        assert not meaningful, f"unexpected content diff lines: {meaningful}"

    def test_non_trivial_mutator_produces_diff(self, tmp_path: Path) -> None:
        """A mutator that injects a comment-like atom should change the output."""
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "dummy_marker", "p52_test_value")

        buf = _buf()
        mutate_and_validate_sch(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        diff = buf.getvalue()
        assert diff, "mutating mutator should produce non-empty diff"
        assert "---" in diff
        assert "+++" in diff
        assert "p52_test_value" in diff

    def test_diff_headers_contain_path_name(self, tmp_path: Path) -> None:
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "marker", "X")

        buf = _buf()
        mutate_and_validate_sch(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        diff = buf.getvalue()
        assert path.name in diff

    def test_diff_emitted_with_dry_run_true(self, tmp_path: Path) -> None:
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "marker", "dryrun")

        buf = _buf()
        mutate_and_validate_sch(
            path, _mutate, dry_run=True, diff_output=buf, mode=ValidationMode.NONE
        )
        assert "dryrun" in buf.getvalue()
        # File must not have changed.
        assert "dryrun" not in path.read_text(encoding="utf-8")

    def test_diff_emitted_with_dry_run_false(self, tmp_path: Path) -> None:
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "marker", "committed")

        buf = _buf()
        mutate_and_validate_sch(
            path, _mutate, dry_run=False, diff_output=buf, mode=ValidationMode.NONE
        )
        assert "committed" in buf.getvalue()
        # File must have been written.
        assert "committed" in path.read_text(encoding="utf-8")

    def test_diff_before_label(self, tmp_path: Path) -> None:
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "m", "v")

        buf = _buf()
        mutate_and_validate_sch(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        assert "(before)" in buf.getvalue()

    def test_diff_after_label(self, tmp_path: Path) -> None:
        path = _sch_copy(tmp_path)

        def _mutate(doc: SchematicDoc) -> None:
            _append_marker(doc, "m", "v")

        buf = _buf()
        mutate_and_validate_sch(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        assert "(after)" in buf.getvalue()


# ---------------------------------------------------------------------------
# Pipeline integration: PCB
# ---------------------------------------------------------------------------


class TestDryRunDiffPcb:
    """Verify diff_output behaviour in the PCB pipeline."""

    def test_noop_mutator_diff_output_no_raise(self, tmp_path: Path) -> None:
        """diff_output with a noop mutator completes without error (pcb)."""
        path = _pcb_copy(tmp_path)
        buf = _buf()
        mutate_and_validate_pcb(path, _noop_pcb, diff_output=buf)
        meaningful = [
            ln for ln in buf.getvalue().splitlines()
            if ln.startswith(("+", "-"))
            and not ln.startswith(("++", "--"))
            and ln.strip() not in ("+)", "-)", "+", "-")
        ]
        assert not meaningful, f"unexpected content diff lines: {meaningful}"

    def test_non_trivial_mutator_produces_diff(self, tmp_path: Path) -> None:
        path = _pcb_copy(tmp_path)

        def _mutate(doc: PcbDoc) -> None:
            _append_marker(doc, "dummy_pcb_marker", "p52_pcb_value")

        buf = _buf()
        mutate_and_validate_pcb(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        diff = buf.getvalue()
        assert diff
        assert "p52_pcb_value" in diff

    def test_diff_emitted_with_dry_run_true(self, tmp_path: Path) -> None:
        path = _pcb_copy(tmp_path)

        def _mutate(doc: PcbDoc) -> None:
            _append_marker(doc, "marker", "pcb_dryrun")

        buf = _buf()
        mutate_and_validate_pcb(
            path, _mutate, dry_run=True, diff_output=buf, mode=ValidationMode.NONE
        )
        assert "pcb_dryrun" in buf.getvalue()
        assert "pcb_dryrun" not in path.read_text(encoding="utf-8")

    def test_diff_headers_contain_filename(self, tmp_path: Path) -> None:
        path = _pcb_copy(tmp_path)

        def _mutate(doc: PcbDoc) -> None:
            _append_marker(doc, "m", "v")

        buf = _buf()
        mutate_and_validate_pcb(path, _mutate, diff_output=buf, mode=ValidationMode.NONE)
        diff = buf.getvalue()
        assert path.name in diff
        assert "(before)" in diff
        assert "(after)" in diff

    def test_none_diff_output_default(self, tmp_path: Path) -> None:
        path = _pcb_copy(tmp_path)
        # Should complete without error.
        mutate_and_validate_pcb(path, _noop_pcb)
