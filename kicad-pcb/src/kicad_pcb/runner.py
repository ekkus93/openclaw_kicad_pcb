"""kicad-cli runner: tool detection and subprocess wrapper."""
from __future__ import annotations

import shutil
import subprocess

from .errors import ToolError

# Auto-detect kicad-cli at import time (Phase 8.3 TODO: inject via DI instead).
KICAD_CLI: str = shutil.which("kicad-cli") or "/usr/bin/kicad-cli"


def check_kicad() -> None:
    """Raise *ToolError* if kicad-cli is not available on PATH."""
    if not shutil.which("kicad-cli"):
        raise ToolError(
            "KiCad CLI not found!\n"
            "\nInstall KiCad:\n"
            "  Ubuntu: sudo apt install kicad\n"
            "  Or: https://www.kicad.org/download/"
        )


def run_kicad_cli(
    args: list[str], capture: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a kicad-cli sub-command and return the CompletedProcess."""
    cmd = [KICAD_CLI, *args]
    if capture:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    return subprocess.run(cmd, text=True, check=False)
