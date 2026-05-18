"""Web-app settings."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebSettings:
    """Runtime settings for the local web app."""

    data_dir: Path
    jobs_dir: Path
    default_host: str = "127.0.0.1"
    default_port: int = 8000


def load_settings() -> WebSettings:
    """Load filesystem-backed settings from the environment."""

    data_dir = Path(os.environ.get("KICAD_PCB_WEB_DATA_DIR", "data")).resolve()
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return WebSettings(data_dir=data_dir, jobs_dir=jobs_dir)
