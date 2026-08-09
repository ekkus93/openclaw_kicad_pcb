"""Factory for constructing the configured web-app LLM client."""

from __future__ import annotations

import httpx

from kicad_pcb.errors import UserError

from ...settings import WebSettings
from .base import HttpLlmClientConfig, LlmClient
from .llama_server_client import LlamaServerLlmClient
from .ollama_client import OllamaLlmClient
from .openai_client import OpenAiLlmClient


def _http_config(settings: WebSettings, *, base_url: str) -> HttpLlmClientConfig:
    llm = settings.llm
    assert llm.model is not None
    return HttpLlmClientConfig(
        model=llm.model,
        base_url=base_url,
        api_key=llm.api_key,
        timeout_s=llm.timeout_s,
        default_temperature=llm.temperature,
        default_max_tokens=llm.max_tokens,
        retry_max_attempts=llm.retry_max_attempts,
        retry_base_delay_s=llm.retry_base_delay_s,
        retry_max_delay_s=llm.retry_max_delay_s,
        retry_jitter_s=llm.retry_jitter_s,
    )


def build_llm_client(
    settings: WebSettings,
    *,
    transport: httpx.BaseTransport | None = None,
) -> LlmClient | None:
    """Construct the configured LLM client, or ``None`` when disabled."""

    llm = settings.llm
    if not llm.enabled:
        return None
    if llm.model is None:
        raise UserError("LLM provider is enabled but llm.model is not configured.")

    if llm.provider == "openai":
        return OpenAiLlmClient(
            provider_name="openai",
            config=_http_config(settings, base_url=llm.base_url or "https://api.openai.com/v1"),
            transport=transport,
        )
    if llm.provider == "ollama":
        assert llm.base_url is not None
        return OllamaLlmClient(
            provider_name="ollama",
            config=_http_config(settings, base_url=llm.base_url),
            transport=transport,
        )
    if llm.provider == "llama_server":
        assert llm.base_url is not None
        return LlamaServerLlmClient(
            provider_name="llama_server",
            config=_http_config(settings, base_url=llm.base_url),
            transport=transport,
        )
    raise UserError(f"Unsupported LLM provider: {llm.provider}")
