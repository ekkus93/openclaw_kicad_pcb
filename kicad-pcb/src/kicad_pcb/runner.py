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


def find_kicad_cli() -> str:
    """Return the kicad-cli executable path, discovered lazily at call time.

    Checks ``PATH`` on every call so that PATH changes between calls are
    respected and tests can control the result via monkeypatching
    ``shutil.which`` or this function itself.  Falls back to the bare name
    ``"kicad-cli"`` (letting the OS resolve it at subprocess-spawn time) if
    the binary is not currently on PATH.
    """
    return shutil.which("kicad-cli") or "kicad-cli"


# Backward-compat alias: frozen once at import time to the result of
# find_kicad_cli().  Retained so existing ``from .runner import KICAD_CLI``
# imports continue to work without change.  The value is now "kicad-cli" when
# the binary is absent from PATH (instead of the old "/usr/bin/kicad-cli"
# hard-code), which lets the OS resolve the name at subprocess-spawn time.
# New code should call find_kicad_cli() to get a fresh PATH lookup each time.
KICAD_CLI: str = find_kicad_cli()


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
    # Call find_kicad_cli() at invocation time so that PATH changes in the
    # current process (e.g. during tests) are always reflected.
    return SubprocessRunner().run([find_kicad_cli(), *args], capture=capture)
