from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.electrical import (
    SchematicElectricalVerificationReport,
    build_schematic_electrical_baseline,
)
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.operations import LayoutOperationBatchResult, LayoutOperationResult
from kicad_pcb.refinement.planner import ValidatedRepairPlan
from kicad_pcb.refinement.rendering import SchematicRenderArtifact
from kicad_pcb_web.services import refinement_evaluation as evaluation
from kicad_pcb_web.services import schematic_refinement as service

_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render(path: Path, output_dir: Path, *, adapter: object) -> SchematicRenderArtifact:
    del adapter
    output_dir.mkdir(parents=True, exist_ok=True)
    svg = output_dir / "schematic.svg"
    png = output_dir / "schematic.png"
    svg.write_text('<svg viewBox="0 0 297 210"/>', encoding="utf-8")
    png.write_bytes(b"png-" + hashlib.sha256(path.read_bytes()).digest())
    return SchematicRenderArtifact(
        schema_version="1.0",
        schematic_hash=_sha(path),
        svg_hash=_sha(svg),
        png_hash=_sha(png),
        kicad_version="9.0.0",
        sheet_id="1",
        width_px=297,
        height_px=210,
        svg_view_box_mm=(0.0, 0.0, 297.0, 210.0),
        pixels_per_mm_x=1.0,
        pixels_per_mm_y=1.0,
        svg_path=svg,
        png_path=png,
    )


def _request() -> evaluation.RefinementEvaluationRequest:
    schematic = _FIXTURE / "baseline_generated.kicad_sch"
    authoritative = CircuitIR.load(_FIXTURE / "circuit_ir.json")
    return evaluation.RefinementEvaluationRequest(
        fixture=evaluation.RefinementEvaluationFixture(
            fixture_id="n3-fixture",
            source_fixture_id="source-fixture",
            categories=("crowded_layout",),
            known_visual_defects=("Dense local grouping reduces readability.",),
        ),
        authoritative_ir=authoritative,
        baseline_schematic=schematic,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        provenance=service.RefinementProvenance(provider="fake-provider", model="fake-model"),
        iteration_limits=service.RefinementIterationLimits(
            max_critic_repairs=0,
            max_planner_repairs=0,
            max_operations=4,
        ),
        loop_limits=service.RefinementLoopLimits(max_rounds=2),
    )


def _analysis(path: Path, request: evaluation.RefinementEvaluationRequest, work: Path):
    baseline = build_schematic_electrical_baseline(request.authoritative_ir, path)
    metrics = compute_refinement_metrics(path)
    render = _render(path, work / "render", adapter=request.adapter)
    critic = CriticResponse.model_validate(
        {
            "source_schematic_hash": baseline.accepted_schematic_hash,
            "render_png_hash": render.png_hash,
            "issues": [],
        }
    )
    return service.RefinementAnalysisResult(
        accepted_hash=baseline.accepted_schematic_hash,
        baseline=baseline,
        metrics=metrics,
        render=render,
        context=object(),  # type: ignore[arg-type]
        critic=critic,
    )


def _electrical_report(
    request: evaluation.RefinementEvaluationRequest,
    baseline_hash: str,
    candidate: Path,
) -> SchematicElectricalVerificationReport:
    baseline = build_schematic_electrical_baseline(
        request.authoritative_ir,
        request.baseline_schematic,
    )
    return SchematicElectricalVerificationReport(
        status="passed",
        authoritative_hash=baseline.authoritative_hash,
        accepted_schematic_hash=baseline_hash,
        candidate_schematic_hash=_sha(candidate),
        candidate_fingerprint_hash=baseline.authoritative_hash,
        kicad_version="9.0.0",
    )


