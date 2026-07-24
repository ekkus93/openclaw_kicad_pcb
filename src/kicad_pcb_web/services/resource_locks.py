"""Cross-process mutation locks for file-backed web resources."""

from __future__ import annotations

import errno
import importlib
import os
import re
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

from ..errors import ResourceBusyError
from ..settings import WebSettings

_SAFE_RESOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_LOCK_POLL_INTERVAL_S = 0.025
_FCNTL: Any = importlib.import_module("fcntl") if os.name == "posix" else None
_MSVCRT: Any = importlib.import_module("msvcrt") if os.name == "nt" else None


def _validated_resource_id(resource_id: str) -> str:
    if not resource_id or not _SAFE_RESOURCE_ID_RE.fullmatch(resource_id):
        raise ValueError(f"Unsafe resource id: {resource_id!r}")
    return resource_id


def resource_lock_path(settings: WebSettings, kind: str, resource_id: str) -> Path:
    """Return a private lock path beneath the configured data directory."""

    safe_id = _validated_resource_id(resource_id)
    if kind not in {"wizard", "jobs"}:
        raise ValueError(f"Unsupported lock kind: {kind!r}")
    return settings.data_dir / ".locks" / kind / f"{safe_id}.lock"


def _try_lock(handle: BinaryIO) -> bool:
    if os.name == "posix":
        if _FCNTL is None:
            raise RuntimeError("POSIX locking backend is unavailable")
        try:
            _FCNTL.flock(handle.fileno(), _FCNTL.LOCK_EX | _FCNTL.LOCK_NB)
        except BlockingIOError:
            return False
        return True

    if os.name == "nt":
        if _MSVCRT is None:
            raise RuntimeError("Windows locking backend is unavailable")
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            _MSVCRT.locking(handle.fileno(), _MSVCRT.LK_NBLCK, 1)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                return False
            raise
        return True

    raise RuntimeError(f"Cross-process file locking is unsupported on os.name={os.name!r}")


def _unlock(handle: BinaryIO) -> None:
    if os.name == "posix":
        if _FCNTL is None:
            raise RuntimeError("POSIX locking backend is unavailable")
        _FCNTL.flock(handle.fileno(), _FCNTL.LOCK_UN)
        return

    if os.name == "nt":
        if _MSVCRT is None:
            raise RuntimeError("Windows locking backend is unavailable")
        handle.seek(0)
        _MSVCRT.locking(handle.fileno(), _MSVCRT.LK_UNLCK, 1)
        return

    raise RuntimeError(f"Cross-process file locking is unsupported on os.name={os.name!r}")


@contextmanager
def resource_lock(
    settings: WebSettings,
    *,
    kind: str,
    resource_id: str,
) -> Iterator[None]:
    """Acquire a bounded cross-process lock or fail explicitly with HTTP 409."""

    path = resource_lock_path(settings, kind, resource_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + settings.mutation_lock_timeout_s

    with path.open("a+b") as handle:
        acquired = False
        while not acquired:
            acquired = _try_lock(handle)
            if acquired:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ResourceBusyError(
                    f"The {kind} resource is already being modified. Retry the operation.",
                    details={"resource_id": resource_id, "resource_kind": kind},
                )
            time.sleep(min(_LOCK_POLL_INTERVAL_S, remaining))

        try:
            yield
        finally:
            _unlock(handle)
