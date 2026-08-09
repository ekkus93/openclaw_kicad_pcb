"""Web-test isolation fixtures."""

from __future__ import annotations

from collections.abc import Generator

import pytest

from kicad_pcb_web.deps import get_settings


@pytest.fixture(autouse=True)
def clear_cached_web_settings() -> Generator[None, None, None]:
    """Keep immutable production settings from leaking between env-driven tests."""

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
