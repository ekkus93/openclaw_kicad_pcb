"""Fail-closed manifest and N2-baseline preflight for Phase N3 evaluation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn, cast

from kicad_pcb.electrical_equivalence import build_circuit_ir_fingerprint
from kicad_pcb.errors import UserError

from ._refinement_evaluation_corpus_inputs import (
    manifest_entries as _manifest_entries,
    prepare_entry as _prepare_entry,
    require_sha256 as _require_sha256,
    require_text as _require_text,
    resolve_repo_root as _resolve_repo_root,
    resolve_within as _resolve_within,
    sha256_file as _sha,
    source_root as _source_root,
)
from ._refinement_evaluation_corpus_models import (
    RefinementCorpusEvaluationRequest,
    RefinementCorpusPreparation,
    RefinementCorpusPreparedFixture,
)

_EXPECTED_SCHEMA_VERSION = "1.0"
_EXPECTED_MANIFEST_PHASE = "N1"
_EXPECTED_EXPECTATIONS_PHASE = "N2"
_DEFAULT_EXPECTATIONS_FILENAME = "baseline_expectations.json"


def prepare_refinement_evaluation_corpus(
    request: RefinementCorpusEvaluationRequest,
) -> RefinementCorpusPreparation:
    """Validate the complete selection before any model or KiCad evaluation runs."""

    repo_root = _resolve_repo_root(request.repo_root)
    manifest_path = _resolve_within(repo_root, request.manifest_path, field="manifest_path")
    payload = _load_manifest(manifest_path)
    source_root = _source_root(repo_root, payload)
    entries = _manifest_entries(payload)
    prepared = tuple(_prepare_entry(entry, source_root) for entry in entries)
    _require_unique_fixture_ids(prepared)
    expectations_path = _expectations_path(request, repo_root, manifest_path)
    expectations = _load_expectations(expectations_path)
    _require_baseline_bindings(prepared, expectations)
    selected = _select_fixtures(prepared, request.fixture_ids)
    return RefinementCorpusPreparation(
        fixtures=selected,
        manifest_hash=_sha(manifest_path),
        expectations_hash=_sha(expectations_path),
    )


def _load_manifest(path: Path) -> dict[str, object]:
    payload = _load_json_object(path, label="manifest")
    if payload.get("schema_version") != _EXPECTED_SCHEMA_VERSION:
        raise UserError(
            "Refinement evaluation corpus manifest schema version is unsupported.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    if payload.get("phase") != _EXPECTED_MANIFEST_PHASE:
        raise UserError(
            "Refinement evaluation corpus manifest phase is unsupported.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return payload


def _load_json_object(path: Path, *, label: str) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserError(
            f"Refinement evaluation corpus {label} could not be loaded.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        ) from exc
    if not isinstance(raw, dict):
        raise UserError(
            f"Refinement evaluation corpus {label} must be a JSON object.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    return cast(dict[str, object], raw)


def _expectations_path(
    request: RefinementCorpusEvaluationRequest,
    repo_root: Path,
    manifest_path: Path,
) -> Path:
    requested = request.expectations_path
    path = manifest_path.parent / _DEFAULT_EXPECTATIONS_FILENAME if requested is None else requested
    return _resolve_within(repo_root, path, field="expectations_path")


def _load_expectations(path: Path) -> dict[str, dict[str, object]]:
    payload = _load_json_object(path, label="baseline expectations")
    if payload.get("schema_version") != _EXPECTED_SCHEMA_VERSION:
        raise UserError(
            "Refinement evaluation baseline expectations schema version is unsupported.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    if payload.get("phase") != _EXPECTED_EXPECTATIONS_PHASE:
        raise UserError(
            "Refinement evaluation baseline expectations phase is unsupported.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    by_id: dict[str, dict[str, object]] = {}
    for entry in _manifest_entries(payload):
        fixture_id = _require_text(entry, "fixture_id")
        if fixture_id in by_id:
            raise UserError(
                "Refinement evaluation baseline expectations contain duplicate fixture IDs.",
                code="REFINEMENT_EVALUATION_CORPUS_INVALID",
            )
        by_id[fixture_id] = entry
    return by_id


def _require_baseline_bindings(
    prepared: Sequence[RefinementCorpusPreparedFixture],
    expectations: dict[str, dict[str, object]],
) -> None:
    expected_ids = {item.fixture.fixture_id for item in prepared}
    if set(expectations) != expected_ids:
        raise UserError(
            "Refinement evaluation baseline expectations do not exactly cover the corpus.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    for item in prepared:
        expectation = expectations[item.fixture.fixture_id]
        source_fixture_id = _require_text(expectation, "source_fixture_id")
        schematic_hash = _require_sha256(expectation, "source_schematic_sha256")
        fingerprint_hash = _require_sha256(expectation, "authoritative_fingerprint_sha256")
        if source_fixture_id != item.fixture.source_fixture_id:
            _raise_baseline_drift(item.fixture.fixture_id)
        if _sha(item.baseline_schematic) != schematic_hash:
            _raise_baseline_drift(item.fixture.fixture_id)
        actual_fingerprint = build_circuit_ir_fingerprint(item.authoritative_ir).sha256()
        if actual_fingerprint != fingerprint_hash:
            _raise_baseline_drift(item.fixture.fixture_id)


def _raise_baseline_drift(fixture_id: str) -> NoReturn:
    raise UserError(
        "Refinement evaluation fixture no longer matches its certified N2 baseline.",
        code="REFINEMENT_EVALUATION_BASELINE_DRIFT",
        details={"fixture_id": fixture_id},
    )


def _require_unique_fixture_ids(prepared: Sequence[RefinementCorpusPreparedFixture]) -> None:
    fixture_ids = [item.fixture.fixture_id for item in prepared]
    source_ids = [item.fixture.source_fixture_id for item in prepared]
    if len(set(fixture_ids)) != len(fixture_ids) or len(set(source_ids)) != len(source_ids):
        raise UserError(
            "Refinement evaluation corpus fixture identifiers must be unique.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )


def _select_fixtures(
    prepared: tuple[RefinementCorpusPreparedFixture, ...],
    fixture_ids: tuple[str, ...],
) -> tuple[RefinementCorpusPreparedFixture, ...]:
    if not fixture_ids:
        return prepared
    if len(set(fixture_ids)) != len(fixture_ids):
        raise UserError(
            "Refinement evaluation fixture selection contains duplicates.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
        )
    selected = set(fixture_ids)
    known = {item.fixture.fixture_id for item in prepared}
    unknown = sorted(selected - known)
    if unknown:
        raise UserError(
            "Refinement evaluation fixture selection contains unknown identifiers.",
            code="REFINEMENT_EVALUATION_CORPUS_INVALID",
            details={"fixture_ids": unknown},
        )
    return tuple(item for item in prepared if item.fixture.fixture_id in selected)
