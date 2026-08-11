from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kicad_pcb.errors import UserError
from kicad_pcb.refinement.critic import CriticResponse
from kicad_pcb.refinement.vision_context import (
    VisionComponentObject,
    VisionNetObject,
    VisionObjectMap,
)
from kicad_pcb_web.errors import LlmInvalidStructuredOutputError
from kicad_pcb_web.services.llm import LlmCompletion, LlmRequest
from kicad_pcb_web.services.refinement_llm import (
    RefinementModelCallBudget,
    RepairPlannerOptions,
    refinement_model_call_upper_bound,
    run_repair_planner,
    run_visual_critic,
)


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        return LlmCompletion(provider="fake", model="fake", content=self.responses.pop(0))


def _context(image: bytes) -> VisionObjectMap:
    return VisionObjectMap(
        schema_version="1.0",
        source_schematic_hash="a" * 64,
        render_png_hash=hashlib.sha256(image).hexdigest(),
        sheet_id="1",
        page_mm=(297.0, 210.0),
        image_px=(2970, 2100),
        components=(
            VisionComponentObject(
                "component:r1",
                "r1",
                "R1",
                "1",
                "Device:R",
                "10k",
                25.4,
                25.4,
                0,
                254.0,
                254.0,
            ),
        ),
        pins=(),
        wires=(),
        labels=(),
        junctions=(),
        nets=(VisionNetObject("net:SIG", "SIG", ("pin:R1:1:1",)),),
        deterministic_metrics={"component_overlap_count": 0},
    )


def _critic_payload(context: VisionObjectMap) -> str:
    return json.dumps(
        {
            "schema_version": "1.0",
            "source_schematic_hash": context.source_schematic_hash,
            "render_png_hash": context.render_png_hash,
            "issues": [
                {
                    "issue_id": "i1",
                    "category": "component_alignment",
                    "severity": "warning",
                    "confidence": 0.9,
                    "affected_object_ids": ["component:r1"],
                    "observation": "R1 is offset.",
                    "desired_outcome": "Align R1.",
                    "evidence": "Visible column offset.",
                }
            ],
        }
    )


