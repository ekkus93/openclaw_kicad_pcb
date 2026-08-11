"""Sanitized external request/result contracts for schematic refinement."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .schematic_refinement import RefinementLoopResult

_ALLOWED_SESSION_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_."
)


class RefinementRunRequest(BaseModel):
    """External request deliberately excludes file paths, provider data, and resource limits."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=96)

    @field_validator("session_id")
    @classmethod
    def validate_session_id(cls, value: str) -> str:
        if any(ch not in _ALLOWED_SESSION_CHARS for ch in value):
            raise ValueError("session_id contains unsupported characters")
        return value


class RefinementRunResponse(BaseModel):
    """Safe external view of one bounded refinement session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: str
    stop_reason: str
    session_id: str
    starting_hash: str
    final_accepted_hash: str
    best_accepted_hash: str
    latest_attempted_hash: str | None
    starting_layout_fingerprint: str
    final_layout_fingerprint: str
    rounds_attempted: int
    accepted_rounds: int
    rejected_rounds: int
    accepted_operations: int
    model_calls_made: int
    model_call_limit: int
    evidence_available: bool


def build_refinement_run_response(
    *,
    session_id: str,
    result: RefinementLoopResult,
) -> RefinementRunResponse:
    return RefinementRunResponse(
        status=result.status,
        stop_reason=result.stop_reason,
        session_id=session_id,
        starting_hash=result.starting_hash,
        final_accepted_hash=result.final_accepted_hash,
        best_accepted_hash=result.best_accepted_hash,
        latest_attempted_hash=result.latest_attempted_hash,
        starting_layout_fingerprint=result.starting_layout_fingerprint,
        final_layout_fingerprint=result.final_layout_fingerprint,
        rounds_attempted=result.rounds_attempted,
        accepted_rounds=result.accepted_rounds,
        rejected_rounds=result.rejected_rounds,
        accepted_operations=result.accepted_operations,
        model_calls_made=result.model_calls_made,
        model_call_limit=result.model_call_limit,
        evidence_available=result.session_evidence_dir is not None,
    )
