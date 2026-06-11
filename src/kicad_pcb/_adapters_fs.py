"""FsProtocol and filesystem implementations (RealFs, FakeFs)."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class FsProtocol(Protocol):
    """Minimal filesystem Protocol for injectable I/O in command modules."""

    def read_text(self, path: Path) -> str:
        """Return the UTF-8 text content of *path*."""
        ...

    def write_text(self, path: Path, content: str) -> None:
        """Write *content* to *path* (overwrite if exists)."""
        ...

    def exists(self, path: Path) -> bool:
        """Return ``True`` if *path* exists."""
        ...

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        """Create directory at *path*."""
        ...

    def glob(self, path: Path, pattern: str) -> list[Path]:
        """Return paths inside *path* matching *pattern*."""
        ...

    def iterdir(self, path: Path) -> list[Path]:
        """Return direct children of *path*."""
        ...

    def unlink(self, path: Path) -> None:
        """Remove the file at *path*."""
        ...

    def stat_size(self, path: Path) -> int:
        """Return the byte size of *path*."""
        ...


class RealFs:
    """Real filesystem implementation — thin delegate to ``pathlib.Path``."""

    def read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8")

    def write_text(self, path: Path, content: str) -> None:
        path.write_text(content, encoding="utf-8")

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path, *, parents: bool = False, exist_ok: bool = False) -> None:
        path.mkdir(parents=parents, exist_ok=exist_ok)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        return list(path.glob(pattern))

    def iterdir(self, path: Path) -> list[Path]:
        return list(path.iterdir())

    def unlink(self, path: Path) -> None:
        path.unlink()

    def stat_size(self, path: Path) -> int:
        return path.stat().st_size


class FakeFs:
    """In-memory filesystem for unit tests.

    Prepopulate with *files* (mapping ``str | Path`` → file content) and
    *dirs* (set of paths to treat as existing directories).  New files and
    directories written at runtime are also stored in memory.

    Example::

        fs = FakeFs(
            files={"/tmp/proj/drc_report.json": '{"violations": []}'},
            dirs={"/tmp/proj"},
        )
        assert fs.exists(Path("/tmp/proj/drc_report.json"))
    """

    def __init__(
        self,
        files: dict[str | Path, str] | None = None,
        *,
        dirs: set[str | Path] | None = None,
    ) -> None:
        self._files: dict[Path, str] = {Path(k): v for k, v in (files or {}).items()}
        self._dirs: set[Path] = {Path(d) for d in (dirs or set())}

    # --- FsProtocol implementation ---

    def read_text(self, path: Path) -> str:
        if path not in self._files:
            raise FileNotFoundError(f"FakeFs: no file at {path!r}")
        return self._files[path]

    def write_text(self, path: Path, content: str) -> None:
        self._files[path] = content

    def exists(self, path: Path) -> bool:
        return path in self._files or path in self._dirs

    def mkdir(
        self,
        path: Path,
        *,
        parents: bool = False,
        exist_ok: bool = False,  # noqa: ARG002
    ) -> None:
        self._dirs.add(path)

    def glob(self, path: Path, pattern: str) -> list[Path]:
        full_pattern = str(path / pattern)
        return [p for p in self._files if fnmatch.fnmatch(str(p), full_pattern)]

    def iterdir(self, path: Path) -> list[Path]:
        return [p for p in self._files if p.parent == path]

    def unlink(self, path: Path) -> None:
        if path not in self._files:
            raise FileNotFoundError(f"FakeFs: no file at {path!r}")
        del self._files[path]

    def stat_size(self, path: Path) -> int:
        if path not in self._files:
            raise FileNotFoundError(f"FakeFs: no file at {path!r}")
        return len(self._files[path].encode("utf-8"))