def test_refinement_model_call_upper_bound_counts_logical_requests() -> None:
    assert (
        refinement_model_call_upper_bound(
            max_rounds=3,
            max_critic_repairs=2,
            max_planner_repairs=1,
        )
        == 15
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_rounds": 0, "max_critic_repairs": 0, "max_planner_repairs": 0},
        {"max_rounds": True, "max_critic_repairs": 0, "max_planner_repairs": 0},
        {"max_rounds": 1, "max_critic_repairs": -1, "max_planner_repairs": 0},
        {"max_rounds": 1, "max_critic_repairs": 0, "max_planner_repairs": 9},
    ],
)
def test_refinement_model_call_upper_bound_rejects_invalid_limits(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        refinement_model_call_upper_bound(**kwargs)  # type: ignore[arg-type]


def test_refinement_model_call_budget_blocks_dispatch_after_limit() -> None:
    delegate = _FakeClient(["ok"])
    budget = RefinementModelCallBudget(client=delegate, max_calls=1)
    request = LlmRequest(messages=[])

    completion = budget.complete(request)

    assert completion.content == "ok"
    assert budget.calls_made == 1
    assert len(delegate.requests) == 1
    with pytest.raises(UserError, match="budget exhausted") as exc_info:
        budget.complete(request)
    assert exc_info.value.code == "REFINEMENT_MODEL_CALL_BUDGET_EXCEEDED"
    assert len(delegate.requests) == 1


def test_repair_planner_options_reject_invalid_bounds() -> None:
    with pytest.raises(ValueError):
        RepairPlannerOptions(iteration_id="iter-1", max_repairs=-1, max_operations=4)
    with pytest.raises(ValueError):
        RepairPlannerOptions(iteration_id="iter-1", max_repairs=0, max_operations=0)
    with pytest.raises(ValueError):
        RepairPlannerOptions(iteration_id="iter-1", max_repairs=9, max_operations=4)
    with pytest.raises(ValueError):
        RepairPlannerOptions(iteration_id="iter-1", max_repairs=0, max_operations=33)


def test_visual_critic_sends_exact_bound_image_and_untrusted_data_instruction(
    tmp_path: Path,
) -> None:
    image = b"deterministic-render"
    path = tmp_path / "schematic.png"
    path.write_bytes(image)
    context = _context(image)
    client = _FakeClient([_critic_payload(context)])

    response = run_visual_critic(llm_client=client, context=context, image_path=path, max_repairs=0)

    assert response.issues[0].issue_id == "i1"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert len(request.images) == 1
    assert request.images[0].media_type == "image/png"
    assert "untrusted data" in request.messages[0].content
    assert "component:r1" in request.messages[1].content


def test_visual_critic_rejects_stale_image_before_model_call(tmp_path: Path) -> None:
    path = tmp_path / "schematic.png"
    path.write_bytes(b"different")
    client = _FakeClient([])
    with pytest.raises(UserError, match="render hash"):
        run_visual_critic(
            llm_client=client,
            context=_context(b"expected"),
            image_path=path,
            max_repairs=0,
        )
    assert client.requests == []


def test_visual_critic_uses_bounded_structured_output_repair(tmp_path: Path) -> None:
    image = b"render"
    path = tmp_path / "schematic.png"
    path.write_bytes(image)
    context = _context(image)
    client = _FakeClient(["not-json", _critic_payload(context)])

    response = run_visual_critic(llm_client=client, context=context, image_path=path, max_repairs=1)

    assert response.issues[0].issue_id == "i1"
    assert len(client.requests) == 2
    assert all(request.images for request in client.requests)


def test_visual_critic_never_exceeds_structured_repair_call_bound(tmp_path: Path) -> None:
    image = b"render"
    path = tmp_path / "schematic.png"
    path.write_bytes(image)
    context = _context(image)
    client = _FakeClient(["not-json", "still-not-json", "also-not-json"])

    with pytest.raises(LlmInvalidStructuredOutputError):
        run_visual_critic(llm_client=client, context=context, image_path=path, max_repairs=2)

    assert len(client.requests) == 3


def test_visual_critic_rejects_invalid_repair_bound_before_model_call(tmp_path: Path) -> None:
    image = b"render"
    path = tmp_path / "schematic.png"
    path.write_bytes(image)
    client = _FakeClient([])

    with pytest.raises(ValueError):
        run_visual_critic(
            llm_client=client,
            context=_context(image),
            image_path=path,
            max_repairs=-1,
        )

    assert client.requests == []


def test_visual_critic_rejects_semantically_unknown_object() -> None:
    image = b"render"
    context = _context(image)
    payload = json.loads(_critic_payload(context))
    payload["issues"][0]["affected_object_ids"] = ["component:invented"]
    path = Path("/tmp/refinement-critic-test.png")
    path.write_bytes(image)
    try:
        client = _FakeClient([json.dumps(payload)])
        with pytest.raises(UserError, match="unknown schematic objects"):
            run_visual_critic(llm_client=client, context=context, image_path=path, max_repairs=0)
    finally:
        path.unlink(missing_ok=True)


def test_repair_planner_emits_only_validated_registered_operations() -> None:
    image = b"render"
    context = _context(image)
    critic = CriticResponse.model_validate(json.loads(_critic_payload(context)))
    client = _FakeClient(
        [
            json.dumps(
                {
                    "schema_version": "1.0",
                    "iteration_id": "iter-1",
                    "source_schematic_hash": context.source_schematic_hash,
                    "operations": [
                        {
                            "operation_id": "op-1",
                            "issue_ids": ["i1"],
                            "expected_visual_benefit": "Align R1.",
                            "operation_type": "move_component",
                            "arguments": {
                                "target": {"ref": "R1", "unit": "1"},
                                "dx_mm": 1.27,
                                "dy_mm": 0.0,
                            },
                        }
                    ],
                }
            )
        ]
    )

    result = run_repair_planner(
        llm_client=client,
        context=context,
        critic=critic,
        options=RepairPlannerOptions(
            iteration_id="iter-1",
            max_repairs=0,
            max_operations=4,
        ),
    )

    assert result.operation_payloads[0]["operation_type"] == "move_component"
    assert not client.requests[0].images
    assert "registered_operation_schemas" in client.requests[0].messages[1].content


def test_repair_planner_does_not_approximate_unsupported_operation() -> None:
    image = b"render"
    context = _context(image)
    critic = CriticResponse.model_validate(json.loads(_critic_payload(context)))
    client = _FakeClient(
        [
            json.dumps(
                {
                    "schema_version": "1.0",
                    "iteration_id": "iter-1",
                    "source_schematic_hash": context.source_schematic_hash,
                    "operations": [
                        {
                            "operation_id": "op-1",
                            "issue_ids": ["i1"],
                            "expected_visual_benefit": "Replace it.",
                            "operation_type": "replace_symbol",
                            "arguments": {"ref": "R1", "symbol": "Device:C"},
                        }
                    ],
                }
            )
        ]
    )

    with pytest.raises(UserError, match="Unsupported layout operation"):
        run_repair_planner(
            llm_client=client,
            context=context,
            critic=critic,
            options=RepairPlannerOptions(
                iteration_id="iter-1",
                max_repairs=0,
                max_operations=4,
            ),
        )
