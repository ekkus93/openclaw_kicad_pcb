"""Direct llama-server chat client.

This client uses llama-server's OpenAI-compatible `/chat/completions` surface,
while its explicit capability contract selects llama-server token-limit semantics.
"""

from __future__ import annotations

from .openai_client import OpenAiLlmClient


class LlamaServerLlmClient(OpenAiLlmClient):
    """llama-server client using the OpenAI-compatible chat API contract."""
