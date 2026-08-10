# KiCad Webapp Wizard LLM Production Hardening Specification

Date: 2026-08-10
Branch: `webapp`
Planning baseline SHA: `0af8061ac9de7c0344a43a58156b1612c685ba0a`

## 1. Purpose

This specification defines the next production-hardening tranche for the KiCad webapp wizard/LLM subsystem after completion of:

- `KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_*`
- `KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_*`

Those prior batches closed the immediate correctness gaps around repair budgets, typed completion handling, temperature policy, bounded retry configuration, debug-artifact retention, explicit failure classification, settings hygiene, real provider parsing tests, route-level failure tests, retry-gate behavior, and best-effort debug capture.

This batch must **not re-litigate or weaken those contracts**. Its purpose is to address the production concerns that were intentionally deferred because they require explicit architecture decisions rather than isolated bug fixes.

The governing principles remain:

1. Fail closed on ambiguous state, malformed explicit configuration, unsafe retries, stale writes, and unsupported capability combinations.
2. Do not introduce silent compatibility fallbacks.
3. Do not convert provider or persistence failures into apparent success.
4. Preserve deterministic schematic/PCB behavior outside the wizard/LLM subsystem.
5. Prefer explicit state-machine and capability contracts over inference from incidental fields.
6. Keep observability useful without leaking prompts, secrets, private filesystem paths, or provider credentials.
7. Record exact implementation-start and accepting SHAs separately from review/planning SHAs.

## 2. Scope

### In scope

This batch may change:

- `src/kicad_pcb_web/services/llm/**`
- wizard orchestration and wizard persistence/state models
- wizard HTTP routes and API error representation
- LLM/web settings validation and capability configuration
- retry/deadline/cancellation infrastructure used by wizard LLM operations
- wizard-specific structured logging/observability
- debug-artifact policy and redaction
- tests covering the above
- documentation and CI evidence directly needed by this batch

### Explicit non-goals

Unless separately authorized by an amended specification, this batch must not change:

- deterministic component placement
- component orientation
- wire routing
- PCB placement/routing/layout heuristics
- Circuit IR semantics unrelated to wizard/LLM operational metadata
- symbol resolution behavior
- KiCad generation semantics
- frontend visual redesign
- authentication/multi-user product architecture
- cloud account architecture
- provider model-selection UX
- automatic model-name heuristics for capabilities
- arbitrary background job framework replacement
- frontend dependency upgrades solely to address the currently reported npm audit findings

The existing npm audit findings must be recorded and dispositioned, but dependency remediation is a separate scope unless a narrowly required upgrade is explicitly added after review.

## 3. Baseline contracts that must remain true

The implementation must preserve all previously accepted behavior, including:

### 3.1 Shared IR repair budget

`generate_ir` structural and semantic repair share one total LLM invocation budget:

`ir_max_repair_rounds + 1`

There must be no nested retry multiplier.

### 3.2 Typed completion outcomes

Truncation, refusal/content filtering, no usable content, malformed structured output, provider/transport errors, and schema failures remain distinguishable.

Generic `ToolError` must not be swallowed by structured-output repair logic.

### 3.3 Temperature policy

`temperature_mode = send | omit` remains explicit.

Unsupported enabled-provider combinations must fail at configuration load rather than silently ignoring the setting.

### 3.4 Retry safety

Ambiguous-delivery POST failures must not be automatically replayed.

Transport retry, structured-output repair, and user-triggered retry must remain conceptually separate.

### 3.5 Debug artifact behavior

Debug capture is optional and default-off.

Capture failures are best-effort and WARNING-visible; canonical wizard/session persistence remains fail-closed.

Retention remains bounded and deterministic.

### 3.6 Explicit failure classification

`failure_kind` remains the authoritative discriminator for new failed states:

- `unsupported_design`
- `operational`
- `generation`

Legacy error-shape interpretation remains read-only compatibility behavior and must not become the mechanism for new writes.

### 3.7 Settings hygiene

Malformed explicit configuration fails closed. Empty path values and implicit string coercion for path-valued settings remain rejected.

### 3.8 Interim finish-reason protection

Until provider-specific normalization is complete, the generic wizard-level finish-reason classifier remains an intentional fail-closed protection for recognized reason strings from clients that do not yet normalize those outcomes themselves.

It must not be removed before equivalent coverage exists at the provider adapter boundary.

## 4. Production hardening objectives

## P1 — Define and implement true operation deadlines, or explicitly decline them

### Problem

