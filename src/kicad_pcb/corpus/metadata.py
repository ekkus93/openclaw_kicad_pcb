"""Typed metadata and fixture-id helpers for model corpus fixtures."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .reports import write_json_report

CorpusFixtureStatus = Literal["ready", "layout_only", "pending_netlist_export", "rejected"]


class CorpusFixtureMetadata(BaseModel):
    """Persistent metadata for one ingested model-corpus fixture."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    fixture_id: str = Field(min_length=1)
    source_file_name: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    status: CorpusFixtureStatus
    status_reasons: list[str] = Field(default_factory=list)
    created_by: str = Field(default="model-corpus ingest")
    kicad_schematic_version: str = ""
    generator: str = ""
    title: str = ""
    license: str = "unknown"
    source_url: str | None = None
    symbol_count: int = 0
    wire_count: int = 0
    label_count: int = 0
    global_label_count: int = 0
    power_symbol_count: int = 0
    has_embedded_symbols: bool = False
    has_circuit_ir: bool = False
    requires_custom_symbols: bool = False
    notes: list[str] = Field(default_factory=list)
    detected_license_hint: str | None = None
    detected_source_url_hint: str | None = None
    metadata_review_required: bool = True


def write_fixture_metadata(metadata: CorpusFixtureMetadata, path: Path) -> None:
    """Write *metadata* as stable JSON."""

    write_json_report(path, metadata.model_dump(mode="json"))


def merge_preserved_metadata(
    *,
    existing: CorpusFixtureMetadata | None,
    generated: CorpusFixtureMetadata,
) -> CorpusFixtureMetadata:
    """Preserve hand-edited metadata fields from an existing metadata file."""

    if existing is None:
        return generated
    return generated.model_copy(
        update={
            "license": existing.license,
            "source_url": existing.source_url,
            "notes": list(existing.notes),
            "detected_license_hint": existing.detected_license_hint,
            "detected_source_url_hint": existing.detected_source_url_hint,
            "metadata_review_required": existing.metadata_review_required,
        }
    )


def make_fixture_id(
    path: Path,
    *,
    relative_to: Path | None = None,
    colliding_paths: set[Path] | None = None,
) -> str:
    """Return a deterministic fixture id for *path*.

    A unique normalized stem uses the base slug directly. Any member of a
    collision group uses ``base-slug--<hash8>`` where the hash input is the
    normalized relative source path.
    """

    base_slug = _normalize_slug(path.stem)
    if not colliding_paths or path not in colliding_paths:
        return base_slug
    normalized_relative_path = _normalized_relative_source_path(path, relative_to=relative_to)
    hash8 = hashlib.sha256(normalized_relative_path.encode("utf-8")).hexdigest()[:8]
    return f"{base_slug}--{hash8}"


def detect_fixture_id_collisions(
    paths: list[Path],
    *,
    relative_to: Path | None = None,
) -> dict[str, set[Path]]:
    """Return base-slug collision groups for *paths*."""

    collisions: dict[str, set[Path]] = {}
    for path in sorted(paths):
        collisions.setdefault(_normalize_slug(path.stem), set()).add(path)
    return {slug: members for slug, members in collisions.items() if len(members) > 1}


def _normalize_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^0-9a-zA-Z]+", "-", normalized.lower()).strip("-")
    return slug or "schematic"


def _normalized_relative_source_path(path: Path, *, relative_to: Path | None = None) -> str:
    relative_path = path.relative_to(relative_to) if relative_to is not None else Path(path.name)
    normalized_parts = [
        unicodedata.normalize("NFKD", part).encode("ascii", "ignore").decode("ascii").lower()
        for part in relative_path.parts
    ]
    return "/".join(normalized_parts)
