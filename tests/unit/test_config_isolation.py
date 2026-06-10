"""Regression tests for config home-directory isolation.

These tests verify that:
- Dynamic helpers honour KICAD_PCB_CONFIG_DIR and KICAD_PCB_PROJECTS_DIR.
- Runtime I/O writes to the override dirs, not to the real home directory.
- Direct Path.home() / ".kicad-pcb" construction does not appear outside config.py.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest

from kicad_pcb.config import (
    get_config_dir,
    get_current_project_file,
    get_current_session_file,
    get_projects_dir,
    set_current_project,
)
from kicad_pcb.models import ProjectRef

# ---------------------------------------------------------------------------
# Default path resolution (env var absent)
# ---------------------------------------------------------------------------


def test_get_config_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default config dir is Path.home() / '.kicad-pcb' when override is absent."""
    monkeypatch.delenv("KICAD_PCB_CONFIG_DIR", raising=False)
    assert get_config_dir() == Path.home() / ".kicad-pcb"


def test_get_projects_dir_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default projects dir is Path.home() / 'kicad-projects' when override is absent."""
    monkeypatch.delenv("KICAD_PCB_PROJECTS_DIR", raising=False)
    assert get_projects_dir() == Path.home() / "kicad-projects"


# ---------------------------------------------------------------------------
# Override path resolution (env var present)
# ---------------------------------------------------------------------------


def test_get_config_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """KICAD_PCB_CONFIG_DIR override is honoured."""
    override = str(tmp_path / "cfg")
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", override)
    assert get_config_dir() == Path(override)


def test_get_projects_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """KICAD_PCB_PROJECTS_DIR override is honoured."""
    override = str(tmp_path / "projects")
    monkeypatch.setenv("KICAD_PCB_PROJECTS_DIR", override)
    assert get_projects_dir() == Path(override)


def test_current_project_file_is_under_config_dir(tmp_path: Path) -> None:
    """get_current_project_file() returns a path under the active config dir."""
    assert get_current_project_file() == get_config_dir() / "current_project.json"


def test_current_session_file_is_under_config_dir(tmp_path: Path) -> None:
    """get_current_session_file() returns a path under the active config dir."""
    assert get_current_session_file() == get_config_dir() / "current_session.json"


# ---------------------------------------------------------------------------
# Runtime write goes to override dir
# ---------------------------------------------------------------------------


def test_set_current_project_writes_under_override_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """set_current_project() creates the file under KICAD_PCB_CONFIG_DIR."""
    override_dir = tmp_path / "isolated-cfg"
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(override_dir))

    ref = ProjectRef(
        name="test",
        path=tmp_path,
        created=datetime.datetime(2026, 1, 1).isoformat(),
        description="",
    )
    set_current_project(ref)

    assert (override_dir / "current_project.json").exists()


# ---------------------------------------------------------------------------
# Source guard
# ---------------------------------------------------------------------------


def test_source_guard_no_hardcoded_home_kicad_pcb_outside_config() -> None:
    """Direct Path.home() / '.kicad-pcb' construction must not appear outside config.py."""
    src_dir = Path(__file__).resolve().parents[2] / "src" / "kicad_pcb"
    allowed_file = src_dir / "config.py"
    forbidden = 'Path.home() / ".kicad-pcb"'

    violations = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.resolve() == allowed_file.resolve():
            continue
        content = py_file.read_text(encoding="utf-8")
        if forbidden in content:
            violations.append(str(py_file.relative_to(src_dir.parent.parent)))

    assert not violations, (
        f"Direct '{forbidden}' construction found outside config.py:\n"
        + "\n".join(f"  {v}" for v in violations)
    )
