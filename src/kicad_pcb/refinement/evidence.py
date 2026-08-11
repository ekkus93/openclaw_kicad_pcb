"""Atomic, sanitized evidence bundles for schematic refinement iterations."""

from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from kicad_pcb.errors import UserError

from .rendering import SchematicRenderArtifact


@dataclass(frozen=True)
class IterationEvidenceInputs:
    iteration_id: str
    accepted_hash_before: str
    authoritative_hash: str
    critic: BaseModel
    plan: BaseModel
    operation_results: object
    electrical_report: object
    structural_report: object
    metrics_before: object
    metrics_after: object
    quality_decision: object
    before_render: SchematicRenderArtifact
    after_render: SchematicRenderArtifact | None
    accepted_hash_after: str | None = None
    disposition: str = "rejected"
    reason_code: str = "REFINEMENT_REJECTED"


def write_iteration_evidence_bundle(
    root: Path,
    evidence: IterationEvidenceInputs,
) -> Path:
    """Atomically publish one complete sanitized iteration evidence directory."""

    _validate_identifier(evidence.iteration_id)
    root.mkdir(parents=True, exist_ok=True)
    final_dir = root / evidence.iteration_id
    if final_dir.exists():
        raise UserError(
            "Refinement evidence iteration already exists.",
            code="REFINEMENT_EVIDENCE_EXISTS",
            details={"iteration_id": evidence.iteration_id},
        )
    temp_dir = Path(tempfile.mkdtemp(prefix=".refinement-evidence-", dir=root))
    try:
        manifest = {
            "schema_version": "1.0",
            "iteration_id": evidence.iteration_id,
            "accepted_hash_before": evidence.accepted_hash_before,
            "accepted_hash_after": evidence.accepted_hash_after,
            "authoritative_hash": evidence.authoritative_hash,
            "disposition": evidence.disposition,
            "reason_code": evidence.reason_code,
            "before_render": _render_metadata(evidence.before_render),
            "after_render": (
                _render_metadata(evidence.after_render)
                if evidence.after_render is not None
                else None
            ),
        }
        _write_json(temp_dir / "manifest.json", manifest)
        _write_json(temp_dir / "critic.json", _jsonable(evidence.critic))
        _write_json(temp_dir / "plan.json", _jsonable(evidence.plan))
        _write_json(temp_dir / "operations.json", _jsonable(evidence.operation_results))
        _write_json(temp_dir / "electrical.json", _jsonable(evidence.electrical_report))
        _write_json(temp_dir / "structural.json", _jsonable(evidence.structural_report))
        _write_json(temp_dir / "metrics_before.json", _jsonable(evidence.metrics_before))
        _write_json(temp_dir / "metrics_after.json", _jsonable(evidence.metrics_after))
        _write_json(temp_dir / "quality.json", _jsonable(evidence.quality_decision))
        _copy_render(evidence.before_render, temp_dir, prefix="before")
        if evidence.after_render is not None:
            _copy_render(evidence.after_render, temp_dir, prefix="after")
        temp_dir.replace(final_dir)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return final_dir


def _copy_render(render: SchematicRenderArtifact, target: Path, *, prefix: str) -> None:
    for source, suffix in ((render.svg_path, ".svg"), (render.png_path, ".png")):
        if not source.is_file():
            raise UserError(
                "Refinement evidence render artifact is missing.",
                code="REFINEMENT_EVIDENCE_WRITE_FAILED",
                details={"artifact": f"{prefix}{suffix}"},
            )
        shutil.copyfile(source, target / f"{prefix}{suffix}")


def _render_metadata(render: SchematicRenderArtifact) -> dict[str, object]:
    return {
        "schema_version": render.schema_version,
        "schematic_hash": render.schematic_hash,
        "svg_hash": render.svg_hash,
        "png_hash": render.png_hash,
        "kicad_version": render.kicad_version,
        "sheet_id": render.sheet_id,
        "width_px": render.width_px,
        "height_px": render.height_px,
        "svg_view_box_mm": list(render.svg_view_box_mm),
        "pixels_per_mm_x": render.pixels_per_mm_x,
        "pixels_per_mm_y": render.pixels_per_mm_y,
    }


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _jsonable(value: object) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(cast(Any, value)))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        raise UserError(
            "Absolute or filesystem paths are forbidden in refinement JSON evidence.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    raise UserError(
        "Unsupported refinement evidence value type.",
        code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        details={"type": type(value).__name__},
    )


def _validate_identifier(value: str) -> None:
    if (
        not value
        or len(value) > 128
        or any(
            ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
            for ch in value
        )
    ):
        raise UserError(
            "Invalid refinement evidence iteration id.", code="REFINEMENT_EVIDENCE_UNSAFE_DATA"
        )
