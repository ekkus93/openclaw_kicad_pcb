"""Injectable adapters for subprocess and filesystem side effects.

This module provides:

* :class:`RunResult` — typed subprocess result replacing ``CompletedProcess``.
* :class:`RunnerProtocol` / :class:`SubprocessRunner` / :class:`FakeRunner` —
  thin injection seam around ``subprocess.run``.
* :class:`FsProtocol` / :class:`RealFs` / :class:`FakeFs` —
  thin injection seam around ``pathlib.Path`` filesystem operations.
* :class:`KicadCliAdapter` — typed, injectable wrapper for all ``kicad-cli``
  sub-commands used by the skill.

Inject :class:`FakeRunner` and :class:`FakeFs` into :class:`KicadCliAdapter`
for unit testing without a real KiCad installation or real filesystem::

    fake_runner = FakeRunner(
        {"pcb drc": RunResult(0, "", "")},
        files={"/tmp/proj/drc_report.json": '{"violations": []}'},
    )
    cli = KicadCliAdapter(runner=fake_runner, fs=fake_runner.fs)
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from .compat import CliCapability, KiCadVersion, parse_version
from .compat import require_capability as _check_capability

# ---------------------------------------------------------------------------
# RunResult — typed subprocess outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunResult:
    """Immutable record of a completed subprocess call.

    Replaces ``subprocess.CompletedProcess[str]`` throughout the codebase.
    All callers that previously accessed ``.returncode``, ``.stdout``, and
    ``.stderr`` continue to work without change.
    """

    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        """True when *returncode* is zero."""
        return self.returncode == 0

    def output_text(self) -> str:
        """Combined stdout + stderr, useful for error messages."""
        parts = [s for s in (self.stdout.strip(), self.stderr.strip()) if s]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# RunnerProtocol + SubprocessRunner + FakeRunner
# ---------------------------------------------------------------------------


@runtime_checkable
class RunnerProtocol(Protocol):
    """Minimal Protocol for running external commands."""

    def run(self, cmd: list[str], *, capture: bool = True) -> RunResult:
        """Execute *cmd* and return a :class:`RunResult`."""
        ...


class SubprocessRunner:
    """Real implementation that delegates to ``subprocess.run``."""

    def run(self, cmd: list[str], *, capture: bool = True) -> RunResult:
        if capture:
            r = subprocess.run(cmd, capture_output=True, text=True, check=False)
            return RunResult(r.returncode, r.stdout, r.stderr)
        r = subprocess.run(cmd, text=True, check=False)
        return RunResult(r.returncode, "", "")


class FakeRunner:
    """Configurable fake runner for unit tests.

    *responses* maps a two-word sub-command key (e.g. ``"pcb drc"``,
    ``"sch erc"``, ``"--version"``) to the :class:`RunResult` to return.
    Falls back to *default_result* (``RunResult(0, "", "")``) when no
    match is found.

    All calls are recorded in :attr:`calls` for assertion in tests.

    Example::

        runner = FakeRunner({
            "pcb drc": RunResult(0, "", ""),
            "sch erc": RunResult(1, "", "ERC found 2 errors"),
        })
    """

    def __init__(
        self,
        responses: dict[str, RunResult] | None = None,
        *,
        default_result: RunResult | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._responses: dict[str, RunResult] = responses or {}
        self._default = default_result or RunResult(returncode=0, stdout="", stderr="")

    def run(self, cmd: list[str], *, capture: bool = True) -> RunResult:  # noqa: ARG002
        self.calls.append(list(cmd))
        # Build lookup key from the first two args after the binary.
        # e.g. ["kicad-cli", "pcb", "drc", ...] → "pcb drc"
        # e.g. ["kicad-cli", "--version"]        → "--version"
        if len(cmd) > 2:
            key = f"{cmd[1]} {cmd[2]}"
        elif len(cmd) > 1:
            key = cmd[1]
        else:
            key = ""
        return self._responses.get(key, self._default)


# ---------------------------------------------------------------------------
# FsProtocol + RealFs + FakeFs
# ---------------------------------------------------------------------------


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
        return path.read_text()

    def write_text(self, path: Path, content: str) -> None:
        path.write_text(content)

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
        return len(self._files[path].encode())


# ---------------------------------------------------------------------------
# KicadCliAdapter — typed wrapper for all kicad-cli operations
# ---------------------------------------------------------------------------


class KicadCliAdapter:
    """Typed, injectable adapter for all ``kicad-cli`` sub-commands.

    Inject *runner* and *fs* for unit testing without a real KiCad
    installation or real filesystem::

        fake_runner = FakeRunner({"pcb drc": RunResult(0, "", "")})
        fake_fs = FakeFs({"/tmp/proj/drc_report.json": '{"violations": []}'})
        cli = KicadCliAdapter(runner=fake_runner, fs=fake_fs)
        result, report = cli.drc(
            Path("/tmp/proj/board.kicad_pcb"),
            Path("/tmp/proj/drc_report.json"),
        )
        assert result.ok
        assert report == {"violations": []}
    """

    def __init__(
        self,
        kicad_cli: str = "kicad-cli",
        *,
        runner: RunnerProtocol | None = None,
        fs: FsProtocol | None = None,
        version: KiCadVersion | None = None,
    ) -> None:
        self._cli = kicad_cli
        self._runner: RunnerProtocol = runner or SubprocessRunner()
        self._fs: FsProtocol = fs or RealFs()
        # Optionally injected for tests; populated lazily on first access otherwise.
        self._detected_version: KiCadVersion | None = version

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run(self, args: list[str], *, capture: bool = True) -> RunResult:
        return self._runner.run([self._cli, *args], capture=capture)

    def _read_json(self, path: Path) -> dict | None:
        """Read *path* as JSON via the injected fs; return ``None`` on failure."""
        if not self._fs.exists(path):
            return None
        try:
            return json.loads(self._fs.read_text(path))
        except (json.JSONDecodeError, OSError):
            return None

    def _read_text_safe(self, path: Path) -> str:
        """Read *path* as text via the injected fs; return empty string on failure."""
        if not self._fs.exists(path):
            return ""
        try:
            return self._fs.read_text(path)
        except OSError:
            return ""

    # ------------------------------------------------------------------
    # Version
    # ------------------------------------------------------------------

    @property
    def detected_version(self) -> KiCadVersion | None:
        """Lazily detect and cache the installed kicad-cli version.

        Returns ``None`` when the version cannot be parsed (e.g. kicad-cli is
        not installed, or a :class:`FakeRunner` returns an empty string).  This
        is intentional: callers that cannot determine the version do not fail.
        """
        if self._detected_version is None:
            with contextlib.suppress(Exception):
                r = self._run(["--version"])
                raw = (r.stdout.strip() or r.stderr.strip()).splitlines()[0]
                self._detected_version = parse_version(raw)
        return self._detected_version

    def require_capability(self, cap: CliCapability) -> None:
        """Raise :class:`~kicad_pcb.errors.ToolError` if the installed
        kicad-cli does not support *cap*.

        Delegates to :func:`~kicad_pcb.compat.require_capability` with the
        lazily-detected version.  When the version is unknown (``None``),
        the check is skipped and the request proceeds.
        """
        _check_capability(self.detected_version, cap)

    def version(self) -> RunResult:
        """Return kicad-cli ``--version`` output."""
        return self._run(["--version"])

    # ------------------------------------------------------------------
    # Validation commands
    # ------------------------------------------------------------------

    def drc(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, dict | None]:
        """Run PCB design rules check.

        Returns ``(RunResult, report_dict | None)``.  *report_dict* is the
        parsed JSON report if kicad-cli wrote it; ``None`` otherwise.
        """
        result = self._run(
            [
                "pcb",
                "drc",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--severity-all",
                str(pcb_file),
            ]
        )
        return result, self._read_json(output_file)

    def erc(self, sch_file: Path, output_file: Path) -> tuple[RunResult, dict | None]:
        """Run schematic electrical rules check.

        Returns ``(RunResult, report_dict | None)``.
        """
        result = self._run(
            [
                "sch",
                "erc",
                "--format",
                "json",
                "--output",
                str(output_file),
                "--severity-all",
                str(sch_file),
            ]
        )
        return result, self._read_json(output_file)

    # ------------------------------------------------------------------
    # Export commands
    # ------------------------------------------------------------------

    def export_gerbers(self, pcb_file: Path, output_dir: Path) -> tuple[RunResult, list[Path]]:
        """Export Gerber files.

        Returns ``(RunResult, [written_files])``.  File list is populated via
        the injected :attr:`_fs``, enabling tests to prepopulate fake files.
        """
        self._fs.mkdir(output_dir, exist_ok=True)
        result = self._run(
            [
                "pcb",
                "export",
                "gerbers",
                "--output",
                str(output_dir),
                str(pcb_file),
            ]
        )
        files = self._fs.glob(output_dir, "*") if result.ok else []
        return result, files

    def export_drill(self, pcb_file: Path, output_dir: Path) -> RunResult:
        """Export Excellon drill files."""
        self._fs.mkdir(output_dir, exist_ok=True)
        return self._run(
            [
                "pcb",
                "export",
                "drill",
                "--output",
                str(output_dir),
                "--format",
                "excellon",
                "--excellon-separate-th",
                "--generate-map",
                "--map-format",
                "pdf",
                str(pcb_file),
            ]
        )

    def export_bom(self, sch_file: Path, output_file: Path) -> tuple[RunResult, list[str]]:
        """Export bill of materials as CSV.

        Returns ``(RunResult, csv_lines)``.
        """
        result = self._run(
            [
                "sch",
                "export",
                "bom",
                "--output",
                str(output_file),
                "--fields",
                "Reference,Value,Footprint,${QUANTITY},Datasheet",
                "--labels",
                "Refs,Value,Footprint,Qty,Datasheet",
                "--group-by",
                "Value,Footprint",
                "--sort-field",
                "Reference",
                str(sch_file),
            ]
        )
        lines: list[str] = []
        if result.ok:
            content = self._read_text_safe(output_file)
            if content:
                lines = content.splitlines()
        return result, lines

    def export_netlist(self, sch_file: Path, output_file: Path) -> tuple[RunResult, str]:
        """Export KiCad XML netlist.

        Returns ``(RunResult, xml_content)`` where *xml_content* is empty on
        failure or when the file was not written.
        """
        result = self._run(
            [
                "sch",
                "export",
                "netlist",
                "--output",
                str(output_file),
                "--format",
                "kicadxml",
                str(sch_file),
            ]
        )
        content = ""
        if result.ok:
            content = self._read_text_safe(output_file)
        return result, content

    def export_pos(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, list[str]]:
        """Export pick-and-place CSV.

        Returns ``(RunResult, csv_lines)``.
        """
        result = self._run(
            [
                "pcb",
                "export",
                "pos",
                "--output",
                str(output_file),
                "--format",
                "csv",
                "--units",
                "mm",
                "--side",
                "both",
                str(pcb_file),
            ]
        )
        lines: list[str] = []
        if result.ok:
            content = self._read_text_safe(output_file)
            if content:
                lines = content.splitlines()
        return result, lines

    def export_step(self, pcb_file: Path, output_file: Path) -> tuple[RunResult, int]:
        """Export STEP 3D model.

        Returns ``(RunResult, file_size_bytes)`` — *file_size_bytes* is 0
        when the file was not written.

        Requires kicad-cli >= 8.0 (``--no-unspecified`` flag).
        """
        self.require_capability(CliCapability.PCB_EXPORT_STEP_NO_UNSPECIFIED)
        result = self._run(
            [
                "pcb",
                "export",
                "step",
                "--output",
                str(output_file),
                "--force",
                "--no-unspecified",
                str(pcb_file),
            ]
        )
        size = 0
        if result.ok and self._fs.exists(output_file):
            with contextlib.suppress(OSError):
                size = self._fs.stat_size(output_file)
        return result, size

    # ------------------------------------------------------------------
    # Preview / SVG export
    # ------------------------------------------------------------------

    def export_svg_sch(self, sch_file: Path, output_file: Path) -> RunResult:
        """Export SVG preview of a schematic."""
        return self._run(
            [
                "sch",
                "export",
                "svg",
                "--output",
                str(output_file),
                str(sch_file),
            ]
        )

    def export_svg_pcb(self, pcb_file: Path, output_file: Path, layer: str) -> RunResult:
        """Export SVG preview for a single PCB layer."""
        return self._run(
            [
                "pcb",
                "export",
                "svg",
                "--output",
                str(output_file),
                "--layers",
                layer,
                str(pcb_file),
            ]
        )

    def export_glb(self, pcb_file: Path, output_file: Path) -> RunResult:
        """Export 3D GLB model.

        Requires kicad-cli >= 8.0.
        """
        self.require_capability(CliCapability.PCB_EXPORT_GLB)
        return self._run(
            [
                "pcb",
                "export",
                "glb",
                "--output",
                str(output_file),
                str(pcb_file),
            ]
        )

    # ------------------------------------------------------------------
    # Specctra (for Freerouting auto-route)
    # ------------------------------------------------------------------

    def export_specctra_dsn(self, pcb_file: Path, output_file: Path) -> RunResult:
        """Export Specctra DSN file for Freerouting."""
        return self._run(
            [
                "pcb",
                "export",
                "specctra",
                "--output",
                str(output_file),
                str(pcb_file),
            ]
        )

    def import_specctra_ses(self, ses_file: Path, output_pcb: Path) -> RunResult:
        """Import a Freerouting SES session file back into the PCB."""
        return self._run(
            [
                "pcb",
                "import",
                "specctra",
                "--output",
                str(output_pcb),
                str(ses_file),
            ]
        )
