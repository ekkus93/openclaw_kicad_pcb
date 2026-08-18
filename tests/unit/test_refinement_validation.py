from __future__ import annotations

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement import validation


def test_erc_violation_count_accepts_legacy_top_level_report() -> None:
    assert validation._erc_violation_count({"violations": [{}, {}]}) == 2


def test_erc_violation_summary_treats_legacy_unclassified_entries_as_blocking() -> None:
    summary = validation._erc_violation_summary({"violations": [{}, {}]})

    assert summary.total_count == 2
    assert summary.blocking_count == 2


def test_erc_violation_count_accepts_kicad_sheet_scoped_report() -> None:
    report = {
        "$schema": "https://schemas.kicad.org/erc.v1.json",
        "sheets": [
            {"path": "/", "violations": [{"severity": "warning"}]},
            {"path": "/Power/", "violations": []},
            {"path": "/logic/", "violations": [{"severity": "error"}]},
        ],
    }

    summary = validation._erc_violation_summary(report)
    assert validation._erc_violation_count(report) == 2
    assert summary.total_count == 2
    assert summary.blocking_count == 1


def test_erc_warning_only_report_is_nonblocking_but_remains_counted() -> None:
    summary = validation._erc_violation_summary(
        {"sheets": [{"path": "/", "violations": [{"severity": "warning"}]}]}
    )

    assert summary.total_count == 1
    assert summary.blocking_count == 0


def test_erc_error_report_is_blocking() -> None:
    summary = validation._erc_violation_summary(
        {"sheets": [{"path": "/", "violations": [{"severity": "error"}]}]}
    )

    assert summary.total_count == 1
    assert summary.blocking_count == 1


@pytest.mark.parametrize(
    "report",
    [
        None,
        {},
        {"violations": {}},
        {"violations": [None]},
        {"violations": [{"severity": 1}]},
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
