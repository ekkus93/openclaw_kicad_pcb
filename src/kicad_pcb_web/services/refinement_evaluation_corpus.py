"""Corpus-level Phase N3 orchestration for schematic refinement evaluation."""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from kicad_pcb.corpus.reports import write_json_report
from kicad_pcb.errors import UserError

from ._refinement_evaluation_corpus_models import (
    REFINEMENT_CORPUS_EVALUATION_SCHEMA_VERSION,
    SUMMARY_FILENAME,
    RefinementCorpusEvaluationRequest,
    RefinementCorpusEvaluationResult,
    RefinementCorpusFixtureResult,
    RefinementCorpusPreparation,
    RefinementCorpusPreparedFixture,
)
from ._refinement_evaluation_corpus_preflight import prepare_refinement_evaluation_corpus
from .refinement_evaluation import (
    RefinementEvaluationFixture,
    RefinementEvaluationRequest,
    run_refinement_evaluation,
)

__all__ = [
    "REFINEMENT_CORPUS_EVALUATION_SCHEMA_VERSION",
    "RefinementCorpusEvaluationRequest",
    "RefinementCorpusEvaluationResult",
    "RefinementCorpusFixtureResult",
    "RefinementCorpusPreparation",
    "RefinementCorpusPreparedFixture",
    "prepare_refinement_evaluation_corpus",
    "run_refinement_evaluation_corpus",
]

_SAFE_CAUSE_DETAIL_KEYS = frozenset(
    {
        "provider",
        "status_code",
        "endpoint",
        "retryable",
        "error_type",
        "elapsed_ms",
        "ambiguous_delivery",
        "automatic_retry",
    }
)
_MAX_PROVIDER_ERROR_CHARS = 500


def run_refinement_evaluation_corpus(
    request: RefinementCorpusEvaluationRequest,
    *,
    output_root: Path,
    work_root: Path,
) -> RefinementCorpusEvaluationResult:
    """Preflight and evaluate selected fixtures, then atomically publish a summary."""

    preparation = prepare_refinement_evaluation_corpus(request)
    prepared = preparation.fixtures
    output_root.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)
    _preflight_output(output_root, prepared)

    results: list[RefinementCorpusFixtureResult] = []
    for item in prepared:
        try:
            result = _execute_fixture(
                item,
                request=request,
                output_root=output_root,
                work_root=work_root,
            )
        except Exception as exc:
            raise UserError(
                "Refinement evaluation corpus fixture failed.",
                code="REFINEMENT_EVALUATION_FIXTURE_FAILED",
                details={
                    "fixture_id": item.fixture.fixture_id,
                    **_exception_diagnostics(exc),
                },
            ) from exc
        results.append(result)

    summary_path = _publish_summary(
        output_root,
        request=request,
        preparation=preparation,
        results=results,
    )
    return RefinementCorpusEvaluationResult(
        output_root=output_root,
        summary_path=summary_path,
        fixture_ids=tuple(item.fixture.fixture_id for item in prepared),
        results=tuple(results),
    )


def _preflight_output(
    output_root: Path,
    prepared: Sequence[RefinementCorpusPreparedFixture],
) -> None:
    collisions = [
        item.fixture.fixture_id
        for item in prepared
        if (output_root / item.fixture.fixture_id).exists()
    ]
    if collisions or (output_root / SUMMARY_FILENAME).exists():
        raise UserError(
            "Refinement evaluation corpus output already exists.",
            code="REFINEMENT_EVALUATION_CORPUS_EXISTS",
            details={"fixture_ids": sorted(collisions)},
        )


def _execute_fixture(
    prepared: RefinementCorpusPreparedFixture,
    *,
    request: RefinementCorpusEvaluationRequest,
    output_root: Path,
    work_root: Path,
) -> RefinementCorpusFixtureResult:
    result = run_refinement_evaluation(
        RefinementEvaluationRequest(
            fixture=RefinementEvaluationFixture(
                fixture_id=prepared.fixture.fixture_id,
                source_fixture_id=prepared.fixture.source_fixture_id,
                categories=prepared.fixture.categories,
                known_visual_defects=prepared.fixture.known_visual_defects,
            ),
            authoritative_ir=prepared.authoritative_ir,
            baseline_schematic=prepared.baseline_schematic,
            adapter=request.adapter,
            llm_client=request.llm_client,
            provenance=request.provenance,
            iteration_limits=request.iteration_limits,
            loop_limits=request.loop_limits,
        ),
        output_root=output_root,
        work_root=work_root,
    )
    return RefinementCorpusFixtureResult(
        fixture_id=result.fixture_id,
        baseline_hash=result.baseline_hash,
        apply_once_status=result.apply_once_status,
        apply_once_code=result.apply_once_code,
        refine_status=result.refine_status,
        refine_stop_reason=result.refine_stop_reason,
        final_accepted_hash=result.final_accepted_hash,
    )


def _publish_summary(
    output_root: Path,
    *,
    request: RefinementCorpusEvaluationRequest,
    preparation: RefinementCorpusPreparation,
    results: Sequence[RefinementCorpusFixtureResult],
) -> Path:
    summary = output_root / SUMMARY_FILENAME
    if summary.exists():
        raise UserError(
            "Refinement evaluation corpus summary already exists.",
            code="REFINEMENT_EVALUATION_CORPUS_EXISTS",
        )
    payload = {
        "schema_version": REFINEMENT_CORPUS_EVALUATION_SCHEMA_VERSION,
        "phase": "N3",
        "status": "completed",
        "source_manifest_sha256": preparation.manifest_hash,
        "baseline_expectations_sha256": preparation.expectations_hash,
        "provider": request.provenance.provider,
        "model": request.provenance.model,
        "implementation_sha": request.provenance.implementation_sha,
        "fixture_count": len(results),
        "fixture_ids": [item.fixture_id for item in results],
        "iteration_limits": asdict(request.iteration_limits),
        "loop_limits": asdict(request.loop_limits),
        "results": [
            {
                **asdict(item),
                "bundle": item.fixture_id,
            }
            for item in results
        ],
    }
    with tempfile.NamedTemporaryFile(
        prefix=".refinement-corpus-summary-",
        dir=output_root,
        delete=False,
    ) as handle:
        temp_path = Path(handle.name)
    try:
        write_json_report(temp_path, payload)
        temp_path.replace(summary)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    return summary


def _exception_diagnostics(exc: Exception) -> dict[str, object]:
    diagnostics: dict[str, object] = {
        "cause_code": _exception_code(exc),
        "cause_type": type(exc).__name__,
        "cause_message": str(exc),
    }
    details = getattr(exc, "details", None)
    if isinstance(details, Mapping):
        for key in _SAFE_CAUSE_DETAIL_KEYS:
            value = details.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                diagnostics[f"cause_{key}"] = value
    provider_error = _ollama_provider_error(exc, details=details)
    if provider_error is not None:
        diagnostics["cause_provider_error"] = provider_error
    return diagnostics


def _ollama_provider_error(exc: Exception, *, details: object) -> str | None:
    if not isinstance(details, Mapping) or details.get("provider") != "ollama":
        return None
    cause = exc.__cause__
    response = getattr(cause, "response", None)
    if response is None:
        return None
    try:
        payload = response.json()
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if isinstance(error, Mapping):
        error = error.get("message")
    if not isinstance(error, str) or not error.strip():
        return None
    return " ".join(error.split())[:_MAX_PROVIDER_ERROR_CHARS]


def _exception_code(exc: Exception) -> str | None:
    value = getattr(exc, "code", None)
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value) if value is not None else None
