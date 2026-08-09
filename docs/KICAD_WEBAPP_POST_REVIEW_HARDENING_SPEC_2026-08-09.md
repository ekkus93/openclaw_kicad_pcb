# KiCad PCB Web App Post-Review Hardening Spec — 2026-08-09

## Purpose

This document specifies the corrective hardening work identified by the August 9, 2026 code review of the `webapp` branch.

The reviewed baseline is:

```text
branch: webapp
commit: 5e2e362880c1fe38b81a71e71a7cb924b893fd0d
CI run: 30109333310
CI result: success
```

The baseline CI is green across Python lint/type/tests, frontend lint/unit/build, KiCad integration tests, packaging, and Playwright smoke tests. The issues in this document are therefore primarily failure-state, state-machine, diagnostics, configuration, and observability defects that are not adequately exercised by the existing happy-path coverage.

This is a focused hardening pass before the planned redesign of schematic component placement and wire routing.

---

## Goals

1. Prevent stale Circuit IR or stale generated-project state from being treated as current after a failed wizard operation.
2. Make wizard mutations transactional with respect to the currently active approved checkpoint.
3. Make backend state-transition rules authoritative and explicit.
4. Make synchronous project-generation failure visible through HTTP semantics as well as persisted job state.
5. Remove unsafe filesystem/configuration fallbacks.
6. Make startup configuration deterministic and fail-fast.
7. Make persisted wizard LLM provenance trustworthy across process restarts and configuration changes.
8. Make `/api/doctor` accurately report optional preview capability and remove fake/no-op network-probe behavior.
9. Improve correlation, public error sanitization, and LLM retry behavior.
10. Preserve the valid non-fatal schematic-preview degradation contract.
11. Add regression coverage specifically around failure transitions and stale-state prevention.

---

## Non-Goals / Explicitly Out of Scope

This hardening pass must **not** redesign schematic placement or wiring.

Do not change or opportunistically refactor:

- component placement algorithms,
- symbol positioning heuristics,
- block placement,
- schematic topology layout,
- wire-routing algorithms,
- orthogonal routing,
- wire crossing minimization,
- whitespace/spacing optimization,
- net-label strategy except where strictly required by a hardening fix,
- component orientation policy,
- PCB placement/routing behavior,
- Circuit IR semantics unrelated to the defects below,
- the deterministic KiCad generation architecture,
- unrelated UI styling or navigation.

Those topics will be handled in a separate design effort after this hardening work is complete.

Also out of scope:

- background job workers,
- multi-user authentication,
- public Internet deployment,
- a full database migration,
- a general wizard rewrite,
- a general LLM-provider abstraction rewrite.

---

## Existing Behavior That Must Be Preserved

Several parts of the current implementation are intentionally good and must not regress.

### Durable authoritative state

- `wizard.json` remains the authoritative wizard-session record.
- `job.json` remains the authoritative job record.
- Atomic temp-write + `fsync` + replace semantics remain in place.
- Corrupt or missing canonical state must fail visibly; it must not be silently reset, omitted, or reconstructed from convenience sidecars.
- Cross-process mutation locks remain bounded and fail closed with an explicit conflict instead of proceeding unlocked.

### Deterministic trust boundary

The LLM may draft a circuit specification and Circuit IR, but it does not directly write arbitrary KiCad output. Final generation continues through the deterministic validation/generation pipeline.

### Structured-output repair

Malformed LLM JSON may be retried within a bounded repair budget. After the configured repair budget is exhausted, the operation must fail explicitly.

### Non-fatal schematic preview degradation

Schematic PNG preview is an optional derived artifact. A missing/failed preview must **not** fail an otherwise valid KiCad project-generation job, provided that:

- the project itself completed successfully,
- the missing preview is clearly recorded as a warning,
- the API/UI do not claim the preview exists,
- diagnostics accurately state why preview generation was skipped.

