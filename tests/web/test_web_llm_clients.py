from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _make_settings(*, provider: str, model: str | None, base_url: str | None) -> WebSettings:
    data_dir = Path("/tmp/web-llm-tests")
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            base_url=base_url,
            api_key="test-key",
            timeout_s=10.0,
            temperature=0.3,
            max_tokens=256,
        ),
    )


def test_factory_returns_none_when_disabled() -> None:
    settings = _make_settings(provider="disabled", model=None, base_url=None)

    client = build_llm_client(settings)

    assert client is None


def test_openai_client_normalizes_completion_and_json_mode() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_123",
                "model": "gpt-4.1-mini",
                "choices": [
                    {
                        "message": {"content": '{"summary":"ok"}'},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    settings = _make_settings(provider="openai", model="gpt-4.1-mini", base_url=None)
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    completion = client.complete(
        LlmRequest(
            messages=[LlmMessage(role="user", content="Summarize this")],
            response_format="json",
        )
    )

    assert completion.provider == "openai"
    assert completion.model == "gpt-4.1-mini"
    assert completion.finish_reason == "stop"
    assert completion.content == '{"summary":"ok"}'
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert '"response_format":{"type":"json_object"}' in str(captured["payload"])


def test_ollama_client_normalizes_chat_response() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"content": "Draft spec"},
                "done_reason": "stop",
            },
        )

    settings = _make_settings(
        provider="ollama",
        model="llama3.1",
        base_url="http://127.0.0.1:11434",
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    completion = client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))

    assert completion.provider == "ollama"
    assert completion.model == "llama3.1"
    assert completion.content == "Draft spec"
    assert completion.finish_reason == "stop"


def test_llama_server_uses_openai_compatible_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "http://127.0.0.1:8080/chat/completions"
        return httpx.Response(
            200,
            json={
                "id": "llama-req-1",
                "model": "qwen2.5",
                "choices": [
                    {"message": {"content": "Hello"}, "finish_reason": "stop"}
                ],
            },
        )

    settings = _make_settings(
        provider="llama_server",
        model="qwen2.5",
        base_url="http://127.0.0.1:8080",
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    completion = client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))

    assert completion.provider == "llama_server"
    assert completion.model == "qwen2.5"
    assert completion.request_id == "llama-req-1"


def test_llama_server_honors_json_mode_and_request_overrides() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "id": "llama-req-2",
                "model": "qwen36-27B-Q3KM-turbo",
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": '{"summary":'},
                                {"type": "text", "text": '"ok"}'},
                            ]
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    settings = _make_settings(
        provider="llama_server",
        model="qwen36-27B-Q3KM-turbo",
        base_url="http://127.0.0.1:8080",
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    completion = client.complete(
        LlmRequest(
            messages=[LlmMessage(role="user", content="Return JSON only")],
            response_format="json",
            temperature=0.05,
            max_tokens=1024,
        )
    )

    assert completion.provider == "llama_server"
    assert completion.model == "qwen36-27B-Q3KM-turbo"
    assert completion.content == '{"summary":"ok"}'
    assert completion.finish_reason == "stop"
    assert completion.request_id == "llama-req-2"
    assert captured["url"] == "http://127.0.0.1:8080/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    payload = str(captured["payload"])
    assert '"response_format":{"type":"json_object"}' in payload
    assert '"temperature":0.05' in payload
    assert '"max_tokens":1024' in payload


def test_llama_server_surfaces_provider_specific_parse_errors() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "llama-req-bad",
                "model": "qwen2.5",
                "choices": [],
            },
        )

    settings = _make_settings(
        provider="llama_server",
        model="qwen2.5",
        base_url="http://127.0.0.1:8080",
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    with pytest.raises(ToolError, match="llama_server returned no completion choices"):
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))


def test_retryable_provider_error_is_retried() -> None:
    attempts = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(503, json={"error": {"message": "busy"}})
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_retry",
                "model": "gpt-4.1-mini",
                "choices": [{"message": {"content": "Recovered"}, "finish_reason": "stop"}],
            },
        )

    settings = _make_settings(provider="openai", model="gpt-4.1-mini", base_url=None)
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    completion = client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))

    assert attempts["count"] == 2
    assert completion.content == "Recovered"


def test_non_retryable_provider_error_fails_immediately() -> None:
    attempts = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(401, json={"error": {"message": "denied"}})

    settings = _make_settings(provider="openai", model="gpt-4.1-mini", base_url=None)
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    with pytest.raises(ToolError, match="openai request failed with HTTP 401"):
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))

    assert attempts["count"] == 1