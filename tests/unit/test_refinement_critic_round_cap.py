from __future__ import annotations

import pytest
from pydantic import ValidationError

from kicad_pcb.refinement.critic import MAX_CRITIC_ISSUES_PER_ROUND, CriticResponse


def _issue(index: int) -> dict[str, object]:
    return {
        "issue_id": f"i{index}",
        "category": "component_alignment",
        "severity": "warning",
        "confidence": 0.9,
        "affected_object_ids": [],
        "observation": "A high-priority layout defect is visible.",
        "desired_outcome": "Improve the layout in this round.",
        "evidence": "The rendered geometry shows the defect.",
        "constraints": [],
    }


def _payload(issue_count: int) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "source_schematic_hash": "a" * 64,
        "render_png_hash": "b" * 64,
        "issues": [_issue(index) for index in range(1, issue_count + 1)],
    }


def test_critic_round_schema_caps_issue_count() -> None:
    schema = CriticResponse.model_json_schema()

    assert MAX_CRITIC_ISSUES_PER_ROUND == 6
    assert schema["properties"]["issues"]["maxItems"] == MAX_CRITIC_ISSUES_PER_ROUND


def test_critic_round_rejects_more_than_issue_cap() -> None:
    with pytest.raises(ValidationError, match="at most 6"):
        CriticResponse.model_validate(_payload(MAX_CRITIC_ISSUES_PER_ROUND + 1))
