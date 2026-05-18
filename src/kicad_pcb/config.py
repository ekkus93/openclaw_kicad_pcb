"""Project configuration constants and R/W helpers.

Symbol library discovery (:func:`discover_symbols_dir`) uses the following
priority chain so that users can override the default system path at any level:

1. Explicit ``Path`` argument (passed by callers, e.g. from ``--symbols-dir``).
2. ``KICAD_SYMBOLS_DIR`` environment variable.
3. ``symbols_dir`` key in ``~/.kicad-pcb/config.json``.
4. Well-known platform-specific paths (:data:`SYMBOLS_CANDIDATES`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .errors import ErrorCode, UserError
from .models import ProjectRef, SessionRef

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_DIR = Path.home() / ".kicad-pcb"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_DIR = Path.home() / "kicad-projects"
CURRENT_PROJECT_FILE = CONFIG_DIR / "current_project.json"
CURRENT_SESSION_FILE = CONFIG_DIR / "current_session.json"

#: Sub-directory within projects_dir where session directories are created.
SESSIONS_SUBDIR = "sessions"

DEFAULT_PCB_OPTIONS: dict = {
    "layers": 2,
    "thickness": 1.6,
    "color": "green",
    "surface_finish": "hasl",
    "copper_weight": "1oz",
    "min_hole": 0.3,
    "min_trace": 0.15,
}

# ---------------------------------------------------------------------------
# Symbol library discovery
# ---------------------------------------------------------------------------

#: Platform-specific candidate paths for the KiCad symbol library directory,
#: tried in order when no explicit path or env-var override is provided.
SYMBOLS_CANDIDATES: tuple[Path, ...] = (
    Path("/usr/share/kicad/symbols"),  # Linux system package
    Path("/usr/local/share/kicad/symbols"),  # Linux local install
    Path.home() / ".local/share/kicad/symbols",  # Linux user install
    # Flatpak — check newest major version first
    Path.home() / ".var/app/org.kicad.KiCad/data/kicad/9.0/symbols",
    Path.home() / ".var/app/org.kicad.KiCad/data/kicad/8.0/symbols",
    Path.home() / ".var/app/org.kicad.KiCad/data/kicad/7.0/symbols",
    # macOS
    Path("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"),
)


@dataclass(frozen=True)
class SymbolsDir:
    """Result of :func:`discover_symbols_dir` — a located path and its origin.

    *source* values:

    * ``"explicit"`` — passed directly by the caller (e.g. ``--symbols-dir``)
    * ``"env:KICAD_SYMBOLS_DIR"`` — from the ``KICAD_SYMBOLS_DIR`` env var
    * ``"config"`` — from the ``symbols_dir`` key in config.json
    * ``"platform:<absolute-path>"`` — a well-known platform path that exists
    """

    path: Path
    source: str

    def __str__(self) -> str:
        return f"{self.path}  (via {self.source})"


def discover_symbols_dir(
    *,
    explicit: Path | None = None,
    strict: bool = False,
) -> SymbolsDir | None:
    """Return the first valid symbol library directory and its discovery source.

    Search order:

    1. *explicit* — caller-supplied path (e.g. from the ``--symbols-dir`` CLI
       flag).  The directory must exist; if it doesn't, the search continues
       unless *strict* is ``True``.
    2. ``KICAD_SYMBOLS_DIR`` environment variable.
    3. ``symbols_dir`` key in ``~/.kicad-pcb/config.json``.
    4. :data:`SYMBOLS_CANDIDATES` — platform-specific well-known paths.

    Returns ``None`` if no existing directory can be found anywhere.
    In strict mode, raises :class:`UserError` when an explicitly configured
    source is invalid, or when nothing resolves.
    """
    if explicit is not None:
        if explicit.is_dir():
            return SymbolsDir(explicit, "explicit")
        if strict:
            raise UserError(
                f"Explicit symbols_dir does not exist: {explicit}",
                code=ErrorCode.SYMBOL_DIR_MISSING,
                details={"source": "explicit", "path": str(explicit)},
            )

    env_val = os.environ.get("KICAD_SYMBOLS_DIR")
    if env_val:
        p = Path(env_val)
        if p.is_dir():
            return SymbolsDir(p, "env:KICAD_SYMBOLS_DIR")
        if strict:
            raise UserError(
                f"KICAD_SYMBOLS_DIR does not exist: {p}",
                code=ErrorCode.SYMBOL_DIR_MISSING,
                details={"source": "env:KICAD_SYMBOLS_DIR", "path": str(p)},
            )

    cfg = load_config()
    cfg_val = cfg.get("symbols_dir")
    if cfg_val:
        p = Path(cfg_val)
        if p.is_dir():
            return SymbolsDir(p, "config")
        if strict:
            raise UserError(
                f"Configured symbols_dir does not exist: {p}",
                code=ErrorCode.SYMBOL_DIR_MISSING,
                details={"source": "config", "path": str(p)},
            )

    for candidate in SYMBOLS_CANDIDATES:
        if candidate.is_dir():
            return SymbolsDir(candidate, f"platform:{candidate}")

    if strict:
        raise UserError(
            "No KiCad symbol libraries found.",
            code=ErrorCode.SYMBOL_DIR_MISSING,
            details={
                "source": "platform",
                "searched_candidates": [str(p) for p in SYMBOLS_CANDIDATES],
            },
        )

    return None


def get_symbols_dir_config() -> str | None:
    """Return the ``symbols_dir`` value from config.json, or ``None``."""
    return load_config().get("symbols_dir")  # type: ignore[return-value]


def set_symbols_dir_config(path: Path | str | None) -> None:
    """Persist (or clear) the ``symbols_dir`` entry in config.json.

    Pass ``None`` to remove the override so that normal discovery applies.
    """
    cfg = load_config()
    if path is None:
        cfg.pop("symbols_dir", None)
    else:
        cfg["symbols_dir"] = str(path)
    save_config(cfg)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ensure_dirs() -> None:
    """Create necessary directories."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Load configuration; return defaults if file is absent or corrupt."""
    ensure_dirs()
    if CONFIG_FILE.exists():
        try:
            with CONFIG_FILE.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {"projects_dir": str(PROJECTS_DIR)}


