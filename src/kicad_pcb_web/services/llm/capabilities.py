"""Explicit provider capability contracts for wizard LLM integrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class LlmJsonMode(StrEnum):
    """Provider-specific structured-JSON request mechanism."""

    OPENAI_JSON_OBJECT = "openai_json_object"
    OLLAMA_JSON = "ollama_json"


class LlmTokenLimitMode(StrEnum):
    """Provider-specific outbound token-limit field semantics."""

    MAX_COMPLETION_TOKENS = "max_completion_tokens"
    MAX_TOKENS = "max_tokens"
    OLLAMA_NUM_PREDICT = "ollama_num_predict"


class LlmTerminalProtocol(StrEnum):
    """Provider response protocol used to normalize terminal outcomes."""

    OPENAI_CHAT = "openai_chat"
    OLLAMA_CHAT = "ollama_chat"


@dataclass(frozen=True)
class LlmProviderCapabilities:
    """Explicit behavior contract for one enabled provider family.

    Capabilities are selected only from configured provider family. Model names are
    deliberately absent so no model-name substring heuristic can change behavior.
    """

    provider: str
    supports_temperature_omit: bool
    json_mode: LlmJsonMode
    token_limit_mode: LlmTokenLimitMode
    terminal_protocol: LlmTerminalProtocol
    request_id_available: bool
    idempotency_key_supported: bool
    supports_image_input: bool
    accepted_image_media_types: tuple[str, ...]
    max_images_per_request: int
    max_image_bytes: int


_PROVIDER_CAPABILITIES: Mapping[str, LlmProviderCapabilities] = MappingProxyType(
    {
        "openai": LlmProviderCapabilities(
            provider="openai",
            supports_temperature_omit=True,
            json_mode=LlmJsonMode.OPENAI_JSON_OBJECT,
            token_limit_mode=LlmTokenLimitMode.MAX_COMPLETION_TOKENS,
            terminal_protocol=LlmTerminalProtocol.OPENAI_CHAT,
            request_id_available=True,
            idempotency_key_supported=False,
            supports_image_input=True,
            accepted_image_media_types=("image/png", "image/jpeg", "image/webp"),
            max_images_per_request=4,
            max_image_bytes=8 * 1024 * 1024,
        ),
        "llama_server": LlmProviderCapabilities(
            provider="llama_server",
            supports_temperature_omit=True,
            json_mode=LlmJsonMode.OPENAI_JSON_OBJECT,
            token_limit_mode=LlmTokenLimitMode.MAX_TOKENS,
            terminal_protocol=LlmTerminalProtocol.OPENAI_CHAT,
            request_id_available=True,
            idempotency_key_supported=False,
            supports_image_input=True,
            accepted_image_media_types=("image/png", "image/jpeg", "image/webp"),
            max_images_per_request=4,
            max_image_bytes=8 * 1024 * 1024,
        ),
        "ollama": LlmProviderCapabilities(
            provider="ollama",
            supports_temperature_omit=False,
            json_mode=LlmJsonMode.OLLAMA_JSON,
            token_limit_mode=LlmTokenLimitMode.OLLAMA_NUM_PREDICT,
            terminal_protocol=LlmTerminalProtocol.OLLAMA_CHAT,
            request_id_available=False,
            idempotency_key_supported=False,
            supports_image_input=True,
            accepted_image_media_types=("image/png", "image/jpeg", "image/webp"),
            max_images_per_request=4,
            max_image_bytes=8 * 1024 * 1024,
        ),
    }
)


def get_llm_provider_capabilities(provider: str) -> LlmProviderCapabilities:
    """Return the explicit capability contract for an enabled provider family."""

    try:
        return _PROVIDER_CAPABILITIES[provider]
    except KeyError as exc:
        raise ValueError(f"Unsupported enabled LLM provider capability set: {provider}") from exc
