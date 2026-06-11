"""Graphviz layout: ``dot`` binary discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from ..errors import ErrorCode, UserError

# Package-local compatibility slot: future distributions can ship a colocated
# binary here without changing discovery code. In today's tree the path does
# not exist, so user-visible resolution is env-var first, then PATH.
_BUNDLED_DOT_PATH: Path = Path(__file__).parent / "bin" / "dot"


def find_dot_binary(*, strict: bool = False) -> str | None:
    """Locate the ``dot`` binary used for Graphviz layout.

    Search order:

    1. **Package-local compatibility slot** — ``<package>/bin/dot`` if present.
    2. :envvar:`GRAPHVIZ_DOT` environment variable.
    3. System :data:`PATH` (``shutil.which``).

    Returns the resolved path string or ``None`` if not found.

    Use :func:`find_dot_source` to get both the path and discovery source.
    """
    # 1. Package-local compatibility slot.
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH)
    # 2. GRAPHVIZ_DOT environment variable.
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val:
        if Path(env_val).is_file() and os.access(env_val, os.X_OK):
            return env_val
        if strict:
            raise UserError(
                "GRAPHVIZ_DOT must point to an executable file",
                code=ErrorCode.TOOL_ERROR,
                details={"GRAPHVIZ_DOT": env_val},
            )
    # 3. System PATH.
    return shutil.which("dot")


def find_dot_source(*, strict: bool = False) -> tuple[str, str] | None:
    """Return ``(path, source)`` for the resolved ``dot`` binary.

    *source* is one of ``"bundled"``, ``"GRAPHVIZ_DOT"``, or ``"PATH"``.
    Returns ``None`` if ``dot`` cannot be found.
    """
    if _BUNDLED_DOT_PATH.is_file() and os.access(_BUNDLED_DOT_PATH, os.X_OK):
        return str(_BUNDLED_DOT_PATH), "bundled"
    env_val = os.environ.get("GRAPHVIZ_DOT", "").strip()
    if env_val:
        if Path(env_val).is_file() and os.access(env_val, os.X_OK):
            return env_val, "GRAPHVIZ_DOT"
        if strict:
            raise UserError(
                "GRAPHVIZ_DOT must point to an executable file",
                code=ErrorCode.TOOL_ERROR,
                details={"GRAPHVIZ_DOT": env_val},
            )
    path = shutil.which("dot")
    if path:
        return path, "PATH"
    return None
