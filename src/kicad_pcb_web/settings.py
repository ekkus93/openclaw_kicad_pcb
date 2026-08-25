"""Web-app settings."""

from __future__ import annotations

import math
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlparse

LlmProvider = Literal["disabled", "openai", "ollama", "llama_server"]
LlmTemperatureMode = Literal["send", "omit"]

_VALID_LLM_PROVIDERS: frozenset[str] = frozenset({"disabled", "openai", "ollama", "llama_server"})
_VALID_TEMPERATURE_MODES: frozenset[str] = frozenset({"send", "omit"})
_TOP_LEVEL_CONFIG_KEYS: frozenset[str] = frozenset({"web", "llm"})
_WEB_CONFIG_KEYS: frozenset[str] = frozenset({"data_dir", "mutation_lock_timeout_s"})
_LLM_CONFIG_KEYS: frozenset[str] = frozenset(
    {
        "provider",
        "model",
        "base_url",
        "api_key",
        "timeout_s",
        "temperature",
        "temperature_mode",
        "max_tokens",
        "system_prompt_version",
        "spec_max_repair_rounds",
        "ir_max_repair_rounds",
        "enable_streaming",
        "request_log_redaction",
        "debug_artifact_capture",
        "vision_enabled",
        "retry_max_attempts",
        "retry_base_delay_s",
        "retry_max_delay_s",
        "retry_jitter_s",
    }
)
_REMOVED_NETWORK_PROBE_ENV = "KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED"
_REMOVED_WEB_BIND_ENVS: frozenset[str] = frozenset({"KICAD_PCB_WEB_HOST", "KICAD_PCB_WEB_PORT"})


@dataclass(frozen=True)
class LlmSettings:
    """Runtime LLM provider settings for the wizard workflow."""

    provider: LlmProvider = "disabled"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_s: float = 60.0
    temperature: float = 0.2
    # ``omit`` is supported by OpenAI and llama-server; Ollama currently requires ``send``.
    temperature_mode: LlmTemperatureMode = "send"
    max_tokens: int | None = None
    system_prompt_version: str = "v1"
    spec_max_repair_rounds: int = 2
    ir_max_repair_rounds: int = 2
    enable_streaming: bool = False
    request_log_redaction: bool = True
    debug_artifact_capture: bool = False
    # Explicit runtime opt-in. Provider/model names never imply image capability.
    vision_enabled: bool = False
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    retry_jitter_s: float = 0.25

    @property
    def enabled(self) -> bool:
        """Return whether an LLM provider is enabled."""

        return self.provider != "disabled"


@dataclass(frozen=True)
class WebSettings:
    """Runtime settings for the local web app."""

    data_dir: Path
    jobs_dir: Path
    mutation_lock_timeout_s: float = 2.0
    llm: LlmSettings = LlmSettings()


@dataclass(frozen=True)
class _ConfigFile:
    """Resolved optional config file payload plus its base directory."""

    path: Path | None
    payload: dict[str, Any]


def _reject_unknown_keys(payload: dict[str, Any], *, allowed: frozenset[str], section: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"Unsupported {section} config setting(s): {', '.join(unknown)}")


def _load_config_file() -> _ConfigFile:
    """Load the optional TOML config file, failing if an explicit path is unusable."""

    config_path_env = os.environ.get("KICAD_PCB_WEB_CONFIG_FILE")
    if config_path_env is not None:
        if not config_path_env.strip():
            raise ValueError("KICAD_PCB_WEB_CONFIG_FILE must not be empty when explicitly set")
        config_path = Path(config_path_env).expanduser().resolve()
        explicit_path = True
    else:
        config_path = Path("kicad_pcb_web.toml").resolve()
        explicit_path = False
    if not config_path.is_file():
        if explicit_path:
            raise ValueError(
                "Configured KICAD_PCB_WEB_CONFIG_FILE does not exist or is not a regular file: "
                f"{config_path}"
            )
        return _ConfigFile(path=None, payload={})

    try:
        with config_path.open("rb") as handle:
            payload = tomllib.load(handle)
    except OSError as exc:
        raise ValueError(f"Unable to read configured web config file: {config_path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Web config file must contain TOML tables: {config_path}")
    _reject_unknown_keys(payload, allowed=_TOP_LEVEL_CONFIG_KEYS, section="top-level")
    return _ConfigFile(path=config_path, payload=payload)


