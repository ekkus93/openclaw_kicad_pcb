"""Web app import smoke test."""

from __future__ import annotations

from kicad_pcb_web.main import app


def test_web_app_import() -> None:
    assert app.title == "KiCad PCB Web App"