The existing `PREVIEW_GENERATION_SKIPPED` behavior is therefore intentionally retained.

---

# Required Invariants

These invariants are the core of this hardening pass.

## Invariant A — Only current validated IR may generate a project

Project generation is allowed only when all of the following are true:

1. the wizard session is in an explicit generation-ready state,
2. an approved spec exists,
3. Circuit IR exists,
4. Circuit IR validation exists,
5. that validation is valid,
6. the IR belongs to the currently active approved spec/checkpoint,
7. no replacement/revision operation is currently pending or has failed in a way that makes the active state ambiguous.

At minimum, with the current data model, `generate_wizard_project()` must require:

```text
status == "ir_ready_for_generation"
spec_approved == true
ir_json != null
ir_validation != null
ir_validation.valid == true
```

Frontend gating must mirror these checks, but frontend gating is not a security/correctness boundary.

## Invariant B — Failure must never reactivate stale state implicitly

If an operation attempts to replace a spec or IR and that replacement fails, previously valid state may remain persisted as a historical/last-known-good checkpoint, but it must not automatically become actionable current state.

A failed IR regeneration must never permit project generation from the old IR merely because `ir_validation.valid` is still true.

## Invariant C — Replacement is committed only after replacement succeeds

Do not destructively erase the last known-good checkpoint merely because an unreliable upstream operation has started.

For example:

```text
approved spec + valid IR + successful prior job
    -> user requests spec revision
    -> LLM provider fails
```

must not silently destroy the persisted previous checkpoint before a replacement spec exists.

The old checkpoint may remain stored for recovery/audit, but must be inactive while the failed revision is unresolved.

Likewise:

```text
valid IR
    -> regenerate IR
    -> provider failure
```

must not cause stale IR to be treated as the new/current result.

## Invariant D — Backend state transitions are authoritative

Every mutating wizard service must validate the source state before acting.

Do not rely on the UI hiding or disabling a button.

Illegal transitions return an explicit `409 WIZARD_STATE_CONFLICT` (or a more specific typed conflict code).

## Invariant E — Explicit operator configuration never silently disappears

If the operator explicitly sets a config-file path, a missing/unreadable/invalid file is fatal. Only absence of the default optional config file may mean “use defaults.”

## Invariant F — Internal containment failure never becomes public path disclosure

A path expected to be inside a job workspace must either resolve inside that workspace or fail. Do not return an absolute filesystem path as a fallback when containment fails.

---

# 1. Wizard stale-IR and state-transition hardening

## Problem

`generate_wizard_ir()` currently changes the session to `drafting_ir` without invalidating or versioning the previous active IR. If a provider or validation operation then raises, failure persistence can leave the old `ir_json` and old `ir_validation.valid == true` attached to a `failed` session.

`generate_wizard_project()` currently checks the presence/validity of IR but does not require the session to be in `ir_ready_for_generation`.

The frontend similarly derives `canGenerateProject` largely from `ir_validation.valid`, and failed sessions containing IR can route to the Generate step.

This permits a sequence like:

```text
valid IR A
-> request IR regeneration B
-> regeneration B fails
-> old IR A remains attached
-> project generation remains possible
```

That is forbidden after this hardening pass.

## Required behavior

### Project generation gate

Backend project generation must require the explicit generation-ready state in addition to valid IR.

If the session is `failed`, `drafting_ir`, `ir_needs_repair`, `spec_approved`, `drafting_spec`, or any other non-ready state, generation must fail with HTTP 409.

### IR replacement transaction

When IR regeneration starts:

- preserve prior known-good state if needed for recovery/audit,
- do not expose it as actionable current IR while replacement is unresolved,
- do not clear the last successful job link merely because the new provider call started,
- on successful new IR validation, atomically commit the new IR and validation and clear the old active generation-result link,
- on failure, persist the operation failure without making the old IR appear current.

The implementation may satisfy this either with explicit revision/checkpoint metadata or with strict status/state gating plus carefully ordered commits. Do not add a large revision-history subsystem unless necessary.

