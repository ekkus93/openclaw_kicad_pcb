from __future__ import annotations

import contextlib
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.deps import get_llm_client
from kicad_pcb_web.errors import LlmCompletionRefusedError, LlmCompletionTruncatedError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.services.wizard import _build_spec_messages, _call_llm_for_json
from kicad_pcb_web.settings import LlmSettings, WebSettings, load_settings
from kicad_pcb_web.wizard_models import SpecConversationOutput, WizardMessage, WizardSessionDetail


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
            max_tokens=512,
        )
    )

    assert completion.provider == "openai"
    assert completion.model == "gpt-4.1-mini"
    assert completion.finish_reason == "stop"
    assert completion.content == '{"summary":"ok"}'
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["authorization"] == "Bearer test-key"
    assert '"response_format":{"type":"json_object"}' in str(captured["payload"])
    assert '"max_completion_tokens":512' in str(captured["payload"])
    assert '"max_tokens":512' not in str(captured["payload"])


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
                "choices": [{"message": {"content": "Hello"}, "finish_reason": "stop"}],
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


@pytest.mark.integration
def test_live_llama_server_handles_real_wizard_spec_prompt() -> None:
    if os.environ.get("RUN_LIVE_PROVIDER_TESTS") != "1":
        pytest.skip("Set RUN_LIVE_PROVIDER_TESTS=1 to run live provider probes.")

    settings = load_settings()
    if settings.llm.provider not in {"llama_server", "openai"}:
        pytest.skip(
            "Live provider probe requires provider=openai or provider=llama_server in web settings."
        )

    client = build_llm_client(settings)
    if client is None:
        pytest.skip("Live provider probe requires an enabled LLM client.")

    session = WizardSessionDetail(
        id="live_llama_spec_probe",
        status="drafting_spec",
        created_at="2026-05-23T00:00:00Z",
        updated_at="2026-05-23T00:00:00Z",
        project_name="DebugWizard",
        symbols_dir=None,
        llm_provider=settings.llm.provider,
        prompt_version=settings.llm.system_prompt_version,
        messages=[
            WizardMessage(
                role="user",
                content=(
                    "Create a simple RC low-pass filter with one input, one output, and 5V supply."
                ),
            )
        ],
    )
    messages = _build_spec_messages(settings, session)
    started_at = time.perf_counter()

    try:
        result = _call_llm_for_json(
            llm_client=client,
            messages=messages,
            response_model=SpecConversationOutput,
            max_repairs=settings.llm.spec_max_repair_rounds,
        )
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()

    elapsed_s = time.perf_counter() - started_at

    assert result.assistant_message.strip()
    assert result.next_state in {"awaiting_user_clarification", "spec_ready_for_review"}
    assert result.spec is not None
    assert elapsed_s > 0


# ---------------------------------------------------------------------------
# get_llm_client dependency lifecycle
# ---------------------------------------------------------------------------


def _disabled_settings(tmp_path: Path) -> WebSettings:
    return WebSettings(
        data_dir=tmp_path,
        jobs_dir=tmp_path / "jobs",
        llm=LlmSettings(
            provider="disabled",
            model=None,
            base_url=None,
            api_key=None,
        ),
    )


def test_dep_disabled_llm_yields_none(tmp_path: Path) -> None:
    settings = _disabled_settings(tmp_path)
    gen = get_llm_client(settings)  # type: ignore[call-arg]
    client = next(gen)
    assert client is None
    with contextlib.suppress(StopIteration):
        next(gen)


def test_dep_closable_client_closed_after_successful_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []

    class _FakeClosable:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("kicad_pcb_web.deps.build_llm_client", lambda _s: _FakeClosable())
    settings = _disabled_settings(tmp_path)
    gen = get_llm_client(settings)  # type: ignore[call-arg]
    client = next(gen)
    assert hasattr(client, "close")
    assert not closed
    with contextlib.suppress(StopIteration):
        next(gen)
    assert closed == [True]


def test_dep_closable_client_closed_after_route_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[bool] = []

    class _FakeClosable:
        def close(self) -> None:
            closed.append(True)

    monkeypatch.setattr("kicad_pcb_web.deps.build_llm_client", lambda _s: _FakeClosable())
    settings = _disabled_settings(tmp_path)
    gen = get_llm_client(settings)  # type: ignore[call-arg]
    next(gen)
    with pytest.raises(RuntimeError):
        gen.throw(RuntimeError("route failed"))
    assert closed == [True]


def test_dep_non_closable_client_does_not_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeNoClose:
        pass

    monkeypatch.setattr("kicad_pcb_web.deps.build_llm_client", lambda _s: _FakeNoClose())
    settings = _disabled_settings(tmp_path)
    gen = get_llm_client(settings)  # type: ignore[call-arg]
    next(gen)
    with contextlib.suppress(StopIteration):
        next(gen)  # finalizer must not raise


def test_followup_ollama_send_mode_preserves_temperature_payload() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "message": {"content": "ok"},
                "done_reason": "stop",
            },
        )

    settings = _make_settings(
        provider="ollama",
        model="llama3.1",
        base_url="http://127.0.0.1:11434",
    )
    assert settings.llm.temperature_mode == "send"
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))
    finally:
        client.close()  # type: ignore[attr-defined]

    options = captured["options"]
    assert isinstance(options, dict)
    assert options["temperature"] == 0.3


@pytest.mark.parametrize("provider", ["openai", "llama_server"])
@pytest.mark.parametrize(
    ("finish_reason", "message", "error_type"),
    [
        ("length", {"content": '{"ok":true}'}, LlmCompletionTruncatedError),
        ("content_filter", {"content": '{"ok":true}'}, LlmCompletionRefusedError),
        ("stop", {"content": '{"ok":true}', "refusal": True}, LlmCompletionRefusedError),
        ("length", {"content": None}, LlmCompletionTruncatedError),
    ],
)
def test_followup_openai_compatible_real_parser_classifies_terminal_outcomes(
    provider: str,
    finish_reason: str,
    message: dict[str, object],
    error_type: type[Exception],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "followup-terminal",
                "model": "test-model",
                "choices": [{"message": message, "finish_reason": finish_reason}],
            },
        )

    base_url = None if provider == "openai" else "http://127.0.0.1:8080"
    settings = _make_settings(provider=provider, model="test-model", base_url=base_url)
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None
    try:
        with pytest.raises(error_type):
            client.complete(LlmRequest(messages=[LlmMessage(role="user", content="Hi")]))
    finally:
        client.close()  # type: ignore[attr-defined]
