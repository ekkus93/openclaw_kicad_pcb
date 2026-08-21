"""Strict repair planner schema constrained to registered deterministic operations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kicad_pcb.errors import UserError

from .critic import CriticResponse
from .operations import (
    AlignComponentsArgs,
    DistributeComponentsArgs,
    MoveComponentArgs,
    MoveComponentGroupArgs,
    MoveLabelArgs,
    MovePowerSymbolArgs,
    RerouteExistingNetOrthogonalArgs,
    RotateComponentArgs,
    ShortenWirePathArgs,
    registered_operation_schemas,
    validate_operation_envelopes,
)
from .vision_context import VisionObjectMap


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, allow_inf_nan=False
    )


class _PlannedOperationBase(_Strict):
    operation_id: str = Field(min_length=1, max_length=128)
    issue_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    expected_visual_benefit: str = Field(min_length=1, max_length=1000)


class _MoveComponentOperation(_PlannedOperationBase):
    operation_type: Literal["move_component"]
    arguments: MoveComponentArgs


class _RotateComponentOperation(_PlannedOperationBase):
    operation_type: Literal["rotate_component"]
    arguments: RotateComponentArgs


class _MoveLabelOperation(_PlannedOperationBase):
    operation_type: Literal["move_label"]
    arguments: MoveLabelArgs


class _MovePowerSymbolOperation(_PlannedOperationBase):
    operation_type: Literal["move_power_symbol"]
    arguments: MovePowerSymbolArgs


class _AlignComponentsOperation(_PlannedOperationBase):
    operation_type: Literal["align_components"]
    arguments: AlignComponentsArgs


class _DistributeComponentsOperation(_PlannedOperationBase):
    operation_type: Literal["distribute_components"]
    arguments: DistributeComponentsArgs


class _MoveComponentGroupOperation(_PlannedOperationBase):
    operation_type: Literal["move_component_group"]
    arguments: MoveComponentGroupArgs


class _ShortenWirePathOperation(_PlannedOperationBase):
    operation_type: Literal["shorten_wire_path"]
    arguments: ShortenWirePathArgs


class _RerouteExistingNetOrthogonalOperation(_PlannedOperationBase):
    operation_type: Literal["reroute_existing_net_orthogonal"]
    arguments: RerouteExistingNetOrthogonalArgs


PlannedOperation = Annotated[
    _MoveComponentOperation
    | _RotateComponentOperation
    | _MoveLabelOperation
    | _MovePowerSymbolOperation
    | _AlignComponentsOperation
    | _DistributeComponentsOperation
    | _MoveComponentGroupOperation
    | _ShortenWirePathOperation
    | _RerouteExistingNetOrthogonalOperation,
    Field(discriminator="operation_type"),
]


MODEL_PLANNABLE_OPERATION_TYPES = frozenset(
    {
        "move_component",
        "rotate_component",
        "move_label",
        "move_power_symbol",
        "align_components",
        "distribute_components",
        "move_component_group",
        "shorten_wire_path",
        "reroute_existing_net_orthogonal",
    }
)


def model_plannable_operation_schemas() -> dict[str, dict[str, object]]:
    """Return operations whose exact preconditions exist in the model-visible context."""
    return {
        name: schema
        for name, schema in registered_operation_schemas().items()
        if name in MODEL_PLANNABLE_OPERATION_TYPES
    }


class RepairPlanResponse(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    iteration_id: str = Field(min_length=1, max_length=128)
    source_schematic_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    operations: tuple[PlannedOperation, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_operation_ids(self) -> RepairPlanResponse:
        ids = [op.operation_id for op in self.operations]
        if len(ids) != len(set(ids)):
            raise ValueError("operation_id values must be unique")
        return self


class ValidatedRepairPlan(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    iteration_id: str
    source_schematic_hash: str
    operation_payloads: tuple[dict[str, object], ...]
    addressed_issue_ids: tuple[str, ...]


def validate_repair_plan(
    response: RepairPlanResponse,
    *,
    critic: CriticResponse,
    context: VisionObjectMap,
    expected_iteration_id: str,
    max_operations: int,
) -> ValidatedRepairPlan:
    if (
        response.iteration_id != expected_iteration_id
        or response.source_schematic_hash != context.source_schematic_hash
    ):
        raise UserError(
            "Repair plan is stale for current iteration/schematic.", code="REFINEMENT_STALE"
        )
    if len(response.operations) > max_operations:
        raise UserError(
            "Repair plan exceeds configured operation budget.",
            code="REFINEMENT_PLAN_INVALID",
            details={"count": len(response.operations), "max_operations": max_operations},
        )
    issue_ids = {issue.issue_id for issue in critic.issues}
    payloads: list[dict[str, object]] = []
    addressed: set[str] = set()
    for operation in response.operations:
        unknown = sorted(set(operation.issue_ids) - issue_ids)
        if unknown:
            raise UserError(
                "Repair plan references unknown critic issue.",
                code="REFINEMENT_PLAN_INVALID_REFERENCE",
                details={"unknown_issue_ids": unknown},
            )
        addressed.update(operation.issue_ids)
        payloads.append(
            {
                "schema_version": "1.0",
                "operation_id": operation.operation_id,
                "source_schematic_hash": response.source_schematic_hash,
                "operation_type": operation.operation_type,
                "arguments": operation.arguments.model_dump(mode="json"),
            }
        )
    try:
        validate_operation_envelopes(
            payloads,
            expected_source_hash=context.source_schematic_hash,
            max_operations=max_operations,
        )
    except ValidationError as exc:
        raise UserError(
            "Repair plan contains invalid operation arguments.",
            code="REFINEMENT_PLAN_INVALID",
            details={"error_type": type(exc).__name__},
        ) from exc
    _validate_object_targets(payloads, context)
    return ValidatedRepairPlan(
        iteration_id=response.iteration_id,
        source_schematic_hash=response.source_schematic_hash,
        operation_payloads=tuple(payloads),
        addressed_issue_ids=tuple(sorted(addressed)),
    )


def _validate_object_targets(payloads: list[dict[str, object]], context: VisionObjectMap) -> None:
    refs = {(component.ref, component.unit) for component in context.components}
    label_uuids = {label.uuid for label in context.labels}
    wire_uuids = {wire.uuid for wire in context.wires}
    net_names = {net.name for net in context.nets}
    for payload in payloads:
        operation_type = str(payload["operation_type"])
        args = payload["arguments"]
        assert isinstance(args, dict)
        if operation_type in {"move_component", "rotate_component", "move_power_symbol"}:
            target = args.get("target")
            if isinstance(target, dict):
                ref = str(target.get("ref", ""))
                unit = target.get("unit")
                if unit is None:
                    if sum(item[0] == ref for item in refs) != 1:
                        raise UserError(
                            "Planner component ref is ambiguous.",
                            code="REFINEMENT_PLAN_INVALID_REFERENCE",
                        )
                elif (ref, str(unit)) not in refs:
                    raise UserError(
                        "Planner referenced unknown component unit.",
                        code="REFINEMENT_PLAN_INVALID_REFERENCE",
                    )
        if operation_type == "move_label" and str(args.get("label_uuid", "")) not in label_uuids:
            raise UserError(
                "Planner referenced unknown label UUID.", code="REFINEMENT_PLAN_INVALID_REFERENCE"
            )
        if operation_type in {
            "remove_redundant_wire_bend",
            "shorten_wire_path",
            "reroute_existing_net_orthogonal",
        }:
            if str(args.get("wire_uuid", "")) not in wire_uuids:
                raise UserError(
                    "Planner referenced unknown wire UUID.",
                    code="REFINEMENT_PLAN_INVALID_REFERENCE",
                )
            if (
                operation_type != "remove_redundant_wire_bend"
                and str(args.get("net_name", "")) not in net_names
            ):
                raise UserError(
                    "Planner referenced unknown net name.", code="REFINEMENT_PLAN_INVALID_REFERENCE"
                )
