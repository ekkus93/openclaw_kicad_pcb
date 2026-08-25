"""Regression coverage for the bounded LLM timeout contract."""

from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb_web.settings import load_settings


def test_n3_timeout_bound_is_accepted(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KICAD_PCB_WEB_CONFIG_FILE", raising=False)
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_PROVIDER", "disabled")
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_TIMEOUT_S", "600")

    settings = load_settings()

    assert settings.llm.timeout_s == 600.0


def test_timeout_above_n3_bound_is_rejected(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KICAD_PCB_WEB_CONFIG_FILE", raising=False)
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_PROVIDER", "disabled")
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_TIMEOUT_S", "600.001")

    with pytest.raises(ValueError, match="llm.timeout_s must be 600 seconds or less"):
        load_settings()
