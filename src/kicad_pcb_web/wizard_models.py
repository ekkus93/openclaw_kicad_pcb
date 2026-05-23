"""Wizard-specific domain and API models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

WizardStatus = Literal[
    "drafting_spec",
    "awaiting_user_clarification",
    "spec_ready_for_review",
    "spec_approved",
    "drafting_ir",
    "ir_needs_repair",
    "ir_ready_for_generation",
    "generation_started",
    "completed",
    "failed",
]

WizardMessageRole = Literal["user", "assistant"]
CircuitBlockType = Literal[
    "gain_stage",
    "voltage_divider",
    "filter_stage",
    "bias_network",
    "power_input",
    "output_driver",
    "timer_oscillator",
    "transistor_stage",
    "connector",
    "custom",
]


class CircuitRailSpec(BaseModel):
    """Named supply rail in the user-facing circuit specification."""

    name: str = Field(min_length=1)
    nominal_voltage: str | None = None
    description: str | None = None


class CircuitPortSpec(BaseModel):
    """Named input or output in the user-facing circuit specification."""

    name: str = Field(min_length=1)
    description: str | None = None
    signal_type: str | None = None


class CircuitBlockSpec(BaseModel):
    """One functional block in the user-facing circuit specification."""

    name: str = Field(min_length=1)
    block_type: CircuitBlockType
    summary: str = Field(min_length=1)
    required_components: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class CircuitPackagingPreferences(BaseModel):
    """Optional package and technology preferences for the design."""

    preferred_technology: Literal["SMD", "THT", "mixed"] | None = None
    preferred_resistor_package: str | None = None
    preferred_capacitor_package: str | None = None
    notes: list[str] = Field(default_factory=list)


class CircuitSpec(BaseModel):
    """User-approved, human-readable circuit specification."""

    project_name: str | None = None
    purpose: str = Field(min_length=1)
    supply_rails: list[CircuitRailSpec] = Field(default_factory=list)
    inputs: list[CircuitPortSpec] = Field(default_factory=list)
    outputs: list[CircuitPortSpec] = Field(default_factory=list)
    blocks: list[CircuitBlockSpec] = Field(default_factory=list)
    required_components: list[str] = Field(default_factory=list)
    packaging_preferences: CircuitPackagingPreferences | None = None
    constraints: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    unsupported_reasons: list[str] = Field(default_factory=list)

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        if len(stripped) > 120:
            raise ValueError("Project name must be 120 characters or fewer.")
        if "/" in stripped or "\\" in stripped:
            raise ValueError("Project name must not contain path separators.")
        return stripped


class WizardMessage(BaseModel):
    """One persisted wizard transcript message."""

    role: WizardMessageRole
    content: str = Field(min_length=1, max_length=8000)


class WizardIrValidation(BaseModel):
    """Validation summary for the current wizard IR draft."""

    valid: bool
    auto_fixed: bool = False
    component_count: int = 0
    net_count: int = 0
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    fixes_applied: list[str] = Field(default_factory=list)
    symbols_dirs_used: list[str] = Field(default_factory=list)
    error_message: str | None = None


class WizardSessionDetail(BaseModel):
    """Persisted wizard session state exposed by the API."""

    id: str
    status: WizardStatus
    created_at: str
    updated_at: str
    project_name: str | None = None
    symbols_dir: str | None = None
    llm_provider: str | None = None
    prompt_version: str | None = None
    messages: list[WizardMessage] = Field(default_factory=list)
    spec: CircuitSpec | None = None
    spec_approved: bool = False
    spec_approved_at: str | None = None
    ir_json: dict[str, Any] | None = None
    ir_validation: WizardIrValidation | None = None
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unsupported_reasons: list[str] = Field(default_factory=list)
    latest_job_id: str | None = None
    error: dict[str, Any] | None = None


class CreateWizardSessionRequest(BaseModel):
    """Request body for starting a new wizard session."""

    message: str = Field(min_length=1, max_length=8000)
    project_name: str | None = None
    symbols_dir: str | None = None


class WizardMessageRequest(BaseModel):
    """Request body for adding a user message to a wizard session."""

    message: str = Field(min_length=1, max_length=8000)


class WizardGenerateProjectResponse(BaseModel):
    """Project-generation response for one wizard session."""

    session: WizardSessionDetail
    job: dict[str, Any]


class SpecConversationOutput(BaseModel):
    """Structured LLM output for spec elicitation and refinement."""

    assistant_message: str = Field(min_length=1)
    next_state: Literal[
        "awaiting_user_clarification",
        "spec_ready_for_review",
        "failed",
    ]
    spec: CircuitSpec | None = None
    assumptions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    unsupported_reasons: list[str] = Field(default_factory=list)


class IrGenerationOutput(BaseModel):
    """Structured LLM output for spec-to-IR conversion and repair."""

    assistant_message: str = Field(min_length=1)
    netlist_json: dict[str, Any]
    assumptions: list[str] = Field(default_factory=list)