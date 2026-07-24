"""Durable atomic file writes for canonical web-app state."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from ..errors import PersistenceError

LOGGER = logging.getLogger("uvicorn.error")


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Atomically replace *path* with fully flushed *payload*.

    The temporary file is created beside the destination so ``os.replace``
    remains an atomic same-filesystem operation. The previous destination is
    never truncated before the replacement is ready.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())

        temp_path.replace(path)
        temp_path = None
        _fsync_directory(path.parent)
    except PersistenceError:
        raise
    except Exception as exc:
        raise PersistenceError(
            "Failed to commit persisted state.",
            details={"filename": path.name, "error_type": type(exc).__name__},
        ) from exc
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                # Preserve the primary failure, but make the orphan visible. It
                # is a hidden non-canonical temp file and is never read as state.
                LOGGER.warning(
                    "failed to remove atomic-write temporary file",
                    extra={
                        "temp_file": temp_path.name,
                        "error_type": type(cleanup_exc).__name__,
                    },
                )


def atomic_write_text(path: Path, payload: str) -> None:
    """Atomically replace *path* with UTF-8 text."""

    atomic_write_bytes(path, payload.encode("utf-8"))


def atomic_write_json(path: Path, payload: Any, *, sort_keys: bool = True) -> None:
    """Serialize *payload* completely, then atomically replace *path*."""

    try:
        encoded = (
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=sort_keys) + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersistenceError(
            "Failed to serialize persisted state.",
            details={"filename": path.name, "error_type": type(exc).__name__},
        ) from exc
    atomic_write_bytes(path, encoded)


def _fsync_directory(directory: Path) -> None:
    """Flush a directory entry after replacement on POSIX platforms."""

    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(directory, flags)
        os.fsync(descriptor)
    except OSError as exc:
        raise PersistenceError(
            "Failed to flush persisted-state directory.",
            details={"directory": directory.name, "error_type": type(exc).__name__},
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