def _coerce_string(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    return value


def _coerce_optional_string(value: Any, *, field_name: str) -> str | None:
    if value is None:
        return None
    return _coerce_string(value, field_name=field_name)


def _coerce_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"Invalid float for {field_name}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float for {field_name}") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be a finite number")
    return result


def _coerce_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"Invalid integer for {field_name}")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid integer for {field_name}") from exc


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        raise ValueError(f"Invalid boolean for {field_name}")
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {field_name}")


def _read_setting(*, env_name: str, config_value: Any, default: Any) -> Any:
    """Read one setting with env override over config file and fallback default."""

    env_value = os.environ.get(env_name)
    if env_value is not None:
        return env_value
    if config_value is not None:
        return config_value
    return default


def _load_llm_settings(config: dict[str, Any]) -> LlmSettings:
    """Load LLM provider settings from env/config."""

    if _REMOVED_NETWORK_PROBE_ENV in os.environ:
        raise ValueError(
            f"{_REMOVED_NETWORK_PROBE_ENV} has been removed; "
            "active network probing is not implemented"
        )

    llm_config = config.get("llm")
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise ValueError("[llm] config must be a TOML table")
    _reject_unknown_keys(llm_config, allowed=_LLM_CONFIG_KEYS, section="[llm]")

    provider_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_PROVIDER",
        config_value=llm_config.get("provider"),
        default="disabled",
    )
    provider = _coerce_string(provider_raw, field_name="llm.provider")
    if provider not in _VALID_LLM_PROVIDERS:
        raise ValueError(
            "Invalid KICAD_PCB_WEB_LLM_PROVIDER; expected one of: "
            + ", ".join(sorted(_VALID_LLM_PROVIDERS))
        )

    timeout_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_TIMEOUT_S",
        config_value=llm_config.get("timeout_s"),
        default=60.0,
    )
    temperature_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_TEMPERATURE",
        config_value=llm_config.get("temperature"),
        default=0.2,
    )
    temperature_mode_raw = _coerce_string(
        _read_setting(
            env_name="KICAD_PCB_WEB_LLM_TEMPERATURE_MODE",
            config_value=llm_config.get("temperature_mode"),
            default="send",
        ),
        field_name="llm.temperature_mode",
    )
    if temperature_mode_raw not in _VALID_TEMPERATURE_MODES:
        raise ValueError("llm.temperature_mode must be one of: omit, send")
    max_tokens_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_MAX_TOKENS",
        config_value=llm_config.get("max_tokens"),
        default=None,
    )
    spec_max_repair_rounds_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_SPEC_MAX_REPAIR_ROUNDS",
        config_value=llm_config.get("spec_max_repair_rounds"),
        default=2,
    )
    ir_max_repair_rounds_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_IR_MAX_REPAIR_ROUNDS",
        config_value=llm_config.get("ir_max_repair_rounds"),
        default=2,
    )
    enable_streaming_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_ENABLE_STREAMING",
        config_value=llm_config.get("enable_streaming"),
        default=False,
    )
    request_log_redaction_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_REQUEST_LOG_REDACTION",
        config_value=llm_config.get("request_log_redaction"),
        default=True,
    )
    debug_artifact_capture_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_DEBUG_ARTIFACT_CAPTURE",
        config_value=llm_config.get("debug_artifact_capture"),
        default=False,
    )
    vision_enabled_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_VISION_ENABLED",
        config_value=llm_config.get("vision_enabled"),
        default=False,
    )
    retry_max_attempts_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_RETRY_MAX_ATTEMPTS",
        config_value=llm_config.get("retry_max_attempts"),
        default=3,
    )
    retry_base_delay_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_RETRY_BASE_DELAY_S",
        config_value=llm_config.get("retry_base_delay_s"),
        default=0.5,
    )
    retry_max_delay_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_RETRY_MAX_DELAY_S",
        config_value=llm_config.get("retry_max_delay_s"),
        default=8.0,
    )
    retry_jitter_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_RETRY_JITTER_S",
        config_value=llm_config.get("retry_jitter_s"),
        default=0.25,
    )

    settings = LlmSettings(
        provider=cast(LlmProvider, provider),
        model=_coerce_optional_string(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_MODEL",
                config_value=llm_config.get("model"),
                default=None,
            ),
            field_name="llm.model",
        ),
        base_url=_coerce_optional_string(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_BASE_URL",
                config_value=llm_config.get("base_url"),
                default=None,
            ),
            field_name="llm.base_url",
        ),
        api_key=_coerce_optional_string(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_API_KEY",
                config_value=llm_config.get("api_key"),
                default=None,
            ),
            field_name="llm.api_key",
        ),
        timeout_s=_coerce_float(timeout_raw, field_name="llm.timeout_s"),
        temperature=_coerce_float(temperature_raw, field_name="llm.temperature"),
        temperature_mode=cast(LlmTemperatureMode, temperature_mode_raw),
        max_tokens=(
            None
            if max_tokens_raw in (None, "")
            else _coerce_int(max_tokens_raw, field_name="llm.max_tokens")
        ),
        system_prompt_version=_coerce_string(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_SYSTEM_PROMPT_VERSION",
                config_value=llm_config.get("system_prompt_version"),
                default="v1",
            ),
            field_name="llm.system_prompt_version",
        ),
        spec_max_repair_rounds=_coerce_int(
            spec_max_repair_rounds_raw, field_name="llm.spec_max_repair_rounds"
        ),
        ir_max_repair_rounds=_coerce_int(
            ir_max_repair_rounds_raw, field_name="llm.ir_max_repair_rounds"
        ),
        enable_streaming=_coerce_bool(enable_streaming_raw, field_name="llm.enable_streaming"),
        request_log_redaction=_coerce_bool(
            request_log_redaction_raw, field_name="llm.request_log_redaction"
        ),
        debug_artifact_capture=_coerce_bool(
            debug_artifact_capture_raw, field_name="llm.debug_artifact_capture"
        ),
        vision_enabled=_coerce_bool(vision_enabled_raw, field_name="llm.vision_enabled"),
        retry_max_attempts=_coerce_int(retry_max_attempts_raw, field_name="llm.retry_max_attempts"),
        retry_base_delay_s=_coerce_float(retry_base_delay_raw, field_name="llm.retry_base_delay_s"),
        retry_max_delay_s=_coerce_float(retry_max_delay_raw, field_name="llm.retry_max_delay_s"),
        retry_jitter_s=_coerce_float(retry_jitter_raw, field_name="llm.retry_jitter_s"),
    )
    _validate_llm_settings(settings)
    return settings


