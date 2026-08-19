from __future__ import annotations

import json
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb_web.services import refinement_evaluation_corpus as corpus
from kicad_pcb_web.services import schematic_refinement as service

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MANIFEST = Path("tests/fixtures/refinement/evaluation_corpus/manifest.json")


def _request(*, fixture_ids: tuple[str, ...] = ()) -> corpus.RefinementCorpusEvaluationRequest:
    return corpus.RefinementCorpusEvaluationRequest(
        repo_root=_REPO_ROOT,
        manifest_path=_MANIFEST,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        provenance=service.RefinementProvenance(provider="openai", model="vision-model"),
        iteration_limits=service.RefinementIterationLimits(0, 0, 4),
        loop_limits=service.RefinementLoopLimits(),
        fixture_ids=fixture_ids,
    )


@pytest.mark.parametrize(
    "fixture_ids",
    [
        ("n1-crowded-power-regulator", "n1-crowded-power-regulator"),
        ("missing-fixture",),
    ],
)
def test_phase_n3_corpus_rejects_invalid_fixture_selection(fixture_ids: tuple[str, ...]) -> None:
    with pytest.raises(UserError) as exc_info:
        corpus.prepare_refinement_evaluation_corpus(_request(fixture_ids=fixture_ids))

    assert exc_info.value.code == "REFINEMENT_EVALUATION_CORPUS_INVALID"


def test_phase_n3_corpus_rejects_missing_repository_root(tmp_path: Path) -> None:
    request = corpus.RefinementCorpusEvaluationRequest(
        repo_root=tmp_path / "missing-repo",
        manifest_path=_MANIFEST,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        provenance=service.RefinementProvenance(provider="openai", model="vision-model"),
        iteration_limits=service.RefinementIterationLimits(0, 0, 4),
        loop_limits=service.RefinementLoopLimits(),
    )

    with pytest.raises(UserError) as exc_info:
        corpus.prepare_refinement_evaluation_corpus(request)

    assert exc_info.value.code == "REFINEMENT_EVALUATION_CORPUS_INVALID"


def test_phase_n3_corpus_rejects_source_path_escape(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (tmp_path / "outside").mkdir()
    manifest = repo_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "N1",
                "source_corpus": "../outside",
                "fixtures": [
                    {
                        "fixture_id": "fixture",
                        "source_fixture_id": "source",
                        "categories": ["crowded_layout"],
                        "known_visual_defects": ["Crowded."],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    request = corpus.RefinementCorpusEvaluationRequest(
        repo_root=repo_root,
        manifest_path=manifest,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        provenance=service.RefinementProvenance(provider="openai", model="vision-model"),
        iteration_limits=service.RefinementIterationLimits(0, 0, 4),
        loop_limits=service.RefinementLoopLimits(),
    )

    with pytest.raises(UserError) as exc_info:
        corpus.prepare_refinement_evaluation_corpus(request)

    assert exc_info.value.code == "REFINEMENT_EVALUATION_CORPUS_INVALID"


def test_phase_n3_corpus_rejects_drift_from_certified_n2_baseline(tmp_path: Path) -> None:
    source_manifest = json.loads((_REPO_ROOT / _MANIFEST).read_text(encoding="utf-8"))
    source_expectations = json.loads(
        (
            _REPO_ROOT
            / "tests/fixtures/refinement/evaluation_corpus/baseline_expectations.json"
        ).read_text(encoding="utf-8")
    )
    entry = source_manifest["fixtures"][0]
    expectation = dict(source_expectations["fixtures"][0])
    expectation["source_schematic_sha256"] = "0" * 64

    repo_root = tmp_path / "repo"
    evaluation_root = repo_root / "tests/fixtures/refinement/evaluation_corpus"
    source_root = repo_root / "tests/fixtures/model_corpus" / entry["source_fixture_id"]
    evaluation_root.mkdir(parents=True)
    source_root.mkdir(parents=True)
    actual_source = _REPO_ROOT / "tests/fixtures/model_corpus" / entry["source_fixture_id"]
    (source_root / "source_normalized.kicad_sch").write_bytes(
        (actual_source / "source_normalized.kicad_sch").read_bytes()
    )
    (source_root / "circuit_ir.json").write_bytes((actual_source / "circuit_ir.json").read_bytes())
    (evaluation_root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "N1",
                "source_corpus": "tests/fixtures/model_corpus",
                "fixtures": [entry],
            }
        ),
        encoding="utf-8",
    )
    (evaluation_root / "baseline_expectations.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "phase": "N2",
                "fixtures": [expectation],
            }
        ),
        encoding="utf-8",
    )
    request = corpus.RefinementCorpusEvaluationRequest(
        repo_root=repo_root,
        manifest_path=Path("tests/fixtures/refinement/evaluation_corpus/manifest.json"),
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        provenance=service.RefinementProvenance(provider="openai", model="vision-model"),
        iteration_limits=service.RefinementIterationLimits(0, 0, 4),
        loop_limits=service.RefinementLoopLimits(),
    )

    with pytest.raises(UserError) as exc_info:
        corpus.prepare_refinement_evaluation_corpus(request)

    assert exc_info.value.code == "REFINEMENT_EVALUATION_BASELINE_DRIFT"
    assert exc_info.value.details["fixture_id"] == entry["fixture_id"]
