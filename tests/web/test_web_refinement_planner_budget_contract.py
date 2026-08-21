from __future__ import annotations

import pytest
from pydantic import ValidationError

from kicad_pcb_web.services.refinement_llm import _repair_plan_response_model


def _operation(index: int) -> dict[str, object]:
    return {
        "operation_id": f"op-{index}",
        "issue_ids": ["i1"],
        "expected_visual_benefit": "Improve alignment.",
        "operation_type": "move_component",
        "arguments": {
            "target": {"ref": "R1", "unit": "1"},
            "dx_mm": 1.27,
            "dy_mm": 0.0,
        },
    }


def _payload(operation_count: int) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "iteration_id": "iter-1",
        "source_schematic_hash": "a" * 64,
        "operations": [_operation(index) for index in range(operation_count)],
    }


def test_provider_repair_plan_schema_uses_runtime_operation_cap() -> None:
    response_model = _repair_plan_response_model(4)

    schema = response_model.model_json_schema()
    assert schema["properties"]["operations"]["maxItems"] == 4
    assert len(response_model.model_validate(_payload(4)).operations) == 4

    with pytest.raises(ValidationError) as exc_info:
        response_model.model_validate(_payload(5))

    assert any(error["type"] == "too_long" for error in exc_info.value.errors())
