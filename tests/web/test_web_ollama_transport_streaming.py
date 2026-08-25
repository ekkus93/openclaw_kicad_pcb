from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings


def _ollama_settings() -> WebSettings:
    data_dir = Path("/tmp/web-ollama-streaming-tests")
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="ollama",
            model="qwen3-vl:8b-instruct-n3-64k",
            base_url="http://127.0.0.1:11434",
            timeout_s=10.0,
            temperature=0.0,
            max_tokens=256,
        ),
    )


def test_ollama_internal_transport_stream_reassembles_structured_completion() -> None:
    captured: dict[str, object] = {}
    schema = {
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
        "additionalProperties": False,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        body = "\n".join(
            [
                json.dumps(
                    {
                        "model": "qwen3-vl:8b-instruct-n3-64k",
                        "message": {"role": "assistant", "content": '{"ok":'},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "model": "qwen3-vl:8b-instruct-n3-64k",
                        "message": {"role": "assistant", "content": "true}"},
                        "done": False,
                    }
                ),
                json.dumps(
                    {
                        "model": "qwen3-vl:8b-instruct-n3-64k",
                        "message": {"role": "assistant", "content": ""},
                        "done": True,
                        "done_reason": "stop",
                    }
                ),
            ]
        )
        return httpx.Response(
            200,
            content=(body + "\n").encode("utf-8"),
            headers={"content-type": "application/x-ndjson"},
        )

    client = build_llm_client(_ollama_settings(), transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        completion = client.complete(
            LlmRequest(
                messages=[LlmMessage(role="user", content="Return JSON")],
                response_format="json",
                json_schema=schema,
            )
        )
    finally:
        client.close()  # type: ignore[attr-defined]

    assert captured["stream"] is True
    assert captured["format"] == schema
    assert captured["think"] is False
    assert completion.content == '{"ok":true}'
    assert completion.finish_reason == "stop"


class _FailAfterFirstChunk(httpx.SyncByteStream):
    def __iter__(self) -> Iterator[bytes]:
        yield (
            b'{"model":"qwen3-vl:8b-instruct-n3-64k",'
            b'"message":{"role":"assistant","content":"partial"},'
            b'"done":false}\n'
        )
        raise httpx.ReadTimeout("simulated mid-stream timeout")


def test_ollama_midstream_transport_failure_is_not_replayed() -> None:
    attempts = {"count": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        return httpx.Response(200, stream=_FailAfterFirstChunk())

    client = build_llm_client(_ollama_settings(), transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        with pytest.raises(ToolError) as caught:
            client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))
    finally:
        client.close()  # type: ignore[attr-defined]

    assert attempts["count"] == 1
    assert caught.value.details["ambiguous_delivery"] is True
    assert caught.value.details["automatic_retry"] is False
    assert caught.value.details["retryable"] is False
    assert caught.value.details["error_type"] == "ReadTimeout"


def test_ollama_stream_without_terminal_chunk_fails_closed() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"model":"qwen3-vl:8b-instruct-n3-64k",'
                b'"message":{"role":"assistant","content":"partial"},'
                b'"done":false}\n'
            ),
            headers={"content-type": "application/x-ndjson"},
        )

    client = build_llm_client(_ollama_settings(), transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        with pytest.raises(ToolError, match="stream ended before a terminal completion"):
            client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))
    finally:
        client.close()  # type: ignore[attr-defined]
