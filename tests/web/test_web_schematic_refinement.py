from __future__ import annotations

import hashlib
from dataclasses import replace
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
from kicad_pcb.refinement.validation import CandidateStructuralValidationReport
from kicad_pcb.refinement.vision_context import build_vision_object_map
from kicad_pcb_web.services import schematic_refinement as service


def _fixture(tmp_path: Path) -> tuple[Path, CircuitIR]:
    root = (
        Path(__file__).parents[1] / "fixtures" / "readability" / "ne5532_headphone_amp_left_current"
    )
    accepted = tmp_path / "accepted.kicad_sch"
    accepted.write_bytes((root / "baseline_generated.kicad_sch").read_bytes())
    return accepted, CircuitIR.load(root / "circuit_ir.json")


def _render(path: Path, output_dir: Path) -> SchematicRenderArtifact:
    output_dir.mkdir(parents=True, exist_ok=True)
    svg = output_dir / "schematic.svg"
    png = output_dir / "schematic.png"
    svg.write_text("<svg/>", encoding="utf-8")
    png.write_bytes(b"render-" + hashlib.sha256(path.read_bytes()).digest())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SchematicRenderArtifact(
        "1.0",
        digest,
        hashlib.sha256(svg.read_bytes()).hexdigest(),
        hashlib.sha256(png.read_bytes()).hexdigest(),
        "9.0.0",
        "1",
        2970,
        2100,
        (0.0, 0.0, 297.0, 210.0),
        10.0,
        10.0,
        svg,
        png,
    )


def _render_adapter(path: Path, output_dir: Path, *, adapter) -> SchematicRenderArtifact:
    del adapter
    return _render(path, output_dir)


def _runtime(
    tmp_path: Path, ir: CircuitIR, *, work_name: str = "work"
) -> service.RefinementRuntime:
    return service.RefinementRuntime(
        authoritative_ir=ir,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        work_dir=tmp_path / work_name,
        evidence_root=tmp_path / "evidence",
    )


def _limits() -> service.RefinementIterationLimits:
    return service.RefinementIterationLimits(
        max_critic_repairs=0,
        max_planner_repairs=0,
        max_operations=4,
    )


def _critic(context) -> CriticResponse:
    return CriticResponse.model_validate(
        {
            "source_schematic_hash": context.source_schematic_hash,
            "render_png_hash": context.render_png_hash,
            "issues": [
                {
                    "issue_id": "i1",
                    "category": "component_alignment",
                    "severity": "warning",
                    "confidence": 0.9,
                    "affected_object_ids": [context.components[0].object_id],
                    "observation": "A component is offset.",
                    "desired_outcome": "Improve column alignment.",
                    "evidence": "Visible offset.",
                }
            ],
        }
    )


def _analysis(tmp_path: Path, accepted: Path, ir: CircuitIR):
    baseline = build_schematic_electrical_baseline(ir, accepted)
    metrics = compute_refinement_metrics(accepted)
    render = _render(accepted, tmp_path / "before-render")
    context = build_vision_object_map(accepted, authoritative_ir=ir, render=render, metrics=metrics)
    return service.RefinementAnalysisResult(
        accepted_hash=baseline.accepted_schematic_hash,
        baseline=baseline,
        metrics=replace(metrics, alignment_residual_mean_mm=2.0),
        render=render,
        context=context,
        critic=_critic(context),
    )


def _plan(analysis, *, operations: bool = True) -> service.RefinementPlanResult:
    payloads = ()
    if operations:
        payloads = (
            {
                "schema_version": "1.0",
                "operation_id": "op-1",
                "source_schematic_hash": analysis.accepted_hash,
                "operation_type": "move_component",
                "arguments": {
                    "target": {"ref": "R1", "unit": "1"},
                    "dx_mm": 1.27,
                    "dy_mm": 0.0,
                },
            },
        )
    plan = ValidatedRepairPlan(
        iteration_id="iter-1",
        source_schematic_hash=analysis.accepted_hash,
        operation_payloads=payloads,
        addressed_issue_ids=("i1",) if operations else (),
    )
    return service.RefinementPlanResult(analysis=analysis, plan=plan)


def _install_apply_fakes(monkeypatch, analysis, *, electrical_passed: bool = True) -> None:
    def execute(path, payloads, *, expected_source_hash, authoritative_ir, policy):
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected_source_hash
        path.write_bytes(path.read_bytes() + b"\n")
        candidate_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        return LayoutOperationBatchResult(
            expected_source_hash,
            candidate_hash,
            (LayoutOperationResult("op-1", "move_component", "applied", {}),),
        )

    def metrics(path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return replace(
            analysis.metrics,
            schematic_hash=digest,
            alignment_residual_mean_mm=1.0,
        )

    def electrical(**kwargs):
        path = kwargs["candidate_schematic"]
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return SchematicElectricalVerificationReport(
            status="passed" if electrical_passed else "failed",
            authoritative_hash=analysis.baseline.authoritative_hash,
            accepted_schematic_hash=analysis.accepted_hash,
            candidate_schematic_hash=digest,
            candidate_fingerprint_hash="f" * 64,
            mismatches=(),
            kicad_version="9.0.0",
        )

    monkeypatch.setattr(service, "execute_layout_operations", execute)
    monkeypatch.setattr(service, "compute_refinement_metrics", metrics)
    monkeypatch.setattr(service, "verify_schematic_electrical_invariance", electrical)
    monkeypatch.setattr(
        service,
        "validate_candidate_structure",
        lambda *args, **kwargs: CandidateStructuralValidationReport("1.0", "passed", (), 0),
    )
    monkeypatch.setattr(
        service,
        "render_schematic_for_refinement",
        _render_adapter,
    )


def test_analyze_and_plan_modes_are_read_only(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()

    monkeypatch.setattr(
        service,
        "render_schematic_for_refinement",
        _render_adapter,
    )
    monkeypatch.setattr(
        service,
        "run_visual_critic",
        lambda *, context, **kwargs: _critic(context),
    )
    monkeypatch.setattr(
        service,
        "run_repair_planner",
        lambda **kwargs: ValidatedRepairPlan(
            iteration_id="iter-1",
            source_schematic_hash=kwargs["context"].source_schematic_hash,
            operation_payloads=(),
            addressed_issue_ids=(),
        ),
    )

    analyzed = service.analyze_schematic_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir, work_name="work-a"),
        max_critic_repairs=0,
    )
    planned = service.plan_schematic_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir, work_name="work-b"),
        iteration_id="iter-1",
        limits=_limits(),
    )
    assert analyzed.accepted_hash == planned.analysis.accepted_hash
    assert accepted.read_bytes() == before


