"""Transactional mutate-and-validate pipeline for ``.kicad_sch`` and ``.kicad_pcb`` files.

:class:`ValidationMode` controls the strictness of checks performed just before
a mutated file is committed to disk.

==========  ============================================================
Mode        Checks applied
==========  ============================================================
``NONE``    No checks — bare atomic write (fastest, unsafe).
``SYNTAX``  Round-trip parse + root-node type assertion.
``LINT``    ``SYNTAX`` plus structural lint rules (error-level only).
``KICAD``   ``LINT`` plus KiCad CLI ERC / DRC validation.
``FULL``    ``KICAD`` plus lint warnings treated as errors.
==========  ============================================================

Usage
-----
::

    from kicad_pcb.pipeline import ValidationMode, mutate_and_validate_sch
    from kicad_pcb.sch_doc import SchematicDoc

    def _mutate(doc: SchematicDoc) -> None:
        doc.add_label("VCC", 60.0, 50.0, new_uuid())

    mutate_and_validate_sch(
        Path("project.kicad_sch"),
        _mutate,
        operation="add-net",
    )
"""

from __future__ import annotations

import contextlib
import difflib
import logging
import time
from collections.abc import Callable
from enum import IntEnum
from pathlib import Path
from typing import IO

from .adapters import KicadCliAdapter
from .errors import ParseError, ToolError
from .fs import _atomic_write, _write_temp_text
from .lint import LintError, LintIssue, LintSeverity, lint_pcb, lint_schematic
from .pcb_doc import PcbDoc
from .sch_doc import SchematicDoc
from .sexpr.nodes import ListNode
from .sexpr.parser import parse
from .sexpr.serializer import serialize

