from __future__ import annotations

from pathlib import Path

import pytest

from kicad_pcb_web.settings import load_settings


def test_load_settings_defaults_when_no_config(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KICAD_PCB_WEB_CONFIG_FILE", raising=False)
    monkeypatch.delenv("KICAD_PCB_WEB_DATA_DIR", raising=False)
    monkeypatch.delenv("KICAD_PCB_WEB_LLM_PROVIDER", raising=False)

    settings = load_settings()

    assert settings.data_dir == (tmp_path / "data").resolve()
    assert settings.jobs_dir == (tmp_path / "data" / "jobs").resolve()
    assert settings.llm.provider == "disabled"
    assert settings.llm.model is None


def test_load_settings_reads_toml_config_file(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "kicad_pcb_web.toml"
    config_path.write_text(
        """
[web]
data_dir = "./runtime-data"
default_host = "0.0.0.0"
default_port = 9001

[llm]
provider = "ollama"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
timeout_s = 120
temperature = 0.1
max_tokens = 4096
system_prompt_version = "v1"
spec_max_repair_rounds = 3
ir_max_repair_rounds = 4
enable_streaming = true
request_log_redaction = true
network_probe_enabled = false
debug_artifact_capture = true
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config_path))

    settings = load_settings()

    assert settings.data_dir == (tmp_path / "runtime-data").resolve()
    assert settings.default_host == "0.0.0.0"
    assert settings.default_port == 9001
    assert settings.llm.provider == "ollama"
    assert settings.llm.model == "llama3.1"
    assert settings.llm.base_url == "http://127.0.0.1:11434"
    assert settings.llm.api_key is None
    assert settings.llm.timeout_s == 120.0
    assert settings.llm.temperature == 0.1
    assert settings.llm.max_tokens == 4096
    assert settings.llm.system_prompt_version == "v1"
    assert settings.llm.spec_max_repair_rounds == 3
    assert settings.llm.ir_max_repair_rounds == 4
    assert settings.llm.enable_streaming is True
    assert settings.llm.request_log_redaction is True
    assert settings.llm.network_probe_enabled is False
    assert settings.llm.debug_artifact_capture is True


def test_env_overrides_config_file(monkeypatch, tmp_path: Path) -> None:
    config_path = tmp_path / "kicad_pcb_web.toml"
    config_path.write_text(
        """
[web]
data_dir = "./runtime-data"

[llm]
provider = "ollama"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
""".strip(),
        encoding="utf-8",
    )
    override_dir = tmp_path / "env-data"
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config_path))
    monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(override_dir))
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_PROVIDER", "openai")
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_MODEL", "gpt-4.1")
    monkeypatch.setenv("KICAD_PCB_WEB_LLM_API_KEY", "test-key")

    settings = load_settings()

    assert settings.data_dir == override_dir.resolve()
    assert settings.llm.provider == "openai"
    assert settings.llm.model == "gpt-4.1"
    assert settings.llm.api_key == "test-key"
    assert settings.llm.base_url == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    ("config_text", "error_text"),
    [
        (
            """
[llm]
provider = "openai"
model = "gpt-4.1"
""".strip(),
            "Missing required setting: llm.api_key",
        ),
        (
            """
[llm]
provider = "ollama"
model = "llama3.1"
""".strip(),
            "Missing required setting: llm.base_url",
        ),
        (
            """
[llm]
provider = "llama_server"
model = "server-model"
base_url = "notaurl"
""".strip(),
            "Invalid URL for llm.base_url",
        ),
        (
            """
[llm]
provider = "openai"
model = "gpt-4.1"
api_key = "test-key"
timeout_s = 0
""".strip(),
            "llm.timeout_s must be greater than zero",
        ),
    ],
)
def test_load_settings_rejects_invalid_llm_config(
    monkeypatch,
    tmp_path: Path,
    config_text: str,
    error_text: str,
) -> None:
    config_path = tmp_path / "kicad_pcb_web.toml"
    config_path.write_text(config_text, encoding="utf-8")
    monkeypatch.setenv("KICAD_PCB_WEB_CONFIG_FILE", str(config_path))

    with pytest.raises(ValueError, match=error_text):
        load_settings()