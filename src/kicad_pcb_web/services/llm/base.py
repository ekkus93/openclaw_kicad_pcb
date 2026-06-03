"""Shared LLM provider abstractions for the web wizard."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx

from kicad_pcb.errors import ToolError

LlmMessageRole = Literal["system", "user", "assistant"]
LlmResponseFormat = Literal["text", "json"]

_RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

LOGGER = logging.getLogger("uvicorn.error")


@dataclass(frozen=True)
class LlmMessage:
    """One normalized chat message passed to an LLM provider."""

    role: LlmMessageRole
    content: str


@dataclass(frozen=True)
class LlmRequest:
    """Normalized chat completion request."""

    messages: list[LlmMessage]
    response_format: LlmResponseFormat = "text"
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass(frozen=True)
class LlmCompletion:
    """Normalized provider completion response."""

    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    request_id: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class HttpLlmClientConfig:
    """Shared HTTP client configuration for one LLM provider instance."""

    model: str
    base_url: str
    timeout_s: float
    default_temperature: float
    default_max_tokens: int | None
    api_key: str | None = None


class LlmClient(Protocol):
    """Provider-agnostic synchronous LLM client interface."""

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Submit one normalized completion request."""


class BaseHttpLlmClient(ABC):
    """HTTP-backed base class with retry and response normalization hooks."""

    def __init__(
        self,
        *,
        provider_name: str,
        config: HttpLlmClientConfig,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.provider_name = provider_name
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.timeout_s = config.timeout_s
        self.default_temperature = config.default_temperature
        self.default_max_tokens = config.default_max_tokens
        self.api_key = config.api_key
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout_s,
            transport=transport,
            headers=self._build_default_headers(),
        )

    def _build_default_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _effective_temperature(self, request: LlmRequest) -> float:
        if request.temperature is not None:
            return request.temperature
        return self.default_temperature

    def _effective_max_tokens(self, request: LlmRequest) -> int | None:
        if request.max_tokens is not None:
            return request.max_tokens
        return self.default_max_tokens

    def _payload_metrics(self, payload: dict[str, Any]) -> tuple[int, str]:
        canonical_payload = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        payload_bytes = len(canonical_payload)

        message_payload = payload.get("messages")
        if isinstance(message_payload, list):
            canonical_prompt = json.dumps(
                message_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        else:
            canonical_prompt = canonical_payload

        prompt_fingerprint = hashlib.sha256(canonical_prompt).hexdigest()[:16]
        return payload_bytes, prompt_fingerprint

    def complete(self, request: LlmRequest) -> LlmCompletion:
        endpoint, payload = self._build_payload(request)
        response_payload = self._post_json(endpoint=endpoint, payload=payload)
        return self._parse_completion(response_payload)

    def close(self) -> None:
        """Release the underlying HTTP client resources."""

        self._client.close()

    def _post_json(self, *, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(3):
            started_at = time.perf_counter()
            payload_bytes, prompt_fingerprint = self._payload_metrics(payload)
            LOGGER.info(
                "llm request started",
                extra={
                    "provider": self.provider_name,
                    "endpoint": endpoint,
                    "base_url": self.base_url,
                    "attempt": attempt + 1,
                    "timeout_s": self.timeout_s,
                    "payload_bytes": payload_bytes,
                    "prompt_fingerprint": prompt_fingerprint,
                },
            )
            try:
                response = self._client.post(endpoint, json=payload)
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                if response.status_code in _RETRYABLE_STATUS_CODES and attempt < 2:
                    LOGGER.warning(
                        "llm request received retryable status",
                        extra={
                            "provider": self.provider_name,
                            "endpoint": endpoint,
                            "attempt": attempt + 1,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "payload_bytes": payload_bytes,
                            "prompt_fingerprint": prompt_fingerprint,
                        },
                    )
                    time.sleep(0.05 + random.random() * 0.05)
                    continue
                response.raise_for_status()
                parsed = response.json()
                if not isinstance(parsed, dict):
                    raise ToolError(
                        f"{self.provider_name} returned a non-object JSON payload.",
                        details={"provider": self.provider_name},
                    )
                LOGGER.info(
                    "llm request succeeded",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt + 1,
                        "status_code": response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                return parsed
            except httpx.HTTPStatusError as exc:
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                last_error = ToolError(
                    f"{self.provider_name} request failed with HTTP {exc.response.status_code}.",
                    details={
                        "provider": self.provider_name,
                        "status_code": exc.response.status_code,
                        "endpoint": endpoint,
                    },
                )
                LOGGER.warning(
                    "llm request failed with http status",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt + 1,
                        "status_code": exc.response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                if exc.response.status_code in _RETRYABLE_STATUS_CODES and attempt < 2:
                    time.sleep(0.05 + random.random() * 0.05)
                    continue
                raise last_error from exc
            except httpx.TransportError as exc:
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                last_error = ToolError(
                    f"{self.provider_name} request failed before a response was received.",
                    details={"provider": self.provider_name, "endpoint": endpoint},
                )
                LOGGER.warning(
                    "llm request failed before response",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt + 1,
                        "elapsed_ms": elapsed_ms,
                        "error_type": type(exc).__name__,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                if attempt < 2:
                    time.sleep(0.05 + random.random() * 0.05)
                    continue
                raise last_error from exc
            except ValueError as exc:
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                LOGGER.warning(
                    "llm request returned invalid json",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt + 1,
                        "elapsed_ms": elapsed_ms,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} returned invalid JSON.",
                    details={"provider": self.provider_name, "endpoint": endpoint},
                ) from exc

        if last_error is not None:
            raise last_error
        raise ToolError(f"{self.provider_name} request failed unexpectedly.")

    def _coerce_text_content(self, value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, list):
            text_parts = [
                item.get("text", "")
                for item in value
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "".join(part for part in text_parts if part)
        raise ToolError(
            f"{self.provider_name} returned an unsupported content shape.",
            details={"provider": self.provider_name},
        )

    @abstractmethod
    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        """Return the provider endpoint plus JSON payload for one request."""

    @abstractmethod
    def _parse_completion(self, payload: dict[str, Any]) -> LlmCompletion:
        """Normalize one provider response payload."""
