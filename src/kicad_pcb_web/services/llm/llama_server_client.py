"""Direct llama-server chat client.

This client assumes an OpenAI-compatible `/chat/completions` API surface.
"""

from __future__ import annotations

from .openai_client import OpenAiLlmClient


class LlamaServerLlmClient(OpenAiLlmClient):
    """llama-server client using the OpenAI-compatible chat API contract."""

    pass