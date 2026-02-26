"""File-system helpers: S-expression validator and atomic writer."""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import uuid as _uuid_module
from pathlib import Path

from .errors import DocSyntaxError, ParseError

# ---------------------------------------------------------------------------
# Root-node allow-list (shared by validator and document loaders)
# ---------------------------------------------------------------------------

SUPPORTED_ROOTS: frozenset[str] = frozenset({"kicad_sch", "kicad_pcb"})

# ---------------------------------------------------------------------------
# UUID utility (used by multiple command modules)
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a fresh random UUID string."""
    return str(_uuid_module.uuid4())


# ---------------------------------------------------------------------------
# S-expression validation
# ---------------------------------------------------------------------------


def _tokenize_sexp(content: str) -> list[str]:
    """Return a flat list of S-expression tokens from *content*.

    String literals are consumed but **not** emitted as tokens — only ``(``,
    ``)``, and bare atoms are collected.  Backslash escapes inside strings
    (``\\"`` and ``\\\\``) are handled correctly so a quote preceded by an
    escaped backslash never prematurely ends the string.
    """
    tokens: list[str] = []
    i = 0
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == '"':
            i += 1
            while i < n:
                c = content[i]
                if c == "\\":
                    i += 2
                elif c == '"':
                    i += 1
                    break
                else:
                    i += 1
        elif ch in "()":
            tokens.append(ch)
            i += 1
        elif ch in " \t\n\r":
            i += 1
        else:
            start = i
            while i < n and content[i] not in ' \t\n\r()"':
                i += 1
            tokens.append(content[start:i])
    return tokens


def _check_sexp(content: str, root: str) -> None:
    """Raise *ParseError* if *content* has unbalanced parens or wrong root node.

    Delegates tokenization to :func:`_tokenize_sexp` so that escape sequences
    inside quoted strings (e.g. ``\\"`` — escaped backslash followed by a
    plain character) are handled correctly and never corrupt the paren count.
    """
    tokens = _tokenize_sexp(content)

    depth = sum(1 if t == "(" else -1 if t == ")" else 0 for t in tokens)
    if depth != 0:
        raise DocSyntaxError(f"Unbalanced parentheses (depth={depth}) \u2014 file may be corrupted")

    if len(tokens) < 2 or tokens[0] != "(" or tokens[1] != root:
        stripped = content.lstrip()
        raise DocSyntaxError(f"Expected root node ({root} ...) but got: {stripped[:40]!r}")


# ---------------------------------------------------------------------------
# Temp-file writer (handles partial-write risk and FD lifecycle)
# ---------------------------------------------------------------------------


def _write_temp_text(directory: Path, suffix: str, content: str) -> Path:
    """Write *content* to a new sibling temp file; flush+fsync; return its path.

    Always closes the file descriptor via ``os.fdopen`` context manager, even
    when a write error occurs.  Calls ``f.flush()`` + ``os.fsync()`` so bytes
    are durable before the caller atomically replaces the target path.

    On any exception the temp file is removed and the exception re-raised.
    """
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        with contextlib.suppress(OSError):
            Path(tmp).unlink()
        raise
    return Path(tmp)


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
            raise DocSyntaxError(f"{path}{op_label}: {exc}", path=path) from exc
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
    tmp = _write_temp_text(path.parent, ".tmp", content)
    try:
        tmp.replace(path)
    except Exception:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
