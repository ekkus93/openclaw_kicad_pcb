"""Session management commands: new-session, session-info, close-session.

A *session* is a uniquely-named directory that groups all artefacts for a
single design task — netlist JSON files, the generated KiCad project
sub-directory, and any zipped schematic outputs.  Using sessions prevents the
bot from accidentally reusing stale files from a previous run.

Workflow::

    kicad_pcb new-session --name headphone_amp
    # ...bot writes headphone_amp_netlist.json into the session dir...
    kicad_pcb new-from-netlist --name HeadphoneAmp \\
        --netlist headphone_amp_netlist.json
    # KiCad project created at <session_dir>/HeadphoneAmp/
    # Zip created at <session_dir>/HeadphoneAmp_schematic.zip
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime

from ..config import (
    clear_current_session,
    get_current_session,
    get_sessions_base_dir,
    set_current_session,
)
from ..errors import UserError
from ..models import SessionRef
from ..results import NewSessionResult, SessionInfoResult

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_new_session(args) -> NewSessionResult:
    """Create a fresh session directory and set it as the current session.

    Usage: new-session [--name NAME] [--description DESC]
    Example: new-session --name headphone_amp
    """
    raw_name: str = (getattr(args, "name", None) or "session").strip()
    description: str = getattr(args, "description", "") or ""

    sessions_base = get_sessions_base_dir()
    sessions_base.mkdir(parents=True, exist_ok=True)

    session_uuid = str(uuid.uuid4())
    slug = re.sub(r"[^a-z0-9]+", "_", raw_name.lower()).strip("_") or "session"
    short_id = session_uuid[:8]
    dir_name = f"{slug}_{short_id}"
    session_path = sessions_base / dir_name

    if session_path.exists():
        raise UserError(
            f"Session directory already exists: {session_path}",
            details={"hint": "UUID collision is extremely unlikely — re-run to get a new ID."},
        )

    session_path.mkdir(parents=True, exist_ok=False)

    session = SessionRef(
        name=raw_name,
        uuid=session_uuid,
        path=session_path,
        created=datetime.now().isoformat(),
        description=description,
    )

    # Write session metadata file inside the session directory.
    (session_path / "session.json").write_text(
        json.dumps(session.to_dict(), indent=2),
        encoding="utf-8",
    )

    set_current_session(session)

    return NewSessionResult(
        name=session.name,
        uuid=session.uuid,
        path=session.path,
        created=session.created,
        description=session.description,
    )


def cmd_session_info(args) -> SessionInfoResult:  # noqa: ARG001
    """Show information about the current session.

    Usage: session-info
    """
    session = get_current_session()
    if session is None:
        raise UserError(
            "No active session.  Run: new-session --name <name>",
            details={"hint": "Use 'new-session' to start a session first."},
        )

    # Count artefacts in the session dir.
    project_count = sum(
        1 for p in session.path.iterdir() if p.is_dir() and (p / f"{p.name}.kicad_pro").exists()
    )
    netlist_files = tuple(
        sorted(f.name for f in session.path.glob("*.json") if f.name != "session.json")
    )
    zip_files = tuple(sorted(f.name for f in session.path.glob("*.zip")))

    return SessionInfoResult(
        session=session,
        project_count=project_count,
        netlist_files=netlist_files,
        zip_files=zip_files,
    )


def cmd_close_session(args) -> dict:  # noqa: ARG001
    """Deactivate the current session without deleting it.

    Usage: close-session
    """
    session = get_current_session()
    if session is None:
        return {"status": "no_active_session", "message": "No active session to close."}

    clear_current_session()
    return {
        "status": "closed",
        "message": f"Session '{session.name}' ({session.short_id}) closed.",
        "path": str(session.path),
    }
