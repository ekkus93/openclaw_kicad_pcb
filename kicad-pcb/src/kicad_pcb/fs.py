"""File-system helpers: S-expression validator and atomic writer."""
from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid as _uuid_module
from pathlib import Path

from .errors import ParseError

# ---------------------------------------------------------------------------
# UUID utility (used by multiple command modules)
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a fresh random UUID string."""
    return str(_uuid_module.uuid4())


# ---------------------------------------------------------------------------
# S-expression validation
# ---------------------------------------------------------------------------


def _check_sexp(content: str, root: str) -> None:
    """Raise *ParseError* if *content* has unbalanced parens or wrong root node."""
    depth = 0
    in_string = False
    for i, ch in enumerate(content):
        if ch == '"' and (i == 0 or content[i - 1] != "\\"):
            in_string = not in_string
        elif not in_string:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
    if depth != 0:
        raise ParseError(
            f"Unbalanced parentheses (depth={depth}) — file may be corrupted"
        )
    stripped = content.lstrip()
    if not stripped.startswith(f"({root}"):
        raise ParseError(
            f"Expected root node ({root} ...) but got: {stripped[:40]!r}"
        )


# ---------------------------------------------------------------------------
# Atomic writer
# ---------------------------------------------------------------------------


def _atomic_write(
    path: Path,
    content: str,
    root: str | None = None,
    *,
    backup: bool = False,
    operation: str | None = None,
) -> None:
    """Write *content* to *path* atomically via a sibling temp file.

    If *root* is given, runs ``_check_sexp()`` on *content* before the replace
    so a corrupted KiCad S-expression file is never written to disk.

    If *backup* is ``True`` and *path* already exists, the original is copied
    to ``<path>.bak`` before being overwritten.

    *operation* is an optional human-readable label (e.g. ``"add-component"``)
    included in the ``ParseError`` message when validation fails, so callers
    can identify which command produced malformed output.
    """
    if root is not None:
        try:
            _check_sexp(content, root)
        except ParseError as exc:
            op_label = f" [{operation}]" if operation else ""
            raise ParseError(f"{path}{op_label}: {exc}") from exc
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.write(fd, content.encode())
        os.close(fd)
        Path(tmp).replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