The current HTTPX scalar timeout and bounded retry configuration constrain connect/read/write/pool inactivity and scheduled retry delays, but they do **not** provide a guaranteed absolute end-to-end elapsed-time deadline for one wizard LLM mutation.

### Required decision

Before implementation, classify the production requirement as one of:

#### P1-A — Absolute operation deadline required

If selected, implement a true deadline spanning the whole mutation:

- initial provider call
- connect/write/read/pool waits
- HTTP response-status retries
- retry sleeps
- structured-output repair calls
- semantic IR repair calls
- remaining-time propagation into each attempt
- deadline exhaustion while holding or reacquiring session state

The deadline contract must define:

1. Which operation(s) are covered: create/revise spec, generate/regenerate IR, or all LLM-backed mutations.
2. When the monotonic clock starts.
3. Configuration key and bounds.
4. How remaining time is propagated into each provider request.
5. Whether retry sleeps are truncated/skipped when insufficient budget remains.
6. Exact typed error/failure_kind on exhaustion.
7. Whether deadline exhaustion is user-retryable.
8. Tests using a fake/controllable monotonic clock rather than wall-clock sleeps.

#### P1-B — No absolute operation deadline in this release

If selected, do not add pseudo-deadline code. Preserve truthful documentation that current limits bound configuration and retry scheduling, not absolute elapsed runtime.

### Acceptance rule

The implementation must not claim an absolute wall-clock guarantee unless P1-A is actually implemented and tested.

## P2 — Concurrency, revision/CAS, and stale-completion protection

### Problem

The current design serializes a session mutation under a per-session lock, including provider waits/retry sleeps. This is safe against lost updates but can monopolize a worker/lock for a long time.

Simply releasing the lock around network calls is **not acceptable** without a stale-write prevention design.

### Required architecture decision

Choose and document either:

#### P2-A — Keep lock-held synchronous mutation for this release

If selected:

- preserve lock-held semantics
- document throughput/latency tradeoff
- add tests that confirm same-session mutations remain serialized
- do not add out-of-lock provider calls opportunistically

#### P2-B — Introduce revision/CAS-based out-of-lock execution

If selected, define an operation token/revision protocol. At minimum:

1. Read session under lock.
2. Validate transition and provenance.
3. Persist a durable in-flight operation record containing operation ID, base revision, operation type, and relevant input fingerprint.
4. Release lock.
5. Execute provider call/repairs outside lock.
6. Reacquire lock.
7. Compare current revision/operation ID against the captured base.
8. Commit only if still current.
9. Reject/discard stale completions without overwriting newer state.
10. Make retry/cancellation behavior explicit.

### Forbidden behavior

- last-writer-wins without revision validation
- silently overwriting a newer user edit
- stale provider completion resurrecting an older state
- duplicate operation completion publishing twice

## P3 — Explicit provider capability contract

### Problem

Provider behavior is currently represented through a mixture of shared request fields, provider classes, and scattered conditionals.

### Required design

Introduce or formalize an explicit capability description for enabled providers. Capabilities must be driven by provider-family configuration, not guessed from model-name substrings.

Candidate capabilities include:

- temperature mode support (`send`, `omit`)
- structured-output/JSON mode availability
- schema-mode availability
- normalized refusal support
- normalized truncation support
- finish-reason vocabulary normalization
- provider request ID availability
- provider idempotency-key support
- token-limit semantics
- tool/function calling support, if ever used by this wizard

### Requirements

1. Unsupported explicit combinations fail at configuration load or operation validation.
2. No `auto` capability mode based on model names in this batch.
3. Capability state that affects reproducibility must be represented in provenance/config revision data where appropriate.
4. Provider payload tests must assert exact key presence/absence.

## P4 — Provider completion normalization and removal plan for generic classifier

### Goal

Move toward one normalized provider-completion contract while preserving fail-closed behavior during migration.

### Requirements

Define a normalized outcome vocabulary, for example:

- `completed`
- `truncated`
- `refused`
- `filtered`
- `no_content`
- `unknown_terminal_reason`

Exact representation may be typed exceptions or a result type, but behavior must be explicit.

For each provider family:

- OpenAI-compatible
- llama-server
- Ollama

specify:

1. Raw fields used for classification.
2. Recognized terminal reasons.
3. Unknown reason behavior.
4. Whether schema-valid content may ever be accepted when the raw provider says the completion was terminal/abnormal.

### Fail-closed requirement

A recognized truncation/refusal/filter outcome must not be accepted merely because the content happens to be valid JSON and schema-conformant.

### Migration rule

