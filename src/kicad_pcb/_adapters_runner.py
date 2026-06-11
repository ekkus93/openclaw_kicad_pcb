"""RunResult and subprocess runner implementations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


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


@runtime_checkable
class RunnerProtocol(Protocol):
    """Minimal Protocol for running external commands."""

    def run(self, cmd: list[str], *, capture: bool = True) -> RunResult:
        """Execute *cmd* and return a :class:`RunResult`."""
        ...


class SubprocessRunner:
    """Real implementation that delegates to ``subprocess.run``."""

    def run(self, cmd: list[str], *, capture: bool = True) -> RunResult:
        try:
            if capture:
                r = subprocess.run(cmd, capture_output=True, text=True, check=False)
                return RunResult(r.returncode, r.stdout, r.stderr)
            r = subprocess.run(cmd, text=True, check=False)
            return RunResult(r.returncode, "", "")
        except FileNotFoundError:
            return RunResult(127, "", f"command not found: {cmd[0]}")
        except PermissionError as exc:
            return RunResult(126, "", f"command not executable: {cmd[0]}: {exc}")
        except OSError as exc:
            return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")


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
