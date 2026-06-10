"""Unit-test configuration: autouse fixture for home-directory isolation.

Sets KICAD_PCB_CONFIG_DIR and KICAD_PCB_PROJECTS_DIR to per-test temp
directories so that no unit test ever reads from or writes to the real
~/.kicad-pcb or ~/kicad-projects.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_config_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(tmp_path / ".kicad-pcb"))
    monkeypatch.setenv("KICAD_PCB_PROJECTS_DIR", str(tmp_path / "kicad-projects"))
    yield
