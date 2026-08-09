# LLM Wizard Design

## Overview

The LLM wizard is a guided, local-first front end for producing validated Circuit IR.
It never writes KiCad files directly.

```text
conversation
  -> circuit spec draft
  -> explicit spec approval
  -> Circuit IR draft
  -> deterministic validation/auto-fix
  -> deterministic project generation job
```

The direct JSON flow and the wizard converge on the same deterministic generation path.

## Human and machine artifacts

`CircuitSpec` is the human-review checkpoint. It captures project intent, rails, ports,
functional blocks, component/package preferences, assumptions, open questions,
unsupported constraints, and acceptance criteria.

Circuit IR JSON is the machine-facing checkpoint. Only a current, valid IR revision can
be used to generate a project.

## Provider abstraction

Provider clients live under `src/kicad_pcb_web/services/llm/` and support:

- `openai`
- `ollama`
- `llama_server`
- `disabled`

Provider requests use normalized request/response objects and direct `httpx` clients.
Retryable HTTP responses use bounded exponential backoff with jitter and honor a valid
`Retry-After` within the configured maximum delay. Ambiguous transport failure after a
POST is not blindly replayed because the provider may already be processing billable
work.

## Authoritative session state

Wizard sessions live under:

```text
<data_dir>/wizard_sessions/<session_id>/
```

Files:

- `wizard.json` — sole authoritative session record
- `spec.json` — derived convenience export
- `circuit_ir.json` — derived convenience export
- `derived_state.json` — identifies which authoritative revision the exports reflect

Canonical writes use temp-file flush, `fsync`, atomic replacement, and a bounded
cross-process session lock. Lock contention returns HTTP 409; there is no unlocked
fallback. API reads trust `wizard.json`, never the sidecars.

A sidecar refresh failure does not roll canonical state backward. It returns an explicit
persistence error that states the authoritative commit already occurred, so callers do
not mistake a derived-export problem for a canonical rollback.

## Session states and actionability

Persisted states are:

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

State, not mere presence of old fields, controls which operation is legal.

Important invariants:

- spec approval is allowed only from `spec_ready_for_review`;
- IR generation requires an approved current spec and an allowed IR-generation state;
- project generation requires current valid IR and a generation-ready/completed state,
  or an explicitly classified retry of a failed project-generation operation;
- a failed spec revision cannot use an older approved spec/IR as though it were current;
- a failed IR replacement can preserve the previous checkpoint for inspection, but that
  preserved IR is not actionable as the newly requested revision;
- failed IR generation and failed project generation have operation-aware retry paths.

This deliberately separates **checkpoint preservation** from **current actionability**.
Keeping the last known-good data visible is not a fallback to using it silently.

## Canonical UI routes

- `drafting_spec` -> `/wizard/{session_id}/describe`
- `awaiting_user_clarification` -> `/wizard/{session_id}/describe`
- `spec_ready_for_review` -> `/wizard/{session_id}/spec`
- `spec_approved` -> `/wizard/{session_id}/ir`
- `drafting_ir` -> `/wizard/{session_id}/ir`
- `ir_needs_repair` -> `/wizard/{session_id}/ir`
- `ir_ready_for_generation` -> generation is unlocked
- `generation_started` -> `/wizard/{session_id}/generate`
- `completed` -> `/wizard/{session_id}/generate`
- `failed` -> an operation-aware recovery route; preserved fields alone do not unlock a
  future step

The frontend mirrors the backend readiness rules and redirects illegal future-step deep
links to the canonical blocking step.

## Revision and failure semantics

A replacement operation does not destructively discard the last-known-good checkpoint
before the replacement succeeds. During a spec or IR request, the session records the
in-progress/failed operation while preserving prior data where useful for diagnosis.
Only a successful replacement becomes the current actionable revision.

Consequences:

- provider failure during spec revision does not masquerade as successful revision;
- provider failure during IR regeneration does not enable project generation from stale
  IR;
- retries are explicit and operation-specific;
- deterministic project generation can be rerun from the same current valid IR after a
  completed job without invoking the LLM again.

## LLM provenance

Each LLM-produced revision records non-secret provenance including provider, model,
prompt version, endpoint identity fingerprint, and a configuration revision fingerprint.
The session also records the provider/model/prompt identity used to create it.

Before an LLM-backed spec revision or IR generation/retry, the server compares persisted
revision provenance with the immutable startup configuration. Provider, model, prompt,
or endpoint drift produces a typed `WIZARD_LLM_PROVENANCE_MISMATCH` conflict before the
LLM request and before session mutation.

Historical sessions remain readable after configuration changes. Deterministic project
generation from an already current, valid IR does not require the LLM configuration to
remain available.

No API key or raw credential is persisted in provenance. Endpoint identity is stored as
a non-secret fingerprint rather than a credential-bearing URL.

## Prompt contracts and repair

Spec output contains:

- `assistant_message`
- `next_state`
- `spec`
- `assumptions`
- `open_questions`
- `unsupported_reasons`

IR output contains:

- `assistant_message`
- `netlist_json`
- `assumptions`

Malformed provider JSON gets bounded structured repair attempts. IR validation uses:

1. LLM draft.
2. Deterministic validation.
3. Deterministic auto-fix.
4. Bounded LLM repair with the validator error if still invalid.
5. Deterministic project generation only after IR is valid/current.

## API surface

```text
/api/wizard/sessions
/api/wizard/sessions/{id}
/api/wizard/sessions/{id}/messages
/api/wizard/sessions/{id}/approve-spec
/api/wizard/sessions/{id}/generate-ir
/api/wizard/sessions/{id}/clear-ir
/api/wizard/sessions/{id}/generate-project
```

Mutations return non-success HTTP status when the requested operation does not complete.
Expected state/provenance conflicts and lock contention return 409. Provider/tooling
failures use typed 502/503 responses. Unexpected internal failures return sanitized 500
responses with non-secret correlation IDs and retain server-side tracebacks.

## Debug artifacts and logging

Normal request logs contain lifecycle metadata, timing, status, payload size, and prompt
fingerprints; they do not contain prompt bodies, provider credentials, or raw auth
headers.

`debug_artifact_capture` is **false by default**. When explicitly enabled, wizard debug
files may contain full user messages/prompts, full model completions, parsed structured
results, and parse/repair context. Request-log redaction does **not** redact these files.
Treat the debug-artifact directory as sensitive local data. On POSIX, the implementation
uses restrictive directory permissions; normal job artifact routes do not expose wizard
debug captures.

## Security/exposure boundary

The current application is local-first and has no public multi-user security boundary.
Provider credentials remain server-side. User-supplied local filesystem paths are a
trusted-local-user capability, not a public-server feature. If remote exposure is added,
authentication, authorization, request isolation, and filesystem sandboxing must be
specified at the API boundary first.

## Explicit non-goal of this hardening pass

This hardening work does **not** redesign schematic component placement, topology,
wire-routing, orthogonal routing, crossing minimization, grouping, or layout aesthetics.
Those concerns remain a separate design effort.
