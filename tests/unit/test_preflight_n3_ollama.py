"""Focused tests for the Phase N3 Ollama preflight helper."""

from __future__ import annotations

import importlib.util
import struct
import sys
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "scripts" / "preflight_n3_ollama.py"
    spec = importlib.util.spec_from_file_location("preflight_n3_ollama", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_phase_n3_preflight_png_preserves_requested_dimensions() -> None:
    module = _load_module()
    payload = module._solid_white_png(3360, 2376)

    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload[12:16] == b"IHDR"
    width, height = struct.unpack(">II", payload[16:24])
    assert (width, height) == (3360, 2376)


def test_phase_n3_preflight_provisions_context_bound_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    ensured: list[tuple[str, str]] = []
    posted: list[tuple[str, dict[str, object]]] = []
    config = module.PreflightConfig(
        base_url="http://127.0.0.1:11434",
        model="qwen3-vl:8b-instruct-n3-32k",
        source_model="qwen3-vl:8b-instruct",
        num_ctx=32768,
        probe_width=3360,
        probe_height=2376,
    )

    monkeypatch.setattr(
        module,
        "_ensure_model",
        lambda base_url, model: ensured.append((base_url, model)),
    )

    def post_json(base_url, path, payload, **kwargs):
        del base_url, kwargs
        posted.append((path, payload))
        return {"status": "success"}

    monkeypatch.setattr(module, "_post_json", post_json)
    monkeypatch.setattr(module, "_installed_models", lambda base_url: {config.model})

    module._provision_model(config)

    assert ensured == [(config.base_url, config.source_model)]
    assert posted == [
        (
            "/api/create",
            {
                "model": config.model,
                "from": config.source_model,
                "parameters": {"num_ctx": 32768},
                "stream": False,
            },
        )
    ]


def test_phase_n3_preflight_vision_probe_uses_native_ollama_images(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_module()
    captured: dict[str, object] = {}
    config = module.PreflightConfig(
        base_url="http://127.0.0.1:11434",
        model="qwen3-vl:8b-instruct-n3-32k",
        source_model=None,
        num_ctx=None,
        probe_width=3360,
        probe_height=2376,
    )

    def post_json(base_url, path, payload, **kwargs):
        captured.update(base_url=base_url, path=path, payload=payload, kwargs=kwargs)
        return {"message": {"content": '{"ok":true}'}}

    monkeypatch.setattr(module, "_post_json", post_json)

    module._probe_chat(config, image_b64="base64-image", operation="vision probe")

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["model"] == config.model
    format_schema = payload["format"]
    assert isinstance(format_schema, dict)
    assert format_schema["type"] == "object"
    assert format_schema["required"] == ["ok"]
    assert format_schema["additionalProperties"] is False
    assert payload["think"] is False
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert messages[0]["images"] == ["base64-image"]
