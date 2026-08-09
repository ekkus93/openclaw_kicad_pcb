"""Regression coverage for bounded and ambiguity-safe LLM retries."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _settings(tmp_path: Path, **overrides: object) -> WebSettings:
    llm_values: dict[str, object] = {
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "base_url": "https://example.invalid/v1",
        "api_key": "test-key",
        "retry_max_attempts": 3,
        "retry_base_delay_s": 0.5,
        "retry_max_delay_s": 8.0,
        "retry_jitter_s": 0.0,
    }
    llm_values.update(overrides)
    return WebSettings(
        data_dir=tmp_path,
        jobs_dir=tmp_path / "jobs",
        llm=LlmSettings(**llm_values),  # type: ignore[arg-type]
    )


def _request() -> LlmRequest:
    return LlmRequest(messages=[LlmMessage(role="user", content="Hello")])


def _success() -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl_retry_hardened",
            "model": "gpt-4.1-mini",
            "choices": [{"message": {"content": "Recovered"}, "finish_reason": "stop"}],
        },
    )


def test_retry_after_header_controls_retry_delay(tmp_path: Path, monkeypatch) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "2"},
                json={"error": {"message": "slow down"}},
            )
        return _success()

    monkeypatch.setattr("kicad_pcb_web.services.llm.base.time.sleep", sleeps.append)
    client = build_llm_client(
        _settings(tmp_path),
        transport=httpx.MockTransport(handler),
    )
    assert client is not None

    completion = client.complete(_request())

    assert completion.content == "Recovered"
    assert attempts["count"] == 2
    assert sleeps == [2.0]


def test_retry_budget_is_configurable(tmp_path: Path, monkeypatch) -> None:
    attempts = {"count": 0}
    sleeps: list[float] = []

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(503, json={"error": {"message": "busy"}})

    monkeypatch.setattr("kicad_pcb_web.services.llm.base.time.sleep", sleeps.append)
    client = build_llm_client(
        _settings(tmp_path, retry_max_attempts=2, retry_base_delay_s=1.0),
        transport=httpx.MockTransport(handler),
    )
    assert client is not None

    with pytest.raises(ToolError, match="request failed with HTTP 503"):
        client.complete(_request())

    assert attempts["count"] == 2
    assert sleeps == [1.0]


def test_transport_failure_is_not_automatically_replayed(tmp_path: Path) -> None:
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        raise httpx.ConnectError("ambiguous transport failure", request=request)

    client = build_llm_client(
        _settings(tmp_path, retry_max_attempts=5),
        transport=httpx.MockTransport(handler),
    )
    assert client is not None

    with pytest.raises(ToolError) as caught:
        client.complete(_request())

    assert attempts["count"] == 1
    assert caught.value.details["ambiguous_delivery"] is True
    assert caught.value.details["automatic_retry"] is False
