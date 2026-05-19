"""Integration tests for session-aware behaviour in cmd_new_from_netlist.

All session paths in cmd_new_from_netlist were previously untested because the
existing test helpers always pass ``out_dir`` explicitly, bypassing the session
logic entirely.  The tests here cover every branch of the session integration:

* project location  — session.path used as out_dir when no explicit flag given
* netlist resolution — bare filename resolved from session dir automatically
* auto-zip creation  — <session_dir>/<name>_schematic.zip created on success
* result fields      — result.session_path / result.zip_path populated correctly
* explicit out_dir   — overrides session; project goes to explicit path instead
* no active session  — zip_path and session_path are None; explicit out_dir used

Patching note
-------------
``cmd_new_from_netlist`` uses ``from ..config import get_current_session`` which
creates a *direct binding* in ``kicad_pcb.commands.netlist``.  Tests must patch
that binding (``kicad_pcb.commands.netlist.get_current_session``), not the
config module attribute.
"""

from __future__ import annotations

import json
import uuid
from argparse import Namespace
from pathlib import Path

import pytest

import kicad_pcb.commands.netlist as netlist_mod
from kicad_pcb.models import SessionRef
from kicad_pcb.results import NewFromNetlistResult

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "symbols"

# Minimal valid Circuit IR (uses TestLib symbols from the fixtures dir).
_SIMPLE_IR: dict = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "TestLib:R", "value": "10k"},
        {"ref": "R2", "symbol": "TestLib:R", "value": "10k"},
    ],
    "nets": [
        {"name": "VCC", "pins": [{"ref": "R1", "pin": "1"}]},
        {"name": "VMID", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "1"}]},
        {"name": "GND", "pins": [{"ref": "R2", "pin": "2"}]},
    ],
}


def _write_ir(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _make_session(session_dir: Path) -> SessionRef:
    """Return a SessionRef whose path is *session_dir* (must already exist)."""
    session_dir.mkdir(parents=True, exist_ok=True)
    return SessionRef(
        name="test_session",
        uuid=str(uuid.uuid4()),
        path=session_dir,
        created="2026-01-01T00:00:00",
        description="",
    )


def _run_new_from_netlist(netlist: str, *, out_dir: str | None = None) -> NewFromNetlistResult:
    """Invoke cmd_new_from_netlist with internal-mode validation."""
    args = Namespace(
        name="TestProject",
        netlist=netlist,
        symbols_dir=str(_FIXTURES_DIR),
        mode="internal",
        out_dir=out_dir,
        description="",
        auto_fix=True,
        strict=False,
    )
    return netlist_mod.cmd_new_from_netlist(args)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNewFromNetlistSessionIntegration:
    """Session-aware behaviour in cmd_new_from_netlist."""

    def test_with_session_project_goes_in_session_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When a session is active and no --out-dir is given, the KiCad project
        is created inside the session directory."""
        session_dir = tmp_path / "session"
        session = _make_session(session_dir)
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: session)

        ir_path = tmp_path / "ir.json"
        _write_ir(ir_path, _SIMPLE_IR)

        result = _run_new_from_netlist(str(ir_path))

        assert Path(result.path).parent == session_dir, (
            f"Project should be inside session dir {session_dir}, but got {result.path}"
        )

    def test_with_session_creates_zip_in_session_dir(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After a successful run with an active session, a zip file is created
        in the session directory."""
        session_dir = tmp_path / "session"
        session = _make_session(session_dir)
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: session)

        ir_path = tmp_path / "ir.json"
        _write_ir(ir_path, _SIMPLE_IR)

        result = _run_new_from_netlist(str(ir_path))

        assert result.zip_path is not None, "zip_path should be set when session is active"
        zip_path = Path(result.zip_path)
        assert zip_path.exists(), f"Zip file not found: {zip_path}"
        assert zip_path.parent == session_dir, (
            f"Zip should be inside session dir {session_dir}, got {zip_path}"
        )

    def test_with_session_result_has_session_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """result.session_path is populated with the active session directory."""
        session_dir = tmp_path / "session"
        session = _make_session(session_dir)
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: session)

        ir_path = tmp_path / "ir.json"
        _write_ir(ir_path, _SIMPLE_IR)

        result = _run_new_from_netlist(str(ir_path))

        assert result.session_path == session_dir

    def test_with_session_resolves_netlist_by_filename(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If --netlist is a bare filename that does not exist at the literal path
        but DOES exist inside the session directory, it is resolved automatically."""
        session_dir = tmp_path / "session"
        session = _make_session(session_dir)
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: session)

        # Write the IR inside the session dir; pass only the filename to the command.
        ir_in_session = session_dir / "circuit.json"
        _write_ir(ir_in_session, _SIMPLE_IR)

        # Passing just the filename — it won't exist at CWD but should be found
        # via the session directory fallback.
        result = _run_new_from_netlist("circuit.json")

        assert result.path is not None, "Should succeed when netlist resolved from session dir"

    def test_explicit_out_dir_overrides_session(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit --out-dir flag takes precedence over the active session dir
        for project placement (auto-zip still goes into the session dir)."""
        session_dir = tmp_path / "session"
        explicit_dir = tmp_path / "explicit"
        session = _make_session(session_dir)
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: session)

        ir_path = tmp_path / "ir.json"
        _write_ir(ir_path, _SIMPLE_IR)

        result = _run_new_from_netlist(str(ir_path), out_dir=str(explicit_dir))

        assert Path(result.path).parent == explicit_dir, (
            f"Project should be in explicit dir {explicit_dir}, got {result.path}"
        )
        # Zip should still go into the session dir (not the explicit out_dir).
        assert result.zip_path is not None
        assert Path(result.zip_path).parent == session_dir

    def test_no_session_no_zip_no_session_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without an active session, zip_path and session_path are both None."""
        monkeypatch.setattr(netlist_mod, "get_current_session", lambda: None)

        ir_path = tmp_path / "ir.json"
        _write_ir(ir_path, _SIMPLE_IR)

        result = _run_new_from_netlist(str(ir_path), out_dir=str(tmp_path))

        assert result.zip_path is None, "No zip should be created without an active session"
        assert result.session_path is None
