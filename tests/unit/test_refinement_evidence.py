from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.evidence import (
    IterationEvidenceInputs,
    SessionEvidenceInputs,
    build_session_iteration_reference,
    write_iteration_evidence_bundle,
    write_session_evidence_bundle,
)
from kicad_pcb.refinement.rendering import SchematicRenderArtifact


class _Model(BaseModel):
    value: str


@dataclass(frozen=True)
class _Report:
    status: str
    count: int


def _render(tmp_path: Path, name: str) -> SchematicRenderArtifact:
    svg = tmp_path / f"{name}.svg"
    png = tmp_path / f"{name}.png"
    svg.write_text("<svg/>", encoding="utf-8")
    png.write_bytes(b"png")
    return SchematicRenderArtifact(
        "1.0",
        "a" * 64,
        "b" * 64,
        "c" * 64,
        "9.0.0",
        "1",
        100,
        100,
        (0.0, 0.0, 10.0, 10.0),
        10.0,
        10.0,
        svg,
        png,
    )


def _inputs(tmp_path: Path) -> IterationEvidenceInputs:
    return IterationEvidenceInputs(
        iteration_id="iter-001",
        accepted_hash_before="a" * 64,
        authoritative_hash="b" * 64,
        critic=_Model(value="critic"),
        plan=_Model(value="plan"),
        operation_results={
            "results": [
                {
                    "operation_id": "op-1",
                    "operation_type": "move_component",
                    "status": "applied",
                    "details": {},
                }
            ]
        },
        electrical_report=_Report("passed", 0),
        structural_report=_Report("passed", 0),
        metrics_before={"bend_count": 4, "wire_segment_count": 8},
        metrics_after={"bend_count": 3, "wire_segment_count": 8},
        quality_decision=_Report("accepted", 1),
        before_render=_render(tmp_path, "before-source"),
        after_render=_render(tmp_path, "after-source"),
        accepted_hash_after="d" * 64,
        disposition="accepted",
        reason_code="REFINEMENT_ACCEPTED",
        candidate_hash="d" * 64,
        candidate_layout_fingerprint="e" * 64,
    )


def test_evidence_bundle_is_complete_sanitized_and_atomic(tmp_path: Path) -> None:
    evidence = _inputs(tmp_path)
    output = write_iteration_evidence_bundle(tmp_path / "evidence", evidence)

    expected = {
        "manifest.json",
        "critic.json",
        "plan.json",
        "operations.json",
        "electrical.json",
        "structural.json",
        "metrics_before.json",
        "metrics_after.json",
        "quality.json",
        "before.svg",
        "before.png",
        "after.svg",
        "after.png",
    }
    assert {path.name for path in output.iterdir()} == expected
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["iteration_id"] == "iter-001"
    assert manifest["candidate_hash"] == "d" * 64
    assert manifest["candidate_layout_fingerprint"] == "e" * 64
    assert "svg_path" not in json.dumps(manifest)
    assert "png_path" not in json.dumps(manifest)
    assert not any(
        path.name.startswith(".refinement-evidence-") for path in output.parent.iterdir()
    )