__all__ = [
    "ValidationMode",
    "mutate_and_validate_pcb",
    "mutate_and_validate_sch",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ValidationMode
# ---------------------------------------------------------------------------


class ValidationMode(IntEnum):
    """Controls which checks run before a file is committed to disk.

    Modes are ordered by strictness; a comparison like
    ``mode >= ValidationMode.LINT`` is safe.
    """

    NONE = 0
    """No validation — bare atomic write.  Do not use in production."""
    SYNTAX = 1
    """Round-trip parse + root-node assertion."""
    LINT = 2
    """``SYNTAX`` + structural lint rules (ERROR-level findings only)."""
    KICAD = 3
    """``LINT`` + KiCad CLI ERC / DRC validation."""
    FULL = 4
    """``KICAD`` + lint WARNING findings treated as errors."""

    @classmethod
    def default(cls) -> ValidationMode:
        """Default mode used by all mutating commands (``LINT``)."""
        return cls.LINT


# ---------------------------------------------------------------------------
# Schematic pipeline
# ---------------------------------------------------------------------------


def mutate_and_validate_sch(  # noqa: PLR0913 — keyword-only args make call sites clean
    path: Path,
    mutator: Callable[[SchematicDoc], None],
    *,
    mode: ValidationMode = ValidationMode.LINT,
    cli: KicadCliAdapter | None = None,
    backup: bool = False,
    operation: str | None = None,
    strict: bool = False,
    dry_run: bool = False,
    diff_output: IO[str] | None = None,
) -> None:
    """Load *path*, apply *mutator*, validate, then commit atomically.

    Parameters
    ----------
    path:
        Path to the ``.kicad_sch`` file to update.
    mutator:
        Callable that receives the loaded :class:`~kicad_pcb.sch_doc.SchematicDoc`
        and performs in-place mutations.  Must **not** call ``.save()``.
    mode:
        How much validation to run before committing.
    cli:
        :class:`~kicad_pcb.adapters.KicadCliAdapter` for ``KICAD`` / ``FULL``
        modes.  Ignored when ``mode < ValidationMode.KICAD``.
    backup:
        When ``True``, copy the original file to ``<path>.bak`` before overwriting.
    operation:
        Human-readable label included in exception messages (e.g. ``"add-component"``).
    strict:
        When ``True``, treat WARNING-level lint issues as errors regardless of *mode*.
        ``FULL`` mode implies *strict* automatically.
    dry_run:
        When ``True``, run all validation steps but **skip the final write**.
        The file is left unchanged; useful for preflight checks.
    diff_output:
        When not ``None``, a unified diff of the original file content versus
        the serialised result is written to this stream after validation
        completes successfully.  Pass ``sys.stdout`` for console output or a
        :class:`io.StringIO` for programmatic access.  Works whether
        *dry_run* is ``True`` or ``False``.

    Raises
    ------
    :exc:`~kicad_pcb.errors.ParseError`
        On syntax / round-trip failure (``SYNTAX`` mode and above).
    :exc:`~kicad_pcb.lint.LintError`
        On structural lint failures (``LINT`` mode and above).
    :exc:`~kicad_pcb.errors.ToolError`
        On KiCad CLI validation failure (``KICAD`` / ``FULL`` modes).
    """
    original_text = path.read_text(encoding="utf-8") if diff_output is not None else ""

    t0 = time.perf_counter()
    doc = SchematicDoc.load(path)
    _log_stage("read", path=path, mode=mode, operation=operation, t0=t0)

    t1 = time.perf_counter()
    mutator(doc)
    _log_stage("mutate", path=path, mode=mode, operation=operation, t0=t1)

    t2 = time.perf_counter()
    content = serialize(doc.root)
    _log_stage("serialize", path=path, mode=mode, operation=operation, t0=t2)

    # SYNTAX: round-trip parse + root-node check.
    parsed_root = None
    if mode >= ValidationMode.SYNTAX:
        t3 = time.perf_counter()
        parsed_root = _syntax_check(content, "kicad_sch", operation=operation)
        _log_stage("parse", path=path, mode=mode, operation=operation, t0=t3)

    # LINT: structural rules on the round-tripped AST.
    if mode >= ValidationMode.LINT:
        t4 = time.perf_counter()
        lint_root = parsed_root if parsed_root is not None else parse(content)
        effective_strict = strict or mode >= ValidationMode.FULL
        issues = lint_schematic(lint_root)
        _raise_if_errors(issues, strict=effective_strict, operation=operation)
        _log_stage("validate.lint", path=path, mode=mode, operation=operation, t0=t4)

    # KICAD: run ERC on the (as-yet uncommitted) new content.
    if mode >= ValidationMode.KICAD and cli is not None:
        t5 = time.perf_counter()
        _kicad_validate_sch(content, path, cli, operation=operation)
        _log_stage("validate.kicad", path=path, mode=mode, operation=operation, t0=t5)

    # Unified diff output (before commit, so visible even in dry-run).
    if diff_output is not None:
        _show_diff(original_text, content, path, diff_output)

    # Commit to disk (skipped in dry-run mode).
    if not dry_run:
        t6 = time.perf_counter()
        _atomic_write(path, content, root="kicad_sch", backup=backup, operation=operation)
        _log_stage("write", path=path, mode=mode, operation=operation, t0=t6)


# ---------------------------------------------------------------------------
# PCB pipeline
# ---------------------------------------------------------------------------


def mutate_and_validate_pcb(  # noqa: PLR0913 — keyword-only args make call sites clean
    path: Path,
    mutator: Callable[[PcbDoc], None],
    *,
    mode: ValidationMode = ValidationMode.LINT,
    cli: KicadCliAdapter | None = None,
    backup: bool = False,
    operation: str | None = None,
    strict: bool = False,
    dry_run: bool = False,
    diff_output: IO[str] | None = None,
) -> None:
    """Load *path*, apply *mutator*, validate, then commit atomically.

    Same contract as :func:`mutate_and_validate_sch` but for ``.kicad_pcb``
    files.  Uses :func:`~kicad_pcb.lint.lint_pcb` for structural checks and
    :meth:`~kicad_pcb.adapters.KicadCliAdapter.drc` for KiCad CLI validation.

    When *dry_run* is ``True`` all validation runs but the file is not written.
    Accepts the same *diff_output* parameter as :func:`mutate_and_validate_sch`.
    """
    original_text = path.read_text(encoding="utf-8") if diff_output is not None else ""

    t0 = time.perf_counter()
    doc = PcbDoc.load(path)
    _log_stage("read", path=path, mode=mode, operation=operation, t0=t0)

    t1 = time.perf_counter()
    mutator(doc)
    _log_stage("mutate", path=path, mode=mode, operation=operation, t0=t1)

    t2 = time.perf_counter()
    content = serialize(doc.root)
    _log_stage("serialize", path=path, mode=mode, operation=operation, t0=t2)

    parsed_root = None
    if mode >= ValidationMode.SYNTAX:
        t3 = time.perf_counter()
        parsed_root = _syntax_check(content, "kicad_pcb", operation=operation)
        _log_stage("parse", path=path, mode=mode, operation=operation, t0=t3)

    if mode >= ValidationMode.LINT:
        t4 = time.perf_counter()
        lint_root = parsed_root if parsed_root is not None else parse(content)
        effective_strict = strict or mode >= ValidationMode.FULL
        issues = lint_pcb(lint_root)
        _raise_if_errors(issues, strict=effective_strict, operation=operation)
        _log_stage("validate.lint", path=path, mode=mode, operation=operation, t0=t4)

    if mode >= ValidationMode.KICAD and cli is not None:
        t5 = time.perf_counter()
        _kicad_validate_pcb(content, path, cli, operation=operation)
        _log_stage("validate.kicad", path=path, mode=mode, operation=operation, t0=t5)

    # Unified diff output (before commit, so visible even in dry-run).
    if diff_output is not None:
        _show_diff(original_text, content, path, diff_output)

    if not dry_run:
        t6 = time.perf_counter()
        _atomic_write(path, content, root="kicad_pcb", backup=backup, operation=operation)
        _log_stage("write", path=path, mode=mode, operation=operation, t0=t6)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _show_diff(
    original: str,
    updated: str,
    path: Path,
    out: IO[str],
) -> None:
    """Write a unified diff of *original* → *updated* to *out*.

    Uses :func:`difflib.unified_diff` with ``fromfile`` / ``tofile`` labels
    derived from *path*.  When *original* and *updated* are identical, nothing
    is written.
    """
    lines_a = original.splitlines(keepends=True)
    lines_b = updated.splitlines(keepends=True)
    diff = difflib.unified_diff(
        lines_a,
        lines_b,
        fromfile=f"{path.name} (before)",
        tofile=f"{path.name} (after)",
    )
    out.writelines(diff)


def _log_stage(
    stage: str,
    *,
    path: Path,
    mode: ValidationMode,
    operation: str | None,
    t0: float,
) -> None:
    """Emit a DEBUG log record for one pipeline stage.

    The *kicad* key in ``extra`` carries the structured context dict so that
    log handlers (e.g. JSON formatters) can surface it machine-readably:

    .. code-block:: json

        {"stage": "read", "path": "/…/proj.kicad_sch",
         "mode": "LINT", "operation": "add-net", "elapsed_ms": 3.14}
    """
    elapsed_ms = round((time.perf_counter() - t0) * 1000, 3)
    logger.debug(
        "[%s] %s  mode=%s  op=%s  elapsed_ms=%.3f",
        stage,
        path.name,
        mode.name,
        operation or "-",
        elapsed_ms,
        extra={
            "kicad": {
                "stage": stage,
                "path": str(path),
                "mode": mode.name,
                "operation": operation,
                "elapsed_ms": elapsed_ms,
            }
        },
    )


def _syntax_check(content: str, expected_root: str, *, operation: str | None) -> ListNode:
    """Parse *content* and assert the root node key matches *expected_root*.

    Returns the parsed root node on success.  Raises :exc:`ParseError` on
    parse failure or root-key mismatch.
    """
    try:
        root = parse(content)
    except ParseError as exc:
        op = f" [{operation}]" if operation else ""
        raise ParseError(f"Round-trip parse failed{op}: {exc}") from exc
    if root.key != expected_root:
        op = f" [{operation}]" if operation else ""
        raise ParseError(f"Expected root node '{expected_root}', got '{root.key}'{op}")
    return root


def _raise_if_errors(
    issues: list[LintIssue],
    *,
    strict: bool,
    operation: str | None,
) -> None:
    """Raise :exc:`LintError` if *issues* contains blocking findings.

    When *strict* is ``True``, WARNING-level issues are also treated as
    blocking.
    """
    errors = [
        i
        for i in issues
        if i.severity == LintSeverity.ERROR or (strict and i.severity == LintSeverity.WARNING)
    ]
    if not errors:
        return
    lead = f"[{operation}] " if operation else ""
    summary = "; ".join(f"{i.code}: {i.message}" for i in errors[:5])
    if len(errors) > 5:
        summary += f" (and {len(errors) - 5} more)"
    raise LintError(f"{lead}Lint errors prevented write: {summary}", issues=errors)


def _kicad_validate_sch(
    content: str,
    original_path: Path,
    cli: KicadCliAdapter,
    *,
    operation: str | None,
) -> None:
    """Write *content* to a temp file and run kicad-cli ERC against it."""
    tmp_report_suffix = ".erc.json"
    tmp_sch = _write_temp_text(original_path.parent, ".kicad_sch.tmp", content)
    tmp_report = tmp_sch.with_suffix(tmp_report_suffix)
    try:
        result, report = cli.erc(tmp_sch, tmp_report)
        _check_kicad_result(result, report, validation_name="ERC", operation=operation)
    finally:
        with contextlib.suppress(OSError):
            tmp_sch.unlink()
        with contextlib.suppress(OSError):
            tmp_report.unlink()


def _kicad_validate_pcb(
    content: str,
    original_path: Path,
    cli: KicadCliAdapter,
    *,
    operation: str | None,
) -> None:
    """Write *content* to a temp file and run kicad-cli DRC against it."""
    tmp_report_suffix = ".drc.json"
    tmp_pcb = _write_temp_text(original_path.parent, ".kicad_pcb.tmp", content)
    tmp_report = tmp_pcb.with_suffix(tmp_report_suffix)
    try:
        result, report = cli.drc(tmp_pcb, tmp_report)
        _check_kicad_result(result, report, validation_name="DRC", operation=operation)
    finally:
        with contextlib.suppress(OSError):
            tmp_pcb.unlink()
        with contextlib.suppress(OSError):
            tmp_report.unlink()


def _check_kicad_result(
    result: object,
    report: dict | None,
    *,
    validation_name: str,
    operation: str | None,
) -> None:
    """Raise :exc:`ToolError` when a kicad-cli validation run failed."""
    # result is a RunResult — access .returncode and .stderr via duck-typing
    rc = getattr(result, "returncode", 0)
    stderr = getattr(result, "stderr", "")
    prefix = f"[{operation}] " if operation else ""

    if rc != 0:
        msg = f"{prefix}KiCad {validation_name} failed"
        if stderr:
            msg += f": {stderr[:300]}"
        raise ToolError(msg)

    if report and report.get("violations"):
        count = len(report["violations"])
        raise ToolError(f"{prefix}KiCad {validation_name} found {count} violation(s)")
