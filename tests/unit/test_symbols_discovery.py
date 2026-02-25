"""Unit tests for Phase 8.2 — symbol library path discovery.

Tests :func:`kicad_pcb.config.discover_symbols_dir` and related helpers,
covering each priority level in the search chain:

1. Explicit path argument
2. ``KICAD_SYMBOLS_DIR`` environment variable
3. ``symbols_dir`` key in config.json
4. Platform candidate paths
5. Nothing found → ``None``

Also covers:

* :class:`kicad_pcb.config.SymbolsDir` dataclass
* :func:`kicad_pcb.config.get_symbols_dir_config` / :func:`~kicad_pcb.config.set_symbols_dir_config`
* ``cmd_add_component`` symbol-dir propagation
* Doctor output now includes discovery source
* ``--symbols-dir`` CLI flag is parseable
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import kicad_pcb.config as cfg_module
import pytest
from kicad_pcb.adapters import FakeRunner
from kicad_pcb.cli import _build_parser
from kicad_pcb.commands.doctor import cmd_doctor
from kicad_pcb.commands.sch import cmd_add_component
from kicad_pcb.config import (
    SymbolsDir,
    discover_symbols_dir,
    get_symbols_dir_config,
    set_symbols_dir_config,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sym_dir(tmp_path: Path, name: str = "symbols") -> Path:
    """Create a minimal fake symbol library directory with one .kicad_sym stub."""
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "Device.kicad_sym").write_text("(kicad_symbol_lib (version 20211014))", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# SymbolsDir dataclass
# ---------------------------------------------------------------------------


class TestSymbolsDir:
    def test_str_shows_path_and_source(self) -> None:
        d = SymbolsDir(Path("/usr/share/kicad/symbols"), "platform:/usr/share/kicad/symbols")
        assert "/usr/share/kicad/symbols" in str(d)
        assert "platform" in str(d)

    def test_frozen(self) -> None:
        d = SymbolsDir(Path("/tmp"), "test")
        with pytest.raises(Exception):
            d.path = Path("/other")  # type: ignore[misc]

    def test_equality(self) -> None:
        a = SymbolsDir(Path("/tmp"), "config")
        b = SymbolsDir(Path("/tmp"), "config")
        assert a == b

    def test_inequality_on_source(self) -> None:
        a = SymbolsDir(Path("/tmp"), "config")
        b = SymbolsDir(Path("/tmp"), "explicit")
        assert a != b


# ---------------------------------------------------------------------------
# discover_symbols_dir — explicit path
# ---------------------------------------------------------------------------


class TestDiscoverExplicit:
    def test_returns_explicit_when_dir_exists(self, tmp_path: Path) -> None:
        sym = _make_sym_dir(tmp_path)
        result = discover_symbols_dir(explicit=sym)
        assert result is not None
        assert result.path == sym
        assert result.source == "explicit"

    def test_falls_through_when_explicit_missing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A non-existent explicit path should be skipped, not returned."""
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", "")  # disable env
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        missing = tmp_path / "nonexistent"
        result = discover_symbols_dir(explicit=missing)
        assert result is None


# ---------------------------------------------------------------------------
# discover_symbols_dir — environment variable
# ---------------------------------------------------------------------------


