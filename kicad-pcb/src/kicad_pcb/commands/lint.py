"""Standalone lint / validate / format commands (Phase 6.3).

These commands operate directly on existing KiCad files without requiring an
active project context.  Each function accepts a plain ``argparse.Namespace``
with a ``path`` attribute pointing to the target file.
"""
from __future__ import annotations

from pathlib import Path

from ..errors import ParseError, UserError
from ..fs import _atomic_write
from ..lint import LintIssue, LintSeverity, lint_pcb, lint_schematic
from ..results import FormatFileResult, LintFileResult, ValidateFileResult
from ..sexpr.nodes import AtomNode, ListNode
from ..sexpr.parser import parse_file
from ..sexpr.serializer import serialize

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_path(raw: str) -> Path:
    """Return an absolute, existing path or raise :class:`~kicad_pcb.errors.UserError`."""
    p = Path(raw).resolve()
    if not p.exists():
        raise UserError(f"File not found: {p}")
    if not p.is_file():
        raise UserError(f"Not a file: {p}")
    return p


def _make_lint_result(path: Path, issues: list[LintIssue]) -> LintFileResult:
    """Build a :class:`~kicad_pcb.results.LintFileResult` from a list of issues."""
    error_count = sum(1 for i in issues if i.severity is LintSeverity.ERROR)
    warning_count = sum(1 for i in issues if i.severity is LintSeverity.WARNING)
    return LintFileResult(
        path=path,
        issues=tuple(issues),
        error_count=error_count,
        warning_count=warning_count,
        ok=(error_count == 0),
    )


def _parse_or_raise(path: Path, expected_root: str) -> ListNode | None:
    """Parse *path* and return the root :class:`~kicad_pcb.sexpr.nodes.ListNode`.

    Returns ``None`` when the file cannot be parsed cleanly so callers can
    handle syntax failures gracefully.
    """
    try:
        root = parse_file(path)
    except (ParseError, OSError):
        return None
    if not isinstance(root, ListNode):
        return None
    # Verify the root node name matches what we expect.
    first = root.items[0] if root.items else None
    if not isinstance(first, AtomNode) or first.value != expected_root:
        return None
    return root


def _validate(
    path: Path,
    *,
    expected_root: str,
    lint_fn: object,  # Callable[[ListNode], list[LintIssue]]
) -> ValidateFileResult:
    """Parse *path*, check root key, then run *lint_fn*."""
    root = _parse_or_raise(path, expected_root)
    if root is None:
        return ValidateFileResult(
            path=path,
            syntax_ok=False,
            lint_issues=(),
            lint_error_count=0,
            lint_warning_count=0,
            kicad_checked=False,
            kicad_ok=False,
            ok=False,
        )
    issues: list[LintIssue] = lint_fn(root)  # type: ignore[operator]
    error_count = sum(1 for i in issues if i.severity is LintSeverity.ERROR)
    warning_count = sum(1 for i in issues if i.severity is LintSeverity.WARNING)
    return ValidateFileResult(
        path=path,
        syntax_ok=True,
        lint_issues=tuple(issues),
        lint_error_count=error_count,
        lint_warning_count=warning_count,
        kicad_checked=False,
        kicad_ok=False,
        ok=(error_count == 0),
    )


def _format_file(path: Path, *, expected_root: str) -> FormatFileResult:
    """Round-trip parse → serialize → atomic-write if content changed."""
    root = _parse_or_raise(path, expected_root)
    if root is None:
        raise ParseError(f"Cannot parse {path} as a valid {expected_root} file")
    serialized = serialize(root)
    original = path.read_text(encoding="utf-8")
    changed = serialized != original
    if changed:
        _atomic_write(path, serialized, expected_root, operation="format")
    return FormatFileResult(
        path=path,
        changed=changed,
        size_bytes=len(serialized.encode("utf-8")),
    )


# ---------------------------------------------------------------------------
# Public command functions
# ---------------------------------------------------------------------------


def cmd_lint_sch(args: object) -> LintFileResult:
    """Lint a ``.kicad_sch`` file and return findings."""
    path = _resolve_path(getattr(args, "path", ""))
    root = _parse_or_raise(path, "kicad_sch")
    if root is None:
        raise ParseError(f"Cannot parse {path} as a valid kicad_sch file")
    return _make_lint_result(path, lint_schematic(root))


def cmd_lint_pcb(args: object) -> LintFileResult:
    """Lint a ``.kicad_pcb`` file and return findings."""
    path = _resolve_path(getattr(args, "path", ""))
    root = _parse_or_raise(path, "kicad_pcb")
    if root is None:
        raise ParseError(f"Cannot parse {path} as a valid kicad_pcb file")
    return _make_lint_result(path, lint_pcb(root))


def cmd_validate_sch(args: object) -> ValidateFileResult:
    """Validate syntax + lint rules for a ``.kicad_sch`` file."""
    path = _resolve_path(getattr(args, "path", ""))
    return _validate(path, expected_root="kicad_sch", lint_fn=lint_schematic)


def cmd_validate_pcb(args: object) -> ValidateFileResult:
    """Validate syntax + lint rules for a ``.kicad_pcb`` file."""
    path = _resolve_path(getattr(args, "path", ""))
    return _validate(path, expected_root="kicad_pcb", lint_fn=lint_pcb)


def cmd_format_sch(args: object) -> FormatFileResult:
    """Canonicalise a ``.kicad_sch`` file in-place (round-trip format)."""
    path = _resolve_path(getattr(args, "path", ""))
    return _format_file(path, expected_root="kicad_sch")


def cmd_format_pcb(args: object) -> FormatFileResult:
    """Canonicalise a ``.kicad_pcb`` file in-place (round-trip format)."""
    path = _resolve_path(getattr(args, "path", ""))
    return _format_file(path, expected_root="kicad_pcb")
