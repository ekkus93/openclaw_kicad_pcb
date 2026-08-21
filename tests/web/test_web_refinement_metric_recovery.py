from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kicad_pcb.circuit_ir import CircuitIR
from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.electrical import build_schematic_electrical_baseline
from kicad_pcb.refinement.metrics import compute_refinement_metrics
from kicad_pcb.refinement.operations import LayoutOperationBatchResult, LayoutOperationResult
from kicad_pcb.refinement.planner import ValidatedRepairPlan
from kicad_pcb.refinement.rendering import SchematicRenderArtifact
from kicad_pcb_web.services import schematic_refinement as service


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    digest = _sha(path)
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


def _planned(tmp_path: Path, accepted: Path, ir: CircuitIR) -> service.RefinementPlanResult:
    baseline = build_schematic_electrical_baseline(ir, accepted)
    metrics = compute_refinement_metrics(accepted)
    render = _render(accepted, tmp_path / "before-render")
    critic = CriticResponse.model_validate(
        {
            "source_schematic_hash": baseline.accepted_schematic_hash,
            "render_png_hash": render.png_hash,
            "issues": [],
        }
    )
    analysis = service.RefinementAnalysisResult(
        accepted_hash=baseline.accepted_schematic_hash,
        baseline=baseline,
        metrics=metrics,
        render=render,
        context=object(),  # type: ignore[arg-type]
        critic=critic,
    )
    plan = ValidatedRepairPlan(
        iteration_id="iter-1",
        source_schematic_hash=analysis.accepted_hash,
        operation_payloads=(
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
        ),
        addressed_issue_ids=(),
    )
    return service.RefinementPlanResult(analysis=analysis, plan=plan)


def _runtime(tmp_path: Path, ir: CircuitIR) -> service.RefinementRuntime:
    return service.RefinementRuntime(
        authoritative_ir=ir,
        adapter=object(),  # type: ignore[arg-type]
        llm_client=object(),  # type: ignore[arg-type]
        work_dir=tmp_path / "work",
        evidence_root=tmp_path / "evidence",
    )


def _install_candidate_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    def execute(path, payloads, *, expected_source_hash, authoritative_ir, policy):
        del payloads, authoritative_ir, policy
        assert _sha(path) == expected_source_hash
        path.write_bytes(path.read_bytes() + b"\n")
        candidate_hash = _sha(path)
        return LayoutOperationBatchResult(
            expected_source_hash,
            candidate_hash,
            (LayoutOperationResult("op-1", "move_component", "applied", {}),),
        )

    monkeypatch.setattr(service, "execute_layout_operations", execute)
    monkeypatch.setattr(
        service,
        "compute_schematic_layout_fingerprint",
        lambda path: SimpleNamespace(digest=_sha(path)),
    )


def test_candidate_metric_invalid_geometry_is_bounded_rejection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    planned = _planned(tmp_path, accepted, ir)
    _install_candidate_fakes(monkeypatch)

    def invalid_metrics(path: Path):
        raise UserError(
            "Refinement metrics require orthogonal wire geometry.",
            code="REFINEMENT_METRIC_INVALID_GEOMETRY",
            details={"path_name": path.name},
        )

    monkeypatch.setattr(service, "compute_refinement_metrics", invalid_metrics)
    result = service.apply_planned_refinement(
        accepted_path=accepted,
        runtime=_runtime(tmp_path, ir),
        planned=planned,
        iteration_id="iter-1",
    )

    assert result.status == "rejected"
    assert result.code == "REFINEMENT_METRIC_INVALID_GEOMETRY"
    assert result.candidate_hash is not None
    assert result.candidate_hash != planned.analysis.accepted_hash
    assert result.accepted_hash_after == planned.analysis.accepted_hash
    assert accepted.read_bytes() == before
    assert result.evidence_dir is not None

    metrics_after = json.loads((result.evidence_dir / "metrics_after.json").read_text())
    assert metrics_after == {
        "code": "REFINEMENT_METRIC_INVALID_GEOMETRY",
        "details": {"path_name": "accepted.kicad_sch"},
        "message": "Refinement metrics require orthogonal wire geometry.",
        "status": "invalid",
    }
    electrical = json.loads((result.evidence_dir / "electrical.json").read_text())
    structural = json.loads((result.evidence_dir / "structural.json").read_text())
    assert electrical == {
        "reason_code": "REFINEMENT_METRIC_INVALID_GEOMETRY",
        "status": "not_run",
    }
    assert structural == electrical


def test_unrelated_candidate_metric_error_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accepted, ir = _fixture(tmp_path)
    before = accepted.read_bytes()
    planned = _planned(tmp_path, accepted, ir)
    _install_candidate_fakes(monkeypatch)

    def unexpected_metrics(path: Path):
        del path
        raise UserError("Unexpected metric failure.", code="REFINEMENT_METRIC_INTERNAL_SENTINEL")

    monkeypatch.setattr(service, "compute_refinement_metrics", unexpected_metrics)
    with pytest.raises(UserError, match="Unexpected metric failure") as exc_info:
        service.apply_planned_refinement(
            accepted_path=accepted,
            runtime=_runtime(tmp_path, ir),
            planned=planned,
            iteration_id="iter-1",
        )

    assert exc_info.value.code == "REFINEMENT_METRIC_INTERNAL_SENTINEL"
    assert accepted.read_bytes() == before
