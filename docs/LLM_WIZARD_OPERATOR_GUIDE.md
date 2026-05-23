# LLM Wizard Operator Guide

## Purpose

This guide covers local operation of the LLM-assisted wizard in the KiCad PCB
web app.

## Start the Web App

From the repository root:

```bash
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/wizard
```

## Config File

Default config file path:

```text
./kicad_pcb_web.toml
```

Override with:

```bash
export KICAD_PCB_WEB_CONFIG_FILE=/path/to/kicad_pcb_web.toml
```

## Provider Examples

### OpenAI

```toml
[llm]
provider = "openai"
model = "gpt-4.1"
api_key = "replace-me"
timeout_s = 60
temperature = 0.2
max_tokens = 4096
system_prompt_version = "v1"
spec_max_repair_rounds = 2
ir_max_repair_rounds = 2
enable_streaming = false
request_log_redaction = true
network_probe_enabled = false
```

### Ollama

```toml
[llm]
provider = "ollama"
model = "llama3.1"
base_url = "http://127.0.0.1:11434"
timeout_s = 120
temperature = 0.2
max_tokens = 4096
system_prompt_version = "v1"
spec_max_repair_rounds = 2
ir_max_repair_rounds = 2
enable_streaming = false
request_log_redaction = true
network_probe_enabled = false
```

### llama-server

```toml
[llm]
provider = "llama_server"
model = "qwen2.5"
base_url = "http://127.0.0.1:8080"
timeout_s = 120
temperature = 0.2
max_tokens = 4096
system_prompt_version = "v1"
spec_max_repair_rounds = 2
ir_max_repair_rounds = 2
enable_streaming = false
request_log_redaction = true
network_probe_enabled = false
```

### Disabled

```toml
[llm]
provider = "disabled"
```

## Common Workflow

1. Open `/wizard`.
2. Enter an initial circuit request.
3. Iterate until the spec is ready for review.
4. Click `Approve Spec`.
5. Click `Generate IR`.
6. Review deterministic fixes and warnings.
7. Click `Generate Project`.
8. Open the generated job detail page.

## What the Wizard Stores

Wizard sessions are stored under:

```text
data/wizard_sessions/<session_id>/
```

Typical files:

- `wizard.json`
- `spec.json`
- `circuit_ir.json`

## Troubleshooting

### Wizard page says the LLM is disabled

Check the `provider` setting in `kicad_pcb_web.toml` and restart the app.

### OpenAI fails immediately

Check:

- `api_key`
- `model`
- optional `base_url` override

### Ollama or llama-server fails immediately

Check:

- the local server is running
- `base_url` is correct
- the named model is available on that server

### Spec will not approve

Inspect the `Open Questions` panel and send another clarification message.

### IR generation fails

Inspect the `Circuit IR Review` panel for the exact validation error and any
deterministic fixes already applied.

### Project generation fails

Open the generated job detail page and inspect:

- warnings
- diagnostics/debug
- raw result JSON
- raw error JSON if present

## Safety Notes

- Keep the app bound to `127.0.0.1` unless you add authentication and request isolation.
- Keep provider credentials in the server-side config/env layer only.
- The wizard is an assistive design front-end, not the source of truth for KiCad output.