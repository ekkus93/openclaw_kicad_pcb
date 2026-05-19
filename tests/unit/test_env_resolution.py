"""Unit tests for Phase 8.3 — runtime environment resolution.

Verifies that critical tool-binary paths (kicad-cli) are discovered lazily
at call time rather than being frozen at import time.

Covered behaviours
------------------
* :func:`kicad_pcb.runner.find_kicad_cli` returns the current PATH result on
  every call — not a value captured at module-import time.
* :func:`kicad_pcb.runner.find_kicad_cli` falls back to the bare name
  ``"kicad-cli"`` (not an absolute fallback path) when the binary is absent
  from PATH, so the OS can still resolve it at subprocess-spawn time.
* Monkeypatching :func:`shutil.which` *after* module import changes the value
  returned by :func:`~kicad_pcb.runner.find_kicad_cli`, confirming no
  import-time freeze.
* :func:`kicad_pcb.runner.run_kicad_cli` builds its argv using
  :func:`~kicad_pcb.runner.find_kicad_cli` at invocation time, so a
  monkeypatched ``shutil.which`` is reflected in the command that is run.
* ``kicad_pcb.runner.KICAD_CLI`` is still exported as a string for
  backward-compat consumers that read it directly.
* Command functions (``cmd_drc``, ``cmd_erc``, ``cmd_export_gerbers``, …)
  call :func:`~kicad_pcb.runner.find_kicad_cli` *inside* the function body
  (at runtime) — import-time grepping for ``KICAD_CLI`` usage in those
  modules confirms there is no frozen constant.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import kicad_pcb
import kicad_pcb.runner as runner_module
from kicad_pcb.adapters import FakeRunner, KicadCliAdapter, RunResult
from kicad_pcb.commands.validation import cmd_drc
from kicad_pcb.config import set_current_project
from kicad_pcb.models import ProjectRef
from kicad_pcb.runner import KICAD_CLI, find_kicad_cli

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_PATH = "/fake/path/kicad-cli"
_OTHER_PATH = "/other/path/kicad-cli"


# ---------------------------------------------------------------------------
# find_kicad_cli — core laziness tests
# ---------------------------------------------------------------------------


class TestFindKicadCli:
    """find_kicad_cli() resolves PATH lazily at each call."""

    def test_returns_which_result_when_found(self) -> None:
        """Returns the full path from shutil.which when available."""
        with patch.object(shutil, "which", return_value=_FAKE_PATH) as mock_which:
            result = find_kicad_cli()
        mock_which.assert_called_once_with("kicad-cli")
        assert result == _FAKE_PATH

    def test_fallback_to_bare_name_when_not_found(self) -> None:
        """Falls back to the bare name 'kicad-cli', not an absolute path."""
        with patch.object(shutil, "which", return_value=None):
            result = find_kicad_cli()
        assert result == "kicad-cli"
        # Must NOT fall back to a hard-coded absolute path such as /usr/bin/kicad-cli.
        assert not result.startswith("/")

    def test_reflects_changed_path_between_calls(self) -> None:
        """Calling find_kicad_cli() twice with different which stubs returns
        different values — confirming no caching at module level."""
        with patch.object(shutil, "which", return_value=_FAKE_PATH):
            first = find_kicad_cli()

        with patch.object(shutil, "which", return_value=_OTHER_PATH):
            second = find_kicad_cli()

        assert first == _FAKE_PATH
        assert second == _OTHER_PATH
        assert first != second

    def test_monkeypatch_after_import_affects_result(self) -> None:
        """Patching shutil.which *after* the module is already imported still
        changes what find_kicad_cli() returns — proof of no import-time freeze."""
        # Module is already imported at this point.
        with patch.object(shutil, "which", return_value="/new/kicad-cli"):
            result = find_kicad_cli()
        assert result == "/new/kicad-cli"

    def test_returns_str(self) -> None:
        """Returned value is always a plain str."""
        with patch.object(shutil, "which", return_value=_FAKE_PATH):
            assert isinstance(find_kicad_cli(), str)

        with patch.object(shutil, "which", return_value=None):
            assert isinstance(find_kicad_cli(), str)


# ---------------------------------------------------------------------------
# KICAD_CLI backward-compat constant
# ---------------------------------------------------------------------------


class TestKicadCliConstant:
    """KICAD_CLI is still exported as a str for backward-compat consumers."""

    def test_kicad_cli_is_str(self) -> None:
        assert isinstance(KICAD_CLI, str)

    def test_kicad_cli_is_available_from_runner_module(self) -> None:
        assert hasattr(runner_module, "KICAD_CLI")
        assert isinstance(runner_module.KICAD_CLI, str)

    def test_kicad_cli_available_from_package(self) -> None:
        assert hasattr(kicad_pcb, "KICAD_CLI")

    def test_kicad_cli_is_not_absolute_fallback(self) -> None:
        """The old frozen fallback '/usr/bin/kicad-cli' must no longer be used.

        If kicad-cli was found on PATH when the module was imported, the value
        will be a real path; if not, it must be the bare name 'kicad-cli' so
        that the OS can resolve it at subprocess-spawn time.
        """
        if shutil.which("kicad-cli"):
            # Binary is on PATH — value may be an absolute path, that's fine.
            assert KICAD_CLI  # non-empty
        else:
            # Binary not on PATH — must NOT be the old hard-coded fallback.
            assert KICAD_CLI == "kicad-cli", (
                f"Expected bare 'kicad-cli' fallback but got {KICAD_CLI!r}. "
                "The old '/usr/bin/kicad-cli' sentinel must not appear here."
            )


# ---------------------------------------------------------------------------
# run_kicad_cli — uses find_kicad_cli() at call time
# ---------------------------------------------------------------------------


class TestRunKicadCliLaziness:
    """run_kicad_cli() builds its argv using find_kicad_cli() each time."""

    def test_run_kicad_cli_calls_find_at_invocation(self) -> None:
        """Monkeypatching find_kicad_cli after import is reflected in the
        subprocess argv used by run_kicad_cli."""
        captured: list[list[str]] = []

        def _fake_find() -> str:
            return _FAKE_PATH

        class _CapturingRunner:
            def run(self, cmd: list[str], capture: bool = True):  # type: ignore[override]
                captured.append(cmd)
                return SimpleNamespace(returncode=0, stdout="", stderr="")

        with (
            patch.object(runner_module, "find_kicad_cli", _fake_find),
            patch("kicad_pcb.runner.SubprocessRunner", return_value=_CapturingRunner()),
        ):
            runner_module.run_kicad_cli(["--version"])

        assert captured, "run_kicad_cli did not invoke the runner"
        assert captured[0][0] == _FAKE_PATH, (
            f"Expected {_FAKE_PATH!r} as first argv element, got {captured[0][0]!r}"
        )


# ---------------------------------------------------------------------------
# Command modules — no import-time KICAD_CLI freeze
# ---------------------------------------------------------------------------


class TestCommandModulesNoImportFreeze:
    """Command modules import find_kicad_cli, not the KICAD_CLI constant."""

    def _source_of(self, module_name: str) -> str:
        mod = importlib.import_module(module_name)
        return inspect.getsource(mod)

    @pytest.mark.parametrize(
        "module_name",
        [
            "kicad_pcb.commands.validation",
            "kicad_pcb.commands.export",
            "kicad_pcb.commands.preview",
            "kicad_pcb.commands.pcb",
        ],
    )
    def test_no_import_time_kicad_cli_usage(self, module_name: str) -> None:
        """Confirm KICAD_CLI is not imported or read as a module-level name."""
        src = self._source_of(module_name)
        # None of these modules should read the frozen constant anymore.
        assert "KICAD_CLI" not in src, (
            f"{module_name} still references frozen KICAD_CLI constant. "
            "Use find_kicad_cli() for lazy path resolution."
        )

    @pytest.mark.parametrize(
        "module_name",
        [
            "kicad_pcb.commands.validation",
            "kicad_pcb.commands.export",
            "kicad_pcb.commands.preview",
            "kicad_pcb.commands.pcb",
        ],
    )
    def test_uses_find_kicad_cli(self, module_name: str) -> None:
        """Confirm each command module uses find_kicad_cli() for lazy lookup."""
        src = self._source_of(module_name)
        assert "find_kicad_cli" in src, (
            f"{module_name} does not use find_kicad_cli(). "
            "Path discovery must be deferred to call time."
        )


# ---------------------------------------------------------------------------
# Command functions — adapter receives call-time path
# ---------------------------------------------------------------------------


class TestCommandAdapterInjection:
    """When no cli is injected, commands call find_kicad_cli() to build one."""

    def _make_fake_project(self, tmp_path: Path) -> None:
        """Populate a minimal project so commands don't fail on missing files."""
        proj_dir = tmp_path / "myproj"
        proj_dir.mkdir()
        (proj_dir / "myproj.kicad_pcb").write_text("(kicad_pcb)", encoding="utf-8")
        (proj_dir / "myproj.kicad_sch").write_text("(kicad_sch)", encoding="utf-8")
        set_current_project(ProjectRef(name="myproj", path=proj_dir))

    def test_cmd_drc_with_injected_cli_avoids_shutil_which(self, tmp_path: Path) -> None:
        """When cli is injected, cmd_drc never calls find_kicad_cli()."""
        self._make_fake_project(tmp_path)

        fake = FakeRunner(
            {
                "--version": RunResult(returncode=0, stdout="KiCad 9.0.7", stderr=""),
                "pcb drc": RunResult(returncode=0, stdout="", stderr=""),
            }
        )

        find_called: list[bool] = []

        def _spy_find() -> str:
            find_called.append(True)
            return _FAKE_PATH

        with patch.object(runner_module, "find_kicad_cli", _spy_find):
            cli = KicadCliAdapter(runner=fake)
            with contextlib.suppress(Exception):
                cmd_drc(SimpleNamespace(), cli=cli)

        assert not find_called, (
            "cmd_drc called find_kicad_cli() even though cli was already injected"
        )

    def test_find_kicad_cli_called_when_no_cli_injected(self, tmp_path: Path) -> None:
        """When cli is None, the command calls find_kicad_cli() to get the path."""
        self._make_fake_project(tmp_path)
        find_called: list[str] = []

        def _spy_find() -> str:
            find_called.append(_FAKE_PATH)
            return _FAKE_PATH

        with (
            patch.object(runner_module, "find_kicad_cli", _spy_find),
            patch("kicad_pcb.commands.validation.check_kicad"),
            patch("kicad_pcb.commands.validation.find_kicad_cli", _spy_find),
            patch("kicad_pcb.commands.validation.KicadCliAdapter") as mock_adapter_cls,
        ):
            mock_adapter_cls.return_value.drc.return_value = (
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                None,
            )
            cmd_drc(SimpleNamespace())

        assert find_called, "find_kicad_cli() was not called when cli was None"
        mock_adapter_cls.assert_called_once_with(kicad_cli=_FAKE_PATH)


# ---------------------------------------------------------------------------
# Integration-style: PATH change reflected without re-import
# ---------------------------------------------------------------------------


class TestPathChangeReflected:
    """Simulates a PATH update mid-process: find_kicad_cli() picks it up."""

    def test_path_change_reflected_between_calls(self) -> None:
        """Changing PATH between two find_kicad_cli() calls yields different values."""
        first_path = "/env1/kicad-cli"
        second_path = "/env2/kicad-cli"

        with patch.object(shutil, "which", return_value=first_path):
            result_before = find_kicad_cli()

        with patch.object(shutil, "which", return_value=second_path):
            result_after = find_kicad_cli()

        assert result_before == first_path
        assert result_after == second_path

    def test_none_to_found_transition(self) -> None:
        """Binary absent at first call, present on second — both values correct."""
        with patch.object(shutil, "which", return_value=None):
            absent = find_kicad_cli()

        with patch.object(shutil, "which", return_value=_FAKE_PATH):
            present = find_kicad_cli()

        assert absent == "kicad-cli"
        assert present == _FAKE_PATH