### Frontend

`canGenerateProject` must require the same generation-ready state as the backend.

Failed or in-progress replacement states must not expose an enabled Generate action.

Routing for failed states must guide the user to recovery/retry rather than inferring “generate” solely from the presence of old IR fields.

## Required tests

At minimum:

```text
valid IR
-> regeneration provider failure
-> generate project
=> 409, no new project job
```

Also test:

- generation from `drafting_ir` is rejected,
- generation from `ir_needs_repair` is rejected,
- generation from `failed` is rejected even when old `ir_validation.valid == true`,
- successful IR regeneration still unlocks generation,
- frontend `canGenerateProject` is false for all non-ready statuses.

---

# 2. Transactional spec-revision semantics

## Problem

`post_wizard_message()` currently invalidates approval, IR, and latest-job state and persists those destructive changes before the LLM returns a replacement spec.

A temporary upstream failure can therefore erase the active checkpoint relationship before any new checkpoint exists.

## Required behavior

A spec-revision request must be modeled as an attempted replacement of the currently active checkpoint.

The implementation must:

1. persist the user revision message and operation state safely,
2. preserve the previous known-good spec/IR/job checkpoint until a replacement spec succeeds,
3. prevent the previous checkpoint from being used as if the requested revision had succeeded,
4. on successful revised spec, atomically:
   - replace the active spec,
   - clear old approval,
   - clear active IR/IR validation,
   - clear the active generation-result link,
   - set the new canonical wizard state,
5. on failed revision, retain sufficient previous state for recovery/audit while keeping the session in a state that cannot generate stale output.

Do not silently roll back the user's revision message. The transcript should accurately show what the user requested.

## Recovery

A failed revision must remain retryable. Recovery behavior must be explicit in backend transition rules and represented in frontend controls.

Do not require manual editing of persisted JSON to recover.

---

# 3. Explicit backend transition matrix

The current wizard logic relies too much on field presence. This pass must introduce explicit transition validation.

Document and enforce a transition matrix similar to the following. Exact helper names may vary.

| Operation | Allowed source state(s) | Required conditions |
|---|---|---|
| revise spec / send message | drafting/clarification/review/approved/IR-ready/recoverable-failure states as intentionally supported | valid session, no conflicting mutation |
| approve spec | `spec_ready_for_review` | spec exists, no open questions, no unsupported reasons |
| generate IR | `spec_approved` plus explicitly supported retry states | approved spec exists |
| clear IR | states where IR exists and spec remains approved | explicit operation, no in-progress mutation |
| generate project | `ir_ready_for_generation` | approved spec + current valid IR |

The backend must reject illegal deep/action transitions even if a handcrafted API request bypasses the UI.

Do not use `assert` for user-reachable state validation. Return typed conflicts.

---

# 4. Synchronous project-generation HTTP contract

## Problem

`generate_project_from_netlist_job()` persists a failed job and returns `JobDetail`. The direct `/api/jobs/from-netlist` endpoint therefore returns normal 2xx even when synchronous generation failed.

That allows API clients and monitoring to mistake request success for generation success.

## Required behavior

Persist the failed job for diagnostics, but expose failure through non-2xx HTTP semantics.

Recommended mapping:

- user/validation generation failure: 422,
- missing/unavailable KiCad/tooling required by the requested mode: 503,
- internal/unexpected generation failure: 500.

The structured public error should include the persisted `job_id` so a frontend/operator can inspect the failed job.

Do not discard the failed job merely to simplify HTTP handling.

The wizard generation endpoint must preserve its existing typed failure behavior and should share common status/error mapping where practical.

## Frontend

The direct JSON page must treat a failed generation request as an error. If the error provides `job_id`, it may offer a link to the persisted failed job, but it must not navigate as though generation succeeded.

---

# 5. Remove explicit-config-file silent fallback

## Problem

