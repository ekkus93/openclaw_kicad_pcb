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

The wizard is organized around four visible steps:

1. `Describe Circuit`
2. `Review Spec`
3. `Review Circuit IR`
4. `Generate Project`

Route family:

```text
/wizard
/wizard/{session_id}
/wizard/{session_id}/describe
/wizard/{session_id}/spec
/wizard/{session_id}/ir
/wizard/{session_id}/generate
```

Normal operator flow:

1. Open `/wizard`.
2. Create a new session from the landing page.
3. Use `/wizard/{session_id}/describe` until the wizard produces a reviewable spec.
4. Move to `/wizard/{session_id}/spec` and either click `Approve Spec` or send a concise revision note.
5. Move to `/wizard/{session_id}/ir`, review the validation banner, fixes, warnings, and optional raw JSON, then generate or repair IR as needed.
6. Move to `/wizard/{session_id}/generate` when the IR is valid.
7. Open the generated job detail page from the final result card.

Route rules:

- `/wizard/{session_id}` redirects to the canonical active step.
- Deep-linking to a future step redirects to the blocking step.
- Earlier completed steps remain revisitable, but editing them can invalidate later work.

Invalidation rules:

- Sending another message from `Describe Circuit` clears spec approval, current Circuit IR, and the active generation result link.
- Sending a revision note from `Review Spec` clears current Circuit IR and the active generation result link.
- Generating Circuit IR again from `Review Circuit IR` clears the active generation result link before the new IR becomes current.

## What the Wizard Stores

Wizard sessions are stored under:

```text
data/wizard_sessions/<session_id>/
```

Typical files:

- `wizard.json` — authoritative private session state
- `spec.json` — derived convenience export
- `circuit_ir.json` — derived convenience export
- `derived_state.json` — export revision/presence metadata

Do not reconstruct a session from `spec.json` or `circuit_ir.json`; they may be
absent after an upstream revision. Inspect `wizard.json` when diagnosing state.
Canonical files are atomically replaced and protected by per-session
cross-process locks. A 409 `RESOURCE_BUSY` response means another mutation is
still active; retry manually after it completes rather than starting parallel
provider requests.

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

Inspect the `Open Questions` and `Unsupported Constraints` boxes, then send one
clarification or revision note from the earlier step.

### IR generation fails

Inspect the `/wizard/{session_id}/ir` page for the exact validation error and any
deterministic fixes already applied.

### Project generation fails

Open the generated job detail page and inspect:

- warnings
- diagnostics/debug
- raw result JSON
- raw error JSON if present

### A step URL redirects somewhere else

The server only renders steps that are currently unlocked. If a route redirects,
the target route is the blocking or canonical step for that session.

## Safety Notes

- Keep the app bound to `127.0.0.1` unless you add authentication and request isolation.
- Keep provider credentials in the server-side config/env layer only.
- The wizard is an assistive design front-end, not the source of truth for KiCad output.
### A wizard mutation returns an error

Use the HTTP status and structured error code first. A 409 indicates an illegal
state transition or an active session lock, 502/503 indicates provider/tooling
availability, and 500 indicates an internal failure. Internal failures include a
non-secret `error_id`; use it to correlate the browser response with the server
traceback. The UI refetches the session after a failed mutation, so inspect the
persisted failed state before retrying. Never copy provider API keys or raw debug
artifacts into issue reports.
