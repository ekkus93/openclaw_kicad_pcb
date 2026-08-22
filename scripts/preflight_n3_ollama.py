#!/usr/bin/env python3
"""Provision and verify the Ollama model used by the Phase N3 live evaluation."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import struct
import time
import urllib.error
import urllib.request
import zlib
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlsplit

_MAX_PROVIDER_ERROR_BYTES = 4096
_MAX_PROVIDER_ERROR_CHARS = 500
_DEFAULT_TIMEOUT_S = 180.0
_PULL_TIMEOUT_S = 3600.0
_CREATE_TIMEOUT_S = 600.0
_READINESS_ATTEMPT_TIMEOUT_S = 10.0
_READINESS_ATTEMPTS = 6
_READINESS_RETRY_DELAY_S = 2.0


@dataclass(frozen=True)
class PreflightConfig:
    base_url: str
    model: str
    source_model: str | None
    num_ctx: int | None
    probe_width: int
    probe_height: int


def _provider_error(exc: urllib.error.HTTPError) -> str:
    try:
        raw = exc.read(_MAX_PROVIDER_ERROR_BYTES).decode("utf-8", errors="replace")
        payload = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "unavailable"
    if not isinstance(payload, dict):
        return "unavailable"
    error = payload.get("error")
    if isinstance(error, str):
        return error[:_MAX_PROVIDER_ERROR_CHARS]
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return str(error["message"])[:_MAX_PROVIDER_ERROR_CHARS]
    return "unavailable"


def _http_failure(operation: str, exc: urllib.error.HTTPError) -> NoReturn:
    raise SystemExit(
        f"{operation} failed: HTTP {exc.code}; error={_provider_error(exc)!r}"
    ) from exc


def _get_json(
    base_url: str,
    path: str,
    *,
    timeout_s: float = 10.0,
    attempts: int = 1,
    retry_delay_s: float = 0.0,
) -> dict[str, Any]:
    if attempts < 1:
        raise ValueError("attempts must be positive")
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(
                f"{base_url}{path}", timeout=timeout_s
            ) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            _http_failure(f"Ollama {path}", exc)
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            if attempt == attempts:
                suffix = f" after {attempts} attempts" if attempts > 1 else ""
                raise SystemExit(
                    f"Unable to query Ollama {path}{suffix}: {type(exc).__name__}"
                ) from exc
            print(
                f"Ollama {path} not ready ({type(exc).__name__}); "
                f"retrying {attempt + 1}/{attempts}."
            )
            time.sleep(retry_delay_s)
            continue
        if not isinstance(payload, dict):
            raise SystemExit(f"Ollama {path} returned non-object JSON")
        return payload
    raise AssertionError("unreachable")


def _post_json(
    base_url: str,
    path: str,
    payload: dict[str, Any],
    *,
    timeout_s: float,
    operation: str,
) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        _http_failure(operation, exc)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise SystemExit(f"{operation} failed: {type(exc).__name__}") from exc
    if not isinstance(result, dict):
        raise SystemExit(f"{operation} returned non-object JSON")
    return result


def _installed_models(base_url: str) -> set[str]:
    models = _get_json(base_url, "/api/tags").get("models")
    if not isinstance(models, list):
        raise SystemExit("Ollama /api/tags response does not contain a model list")
    return {
        str(item.get("name") or item.get("model"))
        for item in models
        if isinstance(item, dict) and (item.get("name") or item.get("model"))
    }


def _ensure_model(base_url: str, model: str) -> None:
    if model in _installed_models(base_url):
        return
    print(f"Ollama model {model!r} is not installed; pulling it once for this runner.")
    _post_json(
        base_url,
        "/api/pull",
        {"model": model, "stream": False},
        timeout_s=_PULL_TIMEOUT_S,
        operation=f"Ollama pull for {model!r}",
    )
    if model not in _installed_models(base_url):
        raise SystemExit(f"Ollama pull completed but model {model!r} is still unavailable")


def _provision_model(config: PreflightConfig) -> None:
    if config.source_model is None:
        _ensure_model(config.base_url, config.model)
        return
    if config.num_ctx is None:
        raise SystemExit("--num-ctx is required when --source-model is provided")
    _ensure_model(config.base_url, config.source_model)
    print(
        f"Provisioning {config.model!r} from {config.source_model!r} with num_ctx={config.num_ctx}."
    )
    _post_json(
        config.base_url,
        "/api/create",
        {
            "model": config.model,
            "from": config.source_model,
            "parameters": {"num_ctx": config.num_ctx},
            "stream": False,
        },
        timeout_s=_CREATE_TIMEOUT_S,
        operation=f"Ollama create for {config.model!r}",
    )
    if config.model not in _installed_models(config.base_url):
        raise SystemExit(f"Ollama created model {config.model!r} is unavailable")


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = binascii.crc32(kind + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)


def _solid_white_png(width: int, height: int) -> bytes:
    scanline = b"\x00" + (b"\xff\xff\xff" * width)
    raw = scanline * height
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(raw, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _validate_chat_content(chat: dict[str, Any], *, operation: str) -> None:
    message = chat.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        thinking = message.get("thinking") if isinstance(message, dict) else None
        thinking_chars = len(thinking) if isinstance(thinking, str) else 0
        raise SystemExit(
            f"{operation} returned no usable message.content (thinking_chars={thinking_chars})"
        )
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{operation} returned invalid JSON content") from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"{operation} returned non-object JSON content")


def _probe_chat(config: PreflightConfig, *, image_b64: str | None, operation: str) -> None:
    message: dict[str, Any] = {
        "role": "user",
        "content": 'Return exactly one JSON object: {"ok":true}',
    }
    if image_b64 is not None:
        message["images"] = [image_b64]
    chat = _post_json(
        config.base_url,
        "/api/chat",
        {
            "model": config.model,
            "messages": [message],
            "stream": False,
            "format": {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "think": False,
            "options": {"temperature": 0, "num_predict": 128},
        },
        timeout_s=_DEFAULT_TIMEOUT_S,
        operation=operation,
    )
    _validate_chat_content(chat, operation=operation)


def run_preflight(config: PreflightConfig) -> None:
    version = _get_json(
        config.base_url,
        "/api/version",
        timeout_s=_READINESS_ATTEMPT_TIMEOUT_S,
        attempts=_READINESS_ATTEMPTS,
        retry_delay_s=_READINESS_RETRY_DELAY_S,
    ).get("version")
    print(f"Ollama version: {version if isinstance(version, str) else 'unknown'}")
    _provision_model(config)
    _probe_chat(config, image_b64=None, operation="Ollama structured chat capability probe")
    probe_png = _solid_white_png(config.probe_width, config.probe_height)
    image_b64 = base64.b64encode(probe_png).decode("ascii")
    _probe_chat(config, image_b64=image_b64, operation="Ollama A3 vision capability probe")
    print(
        f"Ollama model ready for N3 vision evaluation: {config.model} at {config.base_url} "
        f"(probe={config.probe_width}x{config.probe_height})"
    )


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("expected a positive integer")
    return value


def _base_url(raw: str) -> str:
    value = raw.rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise argparse.ArgumentTypeError("expected an absolute HTTP(S) URL")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, type=_base_url)
    parser.add_argument("--model", required=True)
    parser.add_argument("--source-model")
    parser.add_argument("--num-ctx", type=_positive_int)
    parser.add_argument("--probe-width", type=_positive_int, default=1260)
    parser.add_argument("--probe-height", type=_positive_int, default=891)
    return parser


def main() -> int:
    args = _parser().parse_args()
    run_preflight(
        PreflightConfig(
            base_url=args.base_url,
            model=args.model,
            source_model=args.source_model,
            num_ctx=args.num_ctx,
            probe_width=args.probe_width,
            probe_height=args.probe_height,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