def test_session_bundle_references_iteration_hashes_and_writes_human_summary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = build_session_iteration_reference(
        evidence_root=root,
        iteration_id="iter-001",
        status="accepted",
        code="REFINEMENT_ACCEPTED",
        accepted_hash_before="a" * 64,
        accepted_hash_after="d" * 64,
        candidate_hash="d" * 64,
        candidate_layout_fingerprint="e" * 64,
        evidence_dir=iteration,
    )
    session = SessionEvidenceInputs(
        session_id="session-001",
        authoritative_hash="b" * 64,
        starting_accepted_hash="a" * 64,
        provider="fake-provider",
        model="fake-model",
        product_version="0.1.0",
        implementation_sha="f" * 40,
        prompt_versions={"critic": "1.0", "planner": "1.0"},
        schema_versions={"critic": "1.0", "planner": "1.0", "session_evidence": "1.0"},
        configured_bounds={"max_rounds": 3, "max_operations_per_round": 4},
        iterations=(reference,),
        status="completed",
        stop_reason="REFINEMENT_STOP_MAX_ROUNDS",
        final_accepted_hash="d" * 64,
        best_accepted_hash="d" * 64,
        starting_layout_fingerprint="1" * 64,
        final_layout_fingerprint="2" * 64,
        model_calls_made=2,
        model_call_limit=6,
    )

    output = write_session_evidence_bundle(root, session)

    assert {path.name for path in output.iterdir()} == {"manifest.json", "summary.md"}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["provider"] == "fake-provider"
    assert manifest["model"] == "fake-model"
    assert manifest["iterations"][0]["evidence_directory"] == "iter-001"
    assert len(manifest["iterations"][0]["evidence_manifest_sha256"]) == 64
    assert "api_key" not in json.dumps(manifest)
    assert str(tmp_path) not in json.dumps(manifest)

    summary = (output / "summary.md").read_text()
    assert "Starting metrics" in summary
    assert "bend_count: 4" in summary
    assert "Operations: 1 total, 1 applied, 0 rejected" in summary
    assert "Electrical status: `passed`" in summary
    assert "bend_count=-1" in summary
    assert "critic" not in summary.lower() or "critic issues" in summary.lower()


def test_session_bundle_rejects_tampered_iteration_reference(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = build_session_iteration_reference(
        evidence_root=root,
        iteration_id="iter-001",
        status="accepted",
        code="REFINEMENT_ACCEPTED",
        accepted_hash_before="a" * 64,
        accepted_hash_after="d" * 64,
        candidate_hash="d" * 64,
        candidate_layout_fingerprint="e" * 64,
        evidence_dir=iteration,
    )
    (iteration / "manifest.json").write_text("{}\n", encoding="utf-8")
    session = SessionEvidenceInputs(
        session_id="session-tampered",
        authoritative_hash="b" * 64,
        starting_accepted_hash="a" * 64,
        provider="fake-provider",
        model="fake-model",
        product_version=None,
        implementation_sha=None,
        prompt_versions={},
        schema_versions={},
        configured_bounds={"max_rounds": 1},
        iterations=(reference,),
        status="completed",
        stop_reason="REFINEMENT_STOP_MAX_ROUNDS",
        final_accepted_hash="d" * 64,
        best_accepted_hash="d" * 64,
        starting_layout_fingerprint="1" * 64,
        final_layout_fingerprint="2" * 64,
        model_calls_made=1,
        model_call_limit=2,
    )

    with pytest.raises(UserError, match="manifest hash does not match"):
        write_session_evidence_bundle(root, session)

    assert not (root / "sessions" / "session-tampered").exists()


def test_evidence_write_failure_does_not_publish_partial_bundle(tmp_path: Path) -> None:
    evidence = _inputs(tmp_path)
    evidence.before_render.svg_path.unlink()

    with pytest.raises(UserError, match="artifact is missing"):
        write_iteration_evidence_bundle(tmp_path / "evidence", evidence)

    root = tmp_path / "evidence"
    assert not (root / "iter-001").exists()
    assert not any(root.iterdir())


def test_evidence_rejects_path_values_and_unsafe_iteration_ids(tmp_path: Path) -> None:
    evidence = _inputs(tmp_path)
    unsafe = IterationEvidenceInputs(
        **{
            **evidence.__dict__,
            "operation_results": {"private_path": tmp_path / "secret"},
        }
    )
    with pytest.raises(UserError, match="paths are forbidden"):
        write_iteration_evidence_bundle(tmp_path / "evidence-a", unsafe)

    unsafe_id = IterationEvidenceInputs(**{**evidence.__dict__, "iteration_id": "../escape"})
    with pytest.raises(UserError, match="Invalid refinement evidence iteration id"):
        write_iteration_evidence_bundle(tmp_path / "evidence-b", unsafe_id)