The current loader treats both of these cases identically:

1. optional default `./kicad_pcb_web.toml` is absent,
2. operator explicitly sets `KICAD_PCB_WEB_CONFIG_FILE=/some/path.toml`, but that file is absent.

Case 2 is unsafe because the application silently starts with different/default settings.

## Required behavior

- If no explicit config path is set and the default optional file does not exist: use defaults/environment.
- If `KICAD_PCB_WEB_CONFIG_FILE` is explicitly set and the file is missing: fail startup.
- If the explicit path exists but cannot be read: fail startup.
- Invalid TOML/config values continue to fail explicitly.

Add tests for all four cases.

---

# 6. Startup-time immutable configuration snapshot

## Problem

`get_settings()` currently reloads configuration per request. This creates two problems:

1. invalid configuration may not fail until a settings-dependent request arrives,
2. an existing wizard session can continue under a different provider/model/prompt configuration than the one recorded when it was created.

## Required behavior

Load and validate the runtime configuration once during application startup/lifespan and use one immutable settings snapshot for the process lifetime.

Tests may continue to override dependencies/settings explicitly.

Startup must fail if runtime configuration is invalid.

Do not claim “fail fast” while deferring validation to an arbitrary later request.

---

# 7. Wizard LLM provenance integrity

## Required persisted provenance

A wizard session must identify at least:

- provider,
- model,
- system prompt version.

If additional generation-relevant knobs materially affect output, consider recording them as well, but do not persist API keys/secrets.

## Continuation across restart/configuration change

When loading an existing wizard session for a new LLM-backed operation, compare the session's persisted provenance with current runtime provenance.

Preferred fail-closed behavior:

- if provider/model/prompt version no longer match, reject the operation with a clear typed conflict explaining that the session was created under different LLM provenance,
- allow read-only viewing of the historical session,
- require an explicit new session or future migration mechanism rather than silently changing providers mid-session.

Do not silently rewrite old provenance to match the new configuration.

---

# 8. Remove job-path containment fallback

## Problem

`_job_relative_path()` currently attempts to relativize a path to the job workspace and falls back to returning the original path if containment fails.

That turns an internal invariant violation into public absolute-path disclosure.

## Required behavior

Paths expected to be job-local must be strictly contained under the job workspace.

If containment fails:

- raise a typed internal/persistence/containment error,
- log sufficient internal context with correlation ID where applicable,
- do not return the absolute path in the public payload.

Do not replace the fallback with another silent placeholder unless the field is explicitly optional and the omission is itself surfaced as a warning. For required generated-artifact paths, fail the operation.

Add tests with an intentionally external path.

---

# 9. Correct `/api/doctor` preview diagnostics

## Problem

Actual preview generation requires both:

```text
kicad-cli
rsvg-convert
```

The doctor currently considers preview tooling available if it finds `rsvg-convert`, ImageMagick `convert`, **or** `kicad-cli`.

That check does not match runtime behavior.

## Required behavior

The doctor must report preview capability based on the actual preview implementation.

Recommended check:

```text
schematic_preview:
    ok = kicad-cli exists AND rsvg-convert exists
```

The detail should state which dependency is missing.

Because preview is optional, its failure does not have to make overall doctor `ok == false`; however the individual capability check must be truthful.

Do not report ImageMagick `convert` as satisfying preview support unless preview generation actually uses it.

Add matrix tests for:

- both present,
- only `kicad-cli`,
- only `rsvg-convert`,
- neither present.

---

# 10. Remove fake/no-op LLM network-probe setting

## Problem

`network_probe_enabled=true` currently does not perform an active probe. Doctor says probing is not implemented, and the failed check is excluded from overall readiness.

A configuration knob that implies behavior but does nothing is misleading.

## Required behavior

For this hardening pass, prefer the smaller change:

- remove `network_probe_enabled` from active runtime configuration,
- remove the fake `llm_network_probe` doctor behavior,
- remove/update README/config examples and tests.

