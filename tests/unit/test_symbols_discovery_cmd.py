"""Symbols discovery tests — cmd_add_component, doctor, CLI flag."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kicad_pcb.adapters import FakeRunner
from kicad_pcb.cli import _build_parser
from kicad_pcb.commands.doctor import cmd_doctor
from kicad_pcb.commands.sch import cmd_add_component
from kicad_pcb.config import (
    SymbolsDir,
)
from kicad_pcb.errors import ErrorCode, UserError


def _make_sym_dir(tmp_path: Path, name: str = "symbols") -> Path:
    """Create a minimal fake symbol library directory with one .kicad_sym stub."""
    d = tmp_path / name
    d.mkdir(parents=True)
    (d / "Device.kicad_sym").write_text("(kicad_symbol_lib (version 20211014))", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# SymbolsDir dataclass
# ---------------------------------------------------------------------------


class TestCmdAddComponentSymbolDir:
    """cmd_add_component must pass the discovered (or explicit) symbols_dir
    down to read_lib_symbol_pins / read_lib_symbol_def_flat."""

    def test_explicit_symbols_dir_passed_through(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --symbols-dir is set, that path is used for symbol lookups."""
        # Capture the symbols_dir passed to the reader helpers.
        captured: dict = {}

        def fake_read_pins(lib: str, sym: str, *, symbols_dir: Path) -> list[str]:
            captured["pins_dir"] = symbols_dir
            return ["1", "2"]

        def fake_read_def(lib: str, sym: str, *, symbols_dir: Path) -> None:
            captured["def_dir"] = symbols_dir

        sym_dir = _make_sym_dir(tmp_path)

        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_pins", fake_read_pins)
        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_def_flat", fake_read_def)

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

        def fake_read_def(lib: str, sym: str, *, symbols_dir: Path) -> None:
            captured["def_dir"] = symbols_dir

        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_pins", fake_read_pins)
        monkeypatch.setattr("kicad_pcb.commands.sch.read_lib_symbol_def_flat", fake_read_def)
        # Make discover_symbols_dir return our tmp sym_dir.
        monkeypatch.setattr(
            "kicad_pcb.commands.sch.discover_symbols_dir",
            lambda *, explicit=None, strict=False: SymbolsDir(sym_dir, "env:KICAD_SYMBOLS_DIR"),
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

    def test_raises_when_no_symbols_dir_can_be_resolved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "kicad_pcb.commands.sch.discover_symbols_dir",
            lambda *, explicit=None, strict=False: None,
        )

        sch_file = tmp_path / "board.kicad_sch"
        sch_file.write_text("(kicad_sch)", encoding="utf-8")
        project = MagicMock()
        project.sch_file = sch_file
        project.name = "test"
        monkeypatch.setattr("kicad_pcb.commands.sch.get_current_project", lambda: project)

        args = SimpleNamespace(
            lib_sym="Device:R",
            ref="R1",
            value="10k",
            footprint=None,
            symbols_dir=None,
            dry_run=False,
        )

        with pytest.raises(UserError) as exc_info:
            cmd_add_component(args)

        assert exc_info.value.code == ErrorCode.SYMBOL_DIR_MISSING

    def test_raises_when_symbol_pins_not_found(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sym_dir = _make_sym_dir(tmp_path)
        monkeypatch.setattr(
            "kicad_pcb.commands.sch.discover_symbols_dir",
            lambda *, explicit=None, strict=False: SymbolsDir(sym_dir, "explicit"),
        )
        monkeypatch.setattr(
            "kicad_pcb.commands.sch.read_lib_symbol_pins",
            lambda *args, **kwargs: [],
        )

        sch_file = tmp_path / "board.kicad_sch"
        sch_file.write_text("(kicad_sch)", encoding="utf-8")
        project = MagicMock()
        project.sch_file = sch_file
        project.name = "test"
        monkeypatch.setattr("kicad_pcb.commands.sch.get_current_project", lambda: project)

        args = SimpleNamespace(
            lib_sym="Device:Nope",
            ref="R1",
            value="10k",
            footprint=None,
            symbols_dir=str(sym_dir),
            dry_run=False,
        )

        with pytest.raises(UserError) as exc_info:
            cmd_add_component(args)

        assert exc_info.value.code == ErrorCode.SYMBOL_NOT_FOUND


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