def test_phase_n3_runner_isolates_modes_and_publishes_complete_bundle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _request()
    baseline_bytes = request.baseline_schematic.read_bytes()
    baseline_hash = hashlib.sha256(baseline_bytes).hexdigest()
    starts: list[tuple[str, bytes]] = []

    def analyze(*, accepted_path: Path, runtime, max_critic_repairs: int):
        del max_critic_repairs
        starts.append(("analyze", accepted_path.read_bytes()))
        return _analysis(accepted_path, request, runtime.work_dir)

    def plan(*, accepted_path: Path, runtime, iteration_id: str, limits):
        del limits
        starts.append(("plan", accepted_path.read_bytes()))
        analysis = _analysis(accepted_path, request, runtime.work_dir)
        return service.RefinementPlanResult(
            analysis=analysis,
            plan=ValidatedRepairPlan(
                iteration_id=iteration_id,
                source_schematic_hash=analysis.accepted_hash,
                operation_payloads=(),
                addressed_issue_ids=(),
            ),
        )

    def apply_once(*, accepted_path: Path, runtime, iteration_id: str, limits):
        del limits
        starts.append(("apply_once", accepted_path.read_bytes()))
        before = _sha(accepted_path)
        accepted_path.write_bytes(accepted_path.read_bytes() + b"\n")
        after = _sha(accepted_path)
        evidence_dir = runtime.evidence_root / iteration_id
        evidence_dir.mkdir(parents=True)
        (evidence_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
        operations = LayoutOperationBatchResult(
            source_hash=before,
            candidate_hash=after,
            results=(LayoutOperationResult("op-1", "move_component", "applied", {}),),
        )
        return service.RefinementApplyResult(
            status="accepted",
            code="REFINEMENT_ACCEPTED",
            accepted_hash_before=before,
            accepted_hash_after=after,
            candidate_hash=after,
            evidence_dir=evidence_dir,
            operations=operations,
            electrical=_electrical_report(request, baseline_hash, accepted_path),
            structural=None,
            quality=None,
            candidate_layout_fingerprint=after,
        )

    def refine(*, accepted_path: Path, runtime, session_id: str, limits):
        del limits
        starts.append(("refine", accepted_path.read_bytes()))
        before = _sha(accepted_path)
        accepted_path.write_bytes(accepted_path.read_bytes() + b"\n\n")
        after = _sha(accepted_path)
        session_dir = runtime.evidence_root / "sessions" / session_id
        session_dir.mkdir(parents=True)
        (session_dir / "manifest.json").write_text("{}\n", encoding="utf-8")
        iteration = service.RefinementApplyResult(
            status="accepted",
            code="REFINEMENT_ACCEPTED",
            accepted_hash_before=before,
            accepted_hash_after=after,
            candidate_hash=after,
            evidence_dir=None,
            operations=None,
            electrical=None,
            structural=None,
            quality=None,
            candidate_layout_fingerprint=after,
        )
        return service.RefinementLoopResult(
            status="completed",
            stop_reason="REFINEMENT_STOP_MAX_ROUNDS",
            starting_hash=before,
            final_accepted_hash=after,
            best_accepted_hash=after,
            latest_attempted_hash=after,
            starting_layout_fingerprint=before,
            final_layout_fingerprint=after,
            rounds_attempted=1,
            accepted_rounds=1,
            rejected_rounds=0,
            accepted_operations=1,
            model_calls_made=2,
            model_call_limit=4,
            iterations=(iteration,),
            session_evidence_dir=session_dir,
        )

    def electrical(*, authoritative_ir, baseline, candidate_schematic, adapter, work_dir):
        del authoritative_ir, adapter, work_dir
        return SchematicElectricalVerificationReport(
            status="passed",
            authoritative_hash=baseline.authoritative_hash,
            accepted_schematic_hash=baseline.accepted_schematic_hash,
            candidate_schematic_hash=_sha(candidate_schematic),
            candidate_fingerprint_hash=baseline.authoritative_hash,
            kicad_version="9.0.0",
        )

    monkeypatch.setattr(evaluation, "analyze_schematic_refinement", analyze)
    monkeypatch.setattr(evaluation, "plan_schematic_refinement", plan)
    monkeypatch.setattr(evaluation, "apply_once_schematic_refinement", apply_once)
    monkeypatch.setattr(evaluation, "refine_schematic", refine)
    monkeypatch.setattr(evaluation, "require_schematic_electrical_invariance", electrical)
    monkeypatch.setattr(evaluation, "render_schematic_for_refinement", _render)

    result = evaluation.run_refinement_evaluation(
        request,
        output_root=tmp_path / "out",
        work_root=tmp_path / "work",
    )

    assert [name for name, _ in starts] == ["analyze", "plan", "apply_once", "refine"]
    assert all(payload == baseline_bytes for _, payload in starts)
    assert result.baseline_hash == baseline_hash
    assert result.apply_once_status == "accepted"
    assert result.refine_stop_reason == "REFINEMENT_STOP_MAX_ROUNDS"

    manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["phase"] == "N3"
    assert manifest["isolation_policy"] == (
        "each mode starts from identical baseline schematic bytes"
    )
    assert manifest["apply_once"]["status"] == "accepted"
    assert manifest["refine"]["stop_reason"] == "REFINEMENT_STOP_MAX_ROUNDS"
    assert manifest["final_electrical_status"] == "passed"
    assert manifest["before_after"]["before_render"]["png"] == "analyze/render/schematic.png"
    assert manifest["before_after"]["after_render"]["png"] == (
        "refine/final/render/schematic.png"
    )
    assert (result.output_dir / "operations.json").is_file()
    assert (result.output_dir / "apply_once" / "final" / "metrics.json").is_file()
    assert (result.output_dir / "refine" / "final" / "electrical.json").is_file()
    assert (result.output_dir / "refine" / "final" / "accepted.kicad_sch").read_bytes() == (
        baseline_bytes + b"\n\n"
    )
    serialized = json.dumps(manifest)
    assert str(tmp_path) not in serialized
    assert not any(
        path.name.startswith(".refinement-evaluation-")
        for path in (tmp_path / "out").iterdir()
    )
    assert list((tmp_path / "work").iterdir()) == []


def test_phase_n3_runner_refuses_existing_fixture_output(tmp_path: Path) -> None:
    request = _request()
    existing = tmp_path / "out" / request.fixture.fixture_id
    existing.mkdir(parents=True)

    with pytest.raises(UserError) as exc_info:
        evaluation.run_refinement_evaluation(
            request,
            output_root=tmp_path / "out",
            work_root=tmp_path / "work",
        )

    assert exc_info.value.code == "REFINEMENT_EVALUATION_EXISTS"


def test_phase_n3_runner_removes_partial_bundle_on_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = _request()

    def fail(request, *, output_tmp: Path, work_tmp: Path):
        del request
        (output_tmp / "partial.txt").write_text("partial", encoding="utf-8")
        (work_tmp / "scratch.txt").write_text("scratch", encoding="utf-8")
        raise UserError("forced failure", code="REFINEMENT_TEST_FAILURE")

    monkeypatch.setattr(evaluation, "_run_evaluation", fail)

    with pytest.raises(UserError) as exc_info:
        evaluation.run_refinement_evaluation(
            request,
            output_root=tmp_path / "out",
            work_root=tmp_path / "work",
        )

    assert exc_info.value.code == "REFINEMENT_TEST_FAILURE"
    assert list((tmp_path / "out").iterdir()) == []
    assert list((tmp_path / "work").iterdir()) == []
