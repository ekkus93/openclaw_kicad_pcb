"""Deterministic JSON and Markdown writers for corpus artifacts."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _json_default(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    msg = f"Object of type {type(value).__name__} is not JSON serializable"
    raise TypeError(msg)


def write_json_report(path: Path, payload: Any) -> None:
    """Write *payload* as stable JSON with indentation and sorted keys."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=_json_default)
        + "\n",
        encoding="utf-8",
    )


def write_markdown_report(path: Path, content: str) -> None:
    """Write *content* as UTF-8 markdown text, preserving final newline."""

    text = content if content.endswith("\n") else content + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
