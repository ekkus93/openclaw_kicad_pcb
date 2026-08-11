"""Atomic, sanitized evidence bundles for schematic refinement iterations and sessions."""

from __future__ import annotations

import hashlib
import json
import math
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
RETENTION_POLICY_SCHEMA_VERSION = "1.0"
_ALLOWED_ITERATION_DISPOSITIONS = frozenset(
    {"accepted", "approved_for_promotion", "rejected", "no_op"}
)
_ALLOWED_ITERATION_STATUSES = frozenset({"accepted", "rejected", "no_op"})
_ALLOWED_SESSION_STATUSES = frozenset({"completed", "failed"})


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
class SessionIterationReferenceInputs:
    iteration_id: str
    status: str
    code: str
    accepted_hash_before: str
    accepted_hash_after: str
    candidate_hash: str | None
    candidate_layout_fingerprint: str | None
    evidence_dir: Path | None


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

    def __post_init__(self) -> None:
        _validate_identifier(self.iteration_id)
        if self.status not in _ALLOWED_ITERATION_STATUSES:
            raise UserError(
                "Invalid refinement iteration status.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
            )
        _validate_text("iteration code", self.code, maximum=128)
        _validate_sha256("accepted_hash_before", self.accepted_hash_before)
        _validate_sha256("accepted_hash_after", self.accepted_hash_after)
        _validate_optional_sha256("candidate_hash", self.candidate_hash)
        _validate_optional_sha256(
            "candidate_layout_fingerprint",
            self.candidate_layout_fingerprint,
        )
        if (self.evidence_directory is None) != (self.evidence_manifest_sha256 is None):
            raise UserError(
                "Iteration evidence directory and manifest hash must be present together.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
            )
        if self.evidence_directory is not None:
            _validate_identifier(self.evidence_directory)
            if self.evidence_directory != self.iteration_id:
                raise UserError(
                    "Iteration evidence directory does not match iteration id.",
                    code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
                )
            assert self.evidence_manifest_sha256 is not None
            _validate_sha256("evidence_manifest_sha256", self.evidence_manifest_sha256)


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

    _validate_iteration_evidence(evidence)
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
    evidence_root: Path,
    inputs: SessionIterationReferenceInputs,
) -> SessionIterationEvidenceReference:
    """Build a sanitized session reference to one already-published iteration bundle."""

    _validate_identifier(inputs.iteration_id)
    evidence_directory: str | None = None
    manifest_hash: str | None = None
    if inputs.evidence_dir is not None:
        root_resolved = evidence_root.resolve()
        evidence_resolved = inputs.evidence_dir.resolve()
        if evidence_resolved.parent != root_resolved:
            raise UserError(
                "Iteration evidence is outside the configured evidence root.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
            )
        _validate_identifier(evidence_resolved.name)
        if evidence_resolved.name != inputs.iteration_id:
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
        iteration_id=inputs.iteration_id,
        status=inputs.status,
        code=inputs.code,
        accepted_hash_before=inputs.accepted_hash_before,
        accepted_hash_after=inputs.accepted_hash_after,
        candidate_hash=inputs.candidate_hash,
        candidate_layout_fingerprint=inputs.candidate_layout_fingerprint,
        evidence_directory=evidence_directory,
        evidence_manifest_sha256=manifest_hash,
    )


def write_session_evidence_bundle(root: Path, evidence: SessionEvidenceInputs) -> Path:
    """Atomically publish one complete sanitized session manifest and human summary."""

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
            "retention_policy": _retention_policy(evidence),
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
        _write_text(temp_dir / "summary.md", _build_session_summary(root, evidence))
        _fsync_directory(temp_dir)
        temp_dir.replace(final_dir)
        _fsync_directory(sessions_root)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return final_dir


