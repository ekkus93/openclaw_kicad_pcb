"""Pydantic request/response models for the web API."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

JobStatus = Literal["queued", "running", "succeeded", "failed", "cancelled"]


class ValidateNetlistRequest(BaseModel):
    """Request body for Circuit IR validation."""

    netlist_json: dict[str, Any]
    symbols_dir: str | None = None


class ValidateNetlistResponse(BaseModel):
    """Validation response payload."""

    valid: bool
    component_count: int
    net_count: int
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    symbols_dirs_used: list[str] = Field(default_factory=list)


class CreateJobFromNetlistRequest(BaseModel):
    """Request body for synchronous job creation from Circuit IR."""

    project_name: str
    netlist_json: dict[str, Any]
    symbols_dir: str | None = None
    validation: str = "internal"
    layout: str | None = None
    routing: str | None = None
    heuristic_profile: str | None = None
    label_mode: str | None = None
    auto_fix: bool = True
    strict: bool = False

    @field_validator("project_name")
    @classmethod
    def validate_project_name(cls, value: str) -> str:
        """Reject empty and path-like project names."""

        stripped = value.strip()
        if not stripped:
            raise ValueError("Project name must not be empty.")
        if len(stripped) > 120:
            raise ValueError("Project name must be 120 characters or fewer.")
        if "/" in stripped or "\\" in stripped:
            raise ValueError("Project name must not contain path separators.")
        return stripped


class JobSummary(BaseModel):
    """Compact job listing response."""

    id: str
    status: JobStatus
    project_name: str
    created_at: str
    updated_at: str


class JobDetail(JobSummary):
    """Full job detail response."""

    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    artifacts: list[str] = Field(default_factory=list)


class SymbolSearchResult(BaseModel):
    """One symbol-search result row."""

    library: str
    name: str
    qualified_name: str
    aliases: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class SymbolSearchResponse(BaseModel):
    """Search response payload."""

    query: str
    results: list[SymbolSearchResult] = Field(default_factory=list)


class DoctorCheck(BaseModel):
    """One doctor-check result."""

    name: str
    ok: bool
    detail: str


class DoctorResponse(BaseModel):
    """Doctor endpoint response."""

    ok: bool
    checks: list[DoctorCheck] = Field(default_factory=list)


class ArtifactListResponse(BaseModel):
    """Artifact listing payload."""

    job_id: str
    artifacts: list[str] = Field(default_factory=list)


class ErrorPayload(BaseModel):
    """Structured error payload."""

    type: str
    code: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    """Top-level error response envelope."""

    error: ErrorPayload


class UiBootstrapResponse(BaseModel):
    """Initial UI configuration payload for the frontend shell."""

    llm_provider: str
    llm_enabled: bool
    example_netlist_json: dict[str, Any]
