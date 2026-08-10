from __future__ import annotations

import re
import subprocess
from pathlib import Path
from textwrap import dedent

BASELINE = "b5855cc3f0e984f3a189b17fec37ac1cf8685de1"
ROOT = Path.cwd()

TARGETS = [
    "README.md",
    "src/kicad_pcb_web/errors.py",
    "src/kicad_pcb_web/settings.py",
    "src/kicad_pcb_web/wizard_models.py",
    "src/kicad_pcb_web/services/llm/base.py",
    "src/kicad_pcb_web/services/llm/openai_client.py",
    "src/kicad_pcb_web/services/llm/llama_server_client.py",
    "src/kicad_pcb_web/services/llm/factory.py",
    "src/kicad_pcb_web/services/_wizard_llm.py",
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    "src/kicad_pcb_web/services/wizard.py",
]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if text.count(old) != 1:
        raise RuntimeError(
            f"expected exactly one match in {path}: {old[:100]!r}; got {text.count(old)}"
        )
    write(path, text.replace(old, new, 1))


def regex_replace_once(path: str, pattern: str, new: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, new, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"expected exactly one regex match in {path}: {pattern!r}; got {count}")
    write(path, updated)


for target in TARGETS:
    subprocess.run(["git", "diff", "--quiet", BASELINE, "--", target], check=True)

# D2 typed web-facing completion outcomes.
replace_once(
    "src/kicad_pcb_web/errors.py",
    dedent('''\
    class UpstreamProviderError(WebServiceError):
        status_code = 502
        code = "LLM_PROVIDER_FAILED"


    class PersistenceError(WebServiceError):
    '''),
    dedent('''\
    class UpstreamProviderError(WebServiceError):
        status_code = 502
        code = "LLM_PROVIDER_FAILED"


    class LlmInvalidStructuredOutputError(UpstreamProviderError):
        code = "LLM_INVALID_STRUCTURED_OUTPUT"


    class LlmNoUsableContentError(UpstreamProviderError):
        code = "LLM_NO_USABLE_CONTENT"


    class LlmCompletionTruncatedError(UpstreamProviderError):
        code = "LLM_COMPLETION_TRUNCATED"


    class LlmCompletionRefusedError(UpstreamProviderError):
        code = "LLM_COMPLETION_REFUSED"


    class PersistenceError(WebServiceError):
    '''),
)

