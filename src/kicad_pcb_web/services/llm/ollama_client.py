"""Direct Ollama chat client."""

from __future__ import annotations

from typing import Any

from kicad_pcb.errors import ToolError

from .base import BaseHttpLlmClient, LlmCompletion, LlmRequest


class OllamaLlmClient(BaseHttpLlmClient):
    """Ollama chat client using direct HTTP calls."""

    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "stream": False,
            "options": {"temperature": self._effective_temperature(request)},
        }
        max_tokens = self._effective_max_tokens(request)
        if max_tokens is not None:
            payload["options"]["num_predict"] = max_tokens
        if request.response_format == "json":
            payload["format"] = "json"
        return "/api/chat", payload

    def _parse_completion(self, payload: dict[str, Any]) -> LlmCompletion:
        message = payload.get("message")
        if not isinstance(message, dict):
            raise ToolError("ollama returned no message body.", details={"provider": "ollama"})
        return LlmCompletion(
            provider="ollama",
            model=str(payload.get("model") or self.model),
            content=self._coerce_text_content(message.get("content")),
            finish_reason=(
                str(payload["done_reason"]) if payload.get("done_reason") is not None else None
            ),
            request_id=None,
            raw_response=payload,
        )
