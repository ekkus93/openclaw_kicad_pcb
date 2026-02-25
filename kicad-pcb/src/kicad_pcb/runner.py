"""kicad-cli runner: tool detection and subprocess wrapper.

.. note::
    :func:`run_kicad_cli` is kept for backward compatibility.  New code
    should inject a :class:`~kicad_pcb.adapters.KicadCliAdapter` instead of
    calling this function directly.
"""
from __future__ import annotations

import shutil

from .adapters import RunResult, SubprocessRunner
from .errors import ToolError

# Auto-detect kicad-cli at import time (Phase 8.3 TODO: inject via DI instead).
KICAD_CLI: str = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"

_runner = SubprocessRunner()


def check_kicad() -> None:
    """Raise *ToolError* if kicad-cli is not available on PATH."""
    if not shutil.which("kicad-cli"):
        raise ToolError(
            "KiCad CLI not found!\n"
            "\nInstall KiCad:\n"
            "  Ubuntu: sudo apt install kicad\n"
            "  Or: https://www.kicad.org/download/"
        )


def run_kicad_cli(args: list[str], capture: bool = True) -> RunResult:
    """Run a kicad-cli sub-command and return a :class:`~kicad_pcb.adapters.RunResult`.

    .. deprecated::
        Prefer injecting a :class:`~kicad_pcb.adapters.KicadCliAdapter` into
        command functions.  This function is retained for backward compatibility
        with call sites not yet migrated to the adapter pattern.
    """
    return _runner.run([KICAD_CLI, *args], capture=capture)
