"""Unit tests for session management commands."""

from __future__ import annotations

import json
import types
import uuid
from pathlib import Path

import kicad_pcb.commands.session as session_mod
import kicad_pcb.config as cfg_mod
import pytest
from kicad_pcb.commands.session import cmd_close_session, cmd_new_session, cmd_session_info
from kicad_pcb.config import clear_current_session, get_current_session, set_current_session
from kicad_pcb.errors import UserError
from kicad_pcb.models import SessionRef
from kicad_pcb.results import NewSessionResult, SessionInfoResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Patch config so all session I/O goes into tmp_path.

    Two patches are required for each symbol:
    * ``cfg_mod`` — affects code that calls via the module (e.g. config helpers).
    * ``session_mod`` — affects ``cmd_new_session`` / ``cmd_session_info`` /
      ``cmd_close_session``, which import the names directly
      (``from ..config import get_sessions_base_dir``) and therefore hold a
      direct binding that is unaffected by patching the config module.
    """
    sessions_dir = tmp_path / "sessions"
    current_file = tmp_path / "current_session.json"

    # Patch config module (for helpers called via cfg_mod.*)
    monkeypatch.setattr(cfg_mod, "CURRENT_SESSION_FILE", current_file)
    monkeypatch.setattr(cfg_mod, "get_sessions_base_dir", lambda: sessions_dir)

    # Patch commands.session module (for direct-import bindings in cmd_*)
    monkeypatch.setattr(session_mod, "get_sessions_base_dir", lambda: sessions_dir)
    monkeypatch.setattr(session_mod, "get_current_session", get_current_session)
    monkeypatch.setattr(session_mod, "set_current_session", set_current_session)
    monkeypatch.setattr(session_mod, "clear_current_session", clear_current_session)

    return tmp_path


# ---------------------------------------------------------------------------
# SessionRef model
# ---------------------------------------------------------------------------


def test_session_ref_dir_name_slugs_spaces():
    """SessionRef.dir_name replaces spaces and special chars with underscores."""
    uid = uuid.uuid4().hex
    ref = SessionRef(name="My Project!", uuid=uid, path=Path("/tmp/x"))
    assert ref.short_id == uid[:8]
    assert ref.dir_name == f"my_project_{uid[:8]}"


def test_session_ref_round_trip():
    """SessionRef.to_dict / from_dict is lossless."""
    uid = uuid.uuid4().hex
    ref = SessionRef(
        name="amp",
        uuid=uid,
        path=Path("/tmp/amp_12345678"),
        created="2025-01-01T00:00:00",
        description="test",
    )
    restored = SessionRef.from_dict(ref.to_dict())
    assert restored.name == ref.name
    assert restored.uuid == ref.uuid
    assert restored.path == ref.path
    assert restored.created == ref.created
    assert restored.description == ref.description


# ---------------------------------------------------------------------------
# cmd_new_session
# ---------------------------------------------------------------------------


def test_new_session_creates_directory(session_env: Path):
    args = types.SimpleNamespace(name="headphone_amp", description="")
    result = cmd_new_session(args)

    assert isinstance(result, NewSessionResult)
    assert result.name == "headphone_amp"
    assert Path(result.path).exists()
    assert Path(result.path).is_dir()


def test_new_session_writes_session_json(session_env: Path):
    args = types.SimpleNamespace(name="my_board", description="test board")
    result = cmd_new_session(args)

    session_json = Path(result.path) / "session.json"
    assert session_json.exists()
    data = json.loads(session_json.read_text())
    assert data["name"] == "my_board"
    assert data["description"] == "test board"


def test_new_session_dir_name_contains_short_uuid(session_env: Path):
    args = types.SimpleNamespace(name="power_supply", description="")
    result = cmd_new_session(args)
    short_id = result.uuid[:8]
    assert Path(result.path).name.endswith(short_id)


def test_new_session_sets_current_session(session_env: Path):
    args = types.SimpleNamespace(name="amp", description="")
    result = cmd_new_session(args)

    assert cfg_mod.CURRENT_SESSION_FILE.exists()
    data = json.loads(cfg_mod.CURRENT_SESSION_FILE.read_text())
    assert data["name"] == "amp"
    assert data["uuid"] == result.uuid


# ---------------------------------------------------------------------------
# get/set/clear_current_session
# ---------------------------------------------------------------------------


def test_get_current_session_returns_none_when_no_file(session_env: Path):
    assert not cfg_mod.CURRENT_SESSION_FILE.exists()
    assert get_current_session() is None


def test_set_get_current_session_round_trip(session_env: Path, tmp_path: Path):
    uid = uuid.uuid4().hex
    session_dir = tmp_path / "test_dir"
    session_dir.mkdir()
    ref = SessionRef(
        name="test",
        uuid=uid,
        path=session_dir,
        created="2025-01-01T00:00:00",
        description="",
    )
    set_current_session(ref)
    restored = get_current_session()

    assert restored is not None
    assert restored.name == ref.name
    assert restored.uuid == ref.uuid


def test_clear_current_session_removes_file(session_env: Path, tmp_path: Path):
    uid = uuid.uuid4().hex
    session_dir = tmp_path / "x"
    session_dir.mkdir()
    ref = SessionRef(name="x", uuid=uid, path=session_dir, created="", description="")
    set_current_session(ref)
    assert get_current_session() is not None

    clear_current_session()
    assert get_current_session() is None


def test_get_current_session_returns_none_for_missing_dir(session_env: Path, tmp_path: Path):
    """When the session directory has been deleted, get_current_session clears the stale
    marker and returns None instead of returning a ref with a broken path."""
    uid = uuid.uuid4().hex
    missing_dir = tmp_path / "gone"
    # Deliberately do NOT create missing_dir
    ref = SessionRef(name="stale", uuid=uid, path=missing_dir, created="", description="")
    set_current_session(ref)
    assert cfg_mod.CURRENT_SESSION_FILE.exists()

    result = get_current_session()

    assert result is None
    assert not cfg_mod.CURRENT_SESSION_FILE.exists()


# ---------------------------------------------------------------------------
# cmd_session_info
# ---------------------------------------------------------------------------


def test_session_info_raises_when_no_session(session_env: Path):
    args = types.SimpleNamespace()
    with pytest.raises(UserError, match="[Nn]o.*session"):
        cmd_session_info(args)


def test_session_info_counts_files(session_env: Path, tmp_path: Path):
    # Create a session first
    args = types.SimpleNamespace(name="counter_test", description="")
    new_result = cmd_new_session(args)
    session_dir = Path(new_result.path)

    # Populate with some fake files
    (session_dir / "netlist1.json").write_text("{}")
    (session_dir / "netlist2.json").write_text("{}")
    (session_dir / "board1.zip").write_text("fake")

    # Create a fake KiCad project subdirectory
    kicad_proj = session_dir / "MyBoard"
    kicad_proj.mkdir()
    (kicad_proj / "MyBoard.kicad_pro").write_text("{}")

    result = cmd_session_info(types.SimpleNamespace())

    assert isinstance(result, SessionInfoResult)
    assert result.project_count == 1
    assert len(result.netlist_files) == 2
    assert len(result.zip_files) == 1


# ---------------------------------------------------------------------------
# cmd_close_session
# ---------------------------------------------------------------------------


def test_close_session_deactivates_session(session_env: Path):
    args = types.SimpleNamespace(name="to_close", description="")
    cmd_new_session(args)
    assert get_current_session() is not None

    cmd_close_session(types.SimpleNamespace())
    assert get_current_session() is None


def test_close_session_does_not_delete_directory(session_env: Path):
    args = types.SimpleNamespace(name="keep_dir", description="")
    result = cmd_new_session(args)
    session_dir = Path(result.path)
    assert session_dir.exists()

    cmd_close_session(types.SimpleNamespace())
    assert session_dir.exists()  # directory must survive
