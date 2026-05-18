"""Persistent SQLite cache for KiCad symbol library metadata.

Stores ``(lib_file, mtime, sym_name, description, pin_count)`` rows.
Cache entries are invalidated per-file when the library's ``mtime`` changes,
so a partial update (one lib file reparsed) is always consistent.

The default DB location is ``~/.openclaw/kicad-pcb/symbol_index.db``.
Override with the ``KICAD_PCB_CACHE_DIR`` environment variable.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_CACHE_DIR = Path.home() / ".openclaw" / "kicad-pcb"
_DB_FILENAME = "symbol_index.db"

# Increment this whenever the cached data semantics change (e.g. a new
# derivation strategy) so existing DBs are transparently invalidated on
# first open rather than silently serving stale pin counts.
CACHE_VERSION = 2

# WAL mode + NORMAL synchronous gives good write throughput while
# remaining crash-safe (no full fsync on every commit).
_PRAGMAS = "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS symbol_cache (
    lib_file    TEXT    NOT NULL,
    lib_mtime   REAL    NOT NULL,
    sym_name    TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    pin_count   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (lib_file, sym_name)
);
CREATE INDEX IF NOT EXISTS idx_sym_cache_file
    ON symbol_cache (lib_file);
-- Sentinel table: records which files have been fully indexed (even empty ones).
CREATE TABLE IF NOT EXISTS indexed_files (
    lib_file  TEXT PRIMARY KEY NOT NULL,
    lib_mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY NOT NULL,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class CachedSymbol:
    """Lightweight symbol record stored in and returned from the cache."""

    lib_file: Path
    sym_name: str
    description: str
    pin_count: int


def _cache_db_path() -> Path:
    env = os.environ.get("KICAD_PCB_CACHE_DIR")
    base = Path(env) if env else _DEFAULT_CACHE_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base / _DB_FILENAME


class SymbolCache:
    """SQLite-backed cache for ``.kicad_sym`` metadata.

    The cache is intentionally lazy: it opens the database on first use and
    never blocks startup.  Callers follow the pattern::

        symbols = cache.get_symbols(lib_file)
        if symbols is None:                         # cold or stale
            symbols = _parse_file(lib_file)
            cache.store_symbols(lib_file, symbols)
        # symbols is now a list[CachedSymbol]
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db: Path = db_path or _cache_db_path()
        self._connection: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_conn(self) -> sqlite3.Connection:
        if self._connection is None:
            self._connection = sqlite3.connect(str(self._db))
            self._connection.executescript(_PRAGMAS + _SCHEMA)
            # Evict stale entries when the cache semantics have changed.
            conn = self._connection
            row = conn.execute("SELECT value FROM meta WHERE key='version'").fetchone()
            if row is None or int(row[0]) != CACHE_VERSION:
                conn.execute("DELETE FROM symbol_cache")
                conn.execute("DELETE FROM indexed_files")
                conn.execute(
                    "INSERT OR REPLACE INTO meta (key, value) VALUES ('version', ?)",
                    (str(CACHE_VERSION),),
                )
                conn.commit()
        return self._connection

    def close(self) -> None:
        """Close the underlying DB connection, if open."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_symbols(self, lib_file: Path) -> list[CachedSymbol] | None:
        """Return cached symbols for *lib_file* if the cache entry is fresh.

        Returns ``None`` when the file is not yet indexed or when the file's
        ``mtime`` differs from the stored value (i.e. the file has changed).
        """
        try:
            mtime = lib_file.stat().st_mtime
        except OSError:
            return None

        conn = self._get_conn()

        # Check the sentinel first — this correctly handles empty libs.
        sentinel = conn.execute(
            "SELECT lib_mtime FROM indexed_files WHERE lib_file = ?",
            (str(lib_file),),
        ).fetchone()

        if sentinel is None:
            return None  # never indexed

        if sentinel[0] != mtime:
            # File changed — evict and signal a miss.
            conn.execute("DELETE FROM symbol_cache WHERE lib_file = ?", (str(lib_file),))
            conn.execute("DELETE FROM indexed_files WHERE lib_file = ?", (str(lib_file),))
            conn.commit()
            return None

        rows = conn.execute(
            "SELECT sym_name, description, pin_count FROM symbol_cache WHERE lib_file = ?",
            (str(lib_file),),
        ).fetchall()
        return [
            CachedSymbol(
                lib_file=lib_file,
                sym_name=row[0],
                description=row[1],
                pin_count=row[2],
            )
            for row in rows
        ]

    def store_symbols(self, lib_file: Path, symbols: list[CachedSymbol]) -> None:
        """Insert (or replace) cached entries for *lib_file*.

        Existing rows for the file are deleted first so the result is always
        consistent with the current file state.
        """
        try:
            mtime = lib_file.stat().st_mtime
        except OSError:
            return

        conn = self._get_conn()
        conn.execute("DELETE FROM symbol_cache WHERE lib_file = ?", (str(lib_file),))
        conn.executemany(
            "INSERT INTO symbol_cache"
            " (lib_file, lib_mtime, sym_name, description, pin_count)"
            " VALUES (?, ?, ?, ?, ?)",
            [(str(lib_file), mtime, s.sym_name, s.description, s.pin_count) for s in symbols],
        )
        # Always update the sentinel so empty files also register as "indexed".
        conn.execute(
            "INSERT OR REPLACE INTO indexed_files (lib_file, lib_mtime) VALUES (?, ?)",
            (str(lib_file), mtime),
        )
        conn.commit()

    def evict(self, lib_file: Path) -> None:
        """Remove all cache entries for *lib_file*."""
        conn = self._get_conn()
        conn.execute("DELETE FROM symbol_cache WHERE lib_file = ?", (str(lib_file),))
        conn.execute("DELETE FROM indexed_files WHERE lib_file = ?", (str(lib_file),))
        conn.commit()

    def stats(self) -> dict[str, int]:
        """Return counts useful for diagnostics and tests."""
        conn = self._get_conn()
        n_files: int = conn.execute("SELECT COUNT(*) FROM indexed_files").fetchone()[0]
        n_symbols: int = conn.execute("SELECT COUNT(*) FROM symbol_cache").fetchone()[0]
        return {"indexed_files": n_files, "indexed_symbols": n_symbols}
