# LLM Wizard Operator Guide

## Purpose

This guide covers local operation and failure diagnosis for the LLM-assisted wizard in
the KiCad PCB web app.

## Start the web app

From the repository root:

```bash
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/wizard
```

The application validates its runtime configuration during startup, before serving
requests. Settings are cached as one immutable process snapshot. If you change the TOML
file or environment variables, restart the process; requests do not dynamically switch
provider/model configuration mid-session.

## Config file behavior

The optional default file is:

```text
./kicad_pcb_web.toml
```

If no explicit config path is set, absence of that default file is allowed. If you set:

```bash
export KICAD_PCB_WEB_CONFIG_FILE=/path/to/kicad_pcb_web.toml
```

then that file is mandatory. A missing, unreadable, or invalid explicit file fails
startup rather than silently reverting to defaults. Unsupported TOML keys also fail
configuration validation instead of being ignored.

## Provider examples

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
debug_artifact_capture = false
retry_max_attempts = 3
retry_base_delay_s = 0.5
retry_max_delay_s = 8.0
retry_jitter_s = 0.25
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
debug_artifact_capture = false
retry_max_attempts = 3
retry_base_delay_s = 0.5
retry_max_delay_s = 8.0
retry_jitter_s = 0.25
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
debug_artifact_capture = false
retry_max_attempts = 3
retry_base_delay_s = 0.5
retry_max_delay_s = 8.0
retry_jitter_s = 0.25
```

### Disabled

```toml
[llm]
provider = "disabled"
```

There is no `network_probe_enabled` setting. Active LLM network probing is not
implemented, so the previous no-op setting was removed rather than advertised as a
working health check.

`enable_streaming=true` is also unsupported by the current synchronous clients and is
rejected explicitly. Request logging is always redacted; attempting to disable
`request_log_redaction` is rejected.

## Retry behavior

Retryable HTTP statuses such as 429 and selected 5xx responses use bounded exponential
backoff with jitter and honor a valid `Retry-After` value up to the configured maximum.
A transport failure after a POST has ambiguous delivery semantics: the server does not
blindly replay it because the provider may already be processing the request.

If such a failure occurs, retry from the wizard deliberately after inspecting the
persisted state rather than assuming the first provider request was never received.

## Normal workflow

The four visible steps are:

1. `Describe Circuit`
2. `Review Spec`
3. `Circuit Plan`
4. `Generate Project`

Normal flow:

1. Create a session on `/wizard`.
2. Refine the description until the spec is reviewable.
3. Approve the current spec.
4. Generate/repair Circuit IR on the Circuit Plan step.
5. Continue to Generate only when the current IR is valid and generation-ready.
6. Generate the deterministic KiCad job and inspect its job page/artifacts.

The server is authoritative for which step/action is unlocked. A field being present in
`wizard.json` does not by itself mean it is current/actionable.

## Checkpoint preservation and retries

The hardening model preserves last-known-good data for inspection without silently using
it as the result of a failed replacement.

- If a spec revision fails, the earlier checkpoint may still be visible, but it is not
  treated as a newly revised/approved spec.
- If IR regeneration fails after a previously valid IR existed, that older IR may remain
  visible for diagnosis, but Generate is not unlocked as though regeneration succeeded.
- A failed IR operation has an explicit retry path.
- A failed project-generation operation has an explicit retry path.
- A completed session can deliberately generate another job from the same current valid
  IR without invoking the LLM again.

Never interpret preserved checkpoint data in a `failed` session as proof that the failed
operation succeeded.

## LLM provenance and restarts

LLM-produced revisions persist non-secret provider/model/prompt/endpoint fingerprints.
Before spec revision or IR generation, the server compares persisted provenance with the
current startup snapshot.

If provider, model, prompt version, or endpoint identity changed, the mutation fails with
HTTP 409 and code `WIZARD_LLM_PROVENANCE_MISMATCH`. The historical session remains
readable. Restart with the original LLM configuration to continue that LLM-backed
revision, or start a new session under the new provider/model.

Already-valid deterministic IR can still be used for deterministic project generation
when allowed by the session state; project generation itself does not need to call the
LLM.

## What the wizard stores

Sessions live under:

```text
data/wizard_sessions/<session_id>/
```

Typical files:

- `wizard.json` — authoritative session state
- `spec.json` — derived convenience export
- `circuit_ir.json` — derived convenience export
- `derived_state.json` — export revision/presence metadata

Do not reconstruct a session from the derived files. A derived export can be absent or
stale relative to a failed refresh; `wizard.json` is authoritative. Canonical writes are
atomic and protected by a bounded per-session cross-process lock. HTTP 409
`RESOURCE_BUSY` means another mutation owns that lock; retry after it completes rather
than sending parallel provider requests.

## Debug artifact confidentiality

`debug_artifact_capture` is **false by default**.

If you explicitly enable it, files under the wizard session's debug-artifact directory
may contain:

- full user prompts/messages;
- full model completions;
- parsed structured results;
- parse errors and repair context.

`request_log_redaction` applies to normal logs; it does **not** redact debug artifact
files. Treat that directory as sensitive local data, restrict backup/sharing access, and
do not attach raw captures to public issue reports. Normal job artifact download routes
do not expose wizard debug artifacts.

## Project/preview failure behavior

Direct synchronous generation returns non-2xx when the generation operation fails even
though the failed job remains persisted. Use the returned `job_id` to inspect the job.
Unexpected internal failures also return an `error_id` that matches the persisted job
and server log.

A schematic PNG preview is optional derived output. Preview generation requires both
`kicad-cli` and `rsvg-convert`. If the deterministic project and ZIP succeed but only the
preview fails, the job may remain successful with a structured
`PREVIEW_GENERATION_SKIPPED` warning and no preview PNG. That is an intentional visible
degraded mode, not a silent fallback.

## Troubleshooting

### The app will not start after a config change

Read the startup validation error. Explicit config paths and unknown settings fail
closed. Fix the path/key/value and restart rather than deleting the error by falling
back to defaults.

### Wizard says the LLM is disabled

Check `provider` and restart after any configuration change.

### OpenAI fails immediately

Check `api_key`, `model`, and optional `base_url`.

### Ollama or llama-server fails immediately

Check that the local server is running, `base_url` is correct, and the named model is
available.

### Spec will not approve

Resolve open questions/unsupported constraints and ensure you are reviewing the current
`spec_ready_for_review` revision.

### IR generation fails

Inspect the persisted error and Circuit Plan validation state. If the failed operation is
`generate_ir`, use the explicit retry control. Do not proceed to Generate from an older
preserved IR.

### Provenance mismatch after restart

Compare the configured provider/model/prompt/endpoint with the configuration that
created the session. The server intentionally blocks silent drift.

### Project generation fails

Use the non-2xx response's `job_id`; for unexpected failures also record the `error_id`.
Inspect the persisted job error and server logs using those identifiers.

## Safety notes

- Keep the app bound to `127.0.0.1` unless you add authentication, authorization,
  request isolation, and filesystem sandboxing.
- Keep provider credentials in server-side config/env only.
- Treat debug captures as sensitive data.
- The wizard produces structured input for the deterministic engine; it is not an
  alternate KiCad writer.
- This hardening pass intentionally does not redesign component placement or wire
  routing.