def save_config(config: dict) -> None:
    """Persist configuration to disk."""
    ensure_dirs()
    with CONFIG_FILE.open("w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)


def get_current_project() -> ProjectRef | None:
    """Return the currently-selected project as a :class:`.ProjectRef`, or None."""
    if CURRENT_PROJECT_FILE.exists():
        try:
            with CURRENT_PROJECT_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            return ProjectRef.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
            raise UserError(
                f"Failed to load current project state from '{CURRENT_PROJECT_FILE}': {exc}",
                code=ErrorCode.IO_ERROR,
                details={"path": str(CURRENT_PROJECT_FILE)},
            ) from exc
    return None


def set_current_project(project: ProjectRef) -> None:
    """Persist *project* to ``current_project.json``."""
    ensure_dirs()
    with CURRENT_PROJECT_FILE.open("w", encoding="utf-8") as f:
        json.dump(project.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Session config
# ---------------------------------------------------------------------------


def get_current_session() -> SessionRef | None:
    """Return the active :class:`.SessionRef`, or ``None`` if no session is open.

    If the persisted session directory no longer exists on disk (e.g. it was
    manually deleted), the stale marker is silently removed and ``None`` is
    returned so the bot does not accidentally create files in a missing path.
    """
    if CURRENT_SESSION_FILE.exists():
        try:
            with CURRENT_SESSION_FILE.open(encoding="utf-8") as f:
                data = json.load(f)
            ref = SessionRef.from_dict(data)
        except (json.JSONDecodeError, OSError, KeyError, TypeError, ValueError) as exc:
            raise UserError(
                f"Failed to load current session state from '{CURRENT_SESSION_FILE}': {exc}",
                code=ErrorCode.IO_ERROR,
                details={"path": str(CURRENT_SESSION_FILE)},
            ) from exc
        if not ref.path.exists():
            # Session directory was deleted; clean up the stale marker.
            try:
                CURRENT_SESSION_FILE.unlink()
            except OSError as exc:
                raise UserError(
                    f"Failed to clear stale session marker '{CURRENT_SESSION_FILE}': {exc}",
                    code=ErrorCode.IO_ERROR,
                    details={"path": str(CURRENT_SESSION_FILE)},
                ) from exc
            return None
        return ref
    return None


def set_current_session(session: SessionRef) -> None:
    """Persist *session* to ``current_session.json``."""
    ensure_dirs()
    with CURRENT_SESSION_FILE.open("w", encoding="utf-8") as f:
        json.dump(session.to_dict(), f, indent=2)


def clear_current_session() -> None:
    """Remove the active session marker (does not delete the session directory)."""
    if CURRENT_SESSION_FILE.exists():
        CURRENT_SESSION_FILE.unlink()


def get_sessions_base_dir() -> Path:
    """Return the base directory for session folders (``{projects_dir}/sessions/``)."""
    cfg = load_config()
    base = Path(cfg.get("projects_dir", PROJECTS_DIR))
    return base / SESSIONS_SUBDIR