def _validate_iteration_evidence(evidence: IterationEvidenceInputs) -> None:
    _validate_identifier(evidence.iteration_id)
    _validate_sha256("accepted_hash_before", evidence.accepted_hash_before)
    _validate_sha256("authoritative_hash", evidence.authoritative_hash)
    _validate_optional_sha256("accepted_hash_after", evidence.accepted_hash_after)
    _validate_optional_sha256("candidate_hash", evidence.candidate_hash)
    _validate_optional_sha256(
        "candidate_layout_fingerprint",
        evidence.candidate_layout_fingerprint,
    )
    if evidence.disposition not in _ALLOWED_ITERATION_DISPOSITIONS:
        raise UserError(
            "Invalid refinement iteration disposition.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    _validate_text("reason_code", evidence.reason_code, maximum=128)


def _validate_session_evidence(evidence: SessionEvidenceInputs) -> None:
    _validate_identifier(evidence.session_id)
    for name, value in (
        ("authoritative_hash", evidence.authoritative_hash),
        ("starting_accepted_hash", evidence.starting_accepted_hash),
        ("final_accepted_hash", evidence.final_accepted_hash),
        ("best_accepted_hash", evidence.best_accepted_hash),
        ("starting_layout_fingerprint", evidence.starting_layout_fingerprint),
        ("final_layout_fingerprint", evidence.final_layout_fingerprint),
    ):
        _validate_sha256(name, value)
    _validate_text("provider", evidence.provider, maximum=64)
    _validate_text("model", evidence.model, maximum=256)
    if evidence.product_version is not None:
        _validate_text("product_version", evidence.product_version, maximum=128)
    if evidence.implementation_sha is not None:
        _validate_git_sha(evidence.implementation_sha)
    _validate_version_mapping("prompt_versions", evidence.prompt_versions)
    _validate_version_mapping("schema_versions", evidence.schema_versions)
    _validate_configured_bounds(evidence.configured_bounds)
    if evidence.status not in _ALLOWED_SESSION_STATUSES:
        raise UserError(
            "Invalid refinement session evidence status.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    _validate_text("stop_reason", evidence.stop_reason, maximum=128)
    if evidence.failure_code is not None:
        _validate_text("failure_code", evidence.failure_code, maximum=128)
    if (
        type(evidence.model_calls_made) is not int
        or type(evidence.model_call_limit) is not int
        or evidence.model_calls_made < 0
        or evidence.model_call_limit < 1
        or evidence.model_call_limit < evidence.model_calls_made
    ):
        raise UserError(
            "Invalid refinement session model-call accounting.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    if len(evidence.iterations) > evidence.configured_bounds["max_rounds"]:
        raise UserError(
            "Refinement session contains more iterations than its configured bound.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _validate_version_mapping(name: str, mapping: dict[str, str]) -> None:
    if not mapping:
        raise UserError(
            f"{name} must not be empty.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    for key, value in mapping.items():
        _validate_text(f"{name} key", key, maximum=64)
        _validate_text(f"{name} value", value, maximum=128)


def _validate_configured_bounds(bounds: dict[str, int]) -> None:
    required = {
        "max_rounds",
        "max_operations_per_round",
        "max_total_accepted_operations",
        "max_candidate_rejections",
        "max_critic_repairs",
        "max_planner_repairs",
    }
    if set(bounds) != required:
        raise UserError(
            "Refinement session configured bounds are incomplete or unexpected.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
    for name, value in bounds.items():
        if type(value) is not int or value < 0:
            raise UserError(
                "Refinement session configured bounds must be non-negative integers.",
                code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
                details={"bound": name},
            )
    if bounds["max_rounds"] < 1:
        raise UserError(
            "Refinement max_rounds must be positive in session evidence.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _retention_policy(evidence: SessionEvidenceInputs) -> dict[str, object]:
    return {
        "schema_version": RETENTION_POLICY_SCHEMA_VERSION,
        "maximum_iteration_bundles_per_session": evidence.configured_bounds["max_rounds"],
        "rejected_candidate_files": "not_retained_after_iteration",
        "post_edit_render_scratch": "not_retained_after_iteration",
        "durable_renders": "retained_only_inside_iteration_evidence",
        "raw_prompts": "not_retained",
        "raw_provider_payloads": "not_retained",
        "optional_debug_artifacts": "not_retained",
    }


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
    lines = _session_summary_header(evidence)
    _append_starting_metrics(lines, root, evidence)
    lines.extend(["", "## Iterations", ""])
    for reference in evidence.iterations:
        _append_iteration_summary(lines, root, reference)
    return "\n".join(lines).rstrip() + "\n"


def _session_summary_header(evidence: SessionEvidenceInputs) -> list[str]:
    return [
        "# Schematic refinement session summary",
        "",
        f"- Session: `{evidence.session_id}`",
        f"- Status: `{evidence.status}`",
        f"- Stop reason: `{evidence.stop_reason}`",
        f"- Starting accepted hash: `{evidence.starting_accepted_hash}`",
        f"- Final accepted hash: `{evidence.final_accepted_hash}`",
        f"- Best accepted hash: `{evidence.best_accepted_hash}`",
        f"- Provider/model: `{evidence.provider}` / `{evidence.model}`",
        f"- Model calls: {evidence.model_calls_made}/{evidence.model_call_limit}",
        "",
        "## Starting metrics",
        "",
    ]


def _append_starting_metrics(
    lines: list[str],
    root: Path,
    evidence: SessionEvidenceInputs,
) -> None:
    starting_metrics = _first_iteration_json(root, evidence.iterations, "metrics_before.json")
    if not isinstance(starting_metrics, dict):
        lines.append("- No completed iteration metrics were available.")
        return
    numeric_rows = [
        (name, value)
        for name, value in sorted(starting_metrics.items())
        if name not in {"schema_version", "schematic_hash"}
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    ]
    if not numeric_rows:
        lines.append("- No numeric starting metrics were available.")
        return
    lines.extend(f"- {name}: {value}" for name, value in numeric_rows)


def _append_iteration_summary(
    lines: list[str],
    root: Path,
    reference: SessionIterationEvidenceReference,
) -> None:
    lines.extend(
        [
            f"### {reference.iteration_id}",
            "",
            f"- Result: `{reference.status}` / `{reference.code}`",
        ]
    )
    if reference.candidate_hash is not None:
        lines.append(f"- Candidate hash: `{reference.candidate_hash}`")
    bundle = _iteration_bundle(root, reference)
    if bundle is None:
        lines.extend(["- Evidence bundle: not available", ""])
        return

    issues = _critic_issue_summary(bundle)
    lines.append(
        f"- Critic issues: {issues[0]}" + (f" ({', '.join(issues[1])})" if issues[1] else "")
    )
    total, applied, rejected = _operation_summary(bundle)
    lines.append(f"- Operations: {total} total, {applied} applied, {rejected} rejected")
    lines.append(f"- Electrical status: `{_electrical_status(bundle)}`")
    deltas = _numeric_metric_deltas(
        _read_json(bundle / "metrics_before.json"),
        _read_json(bundle / "metrics_after.json"),
    )
    lines.append(
        "- Metric deltas: "
        + (
            ", ".join(f"{name}={delta:+g}" for name, delta in sorted(deltas.items()))
            if deltas
            else "none"
        )
    )
    lines.append("")


def _critic_issue_summary(bundle: Path) -> tuple[int, list[str]]:
    critic = _read_json(bundle / "critic.json")
    issues = critic.get("issues", []) if isinstance(critic, dict) else []
    if not isinstance(issues, list):
        raise UserError(
            "Refinement critic evidence has invalid issue data.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
        )
    categories = sorted(
        {
            str(issue.get("category"))
            for issue in issues
            if isinstance(issue, dict) and issue.get("category")
        }
    )
    return len(issues), categories


def _operation_summary(bundle: Path) -> tuple[int, int, int]:
    operations = _read_json(bundle / "operations.json")
    rows = operations.get("results", []) if isinstance(operations, dict) else []
    if not isinstance(rows, list):
        raise UserError(
            "Refinement operation evidence has invalid result data.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
        )
    applied = sum(isinstance(item, dict) and item.get("status") == "applied" for item in rows)
    rejected = sum(isinstance(item, dict) and item.get("status") == "rejected" for item in rows)
    return len(rows), applied, rejected


def _electrical_status(bundle: Path) -> str:
    electrical = _read_json(bundle / "electrical.json")
    if not isinstance(electrical, dict):
        return "unknown"
    value = electrical.get("status", "unknown")
    return str(value)


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
    bundle = root / reference.evidence_directory
    if not bundle.is_dir():
        raise UserError(
            "Referenced iteration evidence bundle is missing.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"iteration_id": reference.iteration_id},
        )
    manifest_path = bundle / "manifest.json"
    if (
        reference.evidence_manifest_sha256 is None
        or not manifest_path.is_file()
        or _sha256_file(manifest_path) != reference.evidence_manifest_sha256
    ):
        raise UserError(
            "Referenced iteration evidence manifest hash does not match.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"iteration_id": reference.iteration_id},
        )
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or not _iteration_manifest_matches(reference, manifest):
        raise UserError(
            "Referenced iteration evidence manifest does not match session reference.",
            code="REFINEMENT_EVIDENCE_WRITE_FAILED",
            details={"iteration_id": reference.iteration_id},
        )
    return bundle


def _iteration_manifest_matches(
    reference: SessionIterationEvidenceReference,
    manifest: dict[str, object],
) -> bool:
    return (
        manifest.get("iteration_id") == reference.iteration_id
        and manifest.get("reason_code") == reference.code
        and manifest.get("accepted_hash_before") == reference.accepted_hash_before
        and manifest.get("accepted_hash_after") == reference.accepted_hash_after
        and manifest.get("candidate_hash") == reference.candidate_hash
        and manifest.get("candidate_layout_fingerprint")
        == reference.candidate_layout_fingerprint
    )


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
            if math.isfinite(delta) and delta != 0:
                result[str(name)] = delta
    return result


def _write_json(path: Path, payload: object) -> None:
    _write_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False) + "\n",
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
    if isinstance(value, float) and not math.isfinite(value):
        raise UserError(
            "Non-finite floats are forbidden in refinement JSON evidence.",
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
            "Invalid refinement evidence identifier.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _validate_text(name: str, value: str, *, maximum: int) -> None:
    if not value or len(value) > maximum or any(ord(ch) < 32 for ch in value):
        raise UserError(
            f"Invalid refinement evidence {name}.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _validate_sha256(name: str, value: str) -> None:
    if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise UserError(
            f"Invalid {name} SHA-256 digest.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )


def _validate_optional_sha256(name: str, value: str | None) -> None:
    if value is not None:
        _validate_sha256(name, value)


def _validate_git_sha(value: str) -> None:
    if len(value) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in value):
        raise UserError(
            "Invalid refinement implementation Git SHA.",
            code="REFINEMENT_EVIDENCE_UNSAFE_DATA",
        )
