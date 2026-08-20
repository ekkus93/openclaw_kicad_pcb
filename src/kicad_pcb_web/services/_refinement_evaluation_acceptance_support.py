"""Shared helpers for Phase N3 evidence acceptance validation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def fixture_records(path: Path, label: str) -> tuple[dict[str, object], ...]:
    payload = read_json(path, label)
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list):
        raise ValueError(f"{label} does not contain a fixture list")
    records: list[dict[str, object]] = []
    fixture_ids: list[str] = []
    for item in fixtures:
        fixture = require_mapping(item, f"{label} fixture")
        fixture_id = fixture.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError(f"{label} contains an invalid fixture ID")
        fixture_ids.append(fixture_id)
        records.append(fixture)
    if len(set(fixture_ids)) != len(fixture_ids):
        raise ValueError(f"{label} contains duplicate fixture IDs")
    return tuple(records)


def read_json(path: Path, label: str) -> dict[str, object]:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid {label}: {path}") from exc
    return require_mapping(payload, label)


def require_mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def require_file(bundle: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"{label} does not contain a relative file path")
    relative_path = Path(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ValueError(f"{label} contains an unsafe path: {relative}")
    path = (bundle / relative_path).resolve()
    if bundle != path and bundle not in path.parents:
        raise ValueError(f"{label} escapes its fixture bundle: {relative}")
    if not path.is_file():
        raise ValueError(f"Missing {label}: {relative}")
    return path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_equal(actual: object, expected: object, message: str) -> None:
    if actual != expected:
        raise ValueError(message)
