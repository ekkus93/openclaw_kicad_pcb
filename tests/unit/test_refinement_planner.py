from __future__ import annotations

import pytest
from pydantic import ValidationError

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.planner import RepairPlanResponse, validate_repair_plan
from kicad_pcb.refinement.vision_context import (
    VisionComponentObject,
    VisionLabelObject,
    VisionNetObject,
    VisionObjectMap,
    VisionWireObject,
)


def _context() -> VisionObjectMap:
    return VisionObjectMap(
        schema_version="1.0",
        source_schematic_hash="a" * 64,
        render_png_hash="b" * 64,
        sheet_id="1",
        page_mm=(297.0, 210.0),
        image_px=(2970, 2100),
        components=(
            VisionComponentObject(
                "component:r1", "r1", "R1", "1", "Device:R", "10k", 10, 10, 0, 100, 100
            ),
            VisionComponentObject(
                "component:u1a",
                "u1a",
                "U1",
                "1",
                "Amplifier_Operational:NE5532",
                "NE5532",
                20,
                20,
                0,
                200,
                200,
            ),
            VisionComponentObject(
                "component:u1b",
                "u1b",
                "U1",
                "2",
                "Amplifier_Operational:NE5532",
                "NE5532",
                30,
                20,
                0,
                300,
                200,
            ),
        ),
        pins=(),
        wires=(VisionWireObject("wire:w1", "w1", ((10, 10), (20, 10)), ((100, 100), (200, 100))),),
        labels=(VisionLabelObject("label:l1", "l1", "label", "SIG", 10, 10, 100, 100),),
        junctions=(),
        nets=(VisionNetObject("net:SIG", "SIG", ("pin:R1:1:1",)),),
        deterministic_metrics={},
    )


def _critic() -> CriticResponse:
    return CriticResponse.model_validate(
        {
            "source_schematic_hash": "a" * 64,
            "render_png_hash": "b" * 64,
            "issues": [
                {
                    "issue_id": "i1",
                    "category": "component_alignment",
                    "severity": "warning",
                    "confidence": 0.9,
                    "affected_object_ids": ["component:r1"],
                    "observation": "R1 is misaligned.",
                    "desired_outcome": "Align R1.",
                    "evidence": "Visible offset.",
                }
            ],
        }
    )


def _plan(
    operation_type: str = "move_component", arguments: dict[str, object] | None = None
) -> RepairPlanResponse:
    return RepairPlanResponse.model_validate(
        {
            "iteration_id": "iter-1",
            "source_schematic_hash": "a" * 64,
            "operations": [
                {
                    "operation_id": "op-1",
                    "issue_ids": ["i1"],
                    "expected_visual_benefit": "Improve alignment.",
                    "operation_type": operation_type,
                    "arguments": arguments
                    or {"target": {"ref": "R1", "unit": "1"}, "dx_mm": 1.27, "dy_mm": 0.0},
                }
            ],
        }
    )


def test_valid_plan_is_translated_only_to_registered_operation_envelopes() -> None:
    result = validate_repair_plan(
        _plan(),
        critic=_critic(),
        context=_context(),
        expected_iteration_id="iter-1",
        max_operations=4,
    )
    assert result.addressed_issue_ids == ("i1",)
    assert result.operation_payloads[0]["operation_type"] == "move_component"
    assert result.operation_payloads[0]["source_schematic_hash"] == "a" * 64


def test_plan_rejects_stale_iteration_or_schematic() -> None:
    stale = _plan().model_copy(update={"iteration_id": "iter-old"})
    with pytest.raises(UserError, match="stale"):
        validate_repair_plan(
            stale,
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )


def test_plan_rejects_unknown_critic_issue() -> None:
    payload = _plan().model_dump()
    payload["operations"][0]["issue_ids"] = ["invented"]
    with pytest.raises(UserError, match="unknown critic issue"):
        validate_repair_plan(
            RepairPlanResponse.model_validate(payload),
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )


def test_plan_rejects_unsupported_or_semantic_operation() -> None:
    with pytest.raises(UserError, match="Unsupported layout operation"):
        validate_repair_plan(
            _plan("replace_symbol", {"ref": "R1", "symbol": "Device:C"}),
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )


def test_plan_rejects_unknown_and_ambiguous_component_targets() -> None:
    unknown = _plan(arguments={"target": {"ref": "X99", "unit": "1"}, "dx_mm": 1.27, "dy_mm": 0.0})
    with pytest.raises(UserError, match="unknown component unit"):
        validate_repair_plan(
            unknown,
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )

    ambiguous = _plan(arguments={"target": {"ref": "U1"}, "dx_mm": 1.27, "dy_mm": 0.0})
    with pytest.raises(UserError, match="ambiguous"):
        validate_repair_plan(
            ambiguous,
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=4,
        )


def test_plan_rejects_unknown_label_wire_and_net() -> None:
    cases = [
        _plan("move_label", {"label_uuid": "missing", "x_mm": 10.16, "y_mm": 10.16}),
        _plan(
            "remove_redundant_wire_bend",
            {"wire_uuid": "missing", "expected_points_mm": [[10, 10], [15, 10], [20, 10]]},
        ),
        _plan(
            "shorten_wire_path",
            {"wire_uuid": "w1", "net_name": "MISSING", "expected_points_mm": [[10, 10], [20, 10]]},
        ),
    ]
    for plan in cases:
        with pytest.raises(UserError):
            validate_repair_plan(
                plan,
                critic=_critic(),
                context=_context(),
                expected_iteration_id="iter-1",
                max_operations=4,
            )


def test_plan_operation_budget_and_duplicate_ids_fail_closed() -> None:
    payload = _plan().model_dump()
    payload["operations"] = payload["operations"] * 2
    with pytest.raises(ValidationError, match="operation_id values must be unique"):
        RepairPlanResponse.model_validate(payload)

    plan = _plan()
    with pytest.raises(UserError, match="exceeds configured maximum"):
        validate_repair_plan(
            plan,
            critic=_critic(),
            context=_context(),
            expected_iteration_id="iter-1",
            max_operations=0,
        )
