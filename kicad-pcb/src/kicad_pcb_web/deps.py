"""Dependency helpers for the web layer."""

from __future__ import annotations

from pathlib import Path

from fastapi.templating import Jinja2Templates

from .settings import WebSettings, load_settings

PACKAGE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"
_TEMPLATES = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_settings() -> WebSettings:
    """Return current app settings."""

    return load_settings()


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja template renderer."""

    return _TEMPLATES
