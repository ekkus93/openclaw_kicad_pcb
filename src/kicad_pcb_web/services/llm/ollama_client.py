"""Direct Ollama chat client."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from kicad_pcb.errors import ToolError

from .base import (
    LOGGER,
    RETRYABLE_HTTP_STATUS_CODES,
    BaseHttpLlmClient,
    LlmCompletion,
    LlmRequest,
)
from .capabilities import LlmJsonMode, LlmTokenLimitMode


@dataclass(frozen=True)
class _StreamRequestTrace:
    endpoint: str
    attempt: int
    started_at: float
    payload_bytes: int
    prompt_fingerprint: str

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self.started_at) * 1000, 1)


class OllamaLlmClient(BaseHttpLlmClient):
    """Ollama chat client using direct HTTP calls."""

    def complete(self, request: LlmRequest) -> LlmCompletion:
        """Submit one Ollama request using internal NDJSON transport streaming."""

        self._validate_image_request(request)
        endpoint, payload = self._build_payload(request)
        response_payload = self._post_stream_json(endpoint=endpoint, payload=payload)
        completion = self._parse_completion(response_payload)
        self._log_completion_outcome(
            outcome=completion.outcome,
            finish_reason=completion.finish_reason,
            request_id=completion.request_id,
            response_chars=len(completion.content),
        )
        return completion

    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        if request.images:
            if not self.vision_enabled:
                raise ToolError(
                    "ollama vision requests require explicit vision_enabled=true.",
                    details={"provider": "ollama"},
                )
            user_indices = [i for i, message in enumerate(messages) if message["role"] == "user"]
            if not user_indices:
                raise ToolError("Vision request requires a user message.")
            index = user_indices[-1]
            messages[index] = {
                **messages[index],
                "images": [image.base64_data for image in request.images],
            }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "options": {},
        }
        if self.temperature_mode == "send":
            payload["options"]["temperature"] = self._effective_temperature(request)

        max_tokens = self._effective_max_tokens(request)
        if max_tokens is not None:
            if self.capabilities.token_limit_mode is not LlmTokenLimitMode.OLLAMA_NUM_PREDICT:
                raise ToolError(
                    "ollama has an unsupported token-limit capability.",
                    details={
                        "provider": self.provider_name,
                        "token_limit_mode": self.capabilities.token_limit_mode.value,
                    },
                )
            payload["options"]["num_predict"] = max_tokens

        if request.response_format == "json":
            if self.capabilities.json_mode is not LlmJsonMode.OLLAMA_JSON:
                raise ToolError(
                    "ollama has an unsupported structured-JSON capability.",
                    details={
                        "provider": self.provider_name,
                        "json_mode": self.capabilities.json_mode.value,
                    },
                )
            payload["format"] = request.json_schema if request.json_schema is not None else "json"
            # Structured callers consume only the final JSON content. Explicitly disable
            # thinking so thinking-capable Ollama models do not emit a separate reasoning
            # channel or starve the schema-bound final response.
            payload["think"] = False
        return "/api/chat", payload

    def _post_stream_json(self, *, endpoint: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self.retry_max_attempts + 1):
            trace = self._start_stream_trace(endpoint=endpoint, payload=payload, attempt=attempt)
            try:
                with self._client.stream("POST", endpoint, json=payload) as response:
                    retry_delay_s = self._stream_retry_delay(response=response, trace=trace)
                    if retry_delay_s is None:
                        self._raise_for_stream_status(response=response, trace=trace)
                        parsed = self._read_stream_payload(response)
                        self._log_stream_success(response=response, trace=trace)
                        return parsed
            except httpx.TransportError as exc:
                raise self._stream_transport_error(exc=exc, trace=trace) from exc

            time.sleep(retry_delay_s)
        raise AssertionError("unreachable")

    def _start_stream_trace(
        self,
        *,
        endpoint: str,
        payload: dict[str, Any],
        attempt: int,
    ) -> _StreamRequestTrace:
        payload_bytes, prompt_fingerprint = self._payload_metrics(payload)
        trace = _StreamRequestTrace(
            endpoint=endpoint,
            attempt=attempt,
            started_at=time.perf_counter(),
            payload_bytes=payload_bytes,
            prompt_fingerprint=prompt_fingerprint,
        )
        LOGGER.info(
            "llm request started",
            extra={
                "provider": self.provider_name,
                "endpoint": endpoint,
                "attempt": attempt,
                "timeout_s": self.timeout_s,
                "payload_bytes": payload_bytes,
                "prompt_fingerprint": prompt_fingerprint,
                "transport_streaming": True,
            },
        )
        return trace

    def _stream_retry_delay(
        self,
        *,
        response: httpx.Response,
        trace: _StreamRequestTrace,
    ) -> float | None:
        if (
            response.status_code not in RETRYABLE_HTTP_STATUS_CODES
            or trace.attempt >= self.retry_max_attempts
        ):
            return None

        retry_delay_s = self._retry_delay(response, attempt=trace.attempt)
        LOGGER.warning(
            "llm request received retryable status",
            extra={
                "provider": self.provider_name,
                "endpoint": trace.endpoint,
                "attempt": trace.attempt,
                "status_code": response.status_code,
                "elapsed_ms": trace.elapsed_ms,
                "retry_delay_s": round(retry_delay_s, 3),
                "payload_bytes": trace.payload_bytes,
                "prompt_fingerprint": trace.prompt_fingerprint,
                "retryable": True,
            },
        )
        return retry_delay_s

    def _raise_for_stream_status(
        self,
        *,
        response: httpx.Response,
        trace: _StreamRequestTrace,
    ) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            retryable = exc.response.status_code in RETRYABLE_HTTP_STATUS_CODES
            LOGGER.warning(
                "llm request failed with http status",
                extra={
                    "provider": self.provider_name,
                    "endpoint": trace.endpoint,
                    "attempt": trace.attempt,
                    "status_code": exc.response.status_code,
                    "elapsed_ms": trace.elapsed_ms,
                    "payload_bytes": trace.payload_bytes,
                    "prompt_fingerprint": trace.prompt_fingerprint,
                    "retryable": retryable,
                },
            )
            raise ToolError(
                f"{self.provider_name} request failed with HTTP {exc.response.status_code}.",
                details={
                    "provider": self.provider_name,
                    "status_code": exc.response.status_code,
                    "endpoint": trace.endpoint,
                    "retryable": retryable,
                },
            ) from exc

    def _stream_transport_error(
        self,
        *,
        exc: httpx.TransportError,
        trace: _StreamRequestTrace,
    ) -> ToolError:
        elapsed_ms = trace.elapsed_ms
        LOGGER.warning(
            "llm request failed before terminal response; automatic replay suppressed",
            extra={
                "provider": self.provider_name,
                "endpoint": trace.endpoint,
                "attempt": trace.attempt,
                "elapsed_ms": elapsed_ms,
                "error_type": type(exc).__name__,
                "payload_bytes": trace.payload_bytes,
                "prompt_fingerprint": trace.prompt_fingerprint,
                "ambiguous_delivery": True,
                "retryable": False,
            },
        )
        return ToolError(
            f"{self.provider_name} request failed before a terminal response was received.",
            details={
                "provider": self.provider_name,
                "endpoint": trace.endpoint,
                "ambiguous_delivery": True,
                "automatic_retry": False,
                "retryable": False,
                "error_type": type(exc).__name__,
                "elapsed_ms": elapsed_ms,
            },
        )

    def _log_stream_success(
        self,
        *,
        response: httpx.Response,
        trace: _StreamRequestTrace,
    ) -> None:
        LOGGER.info(
            "llm request succeeded",
            extra={
                "provider": self.provider_name,
                "endpoint": trace.endpoint,
                "attempt": trace.attempt,
                "status_code": response.status_code,
                "elapsed_ms": trace.elapsed_ms,
                "payload_bytes": trace.payload_bytes,
                "prompt_fingerprint": trace.prompt_fingerprint,
                "transport_streaming": True,
            },
        )

    def _read_stream_payload(self, response: httpx.Response) -> dict[str, Any]:
        content_parts: list[str] = []
        terminal_payload: dict[str, Any] | None = None
        chunk_count = 0

        for line in response.iter_lines():
            if not line.strip():
                continue
            chunk_count += 1
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ToolError(
                    "ollama returned invalid streaming JSON.",
                    details={"provider": self.provider_name, "endpoint": "/api/chat"},
                ) from exc
            if not isinstance(chunk, dict):
                raise ToolError(
                    "ollama returned a non-object streaming payload.",
                    details={"provider": self.provider_name, "endpoint": "/api/chat"},
                )
            message = chunk.get("message")
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    content_parts.append(content)
            if chunk.get("done") is True or chunk.get("done_reason") is not None:
                terminal_payload = chunk

        if terminal_payload is None:
            raise ToolError(
                "ollama stream ended before a terminal completion payload.",
                details={
                    "provider": self.provider_name,
                    "endpoint": "/api/chat",
                    "stream_chunk_count": chunk_count,
                },
            )

        message = terminal_payload.get("message")
        terminal_message = dict(message) if isinstance(message, dict) else {}
        terminal_message["content"] = "".join(content_parts)
        return {**terminal_payload, "message": terminal_message}

    def _parse_completion(self, payload: dict[str, Any]) -> LlmCompletion:
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ToolError("ollama returned no message body.", details={"provider": "ollama"})

        finish_reason = (
            str(payload["done_reason"]) if payload.get("done_reason") is not None else None
        )
        outcome = self._classify_terminal_outcome(finish_reason)
        self._raise_for_terminal_outcome(outcome, finish_reason=finish_reason)

        return LlmCompletion(
            provider="ollama",
            model=str(payload.get("model") or self.model),
            content=self._coerce_text_content(message.get("content")),
            finish_reason=finish_reason,
            request_id=None,
            outcome=outcome,
            raw_response=payload,
        )
