from __future__ import annotations

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement import validation


def test_erc_violation_count_accepts_legacy_top_level_report() -> None:
    assert validation._erc_violation_count({"violations": [{}, {}]}) == 2


def test_erc_violation_count_accepts_kicad_sheet_scoped_report() -> None:
    report = {
        "$schema": "https://schemas.kicad.org/erc.v1.json",
        "sheets": [
            {"path": "/", "violations": [{"severity": "warning"}]},
            {"path": "/Power/", "violations": []},
            {"path": "/logic/", "violations": [{"severity": "error"}]},
        ],
    }

    assert validation._erc_violation_count(report) == 2


@pytest.mark.parametrize(
    "report",
    [
        None,
        {},
        {"violations": {}},
        {"violations": [None]},
        {"sheets": {}},
        {"sheets": [None]},
        {"sheets": [{"violations": "bad"}]},
        {"sheets": [{"violations": [None]}]},
    ],
)
def test_erc_violation_count_rejects_malformed_reports(report: object) -> None:
    with pytest.raises(UserError) as exc_info:
        validation._erc_violation_count(report)  # type: ignore[arg-type]

    assert exc_info.value.code == "REFINEMENT_ERC_INVALID_REPORT"
