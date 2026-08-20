"""Direct Ollama chat client."""

from __future__ import annotations

from typing import Any

from kicad_pcb.errors import ToolError

from .base import BaseHttpLlmClient, LlmCompletion, LlmRequest
from .capabilities import LlmJsonMode, LlmTokenLimitMode


class OllamaLlmClient(BaseHttpLlmClient):
    """Ollama chat client using direct HTTP calls."""

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
            "stream": False,
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
