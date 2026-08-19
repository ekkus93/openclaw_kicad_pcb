"""Focused tests for safe Phase N3 provider-error diagnostics."""

from __future__ import annotations

import httpx

from kicad_pcb.errors import ToolError, UserError
from kicad_pcb_web.refinement_evaluation_cli import _safe_error_details
from kicad_pcb_web.services.refinement_evaluation_corpus import _exception_diagnostics


def _ollama_tool_error(message: str) -> ToolError:
    request = httpx.Request("POST", "http://127.0.0.1:11434/api/chat")
    response = httpx.Response(400, request=request, json={"error": message})
    cause = httpx.HTTPStatusError(
        "client error",
        request=request,
        response=response,
    )
    try:
        raise ToolError(
            "ollama request failed with HTTP 400.",
            details={
                "provider": "ollama",
                "status_code": 400,
                "endpoint": "/api/chat",
                "retryable": False,
            },
        ) from cause
    except ToolError as exc:
        return exc


def test_exception_diagnostics_include_bounded_ollama_provider_error() -> None:
    exc = _ollama_tool_error("input length exceeds context window\nretry with fewer tokens")

    diagnostics = _exception_diagnostics(exc)

    assert diagnostics["cause_provider_error"] == (
        "input length exceeds context window retry with fewer tokens"
    )
    assert diagnostics["cause_status_code"] == 400
    assert diagnostics["cause_endpoint"] == "/api/chat"


def test_exception_diagnostics_bound_ollama_provider_error() -> None:
    exc = _ollama_tool_error("x" * 1000)

    diagnostics = _exception_diagnostics(exc)

    provider_error = diagnostics["cause_provider_error"]
    assert isinstance(provider_error, str)
    assert len(provider_error) == 500


def test_cli_allows_only_explicit_provider_error_detail() -> None:
    exc = UserError(
        "Refinement evaluation corpus fixture failed.",
        code="REFINEMENT_EVALUATION_FIXTURE_FAILED",
        details={
            "fixture_id": "fixture-1",
            "cause_provider_error": "safe provider reason",
            "raw_response": "must-not-leak",
        },
    )

    details = _safe_error_details(exc)

    assert details == {
        "fixture_id": "fixture-1",
        "cause_provider_error": "safe provider reason",
    }
