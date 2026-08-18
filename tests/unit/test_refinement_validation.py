from __future__ import annotations

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement import validation
from kicad_pcb.refinement.validation import CandidateStructuralValidationReport


def test_erc_violation_count_accepts_legacy_top_level_report() -> None:
    assert validation._erc_violation_count({"violations": [{}, {}]}) == 2


def test_erc_violation_summary_treats_legacy_unclassified_entries_as_blocking() -> None:
    summary = validation._erc_violation_summary({"violations": [{}, {}]})

    assert summary.total_count == 2
    assert summary.blocking_count == 2
    assert summary.blocking_types == (
        validation._LEGACY_ERC_TYPE,
        validation._LEGACY_ERC_TYPE,
    )


def test_erc_violation_count_accepts_kicad_sheet_scoped_report() -> None:
    report = {
        "$schema": "https://schemas.kicad.org/erc.v1.json",
        "sheets": [
            {"path": "/", "violations": [{"severity": "warning", "type": "warning_a"}]},
            {"path": "/Power/", "violations": []},
            {"path": "/logic/", "violations": [{"severity": "error", "type": "error_b"}]},
        ],
    }

    summary = validation._erc_violation_summary(report)
    assert validation._erc_violation_count(report) == 2
    assert summary.total_count == 2
    assert summary.blocking_count == 1
    assert summary.blocking_types == ("error_b",)


def test_erc_warning_only_report_is_nonblocking_but_remains_counted() -> None:
    summary = validation._erc_violation_summary(
        {"sheets": [{"path": "/", "violations": [{"severity": "warning", "type": "warning_a"}]}]}
    )

    assert summary.total_count == 1
    assert summary.blocking_count == 0
    assert summary.blocking_types == ()


def test_erc_error_report_is_blocking() -> None:
    summary = validation._erc_violation_summary(
        {"sheets": [{"path": "/", "violations": [{"severity": "error", "type": "error_a"}]}]}
    )

    assert summary.total_count == 1
    assert summary.blocking_count == 1
    assert summary.blocking_types == ("error_a",)


def test_erc_excluded_error_is_nonblocking_but_remains_counted() -> None:
    summary = validation._erc_violation_summary(
        {
            "sheets": [
                {
                    "path": "/",
                    "violations": [
                        {"severity": "error", "type": "error_a", "excluded": True},
                    ],
                },
            ],
        },
    )

    assert summary.total_count == 1
    assert summary.blocking_count == 0
    assert summary.blocking_types == ()


def test_structural_status_allows_only_preexisting_blocking_finding_types() -> None:
    baseline = CandidateStructuralValidationReport(
        "1.0",
        "failed",
        ("LINT_A",),
        2,
        1,
        ("erc_a",),
    )

    assert (
        validation._structural_status(
            lint_errors=("LINT_A",),
            blocking_erc_types=("erc_a",),
            baseline=baseline,
        )
        == "passed"
    )
    assert (
        validation._structural_status(
            lint_errors=("LINT_A", "LINT_B"),
            blocking_erc_types=("erc_a",),
            baseline=baseline,
        )
        == "failed"
    )
    assert (
        validation._structural_status(
            lint_errors=("LINT_A",),
            blocking_erc_types=("erc_b",),
            baseline=baseline,
        )
        == "failed"
    )


def test_structural_status_preserves_multiplicity_for_regression_detection() -> None:
    baseline = CandidateStructuralValidationReport(
        "1.0",
        "failed",
        (),
        1,
        1,
        ("erc_a",),
    )

    assert (
        validation._structural_status(
            lint_errors=(),
            blocking_erc_types=("erc_a", "erc_a"),
            baseline=baseline,
        )
        == "failed"
    )


@pytest.mark.parametrize(
    "report",
    [
        None,
        {},
        {"violations": {}},
        {"violations": [None]},
        {"violations": [{"severity": 1}]},
        {"violations": [{"severity": "error", "excluded": "yes"}]},
        {"violations": [{"severity": "error", "type": 1}]},
        {"sheets": {}},
        {"sheets": [None]},
        {"sheets": [{"violations": "bad"}]},
        {"sheets": [{"violations": [None]}]},
        {"sheets": [{"violations": [{"severity": 1}]}]},
    ],
)
def test_erc_violation_count_rejects_malformed_reports(report: object) -> None:
    with pytest.raises(UserError) as exc_info:
        validation._erc_violation_summary(report)  # type: ignore[arg-type]

    assert exc_info.value.code == "REFINEMENT_ERC_INVALID_REPORT"