def test_plan_skips_planner_when_critic_has_no_actionable_issues(
    monkeypatch, tmp_path: Path
) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    analysis = _analysis(tmp_path, accepted, ir)
    empty_critic = CriticResponse.model_validate(
        {
            "source_schematic_hash": analysis.context.source_schematic_hash,
            "render_png_hash": analysis.context.render_png_hash,
            "issues": [],
        }
    )
    analysis = replace(analysis, critic=empty_critic)
    monkeypatch.setattr(service, "analyze_schematic_refinement", lambda **kwargs: analysis)

    def unexpected_planner(**kwargs):
        raise AssertionError("planner must not run for an empty critic issue list")

    monkeypatch.setattr(service, "run_repair_planner", unexpected_planner)
    planned = service.plan_schematic_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        iteration_id="iter-empty",
        limits=_limits(),
    )
    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=planned,
        iteration_id="iter-empty",
    )

    assert planned.plan.operation_payloads == ()
    assert result.status == "no_op"
    assert result.code == "REFINEMENT_NO_ACTIONABLE_CRITIC_ISSUES"
    assert accepted.read_bytes() == before


def test_analyze_detects_unexpected_write(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    monkeypatch.setattr(
        service,
        "render_schematic_for_refinement",
        _render_adapter,
    )

    def malicious_critic(*, context, **kwargs):
        accepted.write_bytes(accepted.read_bytes() + b"\n")
        return _critic(context)

    monkeypatch.setattr(service, "run_visual_critic", malicious_critic)
    with pytest.raises(UserError, match="modified accepted schematic bytes"):
        service.analyze_schematic_refinement(
            accepted_path=accepted,
            runtime=_runtime(tmp_path, ir),
            max_critic_repairs=0,
        )


def test_apply_promotes_only_after_all_gates_and_evidence(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    analysis = _analysis(tmp_path, accepted, ir)
    planned = _plan(analysis)
    _install_apply_fakes(monkeypatch, analysis)

    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=planned,
        iteration_id="iter-1",
    )

    assert result.status == "accepted"
    assert result.accepted_hash_after != analysis.accepted_hash
    assert hashlib.sha256(accepted.read_bytes()).hexdigest() == result.accepted_hash_after
    assert result.evidence_dir is not None
    assert (result.evidence_dir / "manifest.json").is_file()


def test_best_known_floor_rejects_mixed_tradeoff_before_promotion(
    monkeypatch, tmp_path: Path
) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    analysis = _analysis(tmp_path, accepted, ir)
    planned = _plan(analysis)
    _install_apply_fakes(monkeypatch, analysis)

    def mixed_metrics(path: Path):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        return replace(
            analysis.metrics,
            schematic_hash=digest,
            alignment_residual_mean_mm=1.0,
            total_wire_manhattan_length_mm=(analysis.metrics.total_wire_manhattan_length_mm + 1.27),
        )

    monkeypatch.setattr(service, "compute_refinement_metrics", mixed_metrics)
    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=planned,
        iteration_id="iter-1",
        enforce_best_known_retention=True,
    )

    assert result.status == "rejected"
    assert result.code == "REFINEMENT_BEST_KNOWN_REGRESSION"
    assert result.quality is not None
    assert not result.quality.accepted
    assert accepted.read_bytes() == before


def test_electrical_rejection_preserves_accepted_bytes(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    analysis = _analysis(tmp_path, accepted, ir)
    _install_apply_fakes(monkeypatch, analysis, electrical_passed=False)

    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=_plan(analysis),
        iteration_id="iter-1",
    )

    assert result.status == "rejected"
    assert result.code == "ELECTRICAL_INVARIANCE_FAILED"
    assert accepted.read_bytes() == before
    assert result.evidence_dir is not None


def test_evidence_failure_prevents_promotion(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    analysis = _analysis(tmp_path, accepted, ir)
    _install_apply_fakes(monkeypatch, analysis)
    monkeypatch.setattr(
        service,
        "write_iteration_evidence_bundle",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("evidence disk full")),
    )

    with pytest.raises(OSError, match="evidence disk full"):
        service.apply_planned_refinement(
            accepted_path=accepted,
            runtime=_runtime(tmp_path, ir),
            planned=_plan(analysis),
            iteration_id="iter-1",
        )
    assert accepted.read_bytes() == before


def test_empty_plan_is_visible_no_op_and_never_mutates(monkeypatch, tmp_path: Path) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    analysis = _analysis(tmp_path, accepted, ir)

    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=_plan(analysis, operations=False),
        iteration_id="iter-1",
    )

    assert result.status == "no_op"
    assert result.code == "REFINEMENT_NO_OPERATIONS"
    assert accepted.read_bytes() == before
