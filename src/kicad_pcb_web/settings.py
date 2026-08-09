"""Web-app settings."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

LlmProvider = Literal["disabled", "openai", "ollama", "llama_server"]

_VALID_LLM_PROVIDERS: frozenset[str] = frozenset({"disabled", "openai", "ollama", "llama_server"})


@dataclass(frozen=True)
class LlmSettings:
    """Runtime LLM provider settings for the wizard workflow."""

    provider: LlmProvider = "disabled"
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    timeout_s: float = 60.0
    temperature: float = 0.2
    max_tokens: int | None = None
    system_prompt_version: str = "v1"
    spec_max_repair_rounds: int = 2
    ir_max_repair_rounds: int = 2
    enable_streaming: bool = False
    request_log_redaction: bool = True
    network_probe_enabled: bool = False
    debug_artifact_capture: bool = False
    retry_max_attempts: int = 3
    retry_base_delay_s: float = 0.5
    retry_max_delay_s: float = 8.0
    retry_jitter_s: float = 0.25

    @property
    def enabled(self) -> bool:
        """Return whether an LLM provider is enabled."""

        return self.provider != "disabled"

    @property
    def network_probe_supported(self) -> bool:
        """Return whether the configured provider could support a health probe."""

        return self.provider in {"openai", "ollama", "llama_server"}


@dataclass(frozen=True)
class WebSettings:
    """Runtime settings for the local web app."""

    data_dir: Path
    jobs_dir: Path
    default_host: str = "127.0.0.1"
    default_port: int = 8000
    mutation_lock_timeout_s: float = 2.0
    llm: LlmSettings = LlmSettings()


@dataclass(frozen=True)
class _ConfigFile:
    """Resolved optional config file payload plus its base directory."""

    path: Path | None
    payload: dict[str, Any]


def _load_config_file() -> _ConfigFile:
    """Load the optional TOML config file, failing if an explicit path is unusable."""

    config_path_env = os.environ.get("KICAD_PCB_WEB_CONFIG_FILE")
    explicit_path = config_path_env is not None
    if explicit_path and not config_path_env.strip():
        raise ValueError("KICAD_PCB_WEB_CONFIG_FILE must not be empty when explicitly set")

    config_path = (
        Path(config_path_env).expanduser().resolve()
        if explicit_path
        else Path("kicad_pcb_web.toml").resolve()
    )
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
    return _ConfigFile(path=config_path, payload=payload)


def _coerce_float(value: Any, *, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid float for {field_name}: {value!r}") from exc


def _coerce_int(value: Any, *, field_name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid integer for {field_name}: {value!r}") from exc


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean for {field_name}: {value!r}")


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

    llm_config = config.get("llm")
    if llm_config is None:
        llm_config = {}
    if not isinstance(llm_config, dict):
        raise ValueError("[llm] config must be a TOML table")

    provider = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_PROVIDER",
        config_value=llm_config.get("provider"),
        default="disabled",
    )
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
    network_probe_enabled_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_NETWORK_PROBE_ENABLED",
        config_value=llm_config.get("network_probe_enabled"),
        default=False,
    )
    debug_artifact_capture_raw = _read_setting(
        env_name="KICAD_PCB_WEB_LLM_DEBUG_ARTIFACT_CAPTURE",
        config_value=llm_config.get("debug_artifact_capture"),
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
        provider=provider,
        model=_read_setting(
            env_name="KICAD_PCB_WEB_LLM_MODEL",
            config_value=llm_config.get("model"),
            default=None,
        ),
        base_url=_read_setting(
            env_name="KICAD_PCB_WEB_LLM_BASE_URL",
            config_value=llm_config.get("base_url"),
            default=None,
        ),
        api_key=_read_setting(
            env_name="KICAD_PCB_WEB_LLM_API_KEY",
            config_value=llm_config.get("api_key"),
            default=None,
        ),
        timeout_s=_coerce_float(timeout_raw, field_name="llm.timeout_s"),
        temperature=_coerce_float(temperature_raw, field_name="llm.temperature"),
        max_tokens=(
            None
            if max_tokens_raw in (None, "")
            else _coerce_int(max_tokens_raw, field_name="llm.max_tokens")
        ),
        system_prompt_version=str(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_SYSTEM_PROMPT_VERSION",
                config_value=llm_config.get("system_prompt_version"),
                default="v1",
            )
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
        network_probe_enabled=_coerce_bool(
            network_probe_enabled_raw, field_name="llm.network_probe_enabled"
        ),
        debug_artifact_capture=_coerce_bool(
            debug_artifact_capture_raw, field_name="llm.debug_artifact_capture"
        ),
        retry_max_attempts=_coerce_int(retry_max_attempts_raw, field_name="llm.retry_max_attempts"),
        retry_base_delay_s=_coerce_float(retry_base_delay_raw, field_name="llm.retry_base_delay_s"),
        retry_max_delay_s=_coerce_float(retry_max_delay_raw, field_name="llm.retry_max_delay_s"),
        retry_jitter_s=_coerce_float(retry_jitter_raw, field_name="llm.retry_jitter_s"),
    )
    _validate_llm_settings(settings)
    return settings


def _validate_http_url(value: str, *, field_name: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid URL for {field_name}: {value!r}")


def _require_non_empty(value: str | None, *, field_name: str) -> None:
    if value is None or not value.strip():
        raise ValueError(f"Missing required setting: {field_name}")


def _validate_llm_settings(settings: LlmSettings) -> None:
    if settings.timeout_s <= 0:
        raise ValueError("llm.timeout_s must be greater than zero")
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
    if settings.network_probe_enabled:
        raise ValueError(
            "llm.network_probe_enabled=true is unsupported; "
            "active network probing is not implemented"
        )
    if not 1 <= settings.retry_max_attempts <= 10:
        raise ValueError("llm.retry_max_attempts must be between 1 and 10")
    if settings.retry_base_delay_s < 0:
        raise ValueError("llm.retry_base_delay_s must be zero or greater")
    if settings.retry_max_delay_s < settings.retry_base_delay_s:
        raise ValueError("llm.retry_max_delay_s must be at least llm.retry_base_delay_s")
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
        if settings.base_url is None:
            return
        _validate_http_url(settings.base_url, field_name="llm.base_url")
        return

    if settings.provider in {"ollama", "llama_server"}:
        _require_non_empty(settings.base_url, field_name="llm.base_url")


def _resolve_config_path(raw_value: Any, *, config_file: _ConfigFile) -> Path:
    path = Path(str(raw_value)).expanduser()
    if path.is_absolute() or config_file.path is None:
        return path.resolve()
    return (config_file.path.parent / path).resolve()


def load_settings() -> WebSettings:
    """Load and validate filesystem-backed settings from env/config."""

    config_file = _load_config_file()
    config = config_file.payload
    web_config = config.get("web")
    if web_config is None:
        web_config = {}
    if not isinstance(web_config, dict):
        raise ValueError("[web] config must be a TOML table")

    data_dir_raw = _read_setting(
        env_name="KICAD_PCB_WEB_DATA_DIR",
        config_value=web_config.get("data_dir"),
        default="data",
    )
    default_host = _read_setting(
        env_name="KICAD_PCB_WEB_HOST",
        config_value=web_config.get("default_host"),
        default="127.0.0.1",
    )
    default_port_raw = _read_setting(
        env_name="KICAD_PCB_WEB_PORT",
        config_value=web_config.get("default_port"),
        default=8000,
    )
    mutation_lock_timeout_raw = _read_setting(
        env_name="KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S",
        config_value=web_config.get("mutation_lock_timeout_s"),
        default=2.0,
    )

    data_dir = _resolve_config_path(data_dir_raw, config_file=config_file)
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    lock_timeout = _coerce_float(
        mutation_lock_timeout_raw, field_name="web.mutation_lock_timeout_s"
    )
    if lock_timeout <= 0:
        raise ValueError("web.mutation_lock_timeout_s must be greater than zero")
    default_port = _coerce_int(default_port_raw, field_name="web.default_port")
    if not 1 <= default_port <= 65535:
        raise ValueError("web.default_port must be between 1 and 65535")

    return WebSettings(
        data_dir=data_dir,
        jobs_dir=jobs_dir,
        default_host=str(default_host),
        default_port=default_port,
        mutation_lock_timeout_s=lock_timeout,
        llm=_load_llm_settings(config),
    )
