"""Atomic, sanitized evidence bundles for schematic refinement iterations and sessions."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel

from kicad_pcb.errors import UserError

from .rendering import SchematicRenderArtifact

ITERATION_EVIDENCE_SCHEMA_VERSION = "1.0"
SESSION_EVIDENCE_SCHEMA_VERSION = "1.0"


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
    candidate_hash: str | None = None
    candidate_layout_fingerprint: str | None = None


@dataclass(frozen=True)
class SessionIterationEvidenceReference:
    iteration_id: str
    status: str
    code: str
    accepted_hash_before: str
    accepted_hash_after: str
    candidate_hash: str | None
    candidate_layout_fingerprint: str | None
    evidence_directory: str | None
    evidence_manifest_sha256: str | None


@dataclass(frozen=True)
class SessionEvidenceInputs:
    session_id: str
    authoritative_hash: str
    starting_accepted_hash: str
    provider: str
    model: str
    product_version: str | None
    implementation_sha: str | None
    prompt_versions: dict[str, str]
    schema_versions: dict[str, str]
    configured_bounds: dict[str, int]
    iterations: tuple[SessionIterationEvidenceReference, ...]
    status: str
    stop_reason: str
    final_accepted_hash: str
    best_accepted_hash: str
    starting_layout_fingerprint: str
    final_layout_fingerprint: str
    model_calls_made: int
    model_call_limit: int
    failure_code: str | None = None


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
            "schema_version": ITERATION_EVIDENCE_SCHEMA_VERSION,
            "iteration_id": evidence.iteration_id,
            "accepted_hash_before": evidence.accepted_hash_before,
            "accepted_hash_after": evidence.accepted_hash_after,
            "candidate_hash": evidence.candidate_hash,
            "candidate_layout_fingerprint": evidence.candidate_layout_fingerprint,
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
        _fsync_directory(temp_dir)
        temp_dir.replace(final_dir)
        _fsync_directory(root)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return final_dir


def build_session_iteration_reference(
    *,
    evidence_root: Path,
    iteration_id: str,
    status: str,
    code: str,
    accepted_hash_before: str,
    accepted_hash_after: str,
    candidate_hash: str | None,
    candidate_layout_fingerprint: str | None,
    evidence_dir: Path | None,
) -> SessionIterationEvidenceReference:
    """Build a sanitized session reference to one already-published iteration bundle."""

    _validate_identifier(iteration_id)
    evidence_directory: str | None = None
    manifest_hash: str | None = None
    if evidence_dir is not None:
        root_resolved = evidence_root.resolve()
        evidence_resolved = evidence_dir.resolve()
        if evidence_resolved.parent != root_resolved:
            raise UserError(
                "Iteration evidence is outside the configured evidence root.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
            )
        _validate_identifier(evidence_resolved.name)
        if evidence_resolved.name != iteration_id:
            raise UserError(
                "Iteration evidence directory does not match iteration id.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
            )
        manifest_path = evidence_resolved / "manifest.json"
        if not manifest_path.is_file():
            raise UserError(
                "Iteration evidence manifest is missing.",
                code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            )
        evidence_directory = evidence_resolved.name
        manifest_hash = _sha256_file(manifest_path)

    return SessionIterationEvidenceReference(
        iteration_id=iteration_id,
        status=status,
        code=code,
        accepted_hash_before=accepted_hash_before,
        accepted_hash_after=accepted_hash_after,
        candidate_hash=candidate_hash,
        candidate_layout_fingerprint=candidate_layout_fingerprint,
        evidence_directory=evidence_directory,
        evidence_manifest_sha256=manifest_hash,
    )


def write_session_evidence_bundle(root: Path, evidence: SessionEvidenceInputs) -> Path:
    """Atomically publish one complete sanitized session manifest and human summary."""

    _validate_identifier(evidence.session_id)
    _validate_session_evidence(evidence)
    sessions_root = root / "sessions"
    sessions_root.mkdir(parents=True, exist_ok=True)
    final_dir = sessions_root / evidence.session_id
    if final_dir.exists():
        raise UserError(
            "Refinement evidence session already exists.",
            code="REFINEMENT_EVIDENCE_EXISTS",
            details={"session_id": evidence.session_id},
        )
    temp_dir = Path(tempfile.mkdtemp(prefix=".refinement-session-", dir=sessions_root))
    try:
        manifest = {
            "schema_version": SESSION_EVIDENCE_SCHEMA_VERSION,
            "session_id": evidence.session_id,
            "implementation": {
                "product_version": evidence.product_version,
                "sha": evidence.implementation_sha,
            },
            "authoritative_hash": evidence.authoritative_hash,
            "starting_accepted_hash": evidence.starting_accepted_hash,
            "provider": evidence.provider,
            "model": evidence.model,
            "prompt_versions": evidence.prompt_versions,
            "schema_versions": evidence.schema_versions,
            "configured_bounds": evidence.configured_bounds,
            "iterations": [_jsonable(item) for item in evidence.iterations],
            "status": evidence.status,
            "stop_reason": evidence.stop_reason,
            "failure_code": evidence.failure_code,
            "final_accepted_hash": evidence.final_accepted_hash,
            "best_accepted_hash": evidence.best_accepted_hash,
            "starting_layout_fingerprint": evidence.starting_layout_fingerprint,
            "final_layout_fingerprint": evidence.final_layout_fingerprint,
            "model_calls_made": evidence.model_calls_made,
            "model_call_limit": evidence.model_call_limit,
        }
        _write_json(temp_dir / "manifest.json", manifest)
        _write_text(
            temp_dir / "summary.md",
            _build_session_summary(root, evidence),
        )
        _fsync_directory(temp_dir)
        temp_dir.replace(final_dir)
        _fsync_directory(sessions_root)
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
        target_path = target / f"{prefix}{suffix}"
        shutil.copyfile(source, target_path)
        _fsync_file(target_path)


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


def _build_session_summary(root: Path, evidence: SessionEvidenceInputs) -> str:
    lines = [
        "# Schematic refinement session summary",
        "",
        f"- Session: `{evidence.session_id}`",
        f"- Status: `{evidence.status}`",
        f"- Stop reason: `{evidence.stop_reason}`",
        f"- Starting accepted hash: `{evidence.starting_accepted_hash}`",
        f"- Final/best accepted hash: `{evidence.best_accepted_hash}`",
        f"- Provider/model: `{evidence.provider}` / `{evidence.model}`",
        f"- Model calls: {evidence.model_calls_made}/{evidence.model_call_limit}",
        "",
        "## Starting metrics",
        "",
    ]
    starting_metrics = _first_iteration_json(root, evidence.iterations, "metrics_before.json")
    if isinstance(starting_metrics, dict):
        for name, value in sorted(starting_metrics.items()):
            if name not in {"schema_version", "schematic_hash"} and isinstance(
                value, (int, float)
            ):
                lines.append(f"- {name}: {value}")
    else:
        lines.append("- No completed iteration metrics were available.")

    lines.extend(["", "## Iterations", ""])
    for reference in evidence.iterations:
        lines.append(f"### {reference.iteration_id}")
        lines.append("")
        lines.append(f"- Result: `{reference.status}` / `{reference.code}`")
        if reference.candidate_hash is not None:
            lines.append(f"- Candidate hash: `{reference.candidate_hash}`")
        bundle = _iteration_bundle(root, reference)
        if bundle is None:
            lines.append("- Evidence bundle: not available")
            lines.append("")
            continue

        critic = _read_json(bundle / "critic.json")
        issues = critic.get("issues", []) if isinstance(critic, dict) else []
        categories = sorted(
            {
                str(issue.get("category"))
                for issue in issues
                if isinstance(issue, dict) and issue.get("category")
            }
        )
        lines.append(
            f"- Critic issues: {len(issues)}"
            + (f" ({', '.join(categories)})" if categories else "")
        )

        operations = _read_json(bundle / "operations.json")
        operation_rows = operations.get("results", []) if isinstance(operations, dict) else []
        applied = sum(
            isinstance(item, dict) and item.get("status") == "applied" for item in operation_rows
        )
        rejected = sum(
            isinstance(item, dict) and item.get("status") == "rejected" for item in operation_rows
        )
        lines.append(
            f"- Operations: {len(operation_rows)} total, {applied} applied, {rejected} rejected"
        )

        electrical = _read_json(bundle / "electrical.json")
        electrical_status = (
            str(electrical.get("status", "unknown")) if isinstance(electrical, dict) else "unknown"
        )
        lines.append(f"- Electrical status: `{electrical_status}`")

        before = _read_json(bundle / "metrics_before.json")
        after = _read_json(bundle / "metrics_after.json")
        deltas = _numeric_metric_deltas(before, after)
        if deltas:
            lines.append(
                "- Metric deltas: "
                + ", ".join(f"{name}={delta:+g}" for name, delta in sorted(deltas.items()))
            )
        else:
            lines.append("- Metric deltas: none")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _first_iteration_json(
    root: Path,
    references: tuple[SessionIterationEvidenceReference, ...],
    name: str,
) -> object | None:
    for reference in references:
        bundle = _iteration_bundle(root, reference)
        if bundle is not None:
            return _read_json(bundle / name)
    return None


def _iteration_bundle(
    root: Path,
    reference: SessionIterationEvidenceReference,
) -> Path | None:
    if reference.evidence_directory is None:
        return None
    _validate_identifier(reference.evidence_directory)
    bundle = root / reference.evidence_directory
    if not bundle.is_dir():
        raise UserError(
            "Referenced iteration evidence bundle is missing.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"iteration_id": reference.iteration_id},
        )
    manifest = bundle / "manifest.json"
    if (
        reference.evidence_manifest_sha256 is None
        or not manifest.is_file()
        or _sha256_file(manifest) != reference.evidence_manifest_sha256
    ):
        raise UserError(
            "Referenced iteration evidence manifest hash does not match.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"iteration_id": reference.iteration_id},
        )
    return bundle


def _numeric_metric_deltas(before: object, after: object) -> dict[str, float]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {}
    result: dict[str, float] = {}
    for name, before_value in before.items():
        after_value = after.get(name)
        if (
            name not in {"schema_version", "schematic_hash"}
            and isinstance(before_value, (int, float))
            and not isinstance(before_value, bool)
            and isinstance(after_value, (int, float))
            and not isinstance(after_value, bool)
        ):
            delta = float(after_value) - float(before_value)
            if delta != 0:
                result[str(name)] = delta
    return result


def _validate_session_evidence(evidence: SessionEvidenceInputs) -> None:
    for name, value in (
        ("authoritative_hash", evidence.authoritative_hash),
        ("starting_accepted_hash", evidence.starting_accepted_hash),
        ("final_accepted_hash", evidence.final_accepted_hash),
        ("best_accepted_hash", evidence.best_accepted_hash),
        ("starting_layout_fingerprint", evidence.starting_layout_fingerprint),
        ("final_layout_fingerprint", evidence.final_layout_fingerprint),
    ):
        _validate_sha256(name, value)
    if not evidence.provider or len(evidence.provider) > 64:
        raise UserError(
            "Invalid refinement provider identity.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    if not evidence.model or len(evidence.model) > 256:
        raise UserError(
            "Invalid refinement model identity.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    if evidence.status not in {"completed", "failed"}:
        raise UserError(
            "Invalid refinement session evidence status.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    if evidence.model_calls_made < 0 or evidence.model_call_limit < evidence.model_calls_made:
        raise UserError(
            "Invalid refinement session model-call accounting.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _write_json(path: Path, payload: object) -> None:
    _write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    )


def _write_text(path: Path, content: str) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UserError(
            "Unable to read referenced refinement evidence JSON.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"artifact": path.name},
        ) from exc


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise UserError(
            f"Invalid {name} SHA-256 digest.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
