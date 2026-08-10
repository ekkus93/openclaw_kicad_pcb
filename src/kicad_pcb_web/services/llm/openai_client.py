"""Direct OpenAI chat-completions client."""

from __future__ import annotations

from typing import Any

from kicad_pcb.errors import ToolError

from ...errors import LlmCompletionRefusedError, LlmCompletionTruncatedError
from .base import BaseHttpLlmClient, LlmCompletion, LlmRequest


class OpenAiLlmClient(BaseHttpLlmClient):
    """OpenAI chat-completions client using direct HTTP calls."""

    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
        }
        if self.temperature_mode == "send":
            payload["temperature"] = self._effective_temperature(request)
        max_tokens = self._effective_max_tokens(request)
        if max_tokens is not None:
            payload["max_completion_tokens"] = max_tokens
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        return "/chat/completions", payload

    def _parse_completion(self, payload: dict[str, Any]) -> LlmCompletion:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ToolError(
                f"{self.provider_name} returned no completion choices.",
                details={"provider": self.provider_name},
            )
        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise ToolError(
                f"{self.provider_name} returned an invalid completion choice.",
                details={"provider": self.provider_name},
            )
        message = first_choice.get("message")
        if not isinstance(message, dict):
            raise ToolError(
                f"{self.provider_name} returned a choice without a message body.",
                details={"provider": self.provider_name},
            )

        finish_reason = (
            str(first_choice["finish_reason"])
            if first_choice.get("finish_reason") is not None
            else None
        )
        normalized_reason = (finish_reason or "").strip().lower()
        if normalized_reason == "length":
            raise LlmCompletionTruncatedError(
                "The configured LLM response was truncated; increase llm.max_tokens.",
                details={"provider": self.provider_name, "finish_reason": finish_reason},
            )
        if normalized_reason in {"content_filter", "refusal"} or message.get("refusal"):
            raise LlmCompletionRefusedError(
                "The configured LLM refused or content-filtered the response.",
                details={"provider": self.provider_name, "finish_reason": finish_reason},
            )

        return LlmCompletion(
            provider=self.provider_name,
            model=str(payload.get("model") or self.model),
            content=self._coerce_text_content(message.get("content")),
            finish_reason=finish_reason,
            request_id=str(payload["id"]) if payload.get("id") is not None else None,
            raw_response=payload,
        )
