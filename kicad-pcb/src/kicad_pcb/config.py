"""Project configuration constants and R/W helpers."""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".kicad-pcb"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_DIR = Path.home() / "kicad-projects"
CURRENT_PROJECT_FILE = CONFIG_DIR / "current_project.json"

DEFAULT_PCB_OPTIONS: dict = {
    "layers": 2,
    "thickness": 1.6,
    "color": "green",
    "surface_finish": "hasl",
    "copper_weight": "1oz",
    "min_hole": 0.3,
    "min_trace": 0.15,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dirs() -> None:
    """Create necessary directories."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration; return defaults if file is absent or corrupt."""
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"projects_dir": str(PROJECTS_DIR)}


def save_config(config: dict) -> None:
    """Persist configuration to disk."""
    ensure_dirs()
    with CONFIG_FILE.open("w") as f:
        json.dump(config, f, indent=2)


def get_current_project() -> dict | None:
    """Return the currently-selected project dict, or None."""
    if CURRENT_PROJECT_FILE.exists():
        try:
            with CURRENT_PROJECT_FILE.open() as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    return None


def set_current_project(project: dict) -> None:
    """Persist the current project."""
    ensure_dirs()
    with CURRENT_PROJECT_FILE.open("w") as f:
        json.dump(project, f, indent=2)
