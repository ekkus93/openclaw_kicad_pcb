"""Layout-result cache for the Graphviz schematic layout engine.

Provides SHA-256–keyed JSON persistence for final layout metadata produced by
:class:`~kicad_pcb.graphviz_layout.GraphvizLayoutEngine`.

Cache format
------------
A single JSON file with the structure::

    {
        "version": 2,
        "key": "<sha256-hex-of-dot-source>",
        "positions": {
            "R1": [30.48, 50.80, 0.0],
            "C1": [60.96, 50.80, 90.0],
            "U1": [91.44, 50.80, null]
        },
        "decoupling_map": {
            "C1": "U1"
        }
    }

Invalidation policy
-------------------
The cache key is the SHA-256 digest of the DOT source string passed to
``dot -Tplain`` plus a layout-algorithm revision salt. Any change in circuit
topology (added/removed component, renamed net) changes the DOT source and
therefore produces a different key. Layout-pipeline changes that affect the
final snapped coordinates but not the DOT source must bump the algorithm
revision so persisted caches are invalidated as well. The ``version`` field
is bumped whenever the JSON schema changes so that old cache files are
automatically discarded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Bump this when the cache JSON schema changes to invalidate all persisted caches.
_CACHE_FORMAT_VERSION = 2
_LAYOUT_ALGORITHM_REVISION = "graphviz-layout-v12"


@dataclass(frozen=True)
class _LayoutCacheEntry:
    """Materialized layout cache payload stored on disk."""

    positions: dict[str, tuple[float, float, float | None]]
    decoupling_map: dict[str, str]


def _layout_cache_key(dot_source: str) -> str:
    """Return a stable SHA-256 hex digest for *dot_source*.

    The digest is used as the cache lookup key. It incorporates both the DOT
    source and the current layout-algorithm revision so post-layout snap
    changes also invalidate persisted caches.
    """
    payload = f"{_LAYOUT_ALGORITHM_REVISION}\0{dot_source}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _load_layout_cache_entry(
    cache_path: Path,
    cache_key: str,
) -> _LayoutCacheEntry | None:
    """Return cached layout metadata if *cache_path* exists and *cache_key* matches.

    Returns ``None`` only for a normal cache miss (missing file, key mismatch,
    version mismatch). Any cache read/parse/shape error raises
    :class:`RuntimeError` so failures are explicit and never silently ignored.
    """
    if not cache_path.exists():
        return None

    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"Failed to read layout cache '{cache_path}': {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON in layout cache '{cache_path}': {exc}") from exc

    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid layout cache format in '{cache_path}': root must be an object")

    if data.get("version") != _CACHE_FORMAT_VERSION or data.get("key") != cache_key:
        return None

    raw: Any = data.get("positions")
    if not isinstance(raw, dict):
        raise RuntimeError(
            f"Invalid layout cache format in '{cache_path}': 'positions' must be an object"
        )

    loaded_positions: dict[str, tuple[float, float, float | None]] = {}
    for ref, value in raw.items():
        if not isinstance(ref, str):
            raise RuntimeError(
                f"Invalid layout cache entry in '{cache_path}': non-string ref key {ref!r}"
            )
        if not isinstance(value, list) or len(value) < 2:
            raise RuntimeError(
                f"Invalid layout cache entry in '{cache_path}' for {ref!r}: expected [x, y, rot?]"
            )
        try:
            x = float(value[0])
            y = float(value[1])
            rot = None if len(value) < 3 or value[2] is None else float(value[2])
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Invalid numeric values in layout cache '{cache_path}' for {ref!r}: {value!r}"
            ) from exc
        loaded_positions[ref] = (x, y, rot)

    raw_decoupling_map: Any = data.get("decoupling_map")
    if not isinstance(raw_decoupling_map, dict):
        raise RuntimeError(
            f"Invalid layout cache format in '{cache_path}': 'decoupling_map' must be an object"
        )

    loaded_decoupling_map: dict[str, str] = {}
    for cap_ref, anchor_ref in raw_decoupling_map.items():
        if not isinstance(cap_ref, str) or not isinstance(anchor_ref, str):
            raise RuntimeError(
                f"Invalid decoupling_map entry in '{cache_path}': expected string-to-string mapping"
            )
        loaded_decoupling_map[cap_ref] = anchor_ref

    return _LayoutCacheEntry(
        positions=loaded_positions,
        decoupling_map=loaded_decoupling_map,
    )


def _load_layout_cache(
    cache_path: Path,
    cache_key: str,
) -> dict[str, tuple[float, float, float | None]] | None:
    """Return cached symbol positions if *cache_path* exists and *cache_key* matches."""
    entry = _load_layout_cache_entry(cache_path, cache_key)
    if entry is None:
        return None
    return entry.positions


def _save_layout_cache(
    cache_path: Path,
    cache_key: str,
    positions: Mapping[str, tuple[float, float, float | None]],
    decoupling_map: Mapping[str, str] | None = None,
) -> None:
    """Persist *positions* to *cache_path*.

    The cache file is a JSON document (see module docstring for the schema).
    Any write failure raises :class:`RuntimeError` so the caller can fail fast.
    """
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": _CACHE_FORMAT_VERSION,
            "key": cache_key,
            "positions": {ref: [x, y, rot] for ref, (x, y, rot) in positions.items()},
            "decoupling_map": dict(sorted((decoupling_map or {}).items())),
        }
        cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Failed to write layout cache '{cache_path}': {exc}") from exc