The generic wizard classifier may be deleted only when tests prove every enabled provider family has equivalent normalization for the recognized outcomes currently protected by the generic layer.

Unknown provider reasons must not be silently mapped to success without an explicit policy.

## P5 — Retry, idempotency, and delivery-state policy

### Goal

Make retry semantics explicit enough that a future provider or transport change cannot accidentally replay a possibly delivered mutation.

### Required separation

Document and test three independent budgets:

1. HTTP/status retry budget.
2. Structured-output/semantic repair budget.
3. User-triggered operation retry.

### Requirements

- retain no-replay behavior on ambiguous delivery
- define retryable response-status set
- define retryable transport failures, if any
- if provider-supported idempotency keys are introduced, specify generation, persistence, reuse, and scope
- never assume an idempotency header works unless the provider contract explicitly supports it
- exact-attempt-count tests are required
- retry logs must state why a retry is allowed

## P6 — Cancellation and supersession semantics

### Problem

Long provider operations need a defined cancellation model, especially if P1 deadlines or P2 out-of-lock execution are introduced.

### Required contract

Define what cancellation means while an operation is:

- queued/awaiting lock
- in provider I/O
- sleeping between retries
- repairing structured output
- repairing semantic IR
- persisting result

### Requirements

1. Cancellation must be explicit state, not inferred from a missing result.
2. A cancelled/superseded operation must not later publish a result over newer state.
3. Cancellation persistence must be durable enough for process restart behavior to be deterministic.
4. Cancellation must not turn ambiguous delivery into an unsafe automatic replay.
5. If active network cancellation cannot be guaranteed for synchronous HTTPX calls, document the limitation and still block stale publication.

## P7 — Centralize wizard state transitions

### Goal

Reduce distributed status mutation and make illegal transitions mechanically testable.

### Required review

Inventory all writes to wizard session status, failure_kind, error, IR/spec fields, operation metadata, and job linkage.

### Preferred direction

Introduce a centralized transition helper/table or similarly explicit state-machine boundary if it materially reduces duplicated transition logic.

The design must define:

- allowed source statuses
- operation
- target status
- required fields
- fields cleared on transition
- fields preserved for inspection
- retryability
- failure_kind expectations

### Requirements

- no implicit transition through `model_copy(update=...)` that bypasses validation if a centralized transition mechanism is adopted
- illegal source-state requests fail closed
- transition tests cover positive and negative cases
- legacy sessions remain readable

## P8 — Crash recovery and durable in-flight state

### Problem

A process can die after an operation begins but before result publication.

### Required contract

Define startup/reload behavior for any persisted in-flight wizard operation.

At minimum distinguish:

- never started
- in flight
- completed
- failed
- cancelled
- abandoned due to process restart

### Requirements

- startup must not silently treat abandoned work as successful
- do not automatically replay a possibly delivered provider POST solely because the process restarted
- expose a deterministic retry path to the user when safe
- stale in-flight records must not block a session forever
- if P2-A keeps all provider work under an in-memory lock and no durable in-flight record exists, explicitly document current crash semantics and whether this batch changes them

## P9 — Structured observability and correlation

### Goal

Make production failures diagnosable without enabling raw prompt logging.

### Required structured fields

Where available, log:

- session ID or safe correlation ID
- operation ID
- operation/stage
- provider family
- model identifier if not sensitive
- attempt number
- repair round
- normalized completion outcome
- retry decision/reason
- elapsed duration
- configured timeout/deadline values
- failure_kind
- stable error code
- provider request ID if safe to retain

### Security requirements

Do not log by default:

- API keys
- Authorization headers
- full prompts
- raw user design text unless explicitly enabled and documented
- private filesystem paths
- raw provider response bodies containing potentially sensitive user content

Logs must not contain a silent catch-and-ignore path. Expected best-effort failures should be WARNING-visible.

## P10 — Debug artifact redaction and lifecycle review

The existing bounded retention behavior remains.

This phase must explicitly decide:

- whether raw prompts are ever stored
- whether raw provider bodies are ever stored
- which headers/metadata are redacted
- whether model/provider identifiers are retained
- whether exception payloads can include secrets
- whether artifact cleanup at startup is needed after abnormal termination

### Requirements

- default capture remains off
- artifact directory remains private
- retention remains deterministic
- newest-artifact preservation rule remains documented
- best-effort capture must never weaken canonical-state persistence
- tests verify redaction of representative API keys/authorization headers/private paths if those values can reach artifacts

## P11 — Configuration audit and capability validation

Perform a complete audit of wizard/LLM-related configuration.

For each setting record:

