"""Regression tests for config home-directory isolation.

These tests verify that:
- Dynamic helpers honour KICAD_PCB_CONFIG_DIR and KICAD_PCB_PROJECTS_DIR.
- Runtime I/O writes to the override dirs, not to the real home directory.
- Direct Path.home() / ".kicad-pcb" construction does not appear outside config.py.
- Deprecated config constants are not imported or used in runtime command modules.
"""

from __future__ import annotations

import datetime
import types
from pathlib import Path

import pytest

from kicad_pcb.adapters import FakeRunner
from kicad_pcb.commands._project import _create_project
from kicad_pcb.commands.doctor import cmd_doctor
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
# Runtime command: cmd_doctor honours KICAD_PCB_PROJECTS_DIR
# ---------------------------------------------------------------------------


def test_cmd_doctor_projects_dir_uses_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """cmd_doctor() reports the override projects dir, not the stale PROJECTS_DIR constant."""
    projects_override = tmp_path / "projects-override"
    monkeypatch.setenv("KICAD_PCB_PROJECTS_DIR", str(projects_override))

    result = cmd_doctor(types.SimpleNamespace(), runner=FakeRunner({}))

    writable_check = next(c for c in result.checks if c.label == "Projects dir writable")
    assert str(projects_override) in writable_check.message
    assert projects_override.exists()


# ---------------------------------------------------------------------------
# Runtime command: project fallback honours get_projects_dir()
# ---------------------------------------------------------------------------


def test_create_project_fallback_uses_projects_dir_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """_create_project(out_dir=None) falls back to get_projects_dir(), not the stale constant."""
    projects_override = tmp_path / "projects-override"
    monkeypatch.setenv("KICAD_PCB_PROJECTS_DIR", str(projects_override))

    ref = _create_project(name="MyProj", out_dir=None, description="")

    assert ref.path.parent == projects_override


# ---------------------------------------------------------------------------
# Source guard: no hardcoded home path outside config.py
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


# ---------------------------------------------------------------------------
# Source guard: deprecated config constants not imported in runtime modules
# ---------------------------------------------------------------------------

_DEPRECATED_CONSTANTS = (
    "CONFIG_DIR",
    "CONFIG_FILE",
    "PROJECTS_DIR",
    "CURRENT_PROJECT_FILE",
    "CURRENT_SESSION_FILE",
)

_ALLOWED_FILES = {"config.py", "__init__.py"}


def test_source_guard_no_deprecated_constants_in_runtime_modules() -> None:
    """Deprecated config constants must not be imported in runtime command modules."""
    src_dir = Path(__file__).resolve().parents[2] / "src" / "kicad_pcb"

    violations: list[str] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if py_file.name in _ALLOWED_FILES:
            continue
        content = py_file.read_text(encoding="utf-8")
        for name in _DEPRECATED_CONSTANTS:
            # Check only import lines to avoid false positives from comments or
            # legitimate local variable names that happen to match.
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("from ") and "import" in stripped and name in stripped:
                    rel = str(py_file.relative_to(src_dir.parent.parent))
                    violations.append(f"{rel}: imports deprecated constant '{name}'")
                    break

    assert not violations, "Deprecated config constants imported in runtime modules:\n" + "\n".join(
        f"  {v}" for v in violations
    )
