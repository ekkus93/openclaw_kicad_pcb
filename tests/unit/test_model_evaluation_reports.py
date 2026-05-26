from __future__ import annotations

import json
from pathlib import Path

from kicad_pcb.evaluation.electrical import ElectricalEquivalenceReport, ElectricalMismatch
from kicad_pcb.evaluation.reports import (
    ActionableFailure,
    EvaluationReport,
    _build_actionable_failures,
    _result_status,
    _total_score,
    write_evaluation_artifacts,
)
from kicad_pcb.evaluation.scoring import IntrinsicQualityReport
from kicad_pcb.evaluation.similarity import LayoutSimilarityReport


def test_write_evaluation_artifacts_emits_json_and_markdown(tmp_path: Path) -> None:
    report = EvaluationReport(
        schema_version="1.0",
        fixture_id="fixture-1",
        source_file_name="fixture-1.kicad_sch",
        result="fail",
        total_score=0.0,
        electrical_equivalence=ElectricalEquivalenceReport(
            status="failed",
            mismatches=(ElectricalMismatch(field="net_names", expected="A", actual="B"),),
        ),
        intrinsic_quality=IntrinsicQualityReport(
            score=65.0,
            sub_scores={"validity": 20.0},
            reasons=("routing_simplicity: wire stub ratio is too high.",),
        ),
        source_similarity=LayoutSimilarityReport(
            score=50.0,
            sub_scores={"role_counts": 10.0},
            reasons=("role_counts: generated symbol-role counts diverge from the source fixture.",),
        ),
        generated_artifacts={"schematic_path": "generated.kicad_sch"},
        actionable_failures=(
            ActionableFailure(
                rule="electrical_equivalence",
                severity="high",
                message="Generated connectivity differs.",
                suggested_files=("src/kicad_pcb/router.py",),
            ),
        ),
    )

    write_evaluation_artifacts(out_dir=tmp_path, report=report)

    payload = json.loads((tmp_path / "evaluation_report.json").read_text(encoding="utf-8"))
    assert payload["fixture_id"] == "fixture-1"
    assert payload["source_file_name"] == "fixture-1.kicad_sch"
    assert payload["electrical_equivalence"]["status"] == "failed"

    markdown = (tmp_path / "actionable_failures.md").read_text(encoding="utf-8")
    assert "Source file" in markdown
    assert "## Sub-scores" in markdown
    assert "Do not special-case this fixture." in markdown
    assert "src/kicad_pcb/router.py" in markdown

    first = (tmp_path / "evaluation_report.json").read_text(encoding="utf-8")
    write_evaluation_artifacts(out_dir=tmp_path, report=report)
    rewritten = (tmp_path / "evaluation_report.json").read_text(encoding="utf-8")
    assert rewritten == first


def test_failure_score_and_ordering_follow_electrical_failure() -> None:
    electrical = ElectricalEquivalenceReport(
        status="failed",
        mismatches=(ElectricalMismatch(field="net_names", expected="A", actual="B"),),
    )
    intrinsic = IntrinsicQualityReport(
        score=92.0,
        sub_scores={"validity": 20.0},
        reasons=("routing_simplicity: wire stub ratio is too high.",),
    )
    similarity = LayoutSimilarityReport(
        score=88.0,
        sub_scores={"role_counts": 25.0},
        reasons=("role_counts: generated symbol-role counts diverge from the source fixture.",),
    )

    failures = _build_actionable_failures(
        electrical=electrical,
        intrinsic=intrinsic,
        similarity=similarity,
    )

    assert failures[0].rule == "electrical_equivalence"
    assert failures[0].severity == "high"
    assert _total_score(electrical=electrical, intrinsic=intrinsic, similarity=similarity) == 0.0
    assert _result_status(electrical=electrical, actionable_failures=failures) == "fail"
