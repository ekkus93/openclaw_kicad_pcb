"""Shared LLM provider abstractions for the web wizard."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

import httpx

from kicad_pcb.errors import ToolError

from ...errors import (
    LlmCompletionRefusedError,
    LlmCompletionTruncatedError,
    LlmNoUsableContentError,
)
from .capabilities import LlmProviderCapabilities, LlmTerminalProtocol

LlmMessageRole = Literal["system", "user", "assistant"]
LlmResponseFormat = Literal["text", "json"]

RETRYABLE_HTTP_STATUS_CODES: frozenset[int] = frozenset({408, 429, 500, 502, 503, 504})

LOGGER = logging.getLogger("uvicorn.error")


def _safe_base_url_for_log(value: str) -> str:
    """Return origin-only provider metadata without credentials, path, query, or fragment."""

    parsed = urlsplit(value)
    safe_netloc = parsed.netloc.rsplit("@", 1)[-1]
    return f"{parsed.scheme}://{safe_netloc}"


class LlmCompletionOutcome(StrEnum):
    """Normalized terminal outcome vocabulary shared by provider clients."""

    COMPLETED = "completed"
    TRUNCATED = "truncated"
    REFUSED = "refused"
    FILTERED = "filtered"
    NO_USABLE_CONTENT = "no_usable_content"
    UNKNOWN_TERMINAL_REASON = "unknown_terminal_reason"


@dataclass(frozen=True)
class LlmMessage:
    """One normalized chat message passed to an LLM provider."""

    role: LlmMessageRole
    content: str


@dataclass(frozen=True)
class LlmImage:
    """One explicit image attachment for a model request."""

    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    base64_data: str


@dataclass(frozen=True)
class LlmRequest:
    """Normalized chat completion request."""

    messages: list[LlmMessage]
    response_format: LlmResponseFormat = "text"
    temperature: float | None = None
    max_tokens: int | None = None
    json_schema: dict[str, Any] | None = None
    images: tuple[LlmImage, ...] = ()


@dataclass(frozen=True)
class LlmCompletion:
    """Normalized provider completion response."""

    provider: str
    model: str
    content: str
    finish_reason: str | None = None
    request_id: str | None = None
    outcome: LlmCompletionOutcome = LlmCompletionOutcome.COMPLETED
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class HttpLlmClientConfig:
    """Shared HTTP client configuration for one LLM provider instance."""

    model: str
    base_url: str
    timeout_s: float
    default_temperature: float
    default_max_tokens: int | None
    capabilities: LlmProviderCapabilities
    temperature_mode: Literal["send", "omit"] = "send"
    api_key: str | None = None
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    retry_jitter_s: float = 0.25
    vision_enabled: bool = False


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
        if config.capabilities.provider != provider_name:
            raise ValueError(
                "LLM provider capability contract does not match configured provider: "
                f"provider={provider_name!r}, capabilities={config.capabilities.provider!r}"
            )
        if config.temperature_mode == "omit" and not config.capabilities.supports_temperature_omit:
            raise ValueError(
                "LLM provider capability contract does not support temperature omission: "
                f"provider={provider_name!r}"
            )

        self.provider_name = provider_name
        self.model = config.model
        self.base_url = config.base_url.rstrip("/")
        self.timeout_s = config.timeout_s
        self.default_temperature = config.default_temperature
        self.default_max_tokens = config.default_max_tokens
        self.temperature_mode = config.temperature_mode
        self.capabilities = config.capabilities
        self.api_key = config.api_key
        self.retry_max_attempts = config.retry_max_attempts
        self.retry_base_delay_s = config.retry_base_delay_s
        self.retry_max_delay_s = config.retry_max_delay_s
        self.retry_jitter_s = config.retry_jitter_s
        self.vision_enabled = config.vision_enabled
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

    @property
    def max_scheduled_retry_sleep_s(self) -> float:
        """Return the maximum total sleep scheduled between HTTP retries.

        This is a bound on this client's own backoff sleeps only. It is not an
        end-to-end HTTP request deadline; HTTPX uses per-phase inactivity timeouts.
        """

        return max(0, self.retry_max_attempts - 1) * self.retry_max_delay_s

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

    def _log_completion_outcome(
        self,
        *,
        outcome: LlmCompletionOutcome,
        finish_reason: str | None,
        request_id: str | None = None,
        response_chars: int | None = None,
    ) -> None:
        extra: dict[str, object] = {
            "provider": self.provider_name,
            "model": self.model,
            "completion_outcome": outcome.value,
            "finish_reason": finish_reason,
        }
        if request_id is not None:
            extra["provider_request_id"] = request_id
        if response_chars is not None:
            extra["response_chars"] = response_chars
        if outcome is LlmCompletionOutcome.COMPLETED:
            LOGGER.info("llm completion normalized", extra=extra)
        else:
            LOGGER.warning("llm completion normalized", extra=extra)

    def _validate_image_request(self, request: LlmRequest) -> None:
        if not request.images:
            return
        if not self.vision_enabled:
            raise ToolError(
                f"{self.provider_name} vision requests require explicit vision_enabled=true.",
                details={"provider": self.provider_name},
            )
        if not self.capabilities.supports_image_input:
            raise ToolError(
                f"{self.provider_name} capability contract does not support image input.",
                details={"provider": self.provider_name},
            )
        if len(request.images) > self.capabilities.max_images_per_request:
            raise ToolError(
                f"{self.provider_name} image count exceeds configured capability bound.",
                details={
                    "provider": self.provider_name,
                    "image_count": len(request.images),
                    "max_images": self.capabilities.max_images_per_request,
                },
            )
        for image in request.images:
            if image.media_type not in self.capabilities.accepted_image_media_types:
                raise ToolError(
                    f"{self.provider_name} image media type is unsupported.",
                    details={"provider": self.provider_name, "media_type": image.media_type},
                )
            try:
                decoded = base64.b64decode(image.base64_data, validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ToolError(
                    f"{self.provider_name} image payload is not valid base64.",
                    details={"provider": self.provider_name},
                ) from exc
            if not decoded:
                raise ToolError(
                    f"{self.provider_name} image payload is empty.",
                    details={"provider": self.provider_name},
                )
            if len(decoded) > self.capabilities.max_image_bytes:
                raise ToolError(
                    f"{self.provider_name} image exceeds configured capability byte bound.",
                    details={
                        "provider": self.provider_name,
                        "image_bytes": len(decoded),
                        "max_image_bytes": self.capabilities.max_image_bytes,
                    },
                )

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self._validate_image_request(request)
        endpoint, payload = self._build_payload(request)
        response_payload = self._post_json(endpoint=endpoint, payload=payload)
        completion = self._parse_completion(response_payload)
        self._log_completion_outcome(
            outcome=completion.outcome,
            finish_reason=completion.finish_reason,
            request_id=completion.request_id,
            response_chars=len(completion.content),
        )
        return completion

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
                    "base_url": _safe_base_url_for_log(self.base_url),
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
                        "retryable": False,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} request failed before a response was received.",
                    details={
                        "provider": self.provider_name,
                        "endpoint": endpoint,
                        "ambiguous_delivery": True,
                        "automatic_retry": False,
                        "retryable": False,
                    },
                ) from exc

            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            if (
                response.status_code in RETRYABLE_HTTP_STATUS_CODES
                and attempt < self.retry_max_attempts
            ):
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
                        "retryable": True,
                    },
                )
                time.sleep(delay_s)
                continue

            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as exc:
                retryable = exc.response.status_code in RETRYABLE_HTTP_STATUS_CODES
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
                        "retryable": retryable,
                    },
                )
                raise ToolError(
                    f"{self.provider_name} request failed with HTTP {exc.response.status_code}.",
                    details={
                        "provider": self.provider_name,
                        "status_code": exc.response.status_code,
                        "endpoint": endpoint,
                        "retryable": retryable,
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

    def _classify_terminal_outcome(
        self,
        finish_reason: str | None,
        *,
        message_refusal: bool = False,
    ) -> LlmCompletionOutcome:
        """Normalize provider terminal metadata without inspecting model names."""

        normalized_reason = (finish_reason or "").strip().lower()
        protocol = self.capabilities.terminal_protocol

        if message_refusal:
            return LlmCompletionOutcome.REFUSED
        if not normalized_reason or normalized_reason == "stop":
            return LlmCompletionOutcome.COMPLETED
        if normalized_reason == "length":
            return LlmCompletionOutcome.TRUNCATED
        if normalized_reason == "content_filter":
            return LlmCompletionOutcome.FILTERED
        if normalized_reason == "refusal":
            return LlmCompletionOutcome.REFUSED

        if protocol in {LlmTerminalProtocol.OPENAI_CHAT, LlmTerminalProtocol.OLLAMA_CHAT}:
            return LlmCompletionOutcome.UNKNOWN_TERMINAL_REASON

        raise ToolError(
            f"{self.provider_name} has an unsupported terminal-reason protocol.",
            details={
                "provider": self.provider_name,
                "terminal_protocol": str(protocol),
            },
        )

    def _raise_for_terminal_outcome(
        self,
        outcome: LlmCompletionOutcome,
        *,
        finish_reason: str | None,
    ) -> None:
        """Raise typed fail-closed errors for every non-success terminal outcome."""

        details: dict[str, object] = {
            "provider": self.provider_name,
            "finish_reason": finish_reason,
            "completion_outcome": outcome.value,
        }
        if outcome is LlmCompletionOutcome.COMPLETED:
            return
        self._log_completion_outcome(outcome=outcome, finish_reason=finish_reason)
        if outcome is LlmCompletionOutcome.TRUNCATED:
            raise LlmCompletionTruncatedError(
                "The configured LLM response was truncated; increase llm.max_tokens.",
                details=details,
            )
        if outcome in {LlmCompletionOutcome.REFUSED, LlmCompletionOutcome.FILTERED}:
            raise LlmCompletionRefusedError(
                "The configured LLM refused or content-filtered the response.",
                details=details,
            )
        if outcome is LlmCompletionOutcome.UNKNOWN_TERMINAL_REASON:
            raise ToolError(
                f"{self.provider_name} returned an unknown terminal reason; refusing content.",
                details=details,
            )
        if outcome is LlmCompletionOutcome.NO_USABLE_CONTENT:
            raise LlmNoUsableContentError(
                "The configured LLM provider returned no usable content.",
                details=details,
            )
        raise ToolError(
            f"{self.provider_name} returned an unsupported completion outcome.",
            details=details,
        )

    def _coerce_text_content(self, value: Any) -> str:
        if isinstance(value, str):
            content = value
        elif isinstance(value, list):
            text_parts = [
                item.get("text", "")
                for item in value
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            content = "".join(part for part in text_parts if part)
        elif value is None:
            self._log_completion_outcome(
                outcome=LlmCompletionOutcome.NO_USABLE_CONTENT,
                finish_reason=None,
            )
            raise LlmNoUsableContentError(
                "The configured LLM provider returned no usable content.",
                details={
                    "provider": self.provider_name,
                    "completion_outcome": LlmCompletionOutcome.NO_USABLE_CONTENT.value,
                },
            )
        else:
            raise ToolError(
                f"{self.provider_name} returned an unsupported content shape.",
                details={"provider": self.provider_name},
            )

        if not content.strip():
            self._log_completion_outcome(
                outcome=LlmCompletionOutcome.NO_USABLE_CONTENT,
                finish_reason=None,
            )
            raise LlmNoUsableContentError(
                "The configured LLM provider returned no usable content.",
                details={
                    "provider": self.provider_name,
                    "completion_outcome": LlmCompletionOutcome.NO_USABLE_CONTENT.value,
                },
            )
        return content

    @abstractmethod
    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        """Return the provider endpoint plus JSON payload for one request."""

    @abstractmethod
    def _parse_completion(self, payload: dict[str, Any]) -> LlmCompletion:
        """Normalize one provider response payload."""
