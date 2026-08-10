from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.errors import LlmCompletionRefusedError, LlmCompletionTruncatedError
from kicad_pcb_web.services.llm import (
    LlmCompletionOutcome,
    LlmMessage,
    LlmRequest,
    build_llm_client,
    get_llm_provider_capabilities,
)
from kicad_pcb_web.services.llm.base import HttpLlmClientConfig
from kicad_pcb_web.services.llm.openai_client import OpenAiLlmClient
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _settings(provider: str, *, model: str = "model-under-test") -> WebSettings:
    data_dir = Path("/tmp/web-llm-production-hardening")
    base_url = None if provider == "openai" else "http://127.0.0.1:18080"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            base_url=base_url,
            api_key="test-key",
            timeout_s=1.0,
            temperature=0.25,
            max_tokens=321,
            retry_max_attempts=3,
            retry_base_delay_s=0.0,
            retry_max_delay_s=0.0,
            retry_jitter_s=0.0,
        ),
    )


def test_p3_capabilities_are_explicit_by_provider_not_model_name() -> None:
    openai = get_llm_provider_capabilities("openai")
    llama = get_llm_provider_capabilities("llama_server")
    ollama = get_llm_provider_capabilities("ollama")

    assert openai.supports_temperature_omit is True
    assert llama.supports_temperature_omit is True
    assert ollama.supports_temperature_omit is False
    assert openai.token_limit_mode.value == "max_completion_tokens"
    assert llama.token_limit_mode.value == "max_tokens"
    assert ollama.token_limit_mode.value == "ollama_num_predict"
    assert openai.idempotency_key_supported is False
    assert llama.idempotency_key_supported is False
    assert ollama.idempotency_key_supported is False

    weird_model = "ollama-looking-model-name"
    settings = _settings("openai", model=weird_model)
    client = build_llm_client(
        settings, transport=httpx.MockTransport(lambda _: httpx.Response(500))
    )
    assert client is not None
    assert client.model == weird_model  # type: ignore[attr-defined]
    assert client.capabilities.provider == "openai"  # type: ignore[attr-defined]
    client.close()  # type: ignore[attr-defined]


def test_p3_unknown_capability_provider_fails_closed() -> None:
    with pytest.raises(ValueError, match="Unsupported enabled LLM provider capability set"):
        get_llm_provider_capabilities("not-a-provider")


def test_p3_mismatched_capability_contract_fails_closed() -> None:
    config = HttpLlmClientConfig(
        model="x",
        base_url="https://example.invalid/v1",
        timeout_s=1.0,
        default_temperature=0.2,
        default_max_tokens=10,
        capabilities=get_llm_provider_capabilities("ollama"),
    )
    with pytest.raises(ValueError, match="capability contract does not match"):
        OpenAiLlmClient(provider_name="openai", config=config)


