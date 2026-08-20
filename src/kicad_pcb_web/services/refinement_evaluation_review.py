"""Objective Phase N4 review-packet extraction from accepted Phase N3 evidence."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from .refinement_evaluation_acceptance import PhaseN3AcceptanceResult


def build_phase_n4_review_packet(
    evidence_root: Path,
    acceptance: PhaseN3AcceptanceResult,
) -> dict[str, object]:
    """Extract non-subjective evidence needed for the Phase N4 human review."""

    root = evidence_root.resolve()
    fixtures = [_fixture_review_record(root, fixture_id) for fixture_id in acceptance.fixture_ids]
    return {
        "schema_version": "1.0",
        "phase": "N4",
        "status": "review_pending",
        "n3_acceptance": acceptance.to_dict(),
        "fixtures": fixtures,
    }


def _fixture_review_record(root: Path, fixture_id: str) -> dict[str, object]:
    bundle = root / fixture_id
    manifest = _read_json(bundle / "manifest.json")
    analyze = _mapping(manifest.get("analyze"), f"{fixture_id} analyze")
    plan = _mapping(manifest.get("plan"), f"{fixture_id} plan")
    refine = _mapping(manifest.get("refine"), f"{fixture_id} refine")
    before_after = _mapping(manifest.get("before_after"), f"{fixture_id} before_after")

    analyze_result = _read_json(bundle / _relative_path(analyze.get("result"), fixture_id))
    plan_result = _read_json(bundle / _relative_path(plan.get("result"), fixture_id))
    refine_result = _read_json(bundle / _relative_path(refine.get("result"), fixture_id))
    operations = _read_json(bundle / _relative_path(manifest.get("operations"), fixture_id))
    final_metrics = _read_json(
        bundle / _relative_path(refine.get("final_metrics"), fixture_id)
    )
    baseline_metrics = _mapping(analyze_result.get("metrics"), f"{fixture_id} baseline metrics")
    before_render = _mapping(
        before_after.get("before_render"), f"{fixture_id} before render"
    )
    after_render = _mapping(before_after.get("after_render"), f"{fixture_id} after render")

    return {
        "fixture_id": fixture_id,
        "categories": manifest.get("categories"),
        "known_visual_defects": manifest.get("known_visual_defects"),
        "before_render_png": _review_path(fixture_id, before_render.get("png")),
        "after_render_png": _review_path(fixture_id, after_render.get("png")),
        "baseline_metrics": baseline_metrics,
        "final_metrics": final_metrics,
        "metric_deltas": _metric_deltas(baseline_metrics, final_metrics),
        "critic": analyze_result.get("critic"),
        "plan": plan_result.get("plan"),
        "apply_once_operations": operations.get("apply_once"),
        "refine_operations": operations.get("refine"),
        "refine_result": _refine_summary(refine_result),
        "stop_reason": manifest.get("stop_reason"),
        "disposition": "PENDING",
        "human_review_reasons": "PENDING",
        "metric_human_disagreement": "PENDING",
        "critic_failure_or_hallucination": "PENDING",
        "operation_vocabulary_gap": "PENDING",
    }


def _metric_deltas(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> dict[str, float]:
    deltas: dict[str, float] = {}
    for name, before_value in before.items():
        if name in {"schema_version", "schematic_hash"}:
            continue
        after_value = after.get(name)
        if (
            isinstance(before_value, (int, float))
            and not isinstance(before_value, bool)
            and isinstance(after_value, (int, float))
            and not isinstance(after_value, bool)
        ):
            deltas[name] = float(after_value) - float(before_value)
    return deltas


def _refine_summary(payload: Mapping[str, object]) -> dict[str, object]:
    names = (
        "status",
        "stop_reason",
        "rounds_attempted",
        "accepted_rounds",
        "rejected_rounds",
        "accepted_operations",
        "model_calls_made",
        "model_call_limit",
        "iterations",
    )
    return {name: payload.get(name) for name in names}


def _review_path(fixture_id: str, value: object) -> str:
    return str(Path(fixture_id) / _relative_path(value, fixture_id))


def _relative_path(value: object, fixture_id: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ValueError(f"Phase N4 review path is missing for {fixture_id}")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"Unsafe Phase N4 review path for {fixture_id}: {value}")
    return path


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return _mapping(payload, str(path))


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value