- source(s): TOML/env/default
- type
- default
- allowed range/enum
- empty-string behavior
- cross-field constraints
- whether it affects provenance
- disabled-provider behavior
- malformed explicit-value behavior

### Requirements

- no accidental `str(...)` coercion for path/enum fields
- no empty-string-to-current-directory behavior
- no contradictory timeout/retry/deadline values
- no unsupported provider/capability combination silently ignored
- configuration errors include the setting name and actionable reason without leaking secrets

## P12 — Security boundary review

### Required review areas

1. Provider base URL validation and SSRF assumptions.
2. API-key storage and error/log redaction.
3. Prompt/user-data leakage through logs and artifacts.
4. Provider error payload sanitization.
5. Debug capture permissions.
6. Local single-user trust assumptions.
7. Any URL redirect behavior used by provider HTTP clients.
8. Whether provider-supplied request IDs or error strings are safe to expose to the frontend.

### Requirements

- document explicit trust boundary
- dangerous fallback behavior must be called out, not normalized as compatibility
- no security check may silently degrade when configuration is malformed

## P13 — Stable API error and retryability contract

### Goal

The frontend should not infer retryability from prose or HTTP status alone.

### Required review

For each wizard operational failure, define:

- stable error code
- HTTP status
- `failure_kind`
- retryable/non-retryable disposition
- whether a new user action is required
- whether prior spec/IR is preserved for inspection

### Preferred addition

If not already represented cleanly, add an explicit retryability field or equivalent stable API contract rather than requiring the frontend to reverse-engineer backend state.

### Requirements

- unsupported-design, operational, and generation failures remain distinguishable
- provider internals/secrets are not exposed
- route-level tests cover representative failures
- failed operations must not navigate/appear as success

## P14 — Test architecture expansion

### Required layers

The batch must use the narrowest realistic layer for each contract:

1. Provider parsing/payload tests using `httpx.MockTransport` or equivalent real client path.
2. Wizard orchestration/state-machine tests.
3. Persistence/crash-recovery tests.
4. HTTP route tests.
5. Concurrency/CAS tests if P2-B is selected.
6. Fake-clock deadline/backoff tests if P1-A is selected.
7. Cancellation/supersession tests if P6 changes behavior.
8. Redaction/logging tests.
9. Configuration matrix tests.

### Test principles

- no real sleeps for deadline/backoff correctness
- no live external provider dependency in mandatory CI
- exact-attempt counts where retry budgets matter
- schema-valid-content tests for abnormal provider terminal outcomes
- state assertions after failures, not merely exception assertions
- canonical persistence failure must remain fatal even when debug capture is best-effort

## P15 — CI, scope guards, and acceptance evidence

### Required local gates