If the implementation instead chooses to retain the setting, then a real bounded provider-specific probe must be implemented and test-covered in the same change. Do not leave the current no-op semantics.

No health probe may expose API keys or full provider responses.

---

# 11. Correlation IDs for unexpected direct-job failures

## Problem

The global uncaught request path and wizard operation path generate correlation IDs, but unexpected failures caught inside `generate_project_from_netlist_job()` currently persist a generic internal error without an ID.

## Required behavior

Every unexpected direct-generation failure must:

1. allocate a correlation/error ID,
2. log that ID with stack trace and job ID,
3. persist the ID in the safe job error payload,
4. return the same ID through any corresponding API error payload.

Do not put raw exception messages or private paths into public payloads merely to improve diagnostics.

---

# 12. Public request-validation sanitization

## Problem

FastAPI/Pydantic request-validation responses currently expose `exc.errors()` directly. Those structures may contain rejected `input` values.

## Required behavior

Public validation errors should retain useful structure without reflecting arbitrary submitted values.

At minimum retain safe fields such as:

- `loc`,
- `type`,
- `msg`.

Omit or sanitize:

- `input`,
- context values that may contain full user payloads, secrets, or private paths.

Continue applying the existing filesystem-path sanitizer to all public strings.

Add tests proving a deliberately sensitive rejected input value is absent from the response.

---

# 13. LLM retry/backoff hardening

## Problem

The HTTP LLM client currently retries selected HTTP statuses and transport failures up to three attempts with only approximately 50–100 ms of delay.

That is too aggressive for 429/5xx conditions and may duplicate expensive completion requests after ambiguous transport failures.

## Required behavior

### HTTP status retries

For retryable HTTP responses such as 429/5xx:

- respect a valid `Retry-After` header where feasible,
- otherwise use bounded exponential backoff with jitter,
- cap total attempts,
- log attempt count without logging prompt contents.

### Transport errors

Differentiate failures that are safe to retry from failures where the provider may already have accepted/processed the request.

Prefer automatic retry for connection-establishment failures.

Be conservative about retrying read/protocol failures after a POST may already have been transmitted. If such retries remain, document the duplicate-inference/billing risk and make the behavior explicit/testable.

### Testability

Do not make unit tests actually sleep for long durations. Isolate delay calculation/sleep injection or otherwise make backoff deterministic/testable.

---

# 14. Debug-artifact confidentiality contract

## Existing behavior

When debug artifact capture is enabled, the server intentionally persists complete LLM request messages and completions.

This is useful for debugging but contains raw user/model content.

## Required behavior

Retain `debug_artifact_capture = false` by default.

Documentation and configuration comments must explicitly state:

- debug artifacts can contain full user prompts,
- debug artifacts can contain full model responses,
- `request_log_redaction` does not imply debug-artifact redaction,
- debug artifacts must be treated as sensitive local data.

Do not expose debug artifacts through the normal public artifact-download API.

Optional improvement: ensure restrictive file/directory permissions where portable and practical.

---

# 15. Frontend error/state alignment

The frontend must not reintroduce failure ambiguity that the backend fixes.

Required changes include:

- `canGenerateProject` mirrors backend generation-ready invariants,
- failed replacement operations do not route to Generate merely because old IR fields exist,
- mutation failures refetch authoritative session state as they do today,
- persisted errors remain visible,
- direct-generation non-2xx failures remain on the input page unless the user explicitly follows a failed-job link,
- retry controls correspond to backend-supported recovery transitions,
- no `catch {}` may hide an error unless the error is demonstrably owned and rendered by the mutation/query state.

The existing React Query authoritative-refetch pattern should be preserved.

---

# 16. Testing Requirements

## Backend unit/web regression tests

Add focused tests for every defect in this spec, including:

