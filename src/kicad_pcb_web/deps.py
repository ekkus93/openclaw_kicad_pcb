"""Dependency helpers for the web layer."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends

from .services.llm import LlmClient, build_llm_client
from .settings import WebSettings, load_settings

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"


def get_settings() -> WebSettings:
    """Return current app settings."""

    return load_settings()


def get_llm_client(settings: WebSettings = Depends(get_settings)) -> LlmClient | None:
    """Return the configured LLM client, or ``None`` when disabled."""

    return build_llm_client(settings)
