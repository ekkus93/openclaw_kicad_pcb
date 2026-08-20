from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kicad_pcb_web.services.refinement_evaluation_acceptance import (
    PhaseN3AcceptanceExpectation,
    validate_phase_n3_evidence,
)
from kicad_pcb_web.services.refinement_evaluation_review import build_phase_n4_review_packet

_IMPLEMENTATION_SHA = "8" * 40
_PROVIDER = "ollama"
_MODEL = "qwen3-vl:8b-instruct-n3-64k"
_FIXTURE_ID = "fixture-one"
_SOURCE_FIXTURE_ID = "source-fixture"
_AUTHORITATIVE_HASH = "a" * 64


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _render(bundle: Path, relative_dir: str, schematic_hash: str) -> dict[str, object]:
    render_dir = bundle / relative_dir
    render_dir.mkdir(parents=True, exist_ok=True)
    png = render_dir / "schematic.png"
    svg = render_dir / "schematic.svg"
    png.write_bytes(f"png:{relative_dir}".encode())
    svg.write_text('<svg viewBox="0 0 10 10"/>\n', encoding="utf-8")
    return {
        "schematic_hash": schematic_hash,
        "png": str(png.relative_to(bundle)),
        "png_hash": _sha(png),
        "svg": str(svg.relative_to(bundle)),
        "svg_hash": _sha(svg),
        "review_regions": [],
    }


def _build_evidence(tmp_path: Path) -> tuple[Path, PhaseN3AcceptanceExpectation]:
    evidence = tmp_path / "evidence"
    bundle = evidence / _FIXTURE_ID
    baseline = bundle / "baseline" / "accepted.kicad_sch"
    baseline.parent.mkdir(parents=True)
    baseline.write_bytes(b"baseline schematic\n")
    baseline_hash = _sha(baseline)

    apply_schematic = bundle / "apply_once" / "final" / "accepted.kicad_sch"
    apply_schematic.parent.mkdir(parents=True)
    apply_schematic.write_bytes(b"apply schematic\n")
    apply_hash = _sha(apply_schematic)

    refine_schematic = bundle / "refine" / "final" / "accepted.kicad_sch"
    refine_schematic.parent.mkdir(parents=True)
    refine_schematic.write_bytes(b"refine schematic\n")
    refine_hash = _sha(refine_schematic)

    analyze_render = _render(bundle, "analyze/render", baseline_hash)
    plan_render = _render(bundle, "plan/render", baseline_hash)
    apply_render = _render(bundle, "apply_once/final/render", apply_hash)
    refine_render = _render(bundle, "refine/final/render", refine_hash)

    analyze_result = {
        "accepted_hash": baseline_hash,
        "metrics": {"schematic_hash": baseline_hash, "bend_count": 5, "wire_segment_count": 10},
        "critic": {"issues": []},
        "render": analyze_render,
    }
    plan_result = {
        "analysis": {
            "accepted_hash": baseline_hash,
            "metrics": {"schematic_hash": baseline_hash, "bend_count": 5, "wire_segment_count": 10},
            "render": plan_render,
        },
        "plan": {"operations": []},
    }
    apply_result = {
        "status": "accepted",
        "code": "REFINEMENT_ACCEPTED",
        "accepted_hash_before": baseline_hash,
        "accepted_hash_after": apply_hash,
    }
    refine_result = {
        "status": "completed",
        "stop_reason": "REFINEMENT_STOP_MAX_ROUNDS",
        "starting_hash": baseline_hash,
        "final_accepted_hash": refine_hash,
        "iterations": [],
    }
    _write_json(bundle / "analyze" / "result.json", analyze_result)
    _write_json(bundle / "plan" / "result.json", plan_result)
    _write_json(bundle / "apply_once" / "result.json", apply_result)
    _write_json(
        bundle / "apply_once" / "final" / "electrical.json",
        {
            "status": "passed",
            "authoritative_hash": _AUTHORITATIVE_HASH,
            "accepted_schematic_hash": baseline_hash,
            "candidate_schematic_hash": apply_hash,
        },
    )
    _write_json(
        bundle / "apply_once" / "final" / "metrics.json",
        {"schematic_hash": apply_hash},
    )
    _write_json(bundle / "refine" / "result.json", refine_result)
    _write_json(
        bundle / "refine" / "final" / "electrical.json",
        {
            "status": "passed",
            "authoritative_hash": _AUTHORITATIVE_HASH,
            "accepted_schematic_hash": baseline_hash,
            "candidate_schematic_hash": refine_hash,
        },
    )
    _write_json(
        bundle / "refine" / "final" / "metrics.json",
        {"schematic_hash": refine_hash, "bend_count": 3, "wire_segment_count": 9},
    )
    _write_json(bundle / "operations.json", {"apply_once": {}, "refine": []})

    manifest = {
        "phase": "N3",
        "fixture_id": _FIXTURE_ID,
        "source_fixture_id": _SOURCE_FIXTURE_ID,
        "categories": ["crowded_layout"],
        "known_visual_defects": ["Dense layout."],
        "baseline_schematic_sha256": baseline_hash,
        "isolation_policy": "each mode starts from identical baseline schematic bytes",
        "analyze": {"result": "analyze/result.json", "render": analyze_render},
        "plan": {"result": "plan/result.json", "render": plan_render},
        "apply_once": {
            "result": "apply_once/result.json",
            "status": "accepted",
            "code": "REFINEMENT_ACCEPTED",
            "final_electrical": "apply_once/final/electrical.json",
            "final_metrics": "apply_once/final/metrics.json",
            "after_render": apply_render,
            "final_schematic": "apply_once/final/accepted.kicad_sch",
        },
        "refine": {
            "result": "refine/result.json",
            "status": "completed",
            "stop_reason": "REFINEMENT_STOP_MAX_ROUNDS",
            "final_electrical": "refine/final/electrical.json",
            "final_metrics": "refine/final/metrics.json",
            "after_render": refine_render,
            "final_schematic": "refine/final/accepted.kicad_sch",
        },
        "operations": "operations.json",
        "before_after": {
            "before_render": analyze_render,
            "after_render": refine_render,
        },
        "final_electrical_status": "passed",
        "final_accepted_hash": refine_hash,
        "stop_reason": "REFINEMENT_STOP_MAX_ROUNDS",
    }
    _write_json(bundle / "manifest.json", manifest)

    source_manifest = tmp_path / "manifest.json"
    baseline_expectations = tmp_path / "baseline_expectations.json"
    _write_json(
        source_manifest,
        {
            "fixtures": [
                {
                    "fixture_id": _FIXTURE_ID,
                    "source_fixture_id": _SOURCE_FIXTURE_ID,
                    "categories": ["crowded_layout"],
                    "known_visual_defects": ["Dense layout."],
                }
            ]
        },
    )
    _write_json(
        baseline_expectations,
        {
            "fixtures": [
                {
                    "fixture_id": _FIXTURE_ID,
                    "source_fixture_id": _SOURCE_FIXTURE_ID,
                    "source_schematic_sha256": baseline_hash,
                    "authoritative_fingerprint_sha256": _AUTHORITATIVE_HASH,
                    "metrics": analyze_result["metrics"],
                }
            ]
        },
    )

    summary = {
        "phase": "N3",
        "status": "completed",
        "implementation_sha": _IMPLEMENTATION_SHA,
        "provider": _PROVIDER,
        "model": _MODEL,
        "source_manifest_sha256": _sha(source_manifest),
        "baseline_expectations_sha256": _sha(baseline_expectations),
        "iteration_limits": {
            "max_critic_repairs": 0,
            "max_planner_repairs": 0,
            "max_operations": 4,
        },
        "loop_limits": {
            "max_rounds": 3,
            "max_operations_per_round": 4,
            "max_total_accepted_operations": 8,
            "max_candidate_rejections": 2,
            "max_critic_repairs": 0,
            "max_planner_repairs": 0,
        },
        "fixture_count": 1,
        "fixture_ids": [_FIXTURE_ID],
        "results": [
            {
                "fixture_id": _FIXTURE_ID,
                "bundle": _FIXTURE_ID,
                "baseline_hash": baseline_hash,
                "apply_once_status": "accepted",
                "apply_once_code": "REFINEMENT_ACCEPTED",
                "refine_status": "completed",
                "refine_stop_reason": "REFINEMENT_STOP_MAX_ROUNDS",
                "final_accepted_hash": refine_hash,
            }
        ],
    }
    _write_json(evidence / "summary.json", summary)

    expectation = PhaseN3AcceptanceExpectation(
        implementation_sha=_IMPLEMENTATION_SHA,
        provider=_PROVIDER,
        model=_MODEL,
        source_manifest=source_manifest,
        baseline_expectations=baseline_expectations,
        fixture_count=1,
    )
    return evidence, expectation


