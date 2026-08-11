"""Direct OpenAI-compatible chat-completions client."""

from __future__ import annotations

from typing import Any

from kicad_pcb.errors import ToolError

from .base import BaseHttpLlmClient, LlmCompletion, LlmRequest
from .capabilities import LlmJsonMode, LlmTokenLimitMode


class OpenAiLlmClient(BaseHttpLlmClient):
    """OpenAI-compatible chat-completions client using direct HTTP calls."""

    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        messages: list[dict[str, Any]] = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        if request.images:
            if not self.vision_enabled:
                raise ToolError(
                    f"{self.provider_name} vision requests require explicit vision_enabled=true.",
                    details={"provider": self.provider_name},
                )
            user_indices = [i for i, message in enumerate(messages) if message["role"] == "user"]
            if not user_indices:
                raise ToolError("Vision request requires a user message.")
            index = user_indices[-1]
            text = str(messages[index]["content"])
            content: list[dict[str, Any]] = [{"type": "text", "text": text}]
            for image in request.images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image.media_type};base64,{image.base64_data}"},
                    }
                )
            messages[index] = {"role": "user", "content": content}
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if self.temperature_mode == "send":
            payload["temperature"] = self._effective_temperature(request)

        max_tokens = self._effective_max_tokens(request)
        if max_tokens is not None:
            if self.capabilities.token_limit_mode is LlmTokenLimitMode.MAX_COMPLETION_TOKENS:
                payload["max_completion_tokens"] = max_tokens
            elif self.capabilities.token_limit_mode is LlmTokenLimitMode.MAX_TOKENS:
                payload["max_tokens"] = max_tokens
            else:
                raise ToolError(
                    f"{self.provider_name} has an unsupported token-limit capability.",
                    details={
                        "provider": self.provider_name,
                        "token_limit_mode": self.capabilities.token_limit_mode.value,
                    },
                )

        if request.response_format == "json":
            if self.capabilities.json_mode is not LlmJsonMode.OPENAI_JSON_OBJECT:
                raise ToolError(
                    f"{self.provider_name} has an unsupported structured-JSON capability.",
                    details={
                        "provider": self.provider_name,
                        "json_mode": self.capabilities.json_mode.value,
                    },
                )
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
        outcome = self._classify_terminal_outcome(
            finish_reason,
            message_refusal=bool(message.get("refusal")),
        )
        self._raise_for_terminal_outcome(outcome, finish_reason=finish_reason)

        return LlmCompletion(
            provider=self.provider_name,
            model=str(payload.get("model") or self.model),
            content=self._coerce_text_content(message.get("content")),
            finish_reason=finish_reason,
            request_id=(
                str(payload["id"])
                if self.capabilities.request_id_available and payload.get("id") is not None
                else None
            ),
            outcome=outcome,
            raw_response=payload,
        )
