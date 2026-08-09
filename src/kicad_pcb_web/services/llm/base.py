"""Shared LLM provider abstractions for the web wizard."""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    retry_jitter_s: float = 0.25


class LlmClient(Protocol):
    """Provider-agnostic synchronous LLM client interface."""

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Submit one normalized completion request."""


class BaseHttpLlmClient(ABC):
    """HTTP-backed base class with bounded response-aware retries."""

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
        self.retry_max_attempts = config.retry_max_attempts
        self.retry_base_delay_s = config.retry_base_delay_s
        self.retry_max_delay_s = config.retry_max_delay_s
        self.retry_jitter_s = config.retry_jitter_s
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

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        stripped = raw.strip()
        try:
            return max(0.0, float(stripped))
        except ValueError:
            pass
        try:
            retry_at = parsedate_to_datetime(stripped)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None

    def _retry_delay(self, response: httpx.Response, *, attempt: int) -> float:
        retry_after = self._retry_after_seconds(response)
        if retry_after is not None:
            return min(retry_after, self.retry_max_delay_s)
        backoff = min(
            self.retry_base_delay_s * (2 ** max(0, attempt - 1)),
            self.retry_max_delay_s,
        )
        jitter = random.random() * self.retry_jitter_s
        return min(backoff + jitter, self.retry_max_delay_s)

    def _post_json(self, *, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.retry_max_attempts + 1):
            started_at = time.perf_counter()
            payload_bytes, prompt_fingerprint = self._payload_metrics(payload)
            LOGGER.info(
                "llm request started",
                extra={
                    "provider": self.provider_name,
                    "endpoint": endpoint,
                    "base_url": self.base_url,
                    "attempt": attempt,
                    "timeout_s": self.timeout_s,
                    "payload_bytes": payload_bytes,
                    "prompt_fingerprint": prompt_fingerprint,
                },
            )
            try:
                response = self._client.post(endpoint, json=payload)
            except httpx.TransportError as exc:
                elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
                LOGGER.warning(
                    "llm request failed before response; automatic replay suppressed",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "error_type": type(exc).__name__,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                        "ambiguous_delivery": True,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} request failed before a response was received.",
                    details={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "ambiguous_delivery": True,
                        "automatic_retry": False,
                    },
                ) from exc

            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            if response.status_code in _RETRYABLE_STATUS_CODES:
                if attempt < self.retry_max_attempts:
                    delay_s = self._retry_delay(response, attempt=attempt)
                    LOGGER.warning(
                        "llm request received retryable status",
                        extra={
                            "provider": self.provider_name,
                            "endpoint": endpoint,
                            "attempt": attempt,
                            "status_code": response.status_code,
                            "elapsed_ms": elapsed_ms,
                            "retry_delay_s": round(delay_s, 3),
                            "payload_bytes": payload_bytes,
                            "prompt_fingerprint": prompt_fingerprint,
                        },
                    )
                    time.sleep(delay_s)
                    continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                LOGGER.warning(
                    "llm request failed with http status",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "status_code": exc.response.status_code,
                        "elapsed_ms": elapsed_ms,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} request failed with HTTP {exc.response.status_code}.",
                    details={
                        "provider": self.provider_name,
                        "status_code": exc.response.status_code,
                        "endpoint": endpoint,
                    },
                ) from exc

            try:
                parsed = response.json()
            except ValueError as exc:
                LOGGER.warning(
                    "llm request returned invalid json",
                    extra={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "payload_bytes": payload_bytes,
                        "prompt_fingerprint": prompt_fingerprint,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} returned invalid JSON.",
                    details={"provider": self.provider_name, "endpoint": endpoint},
                ) from exc

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
                    "attempt": attempt,
                    "status_code": response.status_code,
                    "elapsed_ms": elapsed_ms,
                    "payload_bytes": payload_bytes,
                    "prompt_fingerprint": prompt_fingerprint,
                },
            )
            return parsed

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