@pytest.mark.parametrize(
    ("provider", "expected_path", "expected_payload"),
    [
        (
            "openai",
            "/v1/chat/completions",
            {
                "model": "model-under-test",
                "messages": [{"role": "user", "content": "json please"}],
                "temperature": 0.25,
                "max_completion_tokens": 321,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "llama_server",
            "/chat/completions",
            {
                "model": "model-under-test",
                "messages": [{"role": "user", "content": "json please"}],
                "temperature": 0.25,
                "max_tokens": 321,
                "response_format": {"type": "json_object"},
            },
        ),
        (
            "ollama",
            "/api/chat",
            {
                "model": "model-under-test",
                "messages": [{"role": "user", "content": "json please"}],
                "stream": False,
                "options": {"temperature": 0.25, "num_predict": 321},
                "format": "json",
            },
        ),
    ],
)
def test_p3_exact_provider_payloads(
    provider: str,
    expected_path: str,
    expected_payload: dict[str, object],
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["payload"] = json.loads(request.content.decode("utf-8"))
        if provider == "ollama":
            return httpx.Response(
                200,
                json={
                    "model": "model-under-test",
                    "message": {"content": '{"ok":true}'},
                    "done_reason": "stop",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "request-1",
                "model": "model-under-test",
                "choices": [{"message": {"content": '{"ok":true}'}, "finish_reason": "stop"}],
            },
        )

    client = build_llm_client(_settings(provider), transport=httpx.MockTransport(handler))
    assert client is not None
    completion = client.complete(
        LlmRequest(
            messages=[LlmMessage(role="user", content="json please")],
            response_format="json",
        )
    )
    client.close()  # type: ignore[attr-defined]

    assert captured["path"] == expected_path
    assert captured["payload"] == expected_payload
    assert completion.outcome is LlmCompletionOutcome.COMPLETED


@pytest.mark.parametrize("provider", ["openai", "llama_server"])
def test_p4_openai_compatible_schema_valid_truncation_fails_closed(provider: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-truncated",
                "model": "model-under-test",
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "length",
                    }
                ],
            },
        )

    client = build_llm_client(_settings(provider), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(LlmCompletionTruncatedError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert exc_info.value.details["completion_outcome"] == "truncated"


@pytest.mark.parametrize(
    ("finish_reason", "message", "outcome"),
    [
        ("content_filter", {"content": '{"ok":true}'}, "filtered"),
        ("refusal", {"content": '{"ok":true}'}, "refused"),
        ("stop", {"content": '{"ok":true}', "refusal": "policy"}, "refused"),
    ],
)
def test_p4_openai_refusal_and_filter_outcomes_are_normalized(
    finish_reason: str,
    message: dict[str, object],
    outcome: str,
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-refused",
                "model": "model-under-test",
                "choices": [{"message": message, "finish_reason": finish_reason}],
            },
        )

    client = build_llm_client(_settings("openai"), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(LlmCompletionRefusedError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert exc_info.value.details["completion_outcome"] == outcome


def test_p4_terminal_reason_is_checked_before_null_content() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "request-null",
                "model": "model-under-test",
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            },
        )

    client = build_llm_client(_settings("openai"), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(LlmCompletionTruncatedError):
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]


def test_p4_ollama_schema_valid_length_fails_at_provider_boundary() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "model-under-test",
                "message": {"content": '{"ok":true}'},
                "done_reason": "length",
            },
        )

    client = build_llm_client(_settings("ollama"), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(LlmCompletionTruncatedError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert exc_info.value.details["completion_outcome"] == "truncated"


@pytest.mark.parametrize("provider", ["openai", "llama_server", "ollama"])
def test_p4_unknown_terminal_reason_fails_closed(provider: str) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        if provider == "ollama":
            return httpx.Response(
                200,
                json={
                    "model": "model-under-test",
                    "message": {"content": '{"ok":true}'},
                    "done_reason": "mystery_reason",
                },
            )
        return httpx.Response(
            200,
            json={
                "id": "request-unknown",
                "model": "model-under-test",
                "choices": [
                    {
                        "message": {"content": '{"ok":true}'},
                        "finish_reason": "mystery_reason",
                    }
                ],
            },
        )

    client = build_llm_client(_settings(provider), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(ToolError, match="unknown terminal reason") as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert exc_info.value.details["completion_outcome"] == "unknown_terminal_reason"


def test_p5_status_retry_budget_is_exact_and_separate() -> None:
    attempts = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(503, json={"error": "busy"})

    client = build_llm_client(_settings("openai"), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(ToolError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert attempts == 3
    assert exc_info.value.details["retryable"] is True


def test_p5_ambiguous_transport_failure_is_never_replayed() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadError("ambiguous transport failure", request=request)

    client = build_llm_client(_settings("openai"), transport=httpx.MockTransport(handler))
    assert client is not None
    with pytest.raises(ToolError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert attempts == 1
    assert exc_info.value.details["ambiguous_delivery"] is True
    assert exc_info.value.details["automatic_retry"] is False
    assert exc_info.value.details["retryable"] is False
