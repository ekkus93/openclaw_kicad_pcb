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
        provenance=service.RefinementProvenance(
            provider="openai",
            model="vision-model",
            implementation_sha="a" * 40,
        ),
        iteration_limits=service.RefinementIterationLimits(
            max_critic_repairs=0,
            max_planner_repairs=0,
            max_operations=4,
        ),
        loop_limits=service.RefinementLoopLimits(max_rounds=2),
        fixture_ids=fixture_ids,
    )


def test_phase_n3_corpus_preflight_discovers_all_manifest_fixtures_once() -> None:
    preparation = corpus.prepare_refinement_evaluation_corpus(_request())
    prepared = preparation.fixtures
    manifest = json.loads((_REPO_ROOT / _MANIFEST).read_text(encoding="utf-8"))
    expected = manifest["fixtures"]

    assert len(prepared) == 12
    assert len(preparation.manifest_hash) == 64
    assert len(preparation.expectations_hash) == 64
    assert [item.fixture.fixture_id for item in prepared] == [
        entry["fixture_id"] for entry in expected
    ]
    assert len({item.fixture.fixture_id for item in prepared}) == len(prepared)
    assert [item.fixture.categories for item in prepared] == [
        tuple(entry["categories"]) for entry in expected
    ]
    assert [item.fixture.known_visual_defects for item in prepared] == [
        tuple(entry["known_visual_defects"]) for entry in expected
    ]
    assert all(item.baseline_schematic.name == "source_normalized.kicad_sch" for item in prepared)


def test_phase_n3_corpus_runs_all_preflighted_fixtures_and_writes_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: list[corpus.RefinementCorpusPreparedFixture] = []

    def execute(
        prepared: corpus.RefinementCorpusPreparedFixture,
        *,
        request: corpus.RefinementCorpusEvaluationRequest,
        output_root: Path,
        work_root: Path,
    ) -> corpus.RefinementCorpusFixtureResult:
        del request, work_root
        seen.append(prepared)
        (output_root / prepared.fixture.fixture_id).mkdir()
        return corpus.RefinementCorpusFixtureResult(
            fixture_id=prepared.fixture.fixture_id,
            baseline_hash="b" * 64,
            apply_once_status="accepted",
            apply_once_code="REFINEMENT_ACCEPTED",
            refine_status="completed",
            refine_stop_reason="REFINEMENT_STOP_MAX_ROUNDS",
            final_accepted_hash="c" * 64,
        )

    monkeypatch.setattr(corpus, "_execute_fixture", execute)
    result = corpus.run_refinement_evaluation_corpus(
        _request(),
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
    )

    assert len(seen) == 12
    assert result.fixture_ids == tuple(item.fixture.fixture_id for item in seen)
    assert result.summary_path == tmp_path / "out" / "summary.json"
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["status"] == "completed"
    assert summary["phase"] == "N3"
    assert summary["fixture_count"] == 12
    assert summary["fixture_ids"] == list(result.fixture_ids)
    assert summary["provider"] == "openai"
    assert summary["model"] == "vision-model"
    assert summary["implementation_sha"] == "a" * 40
    assert len(summary["baseline_expectations_sha256"]) == 64
    assert all(entry["bundle"] == entry["fixture_id"] for entry in summary["results"])
    assert str(tmp_path) not in json.dumps(summary)


def test_phase_n3_corpus_selection_preserves_manifest_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    requested = ("n1-w5500-crowded-power", "n1-crowded-power-regulator")
    seen: list[str] = []

    def execute(
        prepared: corpus.RefinementCorpusPreparedFixture,
        *,
        request: corpus.RefinementCorpusEvaluationRequest,
        output_root: Path,
        work_root: Path,
    ) -> corpus.RefinementCorpusFixtureResult:
        del request, work_root
        fixture_id = prepared.fixture.fixture_id
        seen.append(fixture_id)
        (output_root / fixture_id).mkdir()
        return corpus.RefinementCorpusFixtureResult(
            fixture_id=fixture_id,
            baseline_hash="b" * 64,
            apply_once_status="rejected",
            apply_once_code="REFINEMENT_NO_DETERMINISTIC_IMPROVEMENT",
            refine_status="completed",
            refine_stop_reason="REFINEMENT_STOP_NO_IMPROVEMENT",
            final_accepted_hash="b" * 64,
        )

    monkeypatch.setattr(corpus, "_execute_fixture", execute)
    result = corpus.run_refinement_evaluation_corpus(
        _request(fixture_ids=requested),
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
    )

    assert seen == ["n1-crowded-power-regulator", "n1-w5500-crowded-power"]
    assert result.fixture_ids == tuple(seen)


def test_phase_n3_corpus_attributes_fixture_failure_and_omits_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_ids = ("n1-crowded-power-regulator", "n1-repeated-current-source")
    calls = 0

    def execute(
        prepared: corpus.RefinementCorpusPreparedFixture,
        *,
        request: corpus.RefinementCorpusEvaluationRequest,
        output_root: Path,
        work_root: Path,
    ) -> corpus.RefinementCorpusFixtureResult:
        nonlocal calls
        del request, work_root
        calls += 1
        if calls == 2:
            raise UserError("forced failure", code="REFINEMENT_TEST_FAILURE")
        fixture_id = prepared.fixture.fixture_id
        (output_root / fixture_id).mkdir()
        return corpus.RefinementCorpusFixtureResult(
            fixture_id=fixture_id,
            baseline_hash="b" * 64,
            apply_once_status="accepted",
            apply_once_code="REFINEMENT_ACCEPTED",
            refine_status="completed",
            refine_stop_reason="REFINEMENT_STOP_MAX_ROUNDS",
            final_accepted_hash="c" * 64,
        )

    monkeypatch.setattr(corpus, "_execute_fixture", execute)
    with pytest.raises(UserError) as exc_info:
        corpus.run_refinement_evaluation_corpus(
            _request(fixture_ids=fixture_ids),
            output_root=tmp_path / "out",
            work_root=tmp_path / "work",
        )

    assert exc_info.value.code == "REFINEMENT_EVALUATION_FIXTURE_FAILED"
    assert exc_info.value.details["fixture_id"] == "n1-repeated-current-source"
    assert exc_info.value.details["cause_code"] == "REFINEMENT_TEST_FAILURE"
    assert (tmp_path / "out" / "n1-crowded-power-regulator").is_dir()
    assert not (tmp_path / "out" / "summary.json").exists()


def test_phase_n3_corpus_preflights_output_collisions_before_execution(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixture_ids = ("n1-crowded-power-regulator", "n1-repeated-current-source")
    output = tmp_path / "out"
    (output / "n1-repeated-current-source").mkdir(parents=True)
    called = False

    def execute(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("execution must not start after output collision")

    monkeypatch.setattr(corpus, "_execute_fixture", execute)
    with pytest.raises(UserError) as exc_info:
        corpus.run_refinement_evaluation_corpus(
            _request(fixture_ids=fixture_ids),
            output_root=output,
            work_root=tmp_path / "work",
        )

    assert exc_info.value.code == "REFINEMENT_EVALUATION_CORPUS_EXISTS"
    assert called is False
