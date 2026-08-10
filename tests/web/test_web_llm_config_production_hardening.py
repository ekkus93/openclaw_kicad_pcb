"""Production-hardening regressions for wizard/LLM configuration and URL trust boundaries."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from kicad_pcb.errors import ToolError
from kicad_pcb_web.services.llm import LlmMessage, LlmRequest, build_llm_client
from kicad_pcb_web.settings import LlmSettings, WebSettings, load_settings


def _write_config(monkeypatch, tmp_path: Path, text: str) -> None:
    config_path = tmp_path / "kicad_pcb_web.toml"
    config_path.write_text(text.strip(), encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config_path))


@pytest.mark.parametrize(
    ("setting_line", "error_text"),
    [
        ("max_tokens = 4096.5", "Invalid integer for llm.max_tokens"),
        ("spec_max_repair_rounds = 1.5", "Invalid integer for llm.spec_max_repair_rounds"),
        ("ir_max_repair_rounds = 1.5", "Invalid integer for llm.ir_max_repair_rounds"),
        ("retry_max_attempts = 2.5", "Invalid integer for llm.retry_max_attempts"),
        ("debug_artifact_capture = 1", "Invalid boolean for llm.debug_artifact_capture"),
        ("request_log_redaction = 1", "Invalid boolean for llm.request_log_redaction"),
        ("enable_streaming = 0", "Invalid boolean for llm.enable_streaming"),
        ("provider = 123", "llm.provider must be a string"),
        ("temperature_mode = 123", "llm.temperature_mode must be a string"),
        ("model = 123", "llm.model must be a string"),
        ("base_url = 123", "llm.base_url must be a string"),
        ("api_key = 123", "llm.api_key must be a string"),
        ("system_prompt_version = 123", "llm.system_prompt_version must be a string"),
    ],
)
def test_malformed_explicit_toml_values_fail_closed_without_coercion(
    monkeypatch,
    tmp_path: Path,
    setting_line: str,
    error_text: str,
) -> None:
    _write_config(monkeypatch, tmp_path, f"[llm]\n{setting_line}\n")

    with pytest.raises(ValueError, match=error_text):
        load_settings()


@pytest.mark.parametrize(
    ("env_name", "env_value", "error_text"),
    [
        ("KICAD_PCB_WEB_LLM_TIMEOUT_S", "nan", "llm.timeout_s must be a finite number"),
        ("KICAD_PCB_WEB_LLM_TEMPERATURE", "inf", "llm.temperature must be a finite number"),
        (
            "KICAD_PCB_WEB_LLM_RETRY_BASE_DELAY_S",
            "-inf",
            "llm.retry_base_delay_s must be a finite number",
        ),
        (
            "KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S",
            "nan",
            "web.mutation_lock_timeout_s must be a finite number",
        ),
    ],
)
def test_non_finite_numeric_settings_fail_closed(
    monkeypatch,
    tmp_path: Path,
    env_name: str,
    env_value: str,
    error_text: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KICAD_PCB_WEB_CONFIG_FILE", raising=False)
    monkeypatch.setenv(env_name, env_value)

    with pytest.raises(ValueError, match=error_text):
        load_settings()


@pytest.mark.parametrize(
    "base_url",
    [
        "https://operator:URL-SECRET@example.invalid/v1",
        "https://example.invalid/v1?token=URL-SECRET",
        "https://example.invalid/v1#URL-SECRET",
        "https://exa mple.invalid/v1",
    ],
)
def test_provider_base_url_rejects_unsafe_forms_without_echoing_value(
    monkeypatch,
    tmp_path: Path,
    base_url: str,
) -> None:
    _write_config(
        monkeypatch,
        tmp_path,
        f'[llm]\nprovider = "disabled"\nbase_url = "{base_url}"\n',
    )

    with pytest.raises(ValueError, match="Invalid URL for llm.base_url") as exc_info:
        load_settings()

    assert "URL-SECRET" not in str(exc_info.value)
    assert base_url not in str(exc_info.value)


def test_provider_http_client_does_not_follow_redirects() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(
            302,
            headers={"Location": "https://redirect-target.invalid/private"},
        )

    data_dir = Path("/tmp/web-llm-redirect-hardening")
    settings = WebSettings(
        data_dir=data_dir,
        jobs_dir=data_dir / "jobs",
        llm=LlmSettings(
            provider="openai",
            model="model-under-test",
            api_key="test-key",
            retry_max_attempts=3,
            retry_base_delay_s=0.0,
            retry_max_delay_s=0.0,
            retry_jitter_s=0.0,
        ),
    )
    client = build_llm_client(settings, transport=httpx.MockTransport(handler))
    assert client is not None

    with pytest.raises(ToolError) as exc_info:
        client.complete(LlmRequest(messages=[LlmMessage(role="user", content="go")]))
    client.close()  # type: ignore[attr-defined]

    assert requests == ["https://api.openai.com/v1/chat/completions"]
    assert exc_info.value.details["status_code"] == 302
    assert exc_info.value.details["retryable"] is False
