"""Shared pytest fixtures for kicad-pcb skill tests."""
from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCRIPTS_DIR = Path(__file__).parent.parent / "kicad-pcb" / "scripts"
KICAD_PCB_PY = SCRIPTS_DIR / "kicad_pcb.py"


# ---------------------------------------------------------------------------
# Markers / skip helpers
# ---------------------------------------------------------------------------


def kicad_cli_available() -> bool:
    return shutil.which("kicad-cli") is not None


requires_kicad = pytest.mark.skipif(
    not kicad_cli_available(),
    reason="kicad-cli not found on PATH",
)


# ---------------------------------------------------------------------------
# Project fixtures
# ---------------------------------------------------------------------------

# kicad-cli on this system is a Flatpak wrapper and can only access paths
# under the user's home directory. pytest's tmp_path lives under /tmp which
# is outside the Flatpak sandbox. We therefore create integration-test
# scratch directories under ~/tmp/kicad-tests/ and clean up in teardown.
_HOME_TMP_BASE = Path.home() / "tmp" / "kicad-tests"


@pytest.fixture()
def home_tmp() -> Path:
    """Return a unique temp dir under ~/ (accessible to Flatpak kicad-cli)."""
    run_dir = _HOME_TMP_BASE / str(uuid.uuid4())
    run_dir.mkdir(parents=True, exist_ok=True)
    yield run_dir
    shutil.rmtree(run_dir, ignore_errors=True)


@pytest.fixture()
def run_script(tmp_path: Path):
    """Return a helper that runs kicad_pcb.py with given args in a temp home."""
    import sys
    python = sys.executable

    def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}
        return subprocess.run(
            [python, str(KICAD_PCB_PY), *args],
            capture_output=True,
            text=True,
            cwd=str(cwd or tmp_path),
            env=env,
        )

    return _run