class TestDiscoverEnvVar:
    def test_env_var_valid_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sym = _make_sym_dir(tmp_path)
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", str(sym))
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir()
        assert result is not None
        assert result.path == sym
        assert result.source == "env:KICAD_SYMBOLS_DIR"

    def test_env_var_nonexistent_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", str(tmp_path / "nope"))
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir()
        assert result is None

    def test_env_var_empty_string_ignored(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", "")
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir()
        assert result is None

    def test_env_var_unset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir()
        assert result is None


# ---------------------------------------------------------------------------
# discover_symbols_dir — config file
# ---------------------------------------------------------------------------


class TestDiscoverConfig:
    def test_config_symbols_dir_valid(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym = _make_sym_dir(tmp_path)
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {"symbols_dir": str(sym)})

        result = discover_symbols_dir()
        assert result is not None
        assert result.path == sym
        assert result.source == "config"

    def test_config_symbols_dir_nonexistent_falls_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(
            cfg_module, "load_config", lambda: {"symbols_dir": str(tmp_path / "gone")}
        )

        result = discover_symbols_dir()
        assert result is None

    def test_config_missing_key_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir()
        assert result is None


# ---------------------------------------------------------------------------
# discover_symbols_dir — platform candidates
# ---------------------------------------------------------------------------


class TestDiscoverPlatformCandidates:
    def test_first_existing_candidate_returned(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = _make_sym_dir(tmp_path, "first")
        second = _make_sym_dir(tmp_path, "second")
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", (first, second))

        result = discover_symbols_dir()
        assert result is not None
        assert result.path == first
        assert "platform" in result.source

    def test_nonexistent_candidates_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        missing1 = tmp_path / "gone1"
        missing2 = tmp_path / "gone2"
        real = _make_sym_dir(tmp_path, "real")
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", (missing1, missing2, real))

        result = discover_symbols_dir()
        assert result is not None
        assert result.path == real

    def test_all_candidates_missing_returns_none(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})
        monkeypatch.setattr(
            cfg_module,
            "SYMBOLS_CANDIDATES",
            (tmp_path / "a", tmp_path / "b"),
        )

        result = discover_symbols_dir()
        assert result is None


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------


class TestPriorityOrder:
    def test_explicit_beats_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        explicit_dir = _make_sym_dir(tmp_path, "explicit")
        env_dir = _make_sym_dir(tmp_path, "env")
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", str(env_dir))
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})

        result = discover_symbols_dir(explicit=explicit_dir)
        assert result is not None
        assert result.source == "explicit"
        assert result.path == explicit_dir

    def test_env_beats_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        env_dir = _make_sym_dir(tmp_path, "env")
        cfg_dir = _make_sym_dir(tmp_path, "cfg")
        monkeypatch.setenv("KICAD_SYMBOLS_DIR", str(env_dir))
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", ())
        monkeypatch.setattr(cfg_module, "load_config", lambda: {"symbols_dir": str(cfg_dir)})

        result = discover_symbols_dir()
        assert result is not None
        assert result.source == "env:KICAD_SYMBOLS_DIR"

    def test_config_beats_platform(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cfg_dir = _make_sym_dir(tmp_path, "cfg")
        platform_dir = _make_sym_dir(tmp_path, "plat")
        monkeypatch.delenv("KICAD_SYMBOLS_DIR", raising=False)
        monkeypatch.setattr(cfg_module, "SYMBOLS_CANDIDATES", (platform_dir,))
        monkeypatch.setattr(cfg_module, "load_config", lambda: {"symbols_dir": str(cfg_dir)})

        result = discover_symbols_dir()
        assert result is not None
        assert result.source == "config"


# ---------------------------------------------------------------------------
# get_symbols_dir_config / set_symbols_dir_config
# ---------------------------------------------------------------------------


class TestGetSetSymbolsDirConfig:
    def test_set_and_get(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        persisted: dict = {}

        def fake_load() -> dict:
            return dict(persisted)

        def fake_save(d: dict) -> None:
            persisted.clear()
            persisted.update(d)

        monkeypatch.setattr(cfg_module, "load_config", fake_load)
        monkeypatch.setattr(cfg_module, "save_config", fake_save)

        set_symbols_dir_config(tmp_path / "syms")
        assert get_symbols_dir_config() == str(tmp_path / "syms")

    def test_clear_removes_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        persisted: dict = {"symbols_dir": "/some/path"}

        monkeypatch.setattr(cfg_module, "load_config", lambda: dict(persisted))

        saved: list[dict] = []
        monkeypatch.setattr(cfg_module, "save_config", saved.append)

        set_symbols_dir_config(None)
        assert saved and "symbols_dir" not in saved[-1]

    def test_returns_none_when_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg_module, "load_config", lambda: {})
        assert get_symbols_dir_config() is None


# ---------------------------------------------------------------------------
# cmd_add_component — symbol dir propagation
# ---------------------------------------------------------------------------


class TestCmdAddComponentSymbolDir:
    """cmd_add_component must pass the discovered (or explicit) symbols_dir
    down to read_lib_symbol_pins / read_lib_symbol_def."""

    def test_explicit_symbols_dir_passed_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --symbols-dir is set, that path is used for symbol lookups."""
        # Capture the symbols_dir passed to the reader helpers.
        captured: dict = {}

        def fake_read_pins(lib: str, sym: str, *, symbols_dir: Path) -> list[str]:
            captured["pins_dir"] = symbols_dir
            return ["1", "2"]

        def fake_read_def(lib: str, sym: str, *, symbols_dir: Path):  # type: ignore[return]
            captured["def_dir"] = symbols_dir

        sym_dir = _make_sym_dir(tmp_path)

        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_pins", fake_read_pins)
        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_def", fake_read_def)

        # Stub out get_current_project and mutate_and_validate_sch.
        sch_file = tmp_path / "board.kicad_sch"
        sch_file.write_text("(kicad_sch)", encoding="utf-8")

        project = MagicMock()
        project.sch_file = sch_file
        project.name = "test"
        monkeypatch.setattr("kicad_pcb.commands.sch.get_current_project", lambda: project)

        def fake_validate(path, mutator, **kw):  # type: ignore[return]
            mock_doc = MagicMock()
            mock_doc.next_component_position.return_value = (50.0, 50.0)
            mutator(mock_doc)

        monkeypatch.setattr("kicad_pcb.commands.sch.mutate_and_validate_sch", fake_validate)

        args = SimpleNamespace(
            lib_sym="Device:R",
            ref="R1",
            value="10k",
            footprint="Resistor_SMD:R_0402",
            symbols_dir=str(sym_dir),
            dry_run=False,
        )

        cmd_add_component(args)

        assert captured["pins_dir"] == sym_dir
        assert captured["def_dir"] == sym_dir

    def test_no_symbols_dir_uses_discovery(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without --symbols-dir, discover_symbols_dir() result is used."""
        sym_dir = _make_sym_dir(tmp_path)
        captured: dict = {}

        def fake_read_pins(lib: str, sym: str, *, symbols_dir: Path) -> list[str]:
            captured["pins_dir"] = symbols_dir
            return ["1", "2"]

        def fake_read_def(lib: str, sym: str, *, symbols_dir: Path):  # type: ignore[return]
            captured["def_dir"] = symbols_dir

        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_pins", fake_read_pins)
        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_def", fake_read_def)
        # Make discover_symbols_dir return our tmp sym_dir.
        monkeypatch.setattr(
            "kicad_pcb.commands.sch.discover_symbols_dir",
            lambda *, explicit=None: SymbolsDir(sym_dir, "env:KICAD_SYMBOLS_DIR"),
        )

        sch_file = tmp_path / "board.kicad_sch"
        sch_file.write_text("(kicad_sch)", encoding="utf-8")
        project = MagicMock()
        project.sch_file = sch_file
        project.name = "test"
        monkeypatch.setattr("kicad_pcb.commands.sch.get_current_project", lambda: project)

        def fake_validate2(path, mutator, **kw):  # type: ignore[return]
            mock_doc = MagicMock()
            mock_doc.next_component_position.return_value = (50.0, 50.0)
            mutator(mock_doc)

        monkeypatch.setattr("kicad_pcb.commands.sch.mutate_and_validate_sch", fake_validate2)

        args = SimpleNamespace(
            lib_sym="Device:R",
            ref="R1",
            value="10k",
            footprint=None,
            symbols_dir=None,
            dry_run=False,
        )

        cmd_add_component(args)

        assert captured["pins_dir"] == sym_dir


# ---------------------------------------------------------------------------
# doctor — symbol library check shows discovery source
# ---------------------------------------------------------------------------


class TestDoctorSymbolLibraries:
    def test_found_shows_source_in_detail(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir = _make_sym_dir(tmp_path)

        monkeypatch.setattr(
            "kicad_pcb.commands.doctor.discover_symbols_dir",
            lambda: SymbolsDir(sym_dir, "env:KICAD_SYMBOLS_DIR"),
        )

        result = cmd_doctor(SimpleNamespace(), runner=FakeRunner({}))
        sym_checks = [c for c in result.checks if c.label == "Symbol libraries"]
        assert sym_checks, "Expected a 'Symbol libraries' check item"
        check = sym_checks[0]
        assert check.status == "ok"
        assert "env:KICAD_SYMBOLS_DIR" in (check.detail or "")

    def test_not_found_gives_actionable_message(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "kicad_pcb.commands.doctor.discover_symbols_dir",
            lambda: None,
        )

        result = cmd_doctor(SimpleNamespace(), runner=FakeRunner({}))
        sym_checks = [c for c in result.checks if c.label == "Symbol libraries"]
        assert sym_checks
        check = sym_checks[0]
        assert check.status == "error"
        # Message should guide user on how to fix
        assert "KICAD_SYMBOLS_DIR" in check.message or "symbols_dir" in check.message


# ---------------------------------------------------------------------------
# CLI: --symbols-dir flag
# ---------------------------------------------------------------------------


class TestCliSymbolsDirFlag:
    def test_symbols_dir_flag_is_accepted(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["add-component", "Device:R", "R1", "--symbols-dir", "/tmp/syms"])
        assert args.symbols_dir == "/tmp/syms"

    def test_symbols_dir_defaults_to_none(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["add-component", "Device:R", "R1"])
        assert args.symbols_dir is None
