"""Trusted path and fixture-input helpers for Phase N3 corpus evaluation."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import cast

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.evaluation.refinement_baseline import fixture_definition

from ._refinement_evaluation_corpus_models import RefinementCorpusPreparedFixture


def source_root(repo_root: Path, payload: dict[str, object]) -> Path:
    value = payload.get("source_corpus")
    if not isinstance(value, str) or not value.strip():
        raise UserError(
            "Refinement evaluation corpus source path is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    path = Path(value)
    if path.is_absolute():
        raise UserError(
            "Refinement evaluation corpus source path must be repository-relative.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    resolved = resolve_within(repo_root, path, field="source_corpus")
    if not resolved.is_dir():
        raise UserError(
            "Refinement evaluation corpus source directory is missing.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return resolved


def manifest_entries(payload: dict[str, object]) -> tuple[dict[str, object], ...]:
    value = payload.get("fixtures")
    if not isinstance(value, list) or not value:
        raise UserError(
            "Refinement evaluation corpus fixture list is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    entries: list[dict[str, object]] = []
    for item in value:
        if not isinstance(item, dict):
            raise UserError(
                "Refinement evaluation corpus fixture entry is invalid.",
                code="REFINEMENT_EVALUATION_CORPUS_INVALID",
            )
        entries.append(cast(dict[str, object], item))
    return tuple(entries)


def prepare_entry(
    entry: dict[str, object],
    source_root_path: Path,
) -> RefinementCorpusPreparedFixture:
    fixture_id = require_text(entry, "fixture_id")
    source_fixture_id = require_text(entry, "source_fixture_id")
    fixture = fixture_definition(
        fixture_id=fixture_id,
        source_fixture_id=source_fixture_id,
        categories=require_string_list(entry, "categories"),
        known_visual_defects=require_string_list(entry, "known_visual_defects"),
    )
    source_dir = resolve_within(
        source_root_path,
        Path(source_fixture_id),
        field="source_fixture_id",
    )
    schematic = source_dir / "source_normalized.kicad_sch"
    circuit_ir = source_dir / "circuit_ir.json"
    if not schematic.is_file() or not circuit_ir.is_file():
        raise UserError(
            "Refinement evaluation fixture source artifacts are missing.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
            details={"fixture_id": fixture_id},
        )
    try:
        authoritative_ir = CircuitIR.load(circuit_ir)
    except Exception as exc:
        raise UserError(
            "Refinement evaluation fixture Circuit IR is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
            details={"fixture_id": fixture_id},
        ) from exc
    return RefinementCorpusPreparedFixture(
        fixture=fixture,
        authoritative_ir=authoritative_ir,
        baseline_schematic=schematic,
    )


def resolve_repo_root(path: Path) -> Path:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise UserError(
            "Refinement evaluation corpus repository root is missing or invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        ) from exc
    if not resolved.is_dir():
        raise UserError(
            "Refinement evaluation corpus repository root is not a directory.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return resolved


def resolve_within(root: Path, path: Path, *, field: str) -> Path:
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise UserError(
            f"Refinement evaluation corpus {field} escapes or is missing from the trusted root.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        ) from exc
    return resolved


def require_text(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UserError(
            f"Refinement evaluation corpus {key} is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return value


def require_string_list(entry: dict[str, object], key: str) -> tuple[str, ...]:
    value = entry.get(key)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise UserError(
            f"Refinement evaluation corpus {key} is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return tuple(cast(list[str], value))


def require_sha256(entry: dict[str, object], key: str) -> str:
    value = require_text(entry, key)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise UserError(
            f"Refinement evaluation corpus {key} is invalid.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return value


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
