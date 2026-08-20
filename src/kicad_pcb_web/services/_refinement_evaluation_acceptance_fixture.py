"""Fixture-level checks for Phase N3 evidence acceptance."""

from __future__ import annotations

from pathlib import Path

from ._refinement_evaluation_acceptance_artifacts import (
    _validate_render,
    require_analysis_binding as _require_analysis_binding,
    validate_apply_once as _validate_apply_once,
    validate_refine as _validate_refine,
)
from ._refinement_evaluation_acceptance_support import (
    read_json as _read_json,
    require_equal as _require_equal,
    require_file as _require_file,
    require_mapping as _require_mapping,
    sha256 as _sha256,
)


def _validated_fixture_manifest(
    bundle: Path,
    fixture_id: str,
    result: dict[str, object],
    source_fixture: dict[str, object],
    baseline_fixture: dict[str, object],
) -> dict[str, object]:
    _require_equal(result.get("bundle"), fixture_id, f"Summary bundle mismatch: {fixture_id}")
    manifest = _read_json(bundle / "manifest.json", f"{fixture_id} manifest")
    _require_equal(manifest.get("phase"), "N3", f"Invalid Phase N3 manifest: {fixture_id}")
    _require_equal(
        manifest.get("fixture_id"),
        fixture_id,
        f"Fixture binding mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("isolation_policy"),
        "each mode starts from identical baseline schematic bytes",
        f"Invalid mode-isolation policy: {fixture_id}",
    )
    _require_equal(
        manifest.get("source_fixture_id"),
        source_fixture.get("source_fixture_id"),
        f"Source fixture binding mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("categories"),
        source_fixture.get("categories"),
        f"Fixture category binding mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("known_visual_defects"),
        source_fixture.get("known_visual_defects"),
        f"Known-defect binding mismatch: {fixture_id}",
    )
    _require_equal(
        baseline_fixture.get("source_fixture_id"),
        source_fixture.get("source_fixture_id"),
        f"Baseline source fixture mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("baseline_schematic_sha256"),
        result.get("baseline_hash"),
        f"Baseline hash binding mismatch: {fixture_id}",
    )
    _require_equal(
        result.get("baseline_hash"),
        baseline_fixture.get("source_schematic_sha256"),
        f"Baseline expectation hash mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("final_accepted_hash"),
        result.get("final_accepted_hash"),
        f"Final accepted hash binding mismatch: {fixture_id}",
    )
    _require_equal(
        manifest.get("final_electrical_status"),
        "passed",
        f"Final electrical invariance did not pass: {fixture_id}",
    )
    _require_equal(
        manifest.get("stop_reason"),
        result.get("refine_stop_reason"),
        f"Refine stop-reason binding mismatch: {fixture_id}",
    )
    baseline_path = bundle / "baseline" / "accepted.kicad_sch"
    if not baseline_path.is_file() or _sha256(baseline_path) != result.get("baseline_hash"):
        raise ValueError(f"Baseline schematic bytes do not match summary: {fixture_id}")
    return manifest


def validate_fixture(
    root: Path,
    fixture_id: str,
    result: dict[str, object],
    source_fixture: dict[str, object],
    baseline_fixture: dict[str, object],
) -> None:
    bundle = (root / fixture_id).resolve()
    if not bundle.is_dir() or root not in bundle.parents:
        raise ValueError(f"Missing or unsafe Phase N3 evidence bundle: {fixture_id}")
    manifest = _validated_fixture_manifest(
        bundle, fixture_id, result, source_fixture, baseline_fixture
    )

    analyze = _require_mapping(manifest.get("analyze"), f"{fixture_id} analyze")
    plan = _require_mapping(manifest.get("plan"), f"{fixture_id} plan")
    apply_once = _require_mapping(manifest.get("apply_once"), f"{fixture_id} apply_once")
    refine = _require_mapping(manifest.get("refine"), f"{fixture_id} refine")
    before_after = _require_mapping(manifest.get("before_after"), f"{fixture_id} before_after")

    analyze_result_path = _require_file(
        bundle, analyze.get("result"), f"{fixture_id} analyze result"
    )
    plan_result_path = _require_file(bundle, plan.get("result"), f"{fixture_id} plan result")
    analyze_result = _read_json(analyze_result_path, f"{fixture_id} analyze result")
    plan_result = _read_json(plan_result_path, f"{fixture_id} plan result")
    _require_analysis_binding(analyze_result, result.get("baseline_hash"), fixture_id, "analyze")
    _require_equal(
        analyze_result.get("metrics"),
        baseline_fixture.get("metrics"),
        f"Analyze metrics do not match N2 baseline: {fixture_id}",
    )
    plan_analysis = _require_mapping(plan_result.get("analysis"), f"{fixture_id} plan analysis")
    _require_analysis_binding(plan_analysis, result.get("baseline_hash"), fixture_id, "plan")
    _require_equal(
        plan_analysis.get("metrics"),
        baseline_fixture.get("metrics"),
        f"Plan metrics do not match N2 baseline: {fixture_id}",
    )

    before_render = _require_mapping(
        before_after.get("before_render"), f"{fixture_id} before render"
    )
    after_render = _require_mapping(before_after.get("after_render"), f"{fixture_id} after render")
    analyze_render = _require_mapping(analyze.get("render"), f"{fixture_id} analyze render")
    plan_render = _require_mapping(plan.get("render"), f"{fixture_id} plan render")
    refine_render = _require_mapping(refine.get("after_render"), f"{fixture_id} refine render")
    analyze_result_render = _require_mapping(
        analyze_result.get("render"), f"{fixture_id} analyze result render"
    )
    plan_result_render = _require_mapping(
        plan_analysis.get("render"), f"{fixture_id} plan result render"
    )
    if before_render != analyze_render or analyze_render != analyze_result_render:
        raise ValueError(f"Before/analyze render binding mismatch: {fixture_id}")
    if plan_render != plan_result_render:
        raise ValueError(f"Plan render binding mismatch: {fixture_id}")
    if after_render != refine_render:
        raise ValueError(f"After-render binding mismatch: {fixture_id}")
    _validate_render(bundle, before_render, result.get("baseline_hash"), fixture_id, "before")
    _validate_render(bundle, plan_render, result.get("baseline_hash"), fixture_id, "plan")
    _validate_render(bundle, after_render, result.get("final_accepted_hash"), fixture_id, "after")

    operations_path = _require_file(bundle, manifest.get("operations"), f"{fixture_id} operations")
    operations = _read_json(operations_path, f"{fixture_id} operations")
    if not isinstance(operations.get("apply_once"), dict) or not isinstance(
        operations.get("refine"), list
    ):
        raise ValueError(f"Invalid operation record shape: {fixture_id}")

    _validate_apply_once(bundle, fixture_id, result, apply_once, baseline_fixture)
    _validate_refine(bundle, fixture_id, result, refine, baseline_fixture)

