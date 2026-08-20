"""Artifact-level checks for Phase N3 evidence acceptance."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from ._refinement_evaluation_acceptance_support import (
    read_json as _read_json,
    require_equal as _require_equal,
    require_file as _require_file,
    require_mapping as _require_mapping,
    sha256 as _sha256,
)


def validate_apply_once(
    bundle: Path,
    fixture_id: str,
    result: dict[str, object],
    apply_once: dict[str, object],
    baseline_fixture: dict[str, object],
) -> None:
    apply_result = _read_json(
        _require_file(bundle, apply_once.get("result"), f"{fixture_id} apply_once result"),
        f"{fixture_id} apply_once result",
    )
    electrical = _read_json(
        _require_file(
            bundle, apply_once.get("final_electrical"), f"{fixture_id} apply_once electrical"
        ),
        f"{fixture_id} apply_once electrical",
    )
    metrics = _read_json(
        _require_file(bundle, apply_once.get("final_metrics"), f"{fixture_id} apply_once metrics"),
        f"{fixture_id} apply_once metrics",
    )
    schematic = _require_file(
        bundle, apply_once.get("final_schematic"), f"{fixture_id} apply_once schematic"
    )
    final_hash = _sha256(schematic)
    _require_equal(
        apply_once.get("status"),
        result.get("apply_once_status"),
        f"Apply status mismatch: {fixture_id}",
    )
    _require_equal(
        apply_once.get("code"), result.get("apply_once_code"), f"Apply code mismatch: {fixture_id}"
    )
    _require_equal(
        apply_result.get("status"),
        apply_once.get("status"),
        f"Apply result mismatch: {fixture_id}",
    )
    _require_equal(
        apply_result.get("code"),
        apply_once.get("code"),
        f"Apply result code mismatch: {fixture_id}",
    )
    _require_equal(
        apply_result.get("accepted_hash_before"),
        result.get("baseline_hash"),
        f"Apply starting hash mismatch: {fixture_id}",
    )
    _require_equal(
        apply_result.get("accepted_hash_after"),
        final_hash,
        f"Apply schematic hash mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("status"),
        "passed",
        f"Apply electrical status mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("candidate_schematic_hash"),
        final_hash,
        f"Apply electrical hash mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("accepted_schematic_hash"),
        result.get("baseline_hash"),
        f"Apply electrical baseline mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("authoritative_hash"),
        baseline_fixture.get("authoritative_fingerprint_sha256"),
        f"Apply authoritative fingerprint mismatch: {fixture_id}",
    )
    _require_equal(
        metrics.get("schematic_hash"),
        final_hash,
        f"Apply metric hash mismatch: {fixture_id}",
    )
    apply_render = _require_mapping(apply_once.get("after_render"), f"{fixture_id} apply render")
    _validate_render(bundle, apply_render, final_hash, fixture_id, "apply_once")


def validate_refine(
    bundle: Path,
    fixture_id: str,
    result: dict[str, object],
    refine: dict[str, object],
    baseline_fixture: dict[str, object],
) -> None:
    refine_result = _read_json(
        _require_file(bundle, refine.get("result"), f"{fixture_id} refine result"),
        f"{fixture_id} refine result",
    )
    electrical = _read_json(
        _require_file(bundle, refine.get("final_electrical"), f"{fixture_id} refine electrical"),
        f"{fixture_id} refine electrical",
    )
    metrics = _read_json(
        _require_file(bundle, refine.get("final_metrics"), f"{fixture_id} refine metrics"),
        f"{fixture_id} refine metrics",
    )
    schematic = _require_file(
        bundle, refine.get("final_schematic"), f"{fixture_id} refine schematic"
    )
    final_hash = _sha256(schematic)
    _require_equal(
        refine.get("status"),
        result.get("refine_status"),
        f"Refine status mismatch: {fixture_id}",
    )
    _require_equal(
        refine.get("stop_reason"),
        result.get("refine_stop_reason"),
        f"Refine stop mismatch: {fixture_id}",
    )
    _require_equal(
        refine_result.get("status"),
        refine.get("status"),
        f"Refine result mismatch: {fixture_id}",
    )
    _require_equal(
        refine_result.get("stop_reason"),
        refine.get("stop_reason"),
        f"Refine result stop mismatch: {fixture_id}",
    )
    _require_equal(
        refine_result.get("starting_hash"),
        result.get("baseline_hash"),
        f"Refine starting hash mismatch: {fixture_id}",
    )
    _require_equal(
        refine_result.get("final_accepted_hash"),
        final_hash,
        f"Refine schematic hash mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("status"),
        "passed",
        f"Refine electrical status mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("candidate_schematic_hash"),
        final_hash,
        f"Refine electrical hash mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("accepted_schematic_hash"),
        result.get("baseline_hash"),
        f"Refine electrical baseline mismatch: {fixture_id}",
    )
    _require_equal(
        electrical.get("authoritative_hash"),
        baseline_fixture.get("authoritative_fingerprint_sha256"),
        f"Refine authoritative fingerprint mismatch: {fixture_id}",
    )
    _require_equal(
        metrics.get("schematic_hash"),
        final_hash,
        f"Refine metric hash mismatch: {fixture_id}",
    )
    _require_equal(
        final_hash,
        result.get("final_accepted_hash"),
        f"Refine summary hash mismatch: {fixture_id}",
    )


def require_analysis_binding(
    payload: Mapping[str, object], expected_hash: object, fixture_id: str, stage: str
) -> None:
    _require_equal(
        payload.get("accepted_hash"),
        expected_hash,
        f"{stage} baseline binding mismatch: {fixture_id}",
    )
    metrics = _require_mapping(payload.get("metrics"), f"{fixture_id} {stage} metrics")
    _require_equal(
        metrics.get("schematic_hash"), expected_hash, f"{stage} metric hash mismatch: {fixture_id}"
    )


def _validate_render(
    bundle: Path,
    render: Mapping[str, object],
    expected_schematic_hash: object,
    fixture_id: str,
    label: str,
) -> None:
    png = _require_file(bundle, render.get("png"), f"{fixture_id} {label} render PNG")
    svg = _require_file(bundle, render.get("svg"), f"{fixture_id} {label} render SVG")
    _require_equal(
        render.get("schematic_hash"),
        expected_schematic_hash,
        f"{label} render schematic mismatch: {fixture_id}",
    )
    _require_equal(
        render.get("png_hash"),
        _sha256(png),
        f"{label} render PNG hash mismatch: {fixture_id}",
    )
    _require_equal(
        render.get("svg_hash"),
        _sha256(svg),
        f"{label} render SVG hash mismatch: {fixture_id}",
    )
    regions = render.get("review_regions")
    if not isinstance(regions, list):
        raise ValueError(f"Invalid review-region record: {fixture_id} {label}")
    for index, item in enumerate(regions):
        region = _require_mapping(item, f"{fixture_id} {label} review region {index}")
        region_png = _require_file(
            bundle, region.get("png"), f"{fixture_id} {label} review region PNG {index}"
        )
        region_svg = _require_file(
            bundle, region.get("svg"), f"{fixture_id} {label} review region SVG {index}"
        )
        _require_equal(
            region.get("png_hash"),
            _sha256(region_png),
            f"Review-region PNG hash mismatch: {fixture_id}",
        )
        _require_equal(
            region.get("svg_hash"),
            _sha256(region_svg),
            f"Review-region SVG hash mismatch: {fixture_id}",
        )