# Shared LLM client: explicit temperature mode + narrow no-content classification + retry envelope helper.
replace_once(
    "src/kicad_pcb_web/services/llm/base.py",
    "from kicad_pcb.errors import ToolError\n",
    "from kicad_pcb.errors import ToolError\n\nfrom ...errors import LlmNoUsableContentError\n",
)
replace_once(
    "src/kicad_pcb_web/services/llm/base.py",
    "    default_max_tokens: int | None\n    api_key: str | None = None\n",
    "    default_max_tokens: int | None\n    temperature_mode: Literal[\"send\", \"omit\"] = \"send\"\n    api_key: str | None = None\n",
)
replace_once(
    "src/kicad_pcb_web/services/llm/base.py",
    "        self.default_max_tokens = config.default_max_tokens\n        self.api_key = config.api_key\n",
    "        self.default_max_tokens = config.default_max_tokens\n        self.temperature_mode = config.temperature_mode\n        self.api_key = config.api_key\n",
)
replace_once(
    "src/kicad_pcb_web/services/llm/base.py",
    dedent('''\
        def _effective_max_tokens(self, request: LlmRequest) -> int | None:
            if request.max_tokens is not None:
                return request.max_tokens
            return self.default_max_tokens

        def _payload_metrics(self, payload: dict[str, Any]) -> tuple[int, str]:
    '''),
    dedent('''\
        def _effective_max_tokens(self, request: LlmRequest) -> int | None:
            if request.max_tokens is not None:
                return request.max_tokens
            return self.default_max_tokens

        @property
        def max_scheduled_retry_sleep_s(self) -> float:
            """Return the maximum total sleep scheduled between HTTP retries.

            This is a bound on this client's own backoff sleeps only. It is not an
            end-to-end HTTP request deadline; HTTPX uses per-phase inactivity timeouts.
            """

            return max(0, self.retry_max_attempts - 1) * self.retry_max_delay_s

        def _payload_metrics(self, payload: dict[str, Any]) -> tuple[int, str]:
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/llm/base.py",
    r"    def _coerce_text_content\(self, value: Any\) -> str:\n.*?(?=\n    @abstractmethod\n    def _build_payload)",
    dedent('''\
        def _coerce_text_content(self, value: Any) -> str:
            if isinstance(value, str):
                content = value
            elif isinstance(value, list):
                text_parts = [
                    item.get("text", "")
                    for item in value
                    if isinstance(item, dict) and item.get("type") == "text"
                ]
                content = "".join(part for part in text_parts if part)
            elif value is None:
                raise LlmNoUsableContentError(
                    "The configured LLM provider returned no usable content.",
                    details={"provider": self.provider_name},
                )
            else:
                raise ToolError(
                    f"{self.provider_name} returned an unsupported content shape.",
                    details={"provider": self.provider_name},
                )

            if not content.strip():
                raise LlmNoUsableContentError(
                    "The configured LLM provider returned no usable content.",
                    details={"provider": self.provider_name},
                )
            return content
    '''),
)

# OpenAI/llama-server: temperature omission and terminal finish-reason classification.
replace_once(
    "src/kicad_pcb_web/services/llm/openai_client.py",
    "from kicad_pcb.errors import ToolError\n\nfrom .base import BaseHttpLlmClient, LlmCompletion, LlmRequest\n",
    dedent('''\
    from kicad_pcb.errors import ToolError

    from ...errors import LlmCompletionRefusedError, LlmCompletionTruncatedError
    from .base import BaseHttpLlmClient, LlmCompletion, LlmRequest
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/llm/openai_client.py",
    r"    def _build_payload\(self, request: LlmRequest\) -> tuple\[str, dict\[str, Any\]\]:\n.*?(?=\n    def _parse_completion)",
    dedent('''\
        def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
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
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/llm/openai_client.py",
    r"    def _parse_completion\(self, payload: dict\[str, Any\]\) -> LlmCompletion:\n.*\Z",
    dedent('''\
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
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/llm/llama_server_client.py",
    r"    def _build_payload\(self, request: LlmRequest\) -> tuple\[str, dict\[str, Any\]\]:\n.*\Z",
    dedent('''\
        def _build_payload(self, request: LlmRequest) -> tuple[str, dict[str, Any]]:
            payload: dict[str, Any] = {
                "model": self.model,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
            }
            if self.temperature_mode == "send":
                payload["temperature"] = self._effective_temperature(request)
            max_tokens = self._effective_max_tokens(request)
            if max_tokens is not None:
                payload["max_tokens"] = max_tokens
            if request.response_format == "json":
                payload["response_format"] = {"type": "json_object"}
            return "/chat/completions", payload
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/llm/factory.py",
    "        default_temperature=llm.temperature,\n        default_max_tokens=llm.max_tokens,\n",
    "        default_temperature=llm.temperature,\n        default_max_tokens=llm.max_tokens,\n        temperature_mode=llm.temperature_mode,\n",
)

# Settings D3/D4/D7.
replace_once(
    "src/kicad_pcb_web/settings.py",
    "from typing import Any, Literal\n",
    "from typing import Any, Literal, cast\n",
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    'LlmProvider = Literal["disabled", "openai", "ollama", "llama_server"]\n\n_VALID_LLM_PROVIDERS',
    'LlmProvider = Literal["disabled", "openai", "ollama", "llama_server"]\nLlmTemperatureMode = Literal["send", "omit"]\n\n_VALID_LLM_PROVIDERS',
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    '_VALID_LLM_PROVIDERS: frozenset[str] = frozenset({"disabled", "openai", "ollama", "llama_server"})\n',
    '_VALID_LLM_PROVIDERS: frozenset[str] = frozenset({"disabled", "openai", "ollama", "llama_server"})\n_VALID_TEMPERATURE_MODES: frozenset[str] = frozenset({"send", "omit"})\n',
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    '        "temperature",\n        "max_tokens",\n',
    '        "temperature",\n        "temperature_mode",\n        "max_tokens",\n',
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    "    temperature: float = 0.2\n    max_tokens: int | None = None\n",
    '    temperature: float = 0.2\n    temperature_mode: LlmTemperatureMode = "send"\n    max_tokens: int | None = None\n',
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    dedent('''\
        temperature_raw = _read_setting(
            env_name="KICAD_PCB_WEB_LLM_TEMPERATURE",
            config_value=llm_config.get("temperature"),
            default=0.2,
        )
        max_tokens_raw = _read_setting(
    '''),
    dedent('''\
        temperature_raw = _read_setting(
            env_name="KICAD_PCB_WEB_LLM_TEMPERATURE",
            config_value=llm_config.get("temperature"),
            default=0.2,
        )
        temperature_mode_raw = str(
            _read_setting(
                env_name="KICAD_PCB_WEB_LLM_TEMPERATURE_MODE",
                config_value=llm_config.get("temperature_mode"),
                default="send",
            )
        )
        if temperature_mode_raw not in _VALID_TEMPERATURE_MODES:
            raise ValueError("llm.temperature_mode must be one of: omit, send")
        max_tokens_raw = _read_setting(
    '''),
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    "        temperature=_coerce_float(temperature_raw, field_name=\"llm.temperature\"),\n        max_tokens=(\n",
    "        temperature=_coerce_float(temperature_raw, field_name=\"llm.temperature\"),\n        temperature_mode=cast(LlmTemperatureMode, temperature_mode_raw),\n        max_tokens=(\n",
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    dedent('''\
        if settings.timeout_s <= 0:
            raise ValueError("llm.timeout_s must be greater than zero")
        if settings.temperature < 0 or settings.temperature > 2:
    '''),
    dedent('''\
        if settings.timeout_s <= 0:
            raise ValueError("llm.timeout_s must be greater than zero")
        if settings.timeout_s > 300:
            raise ValueError("llm.timeout_s must be 300 seconds or less")
        if settings.temperature_mode not in _VALID_TEMPERATURE_MODES:
            raise ValueError("llm.temperature_mode must be one of: omit, send")
        if settings.temperature < 0 or settings.temperature > 2:
    '''),
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    dedent('''\
        if settings.retry_max_delay_s < settings.retry_base_delay_s:
            raise ValueError("llm.retry_max_delay_s must be at least llm.retry_base_delay_s")
        if settings.retry_jitter_s < 0:
    '''),
    dedent('''\
        if settings.retry_max_delay_s < settings.retry_base_delay_s:
            raise ValueError("llm.retry_max_delay_s must be at least llm.retry_base_delay_s")
        if settings.retry_max_delay_s > 60:
            raise ValueError("llm.retry_max_delay_s must be 60 seconds or less")
        if settings.retry_jitter_s < 0:
    '''),
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    dedent('''\
        if settings.provider == "openai":
            _require_non_empty(settings.api_key, field_name="llm.api_key")
            if settings.base_url is None:
                return
            _validate_http_url(settings.base_url, field_name="llm.base_url")
            return
    '''),
    dedent('''\
        if settings.provider == "openai":
            _require_non_empty(settings.api_key, field_name="llm.api_key")
            return
    '''),
)
replace_once(
    "src/kicad_pcb_web/settings.py",
    dedent('''\
        mutation_lock_timeout_raw = _read_setting(
            env_name="KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S",
            config_value=web_config.get("mutation_lock_timeout_s"),
            default=2.0,
        )

        data_dir = _resolve_config_path(data_dir_raw, config_file=config_file)
        jobs_dir = data_dir / "jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
    '''),
    dedent('''\
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
    '''),
)

# Persisted models: temperature provenance + explicit failure discriminator.
replace_once(
    "src/kicad_pcb_web/wizard_models.py",
    'WizardMessageRole = Literal["user", "assistant"]\n',
    'WizardFailureKind = Literal["unsupported_design", "operational", "generation"]\nWizardMessageRole = Literal["user", "assistant"]\n',
)
replace_once(
    "src/kicad_pcb_web/wizard_models.py",
    "    endpoint_identity: str | None = None\n    config_revision: str\n",
    '    endpoint_identity: str | None = None\n    temperature_mode: Literal["send", "omit"] | None = None\n    config_revision: str\n',
)
replace_once(
    "src/kicad_pcb_web/wizard_models.py",
    "    latest_job_id: str | None = None\n    error: dict[str, Any] | None = None\n",
    "    latest_job_id: str | None = None\n    failure_kind: WizardFailureKind | None = None\n    error: dict[str, Any] | None = None\n",
)

# Debug-artifact deterministic retention and failure_kind-aware error writes.
replace_once(
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    "from ..wizard_models import WizardMessage, WizardMessageRole, WizardSessionDetail\n",
    "from ..wizard_models import (\n    WizardFailureKind,\n    WizardMessage,\n    WizardMessageRole,\n    WizardSessionDetail,\n)\n",
)
replace_once(
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    '_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")\n',
    '_SAFE_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")\n_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE = 20\n_DEBUG_ARTIFACT_MAX_TOTAL_BYTES = 25 * 1024 * 1024\n',
)
replace_once(
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    dedent('''\
    def _make_debug_artifact_writer(
        settings: WebSettings,
        session_id: str,
        *,
        stage: str,
    ) -> Callable[[dict[str, object]], None] | None:
    '''),
    dedent('''\
    def _debug_artifact_order_key(path: Path) -> tuple[str, str]:
        parts = path.name.split("_", 2)
        timestamp = parts[1] if len(parts) > 2 else path.name
        return timestamp, path.name


    def _unlink_debug_artifact(path: Path) -> bool:
        try:
            path.unlink()
        except OSError as exc:
            LOGGER.warning(
                "failed to prune wizard debug artifact",
                extra={"path": str(path), "error_type": type(exc).__name__},
            )
            return False
        return True


    def _prune_debug_artifacts(artifact_dir: Path, *, stage: str, newest: Path) -> None:
        stage_files = sorted(
            artifact_dir.glob(f"{stage}_*.json"), key=_debug_artifact_order_key
        )
        candidates = [path for path in stage_files if path != newest]
        excess = max(0, len(stage_files) - _DEBUG_ARTIFACT_MAX_FILES_PER_STAGE)
        for path in candidates[:excess]:
            _unlink_debug_artifact(path)

        all_files = sorted(artifact_dir.glob("*.json"), key=_debug_artifact_order_key)
        sizes: dict[Path, int] = {}
        total_bytes = 0
        for path in all_files:
            try:
                size = path.stat().st_size
            except OSError as exc:
                LOGGER.warning(
                    "failed to inspect wizard debug artifact during pruning",
                    extra={"path": str(path), "error_type": type(exc).__name__},
                )
                continue
            sizes[path] = size
            total_bytes += size

        if total_bytes <= _DEBUG_ARTIFACT_MAX_TOTAL_BYTES:
            return
        for path in all_files:
            if path == newest or total_bytes <= _DEBUG_ARTIFACT_MAX_TOTAL_BYTES:
                continue
            size = sizes.get(path)
            if size is None:
                continue
            if _unlink_debug_artifact(path):
                total_bytes -= size


    def _make_debug_artifact_writer(
        settings: WebSettings,
        session_id: str,
        *,
        stage: str,
    ) -> Callable[[dict[str, object]], None] | None:
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    '        atomic_write_json(artifact_dir / f"{stage}_{stamp}_{suffix}.json", payload)\n',
    '        artifact_path = artifact_dir / f"{stage}_{stamp}_{suffix}.json"\n        atomic_write_json(artifact_path, payload)\n        _prune_debug_artifacts(artifact_dir, stage=stage, newest=artifact_path)\n',
)
regex_replace_once(
    "src/kicad_pcb_web/services/_wizard_session_io.py",
    r"def _set_error\(\n    session: WizardSessionDetail,\n    error_payload: dict\[str, object\],\n\) -> WizardSessionDetail:\n.*\Z",
    dedent('''\
    def _set_error(
        session: WizardSessionDetail,
        error_payload: dict[str, object],
        *,
        failure_kind: WizardFailureKind = "operational",
    ) -> WizardSessionDetail:
        return session.model_copy(
            update={
                "status": "failed",
                "failure_kind": failure_kind,
                "error": error_payload,
                "updated_at": _utc_now(),
            }
        )
    '''),
)

# Structured-output helper: one attempt primitive for shared IR budget, narrow repairability.
replace_once(
    "src/kicad_pcb_web/services/_wizard_llm.py",
    "from ..errors import ProviderUnavailableError, UpstreamProviderError\n",
    dedent('''\
    from ..errors import (
        LlmCompletionRefusedError,
        LlmCompletionTruncatedError,
        LlmInvalidStructuredOutputError,
        LlmNoUsableContentError,
        ProviderUnavailableError,
    )
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/_wizard_llm.py",
    '_MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)\n',
    dedent('''\
    _MODEL_T = TypeVar("_MODEL_T", bound=BaseModel)


    class _RepairableStructuredOutputError(Exception):
        def __init__(
            self,
            *,
            kind: str,
            repair_message: str,
            error_type: str,
            assistant_content: str | None = None,
        ) -> None:
            super().__init__(repair_message)
            self.kind = kind
            self.repair_message = repair_message
            self.error_type = error_type
            self.assistant_content = assistant_content
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/_wizard_llm.py",
    r"    if prior_ir_json is not None and repair_error is not None:\n.*?\n    return messages",
    dedent('''\
        if repair_error is not None:
            repair_content = (
                "The previous Circuit IR attempt failed. "
                f"Repair it using this exact error context:\\n{repair_error}\\n\\n"
                + _ir_contract_text()
            )
            if prior_ir_json is not None:
                repair_content += "\\n\\nPrevious netlist JSON:\\n" + json.dumps(
                    prior_ir_json, indent=2
                )
            messages.append(LlmMessage(role="user", content=repair_content))
        return messages
    '''),
)
regex_replace_once(
    "src/kicad_pcb_web/services/_wizard_llm.py",
    r"def _call_llm_for_json\(\n.*?(?=\n\ndef _require_llm_client)",
    dedent('''\
    def _raise_structured_output_exhausted(
        error: _RepairableStructuredOutputError,
        *,
        response_model: type[BaseModel],
        attempts: int,
    ) -> None:
        details: dict[str, object] = {
            "response_model": response_model.__name__,
            "attempts": attempts,
            "error_type": error.error_type,
        }
        if error.kind == "no_usable_content":
            raise LlmNoUsableContentError(
                "The configured LLM returned no usable content after all repair attempts.",
                details=details,
            ) from error
        raise LlmInvalidStructuredOutputError(
            "The configured LLM returned invalid structured output after all repair attempts.",
            details=details,
        ) from error


    def _call_llm_for_json_once(
        *,
        llm_client: LlmClient,
        messages: list[LlmMessage],
        response_model: type[_MODEL_T],
        attempt: int,
        max_attempts: int,
        debug_artifact_writer: Callable[[dict[str, object]], None] | None = None,
    ) -> _MODEL_T:
        request = LlmRequest(messages=list(messages), response_format="json")
        prompt_chars = sum(len(message.content) for message in request.messages)
        serialized_messages = [
            {"role": message.role, "content": message.content} for message in request.messages
        ]
        started_at = time.perf_counter()
        LOGGER.info(
            "wizard structured json attempt started",
            extra={
                "response_model": response_model.__name__,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "prompt_message_count": len(request.messages),
                "prompt_chars": prompt_chars,
            },
        )
        try:
            completion = llm_client.complete(request)
        except LlmNoUsableContentError as exc:
            elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
            LOGGER.warning(
                "wizard structured json attempt returned no usable content",
                extra={
                    "response_model": response_model.__name__,
                    "attempt": attempt,
                    "elapsed_ms": elapsed_ms,
                    "error_type": type(exc).__name__,
                },
            )
            if debug_artifact_writer is not None:
                debug_artifact_writer(
                    {
                        "attempt": attempt,
                        "response_model": response_model.__name__,
                        "messages": serialized_messages,
                        "completion_error": {"code": exc.code, "message": str(exc)},
                    }
                )
            raise _RepairableStructuredOutputError(
                kind="no_usable_content",
                repair_message=str(exc),
                error_type=type(exc).__name__,
            ) from exc

        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 1)
        normalized_reason = (completion.finish_reason or "").strip().lower()
        if normalized_reason == "length":
            raise LlmCompletionTruncatedError(
                "The configured LLM response was truncated; increase llm.max_tokens.",
                details={"provider": completion.provider, "finish_reason": completion.finish_reason},
            )
        if normalized_reason in {"content_filter", "refusal"}:
            raise LlmCompletionRefusedError(
                "The configured LLM refused or content-filtered the response.",
                details={"provider": completion.provider, "finish_reason": completion.finish_reason},
            )
        if not completion.content.strip():
            raise _RepairableStructuredOutputError(
                kind="no_usable_content",
                repair_message="The configured LLM provider returned no usable content.",
                error_type="EmptyCompletionContent",
            )

        response_chars = len(completion.content)
        try:
            parsed = json.loads(completion.content)
            validated = response_model.model_validate(parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            LOGGER.warning(
                "wizard structured json attempt failed",
                extra={
                    "response_model": response_model.__name__,
                    "attempt": attempt,
                    "prompt_message_count": len(request.messages),
                    "prompt_chars": prompt_chars,
                    "response_chars": response_chars,
                    "elapsed_ms": elapsed_ms,
                    "provider": completion.provider,
                    "finish_reason": completion.finish_reason,
                    "request_id": completion.request_id,
                    "error_type": type(exc).__name__,
                },
            )
            if debug_artifact_writer is not None:
                debug_artifact_writer(
                    {
                        "attempt": attempt,
                        "response_model": response_model.__name__,
                        "messages": serialized_messages,
                        "completion": {
                            "provider": completion.provider,
                            "model": completion.model,
                            "content": completion.content,
                            "finish_reason": completion.finish_reason,
                            "request_id": completion.request_id,
                        },
                        "parse_error": str(exc),
                    }
                )
            raise _RepairableStructuredOutputError(
                kind="invalid_structured_output",
                repair_message=(
                    "The previous response did not match the required JSON contract. "
                    f"Error: {exc}"
                ),
                error_type=type(exc).__name__,
                assistant_content=completion.content,
            ) from exc

        LOGGER.info(
            "wizard structured json attempt completed",
            extra={
                "response_model": response_model.__name__,
                "attempt": attempt,
                "prompt_message_count": len(request.messages),
                "prompt_chars": prompt_chars,
                "response_chars": response_chars,
                "elapsed_ms": elapsed_ms,
                "provider": completion.provider,
                "finish_reason": completion.finish_reason,
                "request_id": completion.request_id,
            },
        )
        if debug_artifact_writer is not None:
            debug_artifact_writer(
                {
                    "attempt": attempt,
                    "response_model": response_model.__name__,
                    "messages": serialized_messages,
                    "completion": {
                        "provider": completion.provider,
                        "model": completion.model,
                        "content": completion.content,
                        "finish_reason": completion.finish_reason,
                        "request_id": completion.request_id,
                    },
                    "parsed": parsed,
                }
            )
        return validated


    def _call_llm_for_json(
        *,
        llm_client: LlmClient,
        messages: list[LlmMessage],
        response_model: type[_MODEL_T],
        max_repairs: int,
        debug_artifact_writer: Callable[[dict[str, object]], None] | None = None,
    ) -> _MODEL_T:
        schema_json = json.dumps(response_model.model_json_schema(), indent=2)
        current_messages = list(messages)
        max_attempts = max_repairs + 1
        for attempt in range(1, max_attempts + 1):
            try:
                return _call_llm_for_json_once(
                    llm_client=llm_client,
                    messages=current_messages,
                    response_model=response_model,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    debug_artifact_writer=debug_artifact_writer,
                )
            except _RepairableStructuredOutputError as exc:
                if attempt >= max_attempts:
                    _raise_structured_output_exhausted(
                        exc, response_model=response_model, attempts=attempt
                    )
                if exc.assistant_content:
                    current_messages.append(
                        LlmMessage(role="assistant", content=exc.assistant_content)
                    )
                current_messages.append(
                    LlmMessage(
                        role="user",
                        content=(
                            f"{exc.repair_message} Return corrected JSON only.\n"
                            f"JSON schema:\n{schema_json}"
                        ),
                    )
                )
        raise AssertionError("structured-output attempt loop exhausted unexpectedly")
    '''),
)

# Wizard orchestration: explicit failure kind + shared IR budget + provenance.
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    "    WizardGenerateProjectResponse,\n",
    "    WizardFailureKind,\n    WizardGenerateProjectResponse,\n",
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    "    _build_spec_messages,\n    _call_llm_for_json,\n",
    "    _RepairableStructuredOutputError,\n    _build_spec_messages,\n    _call_llm_for_json,\n    _call_llm_for_json_once,\n",
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    "    _format_ir_repair_error,\n    _require_llm_client,\n",
    "    _format_ir_repair_error,\n    _raise_structured_output_exhausted,\n    _require_llm_client,\n",
)
regex_replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    r"def _failure_operation\(session: WizardSessionDetail\) -> str \| None:\n.*?(?=\n\ndef _endpoint_fingerprint)",
    dedent('''\
    def _effective_failure_kind(session: WizardSessionDetail) -> WizardFailureKind | None:
        """Return explicit failure kind, with the documented legacy read fallback."""

        if session.status != "failed":
            return None
        if session.failure_kind is not None:
            return session.failure_kind
        return "unsupported_design" if session.error is None else "operational"


    def _failure_operation(session: WizardSessionDetail) -> str | None:
        """Return the operation that placed an operational/generation failure."""

        if _effective_failure_kind(session) == "unsupported_design":
            return None
        if not isinstance(session.error, dict):
            return None
        details = session.error.get("details")
        if not isinstance(details, dict):
            return None
        operation = details.get("operation")
        return operation if isinstance(operation, str) else None
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '        "temperature": llm.temperature,\n        "max_tokens": llm.max_tokens,\n',
    '        "temperature": llm.temperature,\n        "temperature_mode": llm.temperature_mode,\n        "max_tokens": llm.max_tokens,\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    "        endpoint_identity=endpoint_identity,\n        config_revision=hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()[:16],\n",
    "        endpoint_identity=endpoint_identity,\n        temperature_mode=llm.temperature_mode,\n        config_revision=hashlib.sha256(canonical.encode(\"utf-8\")).hexdigest()[:16],\n",
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    dedent('''\
    def _persist_and_raise_failure(
        settings: WebSettings,
        session: WizardSessionDetail,
        exc: Exception,
        *,
        operation: str,
    ) -> NoReturn:
    '''),
    dedent('''\
    def _persist_and_raise_failure(
        settings: WebSettings,
        session: WizardSessionDetail,
        exc: Exception,
        *,
        operation: str,
        failure_kind: WizardFailureKind = "operational",
    ) -> NoReturn:
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    "    failed = _set_error(session, _public_error_payload(error))\n",
    "    failed = _set_error(session, _public_error_payload(error), failure_kind=failure_kind)\n",
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                    "unsupported_reasons": output.unsupported_reasons,\n                    "error": None,\n                    "updated_at": _utc_now(),\n',
    '                    "unsupported_reasons": output.unsupported_reasons,\n                    "failure_kind": (\n                        "unsupported_design" if output.next_state == "failed" else None\n                    ),\n                    "error": None,\n                    "updated_at": _utc_now(),\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                    "unsupported_reasons": output.unsupported_reasons,\n                    "error": None,\n                    "updated_at": _utc_now(),\n',
    '                    "unsupported_reasons": output.unsupported_reasons,\n                    "failure_kind": (\n                        "unsupported_design" if output.next_state == "failed" else None\n                    ),\n                    "error": None,\n                    "updated_at": _utc_now(),\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                "error": None,\n            }\n        )\n        LOGGER.info("wizard spec approved"',
    '                "failure_kind": None,\n                "error": None,\n            }\n        )\n        LOGGER.info("wizard spec approved"',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                "latest_job_id": None,\n                "error": None,\n                "updated_at": _utc_now(),\n            }\n        )\n        LOGGER.info("wizard circuit IR cleared"',
    '                "latest_job_id": None,\n                "failure_kind": None,\n                "error": None,\n                "updated_at": _utc_now(),\n            }\n        )\n        LOGGER.info("wizard circuit IR cleared"',
)
regex_replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    r"def generate_wizard_ir\(\n.*?(?=\n\ndef _project_failure_error)",
    dedent('''\
    def generate_wizard_ir(
        *,
        settings: WebSettings,
        session_id: str,
        llm_client: LlmClient | None,
    ) -> WizardSessionDetail:
        with resource_lock(settings, kind="wizard", resource_id=session_id):
            session = _read_session_for_mutation(settings, session_id)
            retry_failed_ir = (
                _effective_failure_kind(session) == "operational"
                and _failure_operation(session) == "generate_ir"
            )
            allowed_status = session.status in {
                "spec_approved",
                "ir_needs_repair",
                "ir_ready_for_generation",
                "completed",
            }
            if session.spec is None or not session.spec_approved:
                raise ConflictError(
                    "Approve the circuit spec before generating Circuit IR.",
                    details={"session_id": session_id},
                )
            if not allowed_status and not retry_failed_ir:
                raise ConflictError(
                    "Circuit IR generation is not allowed from the current wizard state.",
                    details={"session_id": session_id, "status": session.status},
                )
            _assert_llm_provenance_matches(
                settings,
                session,
                operation="generate_ir",
                revision_provenance=session.spec_provenance,
            )
            client = _require_llm_client(llm_client)

            session = session.model_copy(
                update={
                    "status": "drafting_ir",
                    "failure_kind": None,
                    "error": None,
                    "updated_at": _utc_now(),
                }
            )
            _persist_session(settings, session)
            last_error: str | None = None
            prior_ir_json: dict[str, object] | None = None
            max_attempts = settings.llm.ir_max_repair_rounds + 1
            try:
                for attempt in range(1, max_attempts + 1):
                    try:
                        output = _call_llm_for_json_once(
                            llm_client=client,
                            messages=_build_ir_messages(
                                settings,
                                session,
                                prior_ir_json=prior_ir_json,
                                repair_error=last_error,
                            ),
                            response_model=IrGenerationOutput,
                            attempt=attempt,
                            max_attempts=max_attempts,
                            debug_artifact_writer=_make_debug_artifact_writer(
                                settings, session.id, stage="ir"
                            ),
                        )
                    except _RepairableStructuredOutputError as exc:
                        last_error = exc.repair_message
                        if attempt >= max_attempts:
                            _raise_structured_output_exhausted(
                                exc, response_model=IrGenerationOutput, attempts=attempt
                            )
                        continue

                    try:
                        prepared = prepare_netlist_dict(
                            netlist_json=output.netlist_json,
                            symbols_dir=(
                                Path(session.symbols_dir).expanduser().resolve()
                                if session.symbols_dir
                                else None
                            ),
                            auto_fix=True,
                        )
                    except UserError as exc:
                        last_error = _format_ir_repair_error(exc)
                        prior_ir_json = output.netlist_json
                        if attempt < max_attempts:
                            continue
                        session = session.model_copy(
                            update={
                                "status": "ir_needs_repair",
                                "ir_json": prior_ir_json,
                                "ir_validation": WizardIrValidation(
                                    valid=False, error_message=last_error
                                ),
                                "ir_provenance": _llm_provenance(settings),
                                "latest_job_id": None,
                                "failure_kind": None,
                                "error": None,
                                "updated_at": _utc_now(),
                            }
                        )
                        return _persist_session(settings, session)

                    session = _append_message(
                        session, role="assistant", content=output.assistant_message
                    )
                    session = session.model_copy(
                        update={
                            "status": "ir_ready_for_generation",
                            "ir_json": prepared.netlist_json,
                            "ir_validation": WizardIrValidation(
                                valid=True,
                                auto_fixed=prepared.auto_fixed,
                                component_count=prepared.component_count,
                                net_count=prepared.net_count,
                                warnings=prepared.warnings,
                                fixes_applied=prepared.fixes_applied,
                                symbols_dirs_used=prepared.symbols_dirs_used,
                            ),
                            "ir_provenance": _llm_provenance(settings),
                            "latest_job_id": None,
                            "assumptions": sorted(
                                {*(session.assumptions or []), *(output.assumptions or [])}
                            ),
                            "failure_kind": None,
                            "error": None,
                            "updated_at": _utc_now(),
                        }
                    )
                    LOGGER.info(
                        "wizard ir ready",
                        extra={
                            "session_id": session.id,
                            "component_count": prepared.component_count,
                            "net_count": prepared.net_count,
                            "auto_fixed": prepared.auto_fixed,
                        },
                    )
                    return _persist_session(settings, session)
                raise AssertionError("IR generation attempt loop exhausted unexpectedly")
            except Exception as exc:
                _persist_and_raise_failure(settings, session, exc, operation="generate_ir")
    '''),
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '        retry_failed_project = (\n            session.status == "failed" and _failure_operation(session) == "generate_project"\n        )\n',
    '        retry_failed_project = (\n            _effective_failure_kind(session) in {"generation", "operational"}\n            and _failure_operation(session) == "generate_project"\n        )\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '        session = session.model_copy(\n            update={"status": "generation_started", "updated_at": _utc_now(), "error": None}\n        )\n',
    '        session = session.model_copy(\n            update={\n                "status": "generation_started",\n                "failure_kind": None,\n                "updated_at": _utc_now(),\n                "error": None,\n            }\n        )\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '        except Exception as exc:\n            _persist_and_raise_failure(settings, session, exc, operation="generate_project")\n',
    '        except Exception as exc:\n            _persist_and_raise_failure(\n                settings,\n                session,\n                exc,\n                operation="generate_project",\n                failure_kind="generation",\n            )\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                    "status": "failed",\n                    "latest_job_id": job.id,\n                    "error": _public_error_payload(project_error),\n',
    '                    "status": "failed",\n                    "failure_kind": "generation",\n                    "latest_job_id": job.id,\n                    "error": _public_error_payload(project_error),\n',
)
replace_once(
    "src/kicad_pcb_web/services/wizard.py",
    '                "status": "completed",\n                "latest_job_id": job.id,\n                "error": None,\n',
    '                "status": "completed",\n                "failure_kind": None,\n                "latest_job_id": job.id,\n                "error": None,\n',
)

# README exposes new explicit parameter compatibility control and D4 scope.
replace_once(
    "README.md",
    "temperature = 0.2\nmax_tokens = 4096\n",
    'temperature = 0.2\ntemperature_mode = "send" # send (default) or omit for models that reject temperature\nmax_tokens = 4096\n',
)
replace_once(
    "README.md",
    "Runtime configuration is validated during application startup before the server accepts requests. An explicitly configured `KICAD_PCB_WEB_CONFIG_FILE` must exist and be readable; unknown TOML keys and unsupported settings fail closed instead of silently reverting to defaults. Settings are cached as one process snapshot, so restart the server after changing provider/model configuration. The old `network_probe_enabled` option was removed because active network probing is not implemented.\n",
    "Runtime configuration is validated during application startup before the server accepts requests. An explicitly configured `KICAD_PCB_WEB_CONFIG_FILE` must exist and be readable; unknown TOML keys and unsupported settings fail closed instead of silently reverting to defaults. Settings are cached as one process snapshot, so restart the server after changing provider/model configuration. `temperature_mode=send` preserves the existing payload behavior; set `temperature_mode=omit` for providers/models that reject the temperature parameter. LLM timeout/retry settings are range-bounded, but HTTPX timeouts retain connect/read/write/pool inactivity semantics and are not an absolute end-to-end request deadline. The old `network_probe_enabled` option was removed because active network probing is not implemented.\n",
)

# Focused unit regressions covering D1-D7.
test_path = ROOT / "tests/unit/test_wizard_llm_robustness.py"
if test_path.exists():
    raise RuntimeError(f"refusing to overwrite existing {test_path}")
test_path.write_text(
    dedent(r'''\
"""Targeted D1-D7 regressions for wizard/LLM robustness."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx
import pytest
from pydantic import BaseModel

from kicad_pcb.errors import ToolError
from kicad_pcb_web.errors import (
    LlmCompletionRefusedError,
    LlmCompletionTruncatedError,
    LlmInvalidStructuredOutputError,
    LlmNoUsableContentError,
)
from kicad_pcb_web.services import _wizard_session_io as session_io
from kicad_pcb_web.services._wizard_llm import _call_llm_for_json
from kicad_pcb_web.services._wizard_session_io import (
    _make_debug_artifact_writer,
    _persist_session,
    _set_error,
    read_wizard_session,
)
from kicad_pcb_web.services.llm import LlmCompletion, LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.services.wizard import (
    _effective_failure_kind,
    _failure_operation,
    _llm_provenance,
    generate_wizard_ir,
)
from kicad_pcb_web.settings import LlmSettings, WebSettings, _validate_llm_settings, load_settings
from kicad_pcb_web.wizard_models import CircuitSpec, WizardSessionDetail

_VALID_NETLIST = {
    "version": "1",
    "components": [
        {"ref": "R1", "symbol": "Device:R", "value": "10k"},
        {"ref": "R2", "symbol": "Device:R", "value": "10k"},
    ],
    "nets": [
        {"name": "N1", "pins": [{"ref": "R1", "pin": "1"}, {"ref": "R2", "pin": "1"}]},
        {"name": "N2", "pins": [{"ref": "R1", "pin": "2"}, {"ref": "R2", "pin": "2"}]},
    ],
}


class _Envelope(BaseModel):
    value: str


class ScriptedClient:
    def __init__(self, responses: list[LlmCompletion | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[LlmRequest] = []

    def complete(self, request: LlmRequest) -> LlmCompletion:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _completion(content: str, *, finish_reason: str = "stop") -> LlmCompletion:
    return LlmCompletion(provider="test", model="test-model", content=content, finish_reason=finish_reason)


def _settings(tmp_path: Path, **overrides: object) -> WebSettings:
    values: dict[str, object] = {
        "provider": "llama_server",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8080",
        "ir_max_repair_rounds": 2,
        "spec_max_repair_rounds": 2,
    }
    values.update(overrides)
    data_dir = tmp_path / "data"
    return WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(**values),  # type: ignore[arg-type]
    )


def _approved_session(settings: WebSettings, session_id: str = "wiz_robustness") -> None:
    _persist_session(
        settings,
        WizardSessionDetail(
            id=session_id,
            status="spec_approved",
            created_at="2026-08-10T00:00:00Z",
            updated_at="2026-08-10T00:00:00Z",
            spec=CircuitSpec(project_name="Robustness", purpose="Exercise LLM robustness."),
            spec_provenance=_llm_provenance(settings),
            spec_approved=True,
            spec_approved_at="2026-08-10T00:00:00Z",
        ),
    )


def _ir_output(netlist: dict[str, object]) -> str:
    return json.dumps({"assistant_message": "IR", "netlist_json": netlist, "assumptions": []})


def test_d1_ir_structural_then_valid_uses_shared_budget(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    client = ScriptedClient([_completion("{"), _completion(_ir_output(_VALID_NETLIST))])
    result = generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert result.status == "ir_ready_for_generation"
    assert len(client.requests) == 2
    assert "did not match the required JSON contract" in client.requests[1].messages[-1].content


def test_d1_ir_structural_exhaustion_is_operational_and_exactly_bounded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    client = ScriptedClient([_completion("{"), _completion("{"), _completion("{")])
    with pytest.raises(LlmInvalidStructuredOutputError):
        generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert len(client.requests) == settings.llm.ir_max_repair_rounds + 1
    persisted = read_wizard_session(settings, "wiz_robustness")
    assert persisted.status == "failed"
    assert persisted.failure_kind == "operational"
    assert _failure_operation(persisted) == "generate_ir"


def test_d1_ir_semantic_exhaustion_preserves_last_parseable_ir(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _approved_session(settings)
    invalid = {
        **_VALID_NETLIST,
        "nets": [
            {"name": "N1", "pins": [{"ref": "R1", "pin": "99"}, {"ref": "R2", "pin": "1"}]},
            _VALID_NETLIST["nets"][1],
        ],
    }
    client = ScriptedClient([_completion(_ir_output(invalid)) for _ in range(3)])
    result = generate_wizard_ir(settings=settings, session_id="wiz_robustness", llm_client=client)
    assert len(client.requests) == settings.llm.ir_max_repair_rounds + 1
    assert result.status == "ir_needs_repair"
    assert result.ir_json == invalid
    assert result.ir_validation is not None and result.ir_validation.valid is False


def test_d2_no_content_repairs_then_recovers() -> None:
    client = ScriptedClient([_completion(""), _completion('{"value":"ok"}')])
    result = _call_llm_for_json(
        llm_client=client,
        messages=[LlmMessage(role="user", content="json")],
        response_model=_Envelope,
        max_repairs=1,
    )
    assert result.value == "ok"
    assert len(client.requests) == 2


def test_d2_no_content_exhaustion_is_distinct() -> None:
    client = ScriptedClient([_completion(""), _completion("")])
    with pytest.raises(LlmNoUsableContentError):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=1,
        )
    assert len(client.requests) == 2


@pytest.mark.parametrize(
    ("reason", "error_type"),
    [("length", LlmCompletionTruncatedError), ("content_filter", LlmCompletionRefusedError)],
)
def test_d2_terminal_finish_reasons_do_not_repair(reason: str, error_type: type[Exception]) -> None:
    client = ScriptedClient([_completion('{"value":"ignored"}', finish_reason=reason)])
    with pytest.raises(error_type):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=2,
        )
    assert len(client.requests) == 1


def test_d2_generic_tool_error_is_not_absorbed() -> None:
    client = ScriptedClient([ToolError("transport failed")])
    with pytest.raises(ToolError, match="transport failed"):
        _call_llm_for_json(
            llm_client=client,
            messages=[LlmMessage(role="user", content="json")],
            response_model=_Envelope,
            max_repairs=2,
        )
    assert len(client.requests) == 1


def _captured_payload(settings: WebSettings) -> dict[str, object]:
    captured: dict[str, object] = {}
    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content.decode("utf-8")))
        return httpx.Response(
            200,
            json={
                "id": "req",
                "model": settings.llm.model,
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
        )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None
    client.complete(LlmRequest(messages=[LlmMessage(role="user", content="hi")]))
    return captured


@pytest.mark.parametrize("provider", ["openai", "llama_server"])
def test_d3_temperature_mode_exact_payload_contract(tmp_path: Path, provider: str) -> None:
    base_url = None if provider == "openai" else "http://127.0.0.1:8080"
    common = {
        "provider": provider,
        "model": "test-model",
        "base_url": base_url,
        "api_key": "test-key",
        "temperature": 0.2,
        "max_tokens": None,
    }
    send = _settings(tmp_path, **common, temperature_mode="send")
    omit = _settings(tmp_path, **common, temperature_mode="omit")
    assert _captured_payload(send) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 0.2,
    }
    assert _captured_payload(omit) == {
        "model": "test-model",
        "messages": [{"role": "user", "content": "hi"}],
    }


def test_d3_temperature_mode_is_explicit_provenance(tmp_path: Path) -> None:
    send = _llm_provenance(_settings(tmp_path, temperature_mode="send"))
    omit = _llm_provenance(_settings(tmp_path, temperature_mode="omit"))
    assert send.temperature_mode == "send"
    assert omit.temperature_mode == "omit"
    assert send.config_revision != omit.config_revision


def test_d3_unknown_temperature_mode_rejects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_TEMPERATURE_MODE", "guess")
    with pytest.raises(ValueError, match="temperature_mode"):
        load_settings()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"timeout_s": 0}, "greater than zero"),
        ({"timeout_s": 301}, "300 seconds or less"),
        ({"retry_max_delay_s": 61}, "60 seconds or less"),
        ({"retry_base_delay_s": 5, "retry_max_delay_s": 4}, "at least llm.retry_base_delay_s"),
        ({"retry_max_attempts": 0}, "between 1 and 10"),
        ({"retry_max_attempts": 11}, "between 1 and 10"),
    ],
)
def test_d4_retry_timeout_configuration_caps(values: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_llm_settings(LlmSettings(**values))  # type: ignore[arg-type]


def test_d4_maximum_scheduled_retry_sleep_is_deterministic(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, retry_max_attempts=10, retry_base_delay_s=1.0, retry_max_delay_s=60.0
    )
    client = build_llm_client(settings)
    assert client is not None
    assert client.max_scheduled_retry_sleep_s == 540.0  # type: ignore[attr-defined]
    close = getattr(client, "close", None)
    if callable(close):
        close()


def test_d5_debug_artifact_count_prunes_oldest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE", 2)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_count", stage="spec")
    assert writer is not None
    writer({"n": 1})
    writer({"n": 2})
    writer({"n": 3})
    artifacts = sorted(
        (settings.data_dir / "wizard_sessions/wiz_debug_count/debug_artifacts").glob("spec_*.json")
    )
    assert len(artifacts) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8"))["n"] for path in artifacts]
    assert payloads == [2, 3]


def test_d5_byte_cap_keeps_newest_even_when_oversize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_TOTAL_BYTES", 100)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_bytes", stage="ir")
    assert writer is not None
    writer({"small": "x"})
    writer({"huge": "x" * 500})
    artifacts = list(
        (settings.data_dir / "wizard_sessions/wiz_debug_bytes/debug_artifacts").glob("*.json")
    )
    assert len(artifacts) == 1
    assert json.loads(artifacts[0].read_text(encoding="utf-8"))["huge"] == "x" * 500


def test_d5_prune_delete_failure_warns_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(tmp_path, debug_artifact_capture=True)
    monkeypatch.setattr(session_io, "_DEBUG_ARTIFACT_MAX_FILES_PER_STAGE", 1)
    original_unlink = Path.unlink
    calls = {"count": 0}
    def fail_first(path: Path, *args: object, **kwargs: object) -> None:
        if "debug_artifacts" in path.parts and calls["count"] == 0:
            calls["count"] += 1
            raise OSError("simulated prune failure")
        original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", fail_first)
    writer = _make_debug_artifact_writer(settings, "wiz_debug_failure", stage="spec")
    assert writer is not None
    with caplog.at_level(logging.WARNING):
        writer({"n": 1})
        writer({"n": 2})
    assert "failed to prune wizard debug artifact" in caplog.text


def test_d6_failure_kind_explicit_and_legacy_interpretation() -> None:
    base = WizardSessionDetail(
        id="wiz_failure_kind",
        status="drafting_ir",
        created_at="2026-08-10T00:00:00Z",
        updated_at="2026-08-10T00:00:00Z",
    )
    operational = _set_error(
        base, {"details": {"operation": "generate_ir"}}, failure_kind="operational"
    )
    generation = _set_error(
        base, {"details": {"operation": "generate_project"}}, failure_kind="generation"
    )
    unsupported = base.model_copy(
        update={"status": "failed", "failure_kind": "unsupported_design", "error": None}
    )
    legacy_soft = base.model_copy(update={"status": "failed", "failure_kind": None, "error": None})
    legacy_error = base.model_copy(
        update={"status": "failed", "failure_kind": None, "error": {"details": {}}}
    )
    assert _effective_failure_kind(operational) == "operational"
    assert _failure_operation(operational) == "generate_ir"
    assert _effective_failure_kind(generation) == "generation"
    assert _failure_operation(generation) == "generate_project"
    assert _effective_failure_kind(unsupported) == "unsupported_design"
    assert _failure_operation(unsupported) is None
    assert _effective_failure_kind(legacy_soft) == "unsupported_design"
    assert _effective_failure_kind(legacy_error) == "operational"


def test_d7_empty_data_dir_env_and_toml_reject(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", "")
    with pytest.raises(ValueError, match="web.data_dir must not be empty"):
        load_settings()
    monkeypatch.delenv("KICAD_PCB_WEB_DATA_DIR")
    config = tmp_path / "empty-data.toml"
    config.write_text('[web]\ndata_dir = ""\n', encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config))
    with pytest.raises(ValueError, match="web.data_dir must not be empty"):
        load_settings()


def test_d7_jobs_dir_mkdir_oserror_is_wrapped_as_valueerror(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    data_file = tmp_path / "not-a-directory"
    data_file.write_text("x", encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(data_file))
    with pytest.raises(ValueError, match="Unable to create jobs directory") as caught:
        load_settings()
    assert str(data_file / "jobs") in str(caught.value)
'''),
    encoding="utf-8",
)

subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "."], check=True)
subprocess.run(
    [
        "uv", "run", "--extra", "dev", "--extra", "web", "python", "-m", "pytest",
        "tests/unit/test_wizard_llm_robustness.py", "tests/web/test_web_llm_retry_hardening.py",
        "tests/web/test_web_llm_clients.py", "tests/web/test_web_wizard.py", "-q",
    ],
    check=True,
)
subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "check", "."], check=True)
subprocess.run(["uv", "run", "--extra", "dev", "--extra", "web", "ruff", "format", "--check", "."], check=True)
subprocess.run(
    ["uv", "run", "--extra", "dev", "--extra", "web", "mypy", "src/kicad_pcb", "src/kicad_pcb_web"],
    check=True,
)
subprocess.run(
    [
        "uv", "run", "--extra", "dev", "--extra", "web", "python", "-m", "pytest",
        "tests/unit", "tests/web", "-q",
    ],
    check=True,
)