At minimum:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit tests/web
```

Run frontend gates only if frontend source or API client code changes.

Permanent CI must still include the repository's normal five-job workflow:

- frontend
- Python
- package build/install smoke
- browser smoke
- KiCad integration

### Exact-SHA discipline

Completion evidence must record separately:

1. planning/review baseline SHA
2. exact implementation-starting SHA immediately before the first product-code change
3. implementation candidate SHA(s)
4. permanent-CI accepting SHA
5. exact final documentation SHA and its permanent-CI result

Do not label a review baseline as the implementation-starting SHA if helper-only or planning commits occur afterward.

### Scope guard

The implementation diff must be audited to prove no changes to:

- deterministic placement
- orientation
- wire routing
- PCB layout/routing
- unrelated Circuit IR semantics

unless the specification is explicitly amended.

## 5. Decision matrix before product changes

A Ralph-loop implementation must record the following decisions before editing product code:

| Decision | Allowed choices | Default recommendation |
|---|---|---|
| P1 absolute deadline | P1-A implement / P1-B defer | P1-A only if product requirement is explicit; otherwise P1-B |
| P2 lock/concurrency | P2-A retain lock / P2-B CAS redesign | P2-A for bounded release; P2-B as deliberate architecture tranche |
| Provider capability representation | typed capability object / equivalent explicit contract | typed capability object |
| Generic finish-reason classifier | retain until provider normalization complete / remove after proof | retain until proof |
| Cancellation | publication-only stale-result prevention / active network cancellation where supported | stale-result prevention is minimum |
| Centralized state transitions | adopt now / document and defer | adopt if it reduces duplicated writes without broad rewrite |
| npm audit findings | separate dependency-hardening batch / narrowly remediate if required | separate batch |

If P1-A or P2-B materially expands scope, the TODO may split them into separately accepting phases rather than combining multiple high-risk architectural migrations in one unreviewable commit.

## 6. Failure semantics

The following principles are mandatory:

1. Unknown/ambiguous state is not success.
2. Recognized abnormal provider terminal outcomes are not accepted just because JSON validates.
3. Deadline/cancellation/stale-revision failures must have explicit stable error codes if implemented.
4. Persistence of canonical wizard state remains fail-closed.
5. Debug/observability side channels remain best-effort and warning-visible.
6. No automatic replay after ambiguous delivery.
7. No stale completion may overwrite newer session state.
8. No broad `except Exception` may be introduced merely to keep the wizard moving.
9. Any deliberately retained fallback must be documented with tests and removal criteria.

## 7. Backward compatibility and migration

The application is not required to preserve undocumented implementation quirks.

However:

- existing persisted wizard sessions must remain readable
- existing `failure_kind` values remain valid
- prior provenance records remain parseable
- legacy sessions using the documented read-only failure interpretation remain supported
- new operation/revision/cancellation fields, if added, must have safe defaults for old sessions

Do not silently rewrite old sessions merely by reading them unless an explicit migration design is added.

## 8. npm dependency-security disposition

Current permanent CI reports eight npm audit findings:

- 1 low
- 1 moderate
- 6 high

This production-hardening batch must:

1. Record that the findings pre-exist the wizard/LLM production-hardening work.
2. Identify direct versus transitive dependencies if a dependency audit is performed.
3. Decide whether remediation is safe and in scope.
4. Avoid running broad `npm audit fix` automatically as part of this LLM batch.
5. Create or recommend a separate frontend dependency-hardening specification if remediation requires dependency churn.

The findings must not be hidden or represented as resolved unless verified.

## 9. Completion evidence document

Create:

`docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_COMPLETION_2026-08-10.md`

It must contain:

- P1–P15 disposition
- decision matrix outcomes
- exact SHA chain
- implementation diff/scope audit
- test counts/skips
- coverage result
- Ruff/format/mypy results
- frontend/browser/integration results when applicable
- permanent CI run/job IDs
- artifact IDs/names where available
- provider-capability contract summary
- deadline semantics or explicit deferral
- concurrency/CAS semantics or explicit lock-held deferral
- cancellation semantics
- crash-recovery semantics
- retry/idempotency semantics
- observability/redaction disposition
- configuration audit disposition
- npm audit disposition
- all deliberately deferred follow-up work

## 10. Definition of done

This batch is complete only when all of the following are true:

1. P1 has an explicit truthful decision; no fake wall-clock guarantee exists.
2. P2 has an explicit concurrency contract; no lock is released around provider I/O without stale-write protection.
3. Enabled providers have an explicit capability contract or an explicitly documented equivalent.
4. Recognized abnormal completion outcomes cannot silently become success.
5. Retry/idempotency behavior remains fail-closed on ambiguous delivery.
6. Cancellation/supersession behavior is defined for the selected concurrency model.
7. Wizard transitions are audited and either centralized or explicitly documented with regression coverage.
8. Crash/restart behavior for in-flight LLM work is explicit and does not auto-replay ambiguous operations.
9. Structured observability exists for production diagnosis without leaking secrets/prompts by default.
10. Debug-artifact redaction/lifecycle behavior is explicitly reviewed and tested where relevant.
11. LLM/web configuration is audited for type/range/cross-field/empty-string/capability correctness.
12. Security/trust-boundary assumptions are documented and no silent security degradation is introduced.
13. API error/retryability behavior is stable and regression-tested.
14. Required local gates are green.
15. Permanent CI is 5/5 green on the exact accepting SHA.
16. The final documentation SHA is itself validated by permanent CI without creating an infinite self-referential documentation loop.
17. Scope audit proves no accidental deterministic schematic/PCB behavior change.
18. All known npm audit findings are explicitly dispositioned rather than ignored.

## 11. Recommended implementation strategy

Do **not** implement every architectural option simply because it appears in this spec.

Recommended ordering is:

1. Establish decisions for P1 and P2 first.
2. Harden provider capabilities/completion normalization and retry policy.
3. Harden state transitions, cancellation/supersession, and crash recovery consistent with the chosen concurrency model.
4. Add observability/redaction/configuration/security contracts.
5. Expand route/provider/state/concurrency tests.
6. Run exact-SHA acceptance and record evidence.

If review determines that P1-A (true deadline) and P2-B (CAS/out-of-lock concurrency) are both required, strongly prefer separate internally accepting commits/phases so each high-risk behavior change is independently testable and reviewable.
