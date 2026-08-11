"""Strict schema and semantic binding for the vision schematic critic."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kicad_pcb.errors import UserError

from .vision_context import VisionObjectMap

CriticCategory = Literal[
    "signal_flow",
    "functional_grouping",
    "component_alignment",
    "component_spacing",
    "component_orientation",
    "wire_crossing",
    "wire_length_bends",
    "label_readability",
    "power_organization",
    "repeated_block_consistency",
    "visual_hierarchy",
    "whitespace_crowding",
    "ambiguous_junction",
]
CriticSeverity = Literal["info", "warning", "error"]


class _Strict(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, str_strip_whitespace=True, allow_inf_nan=False
    )


class CriticRubric(_Strict):
    signal_flow: float | None = Field(default=None, ge=0, le=10)
    functional_grouping: float | None = Field(default=None, ge=0, le=10)
    wire_readability: float | None = Field(default=None, ge=0, le=10)
    overall_readability: float | None = Field(default=None, ge=0, le=10)


class CriticIssue(_Strict):
    issue_id: str = Field(min_length=1, max_length=128)
    category: CriticCategory
    severity: CriticSeverity
    confidence: float = Field(ge=0, le=1)
    affected_object_ids: tuple[str, ...] = Field(default=(), max_length=32)
    observation: str = Field(min_length=1, max_length=1200)
    desired_outcome: str = Field(min_length=1, max_length=1200)
    evidence: str = Field(min_length=1, max_length=1200)
    constraints: tuple[str, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def unique_objects(self) -> CriticIssue:
        if len(self.affected_object_ids) != len(set(self.affected_object_ids)):
            raise ValueError("affected_object_ids must be unique")
        return self


class CriticResponse(_Strict):
    schema_version: Literal["1.0"] = "1.0"
    source_schematic_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    render_png_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    issues: tuple[CriticIssue, ...] = Field(default=(), max_length=64)
    rubric: CriticRubric | None = None

    @model_validator(mode="after")
    def unique_issue_ids(self) -> CriticResponse:
        ids = [issue.issue_id for issue in self.issues]
        if len(ids) != len(set(ids)):
            raise ValueError("issue_id values must be unique")
        return self


def validate_critic_response(response: CriticResponse, context: VisionObjectMap) -> CriticResponse:
    if (
        response.source_schematic_hash != context.source_schematic_hash
        or response.render_png_hash != context.render_png_hash
    ):
        raise UserError("Critic response is stale for current render.", code="REFINEMENT_STALE")
    known = context.object_ids
    unknown = sorted(
        {
            object_id
            for issue in response.issues
            for object_id in issue.affected_object_ids
            if object_id not in known
        }
    )
    if unknown:
        raise UserError(
            "Critic referenced unknown schematic objects.",
            code="REFINEMENT_CRITIC_INVALID_REFERENCE",
            details={"unknown_object_ids": unknown},
        )
    return response