1. stale valid IR cannot generate after failed regeneration,
2. in-progress/repair/failed states cannot generate projects,
3. successful regeneration replaces IR and clears the active old job relationship only on success,
4. failed spec revision does not silently destroy the last known-good persisted checkpoint,
5. direct generation failure returns non-2xx and includes failed `job_id`,
6. explicit missing config path fails startup/loading,
7. absent default optional config remains allowed,
8. invalid startup config prevents app startup,
9. provenance mismatch blocks LLM-backed continuation,
10. external job path cannot leak through `_job_relative_path`,
11. preview doctor capability matrix,
12. no fake network probe remains (or a real probe works, if implemented),
13. unexpected job failures persist/return correlation IDs,
14. request validation omits sensitive rejected input,
15. retry policy respects bounded backoff and intended retry classes,
16. debug artifacts remain disabled by default.

## Frontend unit tests

Add/update tests for:

- generation gating by exact wizard status,
- failed-state canonical routing,
- retry/recovery controls,
- direct-generation API failure handling,
- failed-job link behavior if implemented,
- persisted error visibility.

## Browser smoke tests

At minimum exercise:

- successful direct JSON generation,
- failed direct JSON generation does not look successful,
- wizard happy path,
- wizard IR failure/retry path,
- Generate button unavailable after failed IR replacement.

---

# 17. Validation and CI

The final implementation must pass the repository's existing permanent checks for the exact final SHA, including:

- Ruff lint,
- Ruff formatting check,
- mypy,
- Python unit/web tests with coverage,
- frontend lint,
- frontend unit tests,
- frontend production build,
- committed SPA bundle verification,
- KiCad integration tests,
- wheel/sdist build and install smoke test,
- Playwright browser smoke tests.

Do not weaken CI thresholds, skip tests, or mark failures allowed merely to land this hardening work.

Manual testing, if not performed, must be recorded as not performed rather than implied to have passed.

---

# 18. Documentation Updates

Update active documentation to reflect the final implementation, including as applicable:

- `README.md`,
- `docs/LLM_WIZARD_DESIGN.md`,
- `docs/LLM_WIZARD_OPERATOR_GUIDE.md`,
- `docs/JOB_EXECUTION_MODEL.md`,
- config examples/comments.

Document:

- exact project-generation readiness invariant,
- failure/retry semantics,
- immutable runtime configuration behavior,
- LLM session provenance rule,
- optional preview capability,
- removed or implemented network-probe behavior,
- debug-artifact sensitivity,
- failed-job HTTP/API semantics.

Do not document planned schematic placement/wiring behavior in this hardening batch.

---

# Acceptance Criteria

This hardening batch is complete only when all of the following are true:

1. A failed IR regeneration cannot generate from stale IR.
2. Backend project generation requires an explicit generation-ready wizard state.
3. Frontend generation controls mirror the backend state invariant.
4. Failed replacement operations do not silently destroy the last known-good checkpoint before a replacement exists.
5. Direct synchronous generation failures return non-2xx while preserving the failed job for inspection.
6. An explicitly configured missing config file is fatal.
7. Runtime settings are validated at startup and remain stable for the process lifetime.
8. Existing wizard sessions cannot silently change provider/model/prompt provenance.
9. A job-workspace containment failure never falls back to an absolute public path.
10. Doctor preview readiness matches the actual `kicad-cli` + `rsvg-convert` requirement.
11. The fake/no-op network-probe behavior is removed or replaced by a real bounded probe.
12. Unexpected direct-job failures have correlation IDs in logs, persisted state, and public error details.
13. Public request-validation payloads do not echo arbitrary rejected input values.
14. LLM retries use defensible bounded backoff and conservative transport retry semantics.
15. Debug artifact sensitivity is explicit and capture remains disabled by default.
16. Optional schematic-preview failure remains non-fatal and clearly warned.
17. Regression tests cover the failure sequences identified in the review.
18. The exact final implementation SHA passes all permanent CI suites.
19. No component-placement or schematic-wire-routing redesign is included in this batch.
