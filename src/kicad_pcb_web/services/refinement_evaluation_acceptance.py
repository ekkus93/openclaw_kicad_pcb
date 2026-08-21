"""Post-hoc acceptance validation for complete Phase N3 evidence bundles."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._refinement_evaluation_acceptance_fixture import (
    validate_fixture as _validate_fixture,
)
from ._refinement_evaluation_acceptance_support import (
    fixture_records as _fixture_records,
)
from ._refinement_evaluation_acceptance_support import (
    read_json as _read_json,
)
from ._refinement_evaluation_acceptance_support import (
    require_equal as _require_equal,
)
from ._refinement_evaluation_acceptance_support import (
    require_mapping as _require_mapping,
)
from ._refinement_evaluation_acceptance_support import (
    sha256 as _sha256,
)


@dataclass(frozen=True)
class PhaseN3AcceptanceExpectation:
    """External bindings required to accept one Phase N3 corpus artifact."""

    implementation_sha: str
    provider: str
    model: str
    source_manifest: Path
    baseline_expectations: Path
    fixture_count: int = 12


@dataclass(frozen=True)
class PhaseN3AcceptanceResult:
    """Immutable acceptance summary suitable for binding a Phase N4 review."""

    implementation_sha: str
    provider: str
    model: str
    source_manifest_sha256: str
    baseline_expectations_sha256: str
    fixture_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "status": "accepted",
            "phase": "N3",
            "implementation_sha": self.implementation_sha,
            "provider": self.provider,
            "model": self.model,
            "source_manifest_sha256": self.source_manifest_sha256,
            "baseline_expectations_sha256": self.baseline_expectations_sha256,
            "fixture_count": len(self.fixture_ids),
            "fixture_ids": list(self.fixture_ids),
        }


def validate_phase_n3_evidence(
    evidence_root: Path,
    expectation: PhaseN3AcceptanceExpectation,
) -> PhaseN3AcceptanceResult:
    """Require a complete, externally bound, hash-consistent Phase N3 artifact."""

    root = evidence_root.resolve()
    summary = _read_json(root / "summary.json", "Phase N3 summary")
    fixture_ids = summary.get("fixture_ids")
    results = summary.get("results")

    _require_equal(summary.get("phase"), "N3", "Phase N3 summary phase mismatch")
    _require_equal(summary.get("status"), "completed", "Phase N3 summary is not completed")
    _require_equal(
        summary.get("implementation_sha"),
        expectation.implementation_sha,
        "Phase N3 summary implementation SHA mismatch",
    )
    _require_equal(
        summary.get("provider"), expectation.provider, "Phase N3 summary provider mismatch"
    )
    _require_equal(summary.get("model"), expectation.model, "Phase N3 summary model mismatch")
    _require_equal(
        summary.get("iteration_limits"),
        {"max_critic_repairs": 1, "max_planner_repairs": 1, "max_operations": 4},
        "Phase N3 iteration limits do not match the canonical experiment bounds",
    )
    _require_equal(
        summary.get("loop_limits"),
        {
            "max_rounds": 3,
            "max_operations_per_round": 4,
            "max_total_accepted_operations": 8,
            "max_candidate_rejections": 2,
            "max_critic_repairs": 1,
            "max_planner_repairs": 1,
        },
        "Phase N3 loop limits do not match the canonical experiment bounds",
    )

    source_manifest_hash = _sha256(expectation.source_manifest)
    baseline_expectations_hash = _sha256(expectation.baseline_expectations)
    _require_equal(
        summary.get("source_manifest_sha256"),
        source_manifest_hash,
        "Phase N3 source manifest hash mismatch",
    )
    _require_equal(
        summary.get("baseline_expectations_sha256"),
        baseline_expectations_hash,
        "Phase N3 baseline expectations hash mismatch",
    )

    source_fixtures = _fixture_records(expectation.source_manifest, "Phase N3 source manifest")
    baseline_fixtures = _fixture_records(
        expectation.baseline_expectations, "Phase N3 baseline expectations"
    )
    expected_fixture_ids = tuple(item["fixture_id"] for item in source_fixtures)
    if len(expected_fixture_ids) != expectation.fixture_count:
        raise ValueError(
            "Expected source manifest fixture count does not match acceptance configuration"
        )
    if tuple(item["fixture_id"] for item in baseline_fixtures) != expected_fixture_ids:
        raise ValueError("Phase N3 baseline expectations do not match source fixture order")
    if summary.get("fixture_count") != expectation.fixture_count or not isinstance(
        fixture_ids, list
    ):
        raise ValueError("Phase N3 summary does not contain the expected fixture count")
    if tuple(fixture_ids) != expected_fixture_ids:
        raise ValueError("Phase N3 fixture IDs/order do not match the source manifest")
    if len(set(fixture_ids)) != expectation.fixture_count or any(
        not isinstance(item, str) or not item for item in fixture_ids
    ):
        raise ValueError("Phase N3 fixture IDs are missing, invalid, or duplicated")
    if not isinstance(results, list) or len(results) != expectation.fixture_count:
        raise ValueError("Phase N3 summary does not contain the expected result count")

    result_by_id: dict[str, dict[str, object]] = {}
    for item in results:
        result = _require_mapping(item, "Phase N3 result record")
        fixture_id = result.get("fixture_id")
        if not isinstance(fixture_id, str) or not fixture_id:
            raise ValueError("Phase N3 result record has an invalid fixture_id")
        if fixture_id in result_by_id:
            raise ValueError(f"Duplicate Phase N3 result record: {fixture_id}")
        result_by_id[fixture_id] = result
    if set(result_by_id) != set(fixture_ids):
        raise ValueError("Phase N3 result records do not match the fixture ID set")

    source_by_id = {item["fixture_id"]: item for item in source_fixtures}
    baseline_by_id = {item["fixture_id"]: item for item in baseline_fixtures}
    for fixture_id in fixture_ids:
        _validate_fixture(
            root,
            fixture_id,
            result_by_id[fixture_id],
            source_by_id[fixture_id],
            baseline_by_id[fixture_id],
        )

    return PhaseN3AcceptanceResult(
        implementation_sha=expectation.implementation_sha,
        provider=expectation.provider,
        model=expectation.model,
        source_manifest_sha256=source_manifest_hash,
        baseline_expectations_sha256=baseline_expectations_hash,
        fixture_ids=tuple(fixture_ids),
    )