def _validate_http_url(value: str, *, field_name: str) -> None:
    try:
        parsed = urlparse(value)
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"Invalid URL for {field_name}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.hostname is None
        or any(character.isspace() for character in parsed.netloc)
        or parsed.username is not None
        or parsed.password is not None
        or bool(parsed.query)
        or bool(parsed.fragment)
    ):
        raise ValueError(f"Invalid URL for {field_name}")


def _require_non_empty(value: str | None, *, field_name: str) -> None:
    if value is None or not value.strip():
        raise ValueError(f"Missing required setting: {field_name}")


def _validate_llm_settings(settings: LlmSettings) -> None:
    if settings.timeout_s <= 0:
        raise ValueError("llm.timeout_s must be greater than zero")
    if settings.timeout_s > 600:
        raise ValueError("llm.timeout_s must be 600 seconds or less")
    if settings.temperature_mode not in _VALID_TEMPERATURE_MODES:
        raise ValueError("llm.temperature_mode must be one of: omit, send")
    if settings.provider == "ollama" and settings.temperature_mode == "omit":
        raise ValueError("llm.temperature_mode=omit is not implemented for llm.provider=ollama")
    if settings.temperature < 0 or settings.temperature > 2:
        raise ValueError("llm.temperature must be between 0 and 2")
    if settings.max_tokens is not None and settings.max_tokens <= 0:
        raise ValueError("llm.max_tokens must be greater than zero when set")
    if settings.spec_max_repair_rounds < 0:
        raise ValueError("llm.spec_max_repair_rounds must be zero or greater")
    if settings.ir_max_repair_rounds < 0:
        raise ValueError("llm.ir_max_repair_rounds must be zero or greater")
    if not settings.system_prompt_version.strip():
        raise ValueError("llm.system_prompt_version must not be empty")
    if settings.enable_streaming:
        raise ValueError("llm.enable_streaming=true is unsupported; streaming is not implemented")
    if not settings.request_log_redaction:
        raise ValueError(
            "llm.request_log_redaction=false is unsupported; "
            "provider request logs are always redacted"
        )
    if not 1 <= settings.retry_max_attempts <= 10:
        raise ValueError("llm.retry_max_attempts must be between 1 and 10")
    if settings.retry_base_delay_s < 0:
        raise ValueError("llm.retry_base_delay_s must be zero or greater")
    if settings.retry_max_delay_s < settings.retry_base_delay_s:
        raise ValueError("llm.retry_max_delay_s must be at least llm.retry_base_delay_s")
    if settings.retry_max_delay_s > 60:
        raise ValueError("llm.retry_max_delay_s must be 60 seconds or less")
    if settings.retry_jitter_s < 0:
        raise ValueError("llm.retry_jitter_s must be zero or greater")
    if settings.retry_jitter_s > settings.retry_max_delay_s:
        raise ValueError("llm.retry_jitter_s must not exceed llm.retry_max_delay_s")

    if settings.base_url is not None:
        _validate_http_url(settings.base_url, field_name="llm.base_url")

    if settings.provider == "disabled":
        return

    _require_non_empty(settings.model, field_name="llm.model")

    if settings.provider == "openai":
        _require_non_empty(settings.api_key, field_name="llm.api_key")
        return

    if settings.provider in {"ollama", "llama_server"}:
        _require_non_empty(settings.base_url, field_name="llm.base_url")


