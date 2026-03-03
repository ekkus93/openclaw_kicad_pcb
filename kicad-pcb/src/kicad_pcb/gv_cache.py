"""Layout-result cache for the Graphviz schematic layout engine.

Provides SHA-256–keyed JSON persistence for ``{ref: (x, y, rotation)}``
position maps produced by :class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.

Cache format
------------
A single JSON file with the structure::

    {
        "version": 1,
        "key": "<sha256-hex-of-dot-source>",
        "positions": {
            "R1": [30.48, 50.80, 0.0],
            "C1": [60.96, 50.80, 90.0],
            "U1": [91.44, 50.80, null]
        }
    }

Invalidation policy
-------------------
The cache key is the SHA-256 digest of the DOT source string passed to
``dot -Tplain``.  Any change in circuit topology (added/removed component,
renamed net) changes the DOT source and therefore produces a different key,
guaranteeing a cache miss.  The ``version`` field is bumped whenever the
JSON schema changes so that old cache files are automatically discarded.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Bump this when the cache JSON schema changes to invalidate all persisted caches.
_CACHE_FORMAT_VERSION = 1


def _layout_cache_key(dot_source: str) -> str:
    """Return a stable SHA-256 hex digest for *dot_source*.

    The digest is used as the cache lookup key — any change in circuit topology
    (new component, different net) produces a different key and therefore a
    guaranteed cache miss.
    """
    return hashlib.sha256(dot_source.encode()).hexdigest()


def _load_layout_cache(
    cache_path: Path,
    cache_key: str,
) -> dict[str, tuple[float, float, float | None]] | None:
    """Return cached symbol positions if *cache_path* exists and *cache_key* matches.

    Returns ``None`` on any error (missing file, JSON parse failure, key or
    version mismatch) so the caller always falls through to a fresh layout
    computation.
    """
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        if data.get("version") != _CACHE_FORMAT_VERSION or data.get("key") != cache_key:
            return None
        raw: dict[str, Any] = data.get("positions", {})
        return {
            ref: (float(v[0]), float(v[1]), None)
            for ref, v in raw.items()
            if isinstance(v, list) and len(v) >= 2
        }
    except Exception:  # noqa: BLE001
        return None


def _save_layout_cache(
    cache_path: Path,
    cache_key: str,
    positions: dict[str, tuple[float, float, float | None]],
) -> None:
    """Persist *positions* to *cache_path*.  Silently ignores all write errors.

    The cache file is a JSON document (see module docstring for the schema).
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_FORMAT_VERSION,
            "key": cache_key,
            "positions": {ref: [x, y, rot] for ref, (x, y, rot) in positions.items()},
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception:  # noqa: BLE001
        _log.debug("Failed to write layout cache to %s", cache_path)
