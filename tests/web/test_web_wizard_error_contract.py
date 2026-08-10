"""Stable wizard API error/retryability contract regressions."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kicad_pcb.errors import ToolError
from kicad_pcb_web.errors import (
    LlmCompletionTruncatedError,
    ResourceBusyError,
    WebServiceError,
    handle_web_service_error,
)
from kicad_pcb_web.services.wizard import _operation_error


def _client_for(error: WebServiceError) -> TestClient:
    app = FastAPI()
    app.add_exception_handler(WebServiceError, handle_web_service_error)

    @app.get("/failure")
    def fail() -> None:
        raise error

    return TestClient(app)


def test_retryable_provider_failure_is_explicit_and_does_not_leak_provider_details() -> None:
    secret = "provider-body-secret-should-never-reach-client"
    provider_error = ToolError(
        f"provider returned private detail: {secret}",
        details={
            "retryable": True,
            "provider_body": secret,
            "authorization": "Bearer provider-secret",
        },
    )
    error = _operation_error(
        provider_error,
        session_id="wiz_retryability",
        operation="generate_ir",
    )

    response = _client_for(error).get("/failure")

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["type"] == "web_service_error"
    assert payload["error"]["code"] == "LLM_PROVIDER_FAILED"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["details"] == {
        "session_id": "wiz_retryability",
        "operation": "generate_ir",
        "provider_error_code": "TOOL_ERROR",
    }
    assert secret not in response.text
    assert "provider-secret" not in response.text


def test_non_retryable_truncation_is_explicit_failure_not_success() -> None:
    error = LlmCompletionTruncatedError(
        "The configured LLM response was truncated; increase llm.max_tokens.",
        details={"session_id": "wiz_truncated", "operation": "create_spec"},
    )

    response = _client_for(error).get("/failure")

    assert response.status_code == 502
    payload = response.json()
    assert payload["error"]["code"] == "LLM_COMPLETION_TRUNCATED"
    assert payload["error"]["retryable"] is False
    assert "error" in payload
    assert "success" not in payload


def test_resource_busy_is_explicitly_retryable() -> None:
    error = ResourceBusyError(
        "Wizard session is busy.",
        details={"session_id": "wiz_busy", "operation": "revise_spec"},
    )

    response = _client_for(error).get("/failure")

    assert response.status_code == 409
    payload = response.json()
    assert payload["error"]["code"] == "RESOURCE_BUSY"
    assert payload["error"]["retryable"] is True
