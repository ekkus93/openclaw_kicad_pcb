from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import BaseModel

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.evidence import IterationEvidenceInputs, write_iteration_evidence_bundle
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
        operation_results=_Report("applied", 1),
        electrical_report=_Report("passed", 0),
        structural_report=_Report("passed", 0),
        metrics_before={"bend_count": 4},
        metrics_after={"bend_count": 3},
        quality_decision=_Report("accepted", 1),
        before_render=_render(tmp_path, "before-source"),
        after_render=_render(tmp_path, "after-source"),
        accepted_hash_after="d" * 64,
        disposition="accepted",
        reason_code="REFINEMENT_ACCEPTED",
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
    assert "svg_path" not in json.dumps(manifest)
    assert "png_path" not in json.dumps(manifest)
    assert not any(
        path.name.startswith(".refinement-evidence-") for path in output.parent.iterdir()
    )


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
