"""Unit tests for tests/conftest.py skip helpers."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from tests.conftest import kicad_cli_supports_repo_schematics, kicad_cli_version


def _raise_permission(*args: object, **kwargs: object) -> None:
    raise PermissionError("no permission")


def _raise_oserror(*args: object, **kwargs: object) -> None:
    raise OSError("exec failure")


class TestKicadCliVersion:
    def test_returns_none_when_not_on_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: None)
        assert kicad_cli_version() is None

    def test_returns_none_on_permission_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/kicad-cli")
        monkeypatch.setattr(subprocess, "run", _raise_permission)
        assert kicad_cli_version() is None

    def test_returns_none_on_oserror(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/kicad-cli")
        monkeypatch.setattr(subprocess, "run", _raise_oserror)
        assert kicad_cli_version() is None

    def test_supports_repo_schematics_false_on_oserror(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(shutil, "which", lambda name: "/fake/kicad-cli")
        monkeypatch.setattr(subprocess, "run", _raise_oserror)
        assert kicad_cli_supports_repo_schematics() is False