def test_phase_n3_acceptance_validates_external_and_internal_hash_bindings(
    tmp_path: Path,
) -> None:
    evidence, expectation = _build_evidence(tmp_path)

    result = validate_phase_n3_evidence(evidence, expectation)

    assert result.fixture_ids == (_FIXTURE_ID,)
    assert result.implementation_sha == _IMPLEMENTATION_SHA
    assert result.provider == _PROVIDER
    assert result.model == _MODEL
    assert result.to_dict()["status"] == "accepted"


def test_phase_n3_acceptance_rejects_stale_source_manifest(tmp_path: Path) -> None:
    evidence, expectation = _build_evidence(tmp_path)
    expectation.source_manifest.write_text('{"fixtures": []}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="source manifest hash mismatch"):
        validate_phase_n3_evidence(evidence, expectation)


def test_phase_n3_acceptance_rejects_tampered_render_bytes(tmp_path: Path) -> None:
    evidence, expectation = _build_evidence(tmp_path)
    render = evidence / _FIXTURE_ID / "analyze" / "render" / "schematic.png"
    render.write_bytes(b"tampered")

    with pytest.raises(ValueError, match="render PNG hash mismatch"):
        validate_phase_n3_evidence(evidence, expectation)


def test_phase_n3_acceptance_rejects_noncanonical_loop_bounds(tmp_path: Path) -> None:
    evidence, expectation = _build_evidence(tmp_path)
    summary_path = evidence / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["loop_limits"]["max_rounds"] = 4
    _write_json(summary_path, summary)

    with pytest.raises(ValueError, match="canonical experiment bounds"):
        validate_phase_n3_evidence(evidence, expectation)


def test_phase_n4_review_packet_extracts_objective_review_evidence(tmp_path: Path) -> None:
    evidence, expectation = _build_evidence(tmp_path)
    acceptance = validate_phase_n3_evidence(evidence, expectation)

    packet = build_phase_n4_review_packet(evidence, acceptance)

    assert packet["phase"] == "N4"
    assert packet["status"] == "review_pending"
    fixture = packet["fixtures"][0]
    assert fixture["fixture_id"] == _FIXTURE_ID
    assert fixture["before_render_png"].startswith(f"{_FIXTURE_ID}/")
    assert fixture["after_render_png"].startswith(f"{_FIXTURE_ID}/")
    assert fixture["metric_deltas"]["bend_count"] == -2.0
    assert fixture["metric_deltas"]["wire_segment_count"] == -1.0
    assert fixture["stop_reason"] == "REFINEMENT_STOP_MAX_ROUNDS"
    assert fixture["disposition"] == "PENDING"
