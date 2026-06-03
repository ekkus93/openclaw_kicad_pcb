"""Direct llama-server chat client.

This client assumes an OpenAI-compatible `/chat/completions` API surface.
"""

from __future__ import annotations

from typing import Any

from .base import LlmRequest
from .openai_client import OpenAiLlmClient


class LlamaServerLlmClient(OpenAiLlmClient):
    """llama-server client using the OpenAI-compatible chat API contract."""

    def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "temperature": self._effective_temperature(request),
        }
        max_tokens = self._effective_max_tokens(request)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
        return "/chat/completions", payload
