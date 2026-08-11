from __future__ import annotations

import base64
import json
from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.services.llm import LlmImage, LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.services.llm.capabilities import get_llm_provider_capabilities
from kicad_pcb_web.settings import LlmSettings, WebSettings, load_settings


def _settings(provider: str, *, vision_enabled: bool, model: str = "plain-model") -> WebSettings:
    root = Path("/tmp/refinement-vision-tests")
    return WebSettings(
        data_dir=root,
        jobs_dir=root / "jobs",
        llm=LlmSettings(
            provider=provider,  # type: ignore[arg-type]
            model=model,
            base_url=None if provider == "openai" else "http://127.0.0.1:11434",
            api_key="test-key" if provider == "openai" else None,
            vision_enabled=vision_enabled,
            retry_max_attempts=1,
        ),
    )


def _image(data: bytes = b"png-bytes") -> LlmImage:
    return LlmImage(media_type="image/png", base64_data=base64.b64encode(data).decode("ascii"))


def _response(provider: str) -> dict[str, object]:
    if provider == "ollama":
        return {"model": "plain-model", "message": {"content": "ok"}, "done_reason": "stop"}
    return {
        "id": "req-1",
        "model": "plain-model",
        "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
    }


def test_provider_capability_matrix_explicitly_declares_image_contract() -> None:
    for provider in ("openai", "llama_server", "ollama"):
        capabilities = get_llm_provider_capabilities(provider)
        assert capabilities.supports_image_input is True
        assert capabilities.accepted_image_media_types == ("image/png", "image/jpeg", "image/webp")
        assert capabilities.max_images_per_request == 4
        assert capabilities.max_image_bytes == 8 * 1024 * 1024


def test_vision_looking_model_name_does_not_enable_image_requests() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response("openai"))

    client = build_llm_client(
        _settings("openai", vision_enabled=False, model="super-vision-pro"),
        transport=httpx.MockTransport(handler),
    )
    assert client is not None
    with pytest.raises(ToolError, match="vision_enabled=true"):
        client.complete(
            LlmRequest(messages=[LlmMessage(role="user", content="Inspect")], images=(_image(),))
        )
    assert called is False


def test_openai_vision_payload_requires_opt_in_and_uses_data_uri() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(200, json=_response("openai"))

    client = build_llm_client(
        _settings("openai", vision_enabled=True), transport=httpx.MockTransport(handler)
    )
    assert client is not None
    client.complete(
        LlmRequest(messages=[LlmMessage(role="user", content="Inspect")], images=(_image(),))
    )
    content = captured["messages"][0]["content"]  # type: ignore[index]
    assert content[0] == {"type": "text", "text": "Inspect"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_ollama_vision_payload_uses_explicit_images_array() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.read()))
        return httpx.Response(200, json=_response("ollama"))

    client = build_llm_client(
        _settings("ollama", vision_enabled=True), transport=httpx.MockTransport(handler)
    )
    assert client is not None
    image = _image()
    client.complete(
        LlmRequest(messages=[LlmMessage(role="user", content="Inspect")], images=(image,))
    )
    assert captured["messages"][0]["images"] == [image.base64_data]  # type: ignore[index]


def test_image_validation_rejects_invalid_base64_and_count_before_network() -> None:
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response("openai"))

    client = build_llm_client(
        _settings("openai", vision_enabled=True), transport=httpx.MockTransport(handler)
    )
    assert client is not None
    with pytest.raises(ToolError, match="valid base64"):
        client.complete(
            LlmRequest(
                messages=[LlmMessage(role="user", content="Inspect")],
                images=(LlmImage("image/png", "%%%"),),
            )
        )
    with pytest.raises(ToolError, match="image count"):
        client.complete(
            LlmRequest(
                messages=[LlmMessage(role="user", content="Inspect")],
                images=tuple(_image() for _ in range(5)),
            )
        )
    assert called is False


def test_vision_setting_defaults_false_and_requires_explicit_boolean(
    monkeypatch, tmp_path: Path
) -> None:
    config = tmp_path / "web.toml"
    config.write_text(
        '[web]\ndata_dir = "./data"\n[llm]\nprovider = "disabled"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config))
    assert load_settings().llm.vision_enabled is False

    monkeypatch.setenv("KICAD_PCB_WEB_LLM_VISION_ENABLED", "true")
    assert load_settings().llm.vision_enabled is True

    monkeypatch.setenv("KICAD_PCB_WEB_LLM_VISION_ENABLED", "maybe")
    with pytest.raises(ValueError, match="llm.vision_enabled"):
        load_settings()