def _resolve_config_path(raw_value: Any, *, config_file: _ConfigFile) -> Path:
    if not isinstance(raw_value, str):
        raise ValueError(f"web.data_dir must be a string, got {type(raw_value).__name__}")
    path = Path(raw_value).expanduser()
    if path.is_absolute() or config_file.path is None:
        return path.resolve()
    return (config_file.path.parent / path).resolve()


def load_settings() -> WebSettings:
    """Load and validate filesystem-backed settings from env/config."""

    for removed_env in _REMOVED_WEB_BIND_ENVS:
        if removed_env in os.environ:
            raise ValueError(f"{removed_env} has been removed; pass host/port to the ASGI server")

    config_file = _load_config_file()
    config = config_file.payload
    web_config = config.get("web")
    if web_config is None:
        web_config = {}
    if not isinstance(web_config, dict):
        raise ValueError("[web] config must be a TOML table")
    _reject_unknown_keys(web_config, allowed=_WEB_CONFIG_KEYS, section="[web]")

    data_dir_raw = _read_setting(
        env_name="KICAD_PCB_WEB_DATA_DIR",
        config_value=web_config.get("data_dir"),
        default="data",
    )
    mutation_lock_timeout_raw = _read_setting(
        env_name="KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S",
        config_value=web_config.get("mutation_lock_timeout_s"),
        default=2.0,
    )

    if isinstance(data_dir_raw, str) and not data_dir_raw.strip():
        raise ValueError("web.data_dir must not be empty")
    data_dir = _resolve_config_path(data_dir_raw, config_file=config_file)
    jobs_dir = data_dir / "jobs"
    try:
        jobs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"Unable to create jobs directory: {jobs_dir}") from exc
    lock_timeout = _coerce_float(
        mutation_lock_timeout_raw, field_name="web.mutation_lock_timeout_s"
    )
    if lock_timeout <= 0:
        raise ValueError("web.mutation_lock_timeout_s must be greater than zero")
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=jobs_dir,
        mutation_lock_timeout_s=lock_timeout,
        llm=_load_llm_settings(config),
    )
