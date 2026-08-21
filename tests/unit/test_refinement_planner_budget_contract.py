from __future__ import annotations

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.planner import RepairPlanResponse, validate_repair_plan
from kicad_pcb.refinement.vision_context import VisionObjectMap


def _context() -> VisionObjectMap:
    return VisionObjectMap(
        schema_version="1.0",
        source_schematic_hash="a" * 64,
        render_png_hash="b" * 64,
        sheet_id="1",
        page_mm=(297.0, 210.0),
        image_px=(2970, 2100),
        components=(),
        pins=(),
        wires=(),
        labels=(),
        junctions=(),
        nets=(),
        deterministic_metrics={},
    )


def _critic() -> CriticResponse:
    return CriticResponse.model_validate(
        {
            "source_schematic_hash": "a" * 64,
            "render_png_hash": "b" * 64,
            "issues": [],
        }
    )


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


def test_over_budget_plan_is_normalized_before_executor_validation() -> None:
    plan = RepairPlanResponse.model_validate(
        {
            "iteration_id": "iter-1",
            "source_schematic_hash": "a" * 64,
            "operations": [_operation(index) for index in range(5)],
        }
    )

    with pytest.raises(UserError) as exc_info:
        validate_repair_plan(
            plan,
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )

    assert exc_info.value.code == "REFINEMENT_PLAN_INVALID"
    assert exc_info.value.details == {"count": 5, "max_operations": 4}
