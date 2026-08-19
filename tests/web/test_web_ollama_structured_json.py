from __future__ import annotations

import json
from pathlib import Path

import httpx

from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _settings() -> WebSettings:
    data_dir = Path("/tmp/web-ollama-structured-json-tests")
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="ollama",
            model="qwen3-vl:8b",
            base_url="http://127.0.0.1:11434",
            temperature=0.2,
            vision_enabled=True,
        ),
    )


def test_ollama_structured_json_explicitly_disables_thinking() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "qwen3-vl:8b",
                "message": {"content": '{"status":"ok"}'},
                "done_reason": "stop",
            },
        )

    client = build_llm_client(_settings(), transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        completion = client.complete(
            LlmRequest(
                messages=[LlmMessage(role="user", content="Return JSON only")],
                response_format="json",
            )
        )
    finally:
        client.close()  # type: ignore[attr-defined]

    assert completion.content == '{"status":"ok"}'
    assert captured["format"] == "json"
    assert captured["think"] is False


def test_ollama_text_request_does_not_force_thinking_mode() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "qwen3-vl:8b",
                "message": {"content": "ok"},
                "done_reason": "stop",
            },
        )

    client = build_llm_client(_settings(), transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hello")]))
    finally:
        client.close()  # type: ignore[attr-defined]

    assert "format" not in captured
    assert "think" not in captured
