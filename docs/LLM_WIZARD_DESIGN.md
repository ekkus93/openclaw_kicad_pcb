# LLM Wizard Design

## Overview

The LLM wizard adds a guided circuit-design workflow to the local web app.

Supported pipeline:

```text
conversation
  -> circuit spec draft
  -> explicit spec approval
  -> Circuit IR draft
  -> deterministic validation + auto-fix
  -> project generation job
```

The LLM never writes KiCad project files directly.

## System Boundaries

### Human-facing artifact

The user reviews and approves a `CircuitSpec` model first.

This captures:

- project name
- purpose
- rails
- inputs and outputs
- functional blocks
- packaging preferences
- assumptions
- open questions
- unsupported reasons

### Machine-facing artifact

After approval, the wizard converts the spec into canonical Circuit IR JSON.

That IR is then passed through the existing deterministic path in
`src/kicad_pcb_web/services/netlists.py`.

## Provider Abstraction

Provider clients live under:

```text
src/kicad_pcb_web/services/llm/
```

Supported modes:

- `openai`
- `ollama`
- `llama_server`
- `disabled`

Implementation rules:

- direct `httpx` clients only
- no LiteLLM
- provider requests normalized into shared request/response objects
- bounded retry for transient transport and retryable HTTP failures only

`llama_server` currently assumes an OpenAI-compatible `/chat/completions`
contract.

## Wizard Session Model

Wizard session state is persisted under:

```text
<data_dir>/wizard_sessions/<session_id>/
```

Persisted artifacts:

- `wizard.json`
- `spec.json` when available
- `circuit_ir.json` when available

Session states:

- `drafting_spec`
- `awaiting_user_clarification`
- `spec_ready_for_review`
- `spec_approved`
- `drafting_ir`
- `ir_needs_repair`
- `ir_ready_for_generation`
- `generation_started`
- `completed`
- `failed`

Canonical UI route mapping:

- `drafting_spec` -> `/wizard/{session_id}/describe`
- `awaiting_user_clarification` -> `/wizard/{session_id}/describe`
- `spec_ready_for_review` -> `/wizard/{session_id}/spec`
- `spec_approved` -> `/wizard/{session_id}/ir`
- `drafting_ir` -> `/wizard/{session_id}/ir`
- `ir_needs_repair` -> `/wizard/{session_id}/ir`
- `ir_ready_for_generation` -> `/wizard/{session_id}/generate`
- `generation_started` -> `/wizard/{session_id}/generate`
- `completed` -> `/wizard/{session_id}/generate`
- `failed` -> nearest step with persisted state available

## Prompt Contracts

The wizard uses two structured JSON contracts.

### Spec contract

LLM output keys:

- `assistant_message`
- `next_state`
- `spec`
- `assumptions`
- `open_questions`
- `unsupported_reasons`

### IR contract

LLM output keys:

- `assistant_message`
- `netlist_json`
- `assumptions`

If the provider returns malformed JSON, the wizard performs bounded structured
repair attempts using the configured prompt version and repair limits.

## Repair Order

IR generation uses this order:

1. LLM drafts Circuit IR JSON.
2. Deterministic validation runs.
3. Deterministic auto-fix runs before any LLM repair loop.
4. If validation still fails, the exact validator error is fed back into a
   bounded IR repair loop.
5. Once IR validates, only the deterministic job generator runs.

## API Surface

Wizard API routes:

```text
/api/wizard/sessions
/api/wizard/sessions/{id}
/api/wizard/sessions/{id}/messages
/api/wizard/sessions/{id}/approve-spec
/api/wizard/sessions/{id}/generate-ir
/api/wizard/sessions/{id}/generate-project
```

## UI Surface

The routed wizard UI uses:

- `/wizard` as the start page for creating sessions
- `/wizard/{session_id}` as the canonical-step redirector
- dedicated describe, spec, IR, and generate pages for the active session

Shared layout surfaces:

- step tracker
- session metadata rail
- route-safe notice region
- explicit back/continue navigation

Step-local surfaces:

- `describe`: project inputs, message composer, transcript
- `spec`: human-readable circuit spec plus revision/approval controls
- `ir`: validation-first IR summary, repair action, raw JSON disclosure
- `generate`: readiness summary, project-generation action, latest job result

Route guard rules:

- future-step URLs redirect to the blocking canonical step
- earlier completed steps remain viewable
- backward edits invalidate later derived artifacts deterministically

Invalidation rules:

- conversation changes clear spec approval, IR, and active job link
- spec revision changes clear IR and active job link
- IR regeneration clears the active job link before a new IR becomes current

## Security and Exposure Boundary

Current posture:

- local-first web app
- no browser-side provider credentials
- no public-exposure support

If remote exposure is ever enabled, authentication and authorization should be
added at the API boundary around `/api/wizard/*` and `/api/jobs/*` before
exposure, not inside provider-specific code.

## Logging

Current wizard logs record lifecycle events only:

- session created
- session updated
- spec approved
- IR ready
- project generation finished

Prompt bodies, provider secrets, and raw auth headers are intentionally not
logged.