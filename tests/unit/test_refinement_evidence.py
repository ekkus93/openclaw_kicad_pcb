from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from pydantic import BaseModel

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.evidence import (
    IterationEvidenceInputs,
    SessionEvidenceInputs,
    SessionIterationReferenceInputs,
    build_session_iteration_reference,
    write_iteration_evidence_bundle,
    write_session_evidence_bundle,
)
from kicad_pcb.refinement.rendering import (
    SchematicRenderArtifact,
    SchematicRenderRegionArtifact,
)


class _Model(BaseModel):
    value: str


@dataclass(frozen=True)
class _Report:
    status: str
    count: int


def _bounds(*, max_rounds: int = 3) -> dict[str, int]:
    return {
        "max_rounds": max_rounds,
        "max_operations_per_round": 4,
        "max_total_accepted_operations": 8,
        "max_candidate_rejections": 2,
        "max_critic_repairs": 0,
        "max_planner_repairs": 0,
    }


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


def _render_with_regions(tmp_path: Path, name: str) -> SchematicRenderArtifact:
    render = _render(tmp_path, name)
    regions = []
    for index, region_id in enumerate(("r00-c00", "r00-c01")):
        svg = tmp_path / f"{name}-{region_id}.svg"
        png = tmp_path / f"{name}-{region_id}.png"
        svg.write_text(f"<svg>{region_id}</svg>", encoding="utf-8")
        png.write_bytes(f"png-{region_id}".encode())
        regions.append(
            SchematicRenderRegionArtifact(
                region_id=region_id,
                image_index=index,
                row=0,
                column=index,
                svg_hash=f"{index + 1}" * 64,
                png_hash=f"{index + 3}" * 64,
                view_box_mm=(index * 142.15, 0.0, 154.85, 210.0),
                width_px=1239,
                height_px=1680,
                pixels_per_mm_x=8.001,
                pixels_per_mm_y=8.0,
                svg_path=svg,
                png_path=png,
            )
        )
    return replace(render, schema_version="1.1", review_regions=tuple(regions))


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


def _reference(
    root: Path,
    iteration: Path,
    *,
    code: str = "REFINEMENT_ACCEPTED",
):
    return build_session_iteration_reference(
        root,
        SessionIterationReferenceInputs(
            iteration_id="iter-001",
            status="accepted",
            code=code,
            accepted_hash_before="a" * 64,
            accepted_hash_after="d" * 64,
            candidate_hash="d" * 64,
            candidate_layout_fingerprint="e" * 64,
            evidence_dir=iteration,
        ),
    )


def _session(
    *,
    session_id: str,
    reference,
    max_rounds: int = 3,
) -> SessionEvidenceInputs:
    return SessionEvidenceInputs(
        session_id=session_id,
        authoritative_hash="b" * 64,
        starting_accepted_hash="a" * 64,
        provider="fake-provider",
        model="fake-model",
        product_version="0.1.0",
        implementation_sha="f" * 40,
        prompt_versions={"critic": "1.0", "planner": "1.0"},
        schema_versions={
            "critic": "1.0",
            "planner": "1.0",
            "session_evidence": "1.0",
        },
        configured_bounds=_bounds(max_rounds=max_rounds),
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


def test_evidence_bundle_retains_review_regions_with_path_free_mapping(tmp_path: Path) -> None:
    evidence = _inputs(tmp_path)
    evidence = replace(
        evidence,
        before_render=_render_with_regions(tmp_path, "before-tiled"),
        after_render=None,
    )

    output = write_iteration_evidence_bundle(tmp_path / "evidence", evidence)

    assert (output / "before-r00-c00.svg").is_file()
    assert (output / "before-r00-c00.png").is_file()
    assert (output / "before-r00-c01.svg").is_file()
    assert (output / "before-r00-c01.png").is_file()
    manifest = json.loads((output / "manifest.json").read_text())
    regions = manifest["before_render"]["review_regions"]
    assert [region["region_id"] for region in regions] == ["r00-c00", "r00-c01"]
    assert [region["image_index"] for region in regions] == [0, 1]
    assert regions[1]["view_box_mm"] == [142.15, 0.0, 154.85, 210.0]
    assert str(tmp_path) not in json.dumps(regions)


def test_session_bundle_references_iteration_hashes_and_writes_human_summary(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = _reference(root, iteration)

    output = write_session_evidence_bundle(
        root,
        _session(session_id="session-001", reference=reference),
    )

    assert {path.name for path in output.iterdir()} == {"manifest.json", "summary.md"}
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["provider"] == "fake-provider"
    assert manifest["model"] == "fake-model"
    assert manifest["iterations"][0]["evidence_directory"] == "iter-001"
    assert len(manifest["iterations"][0]["evidence_manifest_sha256"]) == 64
    retention = manifest["retention_policy"]
    assert retention["maximum_iteration_bundles_per_session"] == 3
    assert retention["rejected_candidate_files"] == "not_retained_after_iteration"
    assert retention["post_edit_render_scratch"] == "not_retained_after_iteration"
    assert retention["raw_prompts"] == "not_retained"
    assert retention["raw_provider_payloads"] == "not_retained"
    assert retention["optional_debug_artifacts"] == "not_retained"
    assert "api_key" not in json.dumps(manifest)
    assert str(tmp_path) not in json.dumps(manifest)

    summary = (output / "summary.md").read_text()
    assert "Starting metrics" in summary
    assert "bend_count: 4" in summary
    assert "Operations: 1 total, 1 applied, 0 rejected" in summary
    assert "Electrical status: `passed`" in summary
    assert "bend_count=-1" in summary
    assert "A component is offset" not in summary


def test_session_bundle_rejects_tampered_iteration_hash(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = _reference(root, iteration)
    (iteration / "manifest.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(UserError, match="manifest hash does not match"):
        write_session_evidence_bundle(
            root,
            _session(session_id="session-tampered", reference=reference),
        )

    assert not (root / "sessions" / "session-tampered").exists()


def test_session_bundle_rejects_semantically_mismatched_iteration_reference(
    tmp_path: Path,
) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = _reference(root, iteration, code="REFINEMENT_DIFFERENT_CODE")

    with pytest.raises(UserError, match="does not match session reference"):
        write_session_evidence_bundle(
            root,
            _session(session_id="session-mismatch", reference=reference),
        )

    assert not (root / "sessions" / "session-mismatch").exists()


def test_session_bundle_rejects_incomplete_configured_bounds(tmp_path: Path) -> None:
    root = tmp_path / "evidence"
    iteration = write_iteration_evidence_bundle(root, _inputs(tmp_path))
    reference = _reference(root, iteration)
    session = _session(session_id="session-bounds", reference=reference)
    invalid = SessionEvidenceInputs(**{**session.__dict__, "configured_bounds": {"max_rounds": 3}})

    with pytest.raises(UserError, match="configured bounds"):
        write_session_evidence_bundle(root, invalid)


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
    with pytest.raises(UserError, match="Invalid refinement evidence identifier"):
        write_iteration_evidence_bundle(tmp_path / "evidence-b", unsafe_id)
