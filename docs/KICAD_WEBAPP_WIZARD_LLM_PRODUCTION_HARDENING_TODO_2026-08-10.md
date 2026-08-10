# KiCad Webapp Wizard LLM Production Hardening TODO

Date: 2026-08-10
Branch: `webapp`
Specification: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_SPEC_2026-08-10.md`
Planning baseline before this document pair: `0af8061ac9de7c0344a43a58156b1612c685ba0a`
Spec commit: `4fdac562e590d7b8b85937963592db70b50d365a`

This TODO implements the production-hardening specification after the completed wizard/LLM robustness and follow-up-hardening batches.

Do not weaken previously accepted fail-closed behavior to make this checklist easier to complete.

## Global execution rules

- [ ] Work on `webapp` unless explicitly instructed otherwise.
- [ ] Do not create a PR unless explicitly requested.
- [ ] Before the first product-code change, record the **exact current `webapp` HEAD** as the implementation-starting SHA.
- [ ] Do not confuse the planning baseline, spec/TODO documentation commits, helper-only commits, or review commits with the implementation-starting SHA.
- [ ] Establish a green baseline on the exact implementation-starting product tree before product changes.
- [ ] Preserve all previously accepted wizard/LLM robustness and follow-up-hardening contracts.
- [ ] Do not introduce broad `except Exception` recovery merely to keep a wizard operation moving.
- [ ] Do not introduce silent capability fallbacks or model-name heuristics.
- [ ] Do not replay ambiguous-delivery POST requests automatically.
- [ ] Do not release a per-session lock around provider I/O unless stale-write protection is designed, implemented, and tested first.
- [ ] Keep canonical session persistence fail-closed.
- [ ] Keep debug/observability side channels best-effort and WARNING-visible.
- [ ] Keep deterministic placement/orientation/wire routing/PCB layout and unrelated Circuit IR semantics out of scope.

---

# Phase 0 — Baseline, decision record, and scope capture

## 0.1 Capture exact repository state

- [ ] Record current `webapp` HEAD as the planning/documentation head.
- [ ] Immediately before the first product-code edit, record the exact implementation-starting SHA.
- [ ] Record the prior accepted follow-up-hardening final SHA for historical traceability.
- [ ] Record the expected in-scope directories/files before implementation.
- [ ] Record explicit out-of-scope deterministic-engine/frontend areas.

## 0.2 Establish green implementation baseline

Run on the exact implementation-starting product tree:

- [ ] `uv run --extra dev --extra web ruff check .`
- [ ] `uv run --extra dev --extra web ruff format --check .`
- [ ] `uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web`
- [ ] `uv run --extra dev --extra web python -m pytest tests/unit tests/web`
- [ ] Record counts/skips and any known baseline warnings.
- [ ] Confirm permanent CI for the baseline SHA is green when a normal workflow run exists for that SHA.

## 0.3 Make architecture decisions **before coding**

Create a short decision record in the completion evidence or an implementation note before editing product code.

### P1 — Absolute deadline

Select exactly one:

- [ ] **P1-A:** implement a real absolute operation deadline.
- [ ] **P1-B:** explicitly defer absolute deadline support and retain truthful bounded-timeout/retry-envelope semantics.

If P1-A is selected, record:

- [ ] covered operations
- [ ] monotonic-clock start point
- [ ] configuration key/default/range
- [ ] remaining-time propagation rule
- [ ] retry-sleep truncation rule
- [ ] deadline exhaustion error code/failure_kind/retryability

If P1-B is selected:

- [ ] verify no docs/tests claim a hard end-to-end wall-clock limit
- [ ] record why an absolute deadline is deferred

### P2 — Concurrency model

Select exactly one:

- [ ] **P2-A:** retain lock-held synchronous provider execution.
- [ ] **P2-B:** implement revision/CAS-based out-of-lock execution.

If P2-A is selected:

- [ ] explicitly document worker/lock throughput tradeoff
- [ ] preserve serialization tests
- [ ] prohibit opportunistic lock release in this batch

If P2-B is selected:

- [ ] define operation ID
- [ ] define session revision/base revision
- [ ] define durable in-flight metadata
- [ ] define stale-completion rejection
- [ ] define duplicate-completion handling
- [ ] define cancellation/supersession interaction
- [ ] define crash/restart handling

### Other decisions

- [ ] Choose explicit provider-capability representation.
- [ ] Confirm generic finish-reason classifier remains until provider normalization proves equivalent protection.
- [ ] Choose cancellation scope for this release.
- [ ] Decide whether state transitions are centralized now or only audited/documented.
- [ ] Record npm audit disposition as separate dependency-hardening work unless a narrowly required change is approved.

**Phase 0 exit criterion:** no product code is changed until P1/P2 decisions are explicit and the baseline gates are green.

---

# Phase 1 — P3 provider capability contract

## 1.1 Inventory current provider behavior

For each enabled provider family:

- [ ] OpenAI-compatible
- [ ] llama-server
- [ ] Ollama

Record support/behavior for:

- [ ] temperature send
- [ ] temperature omit
- [ ] structured JSON response expectations
- [ ] native JSON/schema mode if any
- [ ] refusal metadata
- [ ] truncation metadata
- [ ] finish-reason vocabulary
- [ ] request ID availability
- [ ] idempotency-key support
- [ ] token-limit semantics

## 1.2 Implement explicit capability representation

- [ ] Add a typed capability object/contract or equivalent explicit representation.
- [ ] Do not infer capabilities from model-name substrings.
- [ ] Keep `provider=disabled` exempt from enabled-provider payload capabilities.
- [ ] Fail configuration/operation validation for unsupported explicit combinations.
- [ ] Preserve `temperature_mode=send|omit` semantics.
- [ ] Include capability-affecting configuration in provenance/config revision where required for reproducibility.

## 1.3 Provider capability tests

- [ ] Exact OpenAI payload assertion for supported fields.
- [ ] Exact llama-server payload assertion for supported fields.
- [ ] Exact Ollama payload assertion for supported fields.
- [ ] Unsupported provider/capability combination fails closed.
- [ ] Unknown capability enum/config value fails closed.
- [ ] No model-name heuristic path exists.

---

# Phase 2 — P4 completion normalization and generic-classifier migration safety

## 2.1 Define normalized completion contract

- [ ] Define normalized outcome vocabulary.
- [ ] Define typed representation: exceptions/result enum/result object.
- [ ] Define raw provider fields mapped by each provider family.
- [ ] Define unknown terminal-reason behavior.

At minimum distinguish:

- [ ] completed
- [ ] truncated
- [ ] refused
- [ ] filtered
- [ ] no usable content
- [ ] unknown terminal reason where applicable

## 2.2 Preserve fail-closed protection

- [ ] Recognized truncation cannot be accepted merely because content is schema-valid JSON.
- [ ] Recognized refusal/filter cannot be accepted merely because content is schema-valid JSON.
- [ ] Keep generic wizard classifier until provider-specific normalization is proven equivalent.
- [ ] Do not remove generic protection for Ollama prematurely.

## 2.3 Provider-specific tests

Use real provider-client paths with `httpx.MockTransport` or equivalent.

### OpenAI-compatible

- [ ] normal completion
- [ ] truncation
- [ ] content filtering/refusal
- [ ] message-level refusal if supported
- [ ] `content=null` ordering/behavior
- [ ] schema-valid content paired with abnormal terminal reason remains terminal failure

### llama-server

- [ ] normal completion
- [ ] truncation
- [ ] refusal/filter behavior if represented
- [ ] no-content behavior
- [ ] schema-valid content paired with abnormal terminal reason remains terminal failure

### Ollama

- [ ] normal completion
- [ ] recognized `done_reason="length"` with **schema-valid JSON** still fails closed
- [ ] other deliberately recognized terminal reasons
- [ ] unknown reason behavior is explicit and tested

## 2.4 Generic-classifier deletion gate

Only if provider normalization is complete:

- [ ] Prove every enabled provider family has equivalent coverage for currently recognized abnormal outcomes.
- [ ] Remove the generic classifier.
- [ ] Re-run all D2/F2 regressions.

Otherwise:

- [ ] Leave the generic classifier in place.
- [ ] Document it as intentional interim protection and technical debt.
- [ ] Record exact removal prerequisite.

---

# Phase 3 — P5 retry/idempotency/delivery-state hardening

## 3.1 Formalize three independent retry domains

- [ ] HTTP/status retry budget.
- [ ] Structured-output/semantic repair budget.
- [ ] User-triggered operation retry.

## 3.2 HTTP retry contract

- [ ] Enumerate retryable HTTP statuses.
- [ ] Enumerate retryable transport failures, if any.
- [ ] Preserve no-replay behavior for ambiguous delivery.
- [ ] Log retry reason/attempt without leaking secrets.
- [ ] Ensure retry counters cannot multiply structured-repair counters.

## 3.3 Idempotency

If provider-supported idempotency keys are used:

- [ ] Verify provider explicitly supports the mechanism.
- [ ] Define key generation.
- [ ] Define key persistence/lifetime.
- [ ] Define whether retries reuse the same key.
- [ ] Define scope across user-triggered retries.
- [ ] Test exact header/key behavior.

If not used:

- [ ] Explicitly record no idempotency-key behavior rather than simulating support.

## 3.4 Exact-attempt tests

- [ ] status retry exact attempt count
- [ ] no retry after ambiguous delivery
- [ ] repair budget exact attempt count
- [ ] user-triggered retry does not inherit stale internal attempt state

---

# Phase 4 — P1 absolute deadline implementation or explicit deferral

## If P1-B was selected

- [ ] No deadline implementation code is added.
- [ ] Tests/docs use truthful HTTPX timeout semantics.
- [ ] Retry-sleep/configuration envelope remains bounded.
- [ ] Completion evidence records the deferral.
- [ ] Skip remaining Phase 4 implementation tasks as N/A with rationale.

## If P1-A was selected

### 4.1 Configuration

- [ ] Add explicit operation-deadline setting.
- [ ] Define default/min/max.
- [ ] Reject zero/negative/out-of-range values.
- [ ] Define interaction with HTTPX timeout and retry delay settings.
- [ ] Include in provenance/config revision where appropriate.

### 4.2 Runtime deadline propagation

Use a monotonic clock.

- [ ] Start deadline at documented mutation boundary.
- [ ] Calculate remaining budget before every provider attempt.
- [ ] Limit each HTTP attempt to remaining budget without pretending HTTPX scalar timeout is itself a total deadline.
- [ ] Truncate/skip retry sleeps when remaining budget is insufficient.
- [ ] Include structured-output repair attempts in the same absolute budget if specified.
- [ ] Include semantic IR repair attempts in the same absolute budget if specified.
- [ ] Raise/persist one explicit deadline-exhaustion outcome.

### 4.3 Fake-clock tests

- [ ] no real sleeps
- [ ] deadline expires during first provider attempt simulation
- [ ] deadline expires before retry sleep
- [ ] retry sleep is clipped/skipped by remaining budget
- [ ] deadline expires during repair sequence
- [ ] exact terminal state/error/failure_kind
- [ ] no post-deadline extra provider invocation

---

# Phase 5 — P2 concurrency/CAS model

## If P2-A was selected

- [ ] Keep per-session lock held during provider call/retries/repairs.
- [ ] Add/retain test proving same-session mutations serialize.
- [ ] Add/retain test proving different sessions do not share the same session lock unnecessarily, if current infrastructure permits.
- [ ] Document long-operation throughput tradeoff.
- [ ] Confirm no new lock-release path exists.
- [ ] Skip P2-B implementation tasks as N/A with rationale.

## If P2-B was selected

### 5.1 Revision and operation identity

- [ ] Add monotonic/session revision field or equivalent CAS token.
- [ ] Add operation ID.
- [ ] Persist operation type.
- [ ] Persist base revision/input fingerprint as needed.
- [ ] Safe defaults for legacy sessions.

### 5.2 Out-of-lock execution protocol

- [ ] Validate state under lock.
- [ ] Persist in-flight operation under lock.
- [ ] Release lock.
- [ ] Perform provider/repair work outside lock.
- [ ] Reacquire lock.
- [ ] Verify current operation ID/base revision.
- [ ] Commit only if still current.
- [ ] Reject stale result without overwriting newer state.

### 5.3 Concurrency tests

Use deterministic synchronization primitives rather than timing sleeps.

- [ ] two concurrent mutations on same session
- [ ] stale first completion after newer second operation
- [ ] duplicate completion attempt
- [ ] concurrent user edit while provider call is out of lock
- [ ] retry result arriving after supersession
- [ ] different sessions can progress independently

### 5.4 Fail-closed behavior

- [ ] CAS mismatch is explicit, not silently ignored as success.
- [ ] stale completion cannot resurrect old spec/IR/status.
- [ ] duplicate publish cannot run project generation twice.

---

# Phase 6 — P6 cancellation and supersession

## 6.1 Define state contract

- [ ] Define cancelled/superseded representation.
- [ ] Define stable error/status/API code where needed.
- [ ] Define retryability.
- [ ] Define whether cancellation is persisted before provider I/O can be interrupted.

## 6.2 Cover operation stages

Define behavior while:

- [ ] waiting for lock
- [ ] provider I/O is active
- [ ] retry sleep is pending
- [ ] structured-output repair is active
- [ ] semantic IR repair is active
- [ ] result persistence is active

## 6.3 Safety requirements

- [ ] cancelled/superseded operation cannot later publish stale success
- [ ] cancellation does not trigger unsafe replay
- [ ] cancellation state survives process restart as designed
- [ ] if active synchronous HTTPX cancellation is not supported, document it and still prevent stale publication

## 6.4 Tests

- [ ] cancel before provider invocation
- [ ] cancel/supersede during provider wait simulation
- [ ] cancel before retry
- [ ] late completion after cancellation cannot publish
- [ ] cancellation state/retry path is explicit

---

# Phase 7 — P7 wizard state-machine transition hardening

## 7.1 Inventory mutations

Search/audit every write to:

- [ ] `status`
- [ ] `failure_kind`
- [ ] `error`
- [ ] spec fields
- [ ] IR fields
- [ ] validation fields
- [ ] provenance/config revision
- [ ] operation/revision fields if added
- [ ] `latest_job_id`

Record all direct `model_copy(update=...)` transition sites.

## 7.2 Decide centralization

Select:

- [ ] centralized transition helper/table now
- [ ] audit/document only; defer centralization

If centralized:

- [ ] define allowed source status per operation
- [ ] define target status
- [ ] define required fields
- [ ] define cleared fields
- [ ] define preserved inspection fields
- [ ] define retryability/failure_kind expectations
- [ ] route all new relevant transitions through the boundary

## 7.3 State-machine tests

- [ ] every legal operation/source-state pair
- [ ] representative illegal source-state pairs
- [ ] operational retry gates
- [ ] generation retry gates
- [ ] unsupported-design retry gates
- [ ] legacy session read behavior
- [ ] no failure state inferred from `error` shape for new writes

---

# Phase 8 — P8 crash recovery and durable in-flight semantics

## 8.1 Define process-restart behavior

Explicitly disposition:

- [ ] process dies before provider request
- [ ] process dies after request may have been delivered
- [ ] process dies during retry sleep
- [ ] process dies after provider result but before state publication
- [ ] process dies after state publication but before HTTP response to client

## 8.2 In-flight state

If durable in-flight metadata is added:

- [ ] define abandoned-on-startup transition
- [ ] define safe user retry behavior
- [ ] prohibit automatic replay after ambiguous delivery
- [ ] stale in-flight records cannot block forever
- [ ] startup recovery is idempotent

If no durable in-flight state is added:

- [ ] explicitly document current crash semantics and limitations
- [ ] confirm no new code claims crash recovery that does not exist

## 8.3 Tests

- [ ] persisted in-flight session loaded after simulated restart
- [ ] abandoned operation becomes explicit non-success state
- [ ] no automatic provider replay
- [ ] safe retry path available when contract allows it

---

# Phase 9 — P9 structured observability

## 9.1 Define structured event fields

Add/document where appropriate:

- [ ] safe session/correlation ID
- [ ] operation ID
- [ ] operation/stage
- [ ] provider family
- [ ] model identifier if safe
- [ ] attempt number
- [ ] repair round
- [ ] normalized completion outcome
- [ ] retry decision/reason
- [ ] elapsed duration
- [ ] configured timeout/deadline values
- [ ] stable error code
- [ ] failure_kind
- [ ] provider request ID if safe

## 9.2 Redaction requirements

Verify logs do not contain by default:

- [ ] API keys
- [ ] authorization headers
- [ ] full prompts
- [ ] raw user design text
- [ ] private filesystem paths
- [ ] raw provider response bodies containing user content

## 9.3 Observability tests

- [ ] successful operation structured log
- [ ] retry structured log
- [ ] terminal provider failure structured log
- [ ] persistence/debug warning structured log
- [ ] representative secret/path values are absent

---

# Phase 10 — P10 debug-artifact redaction and lifecycle

## 10.1 Inventory captured content

- [ ] prompts
- [ ] provider request payloads
- [ ] provider response payloads
- [ ] headers
- [ ] error details
- [ ] paths
- [ ] model/provider identifiers

## 10.2 Define redaction policy

- [ ] API keys/authorization always redacted
- [ ] private paths redacted or omitted where appropriate
- [ ] decide whether prompt/user content is stored at all
- [ ] decide whether raw provider body is stored at all
- [ ] document model/provider metadata retention

## 10.3 Preserve lifecycle contracts

- [ ] debug capture remains default-off
- [ ] private directory permissions remain enforced
- [ ] 20 artifacts per stage retention remains unless deliberately revised
- [ ] 25 MiB/session retention remains unless deliberately revised
- [ ] deterministic oldest-first pruning remains
- [ ] newest-artifact preservation remains
- [ ] prune/write failures remain WARNING-visible and non-fatal to wizard operation
- [ ] canonical persistence failures remain fatal

## 10.4 Tests

- [ ] representative secret redaction
- [ ] authorization-header redaction
- [ ] private-path redaction where relevant
- [ ] raw prompt/body policy assertion
- [ ] existing retention regressions remain green
- [ ] debug writer failure does not hard-fail wizard
- [ ] canonical session persistence failure still hard-fails

---

# Phase 11 — P11 configuration audit

Create a table in completion evidence covering all wizard/LLM settings.

For each setting record:

- [ ] TOML key
- [ ] env override
- [ ] Python type
- [ ] default
- [ ] valid enum/range
- [ ] empty-string behavior
- [ ] cross-field constraints
- [ ] provider applicability
- [ ] provenance impact
- [ ] malformed explicit-value behavior

## Required regression areas

- [ ] timeout ranges
- [ ] retry delay/attempt constraints
- [ ] deadline constraints if P1-A
- [ ] `temperature_mode`
- [ ] provider/capability combinations
- [ ] base URL validation
- [ ] data/path values
- [ ] non-string TOML values
- [ ] disabled-provider behavior
- [ ] empty env/TOML strings

## Audit requirements

- [ ] no path value can collapse to current directory via empty string
- [ ] no accidental `str(...)` coercion for path/enum values
- [ ] no unsupported explicit capability is ignored
- [ ] actionable error messages identify setting without leaking secret value

---

# Phase 12 — P12 security/trust-boundary review

## 12.1 Base URL / SSRF assumptions

- [ ] Document trusted/local deployment assumptions.
- [ ] Review allowed provider URL schemes/hosts.
- [ ] Review redirect behavior.
- [ ] Verify malformed/unsupported URL fails closed.
- [ ] Do not add a permissive compatibility fallback.

## 12.2 Secret handling

- [ ] API keys never logged.
- [ ] API keys never included in frontend errors.
- [ ] Authorization headers never captured in debug artifacts.
- [ ] Provider error sanitization reviewed.

## 12.3 Provider-supplied metadata

- [ ] Request IDs safe to log/expose or kept internal.
- [ ] Provider error strings sanitized before frontend exposure.
- [ ] No private path or raw prompt leaks through exception detail.

## 12.4 Security fallback audit

Search new/modified code for:

- [ ] broad catches
- [ ] `pass` after exceptions
- [ ] fallback-to-default after invalid explicit configuration
- [ ] guessed capability based on model name
- [ ] retry after ambiguous delivery
- [ ] stale-write last-writer-wins behavior

Document every intentional fallback and justification.

---

# Phase 13 — P13 stable API error/retryability contract

## 13.1 Inventory failure surfaces

For each representative wizard failure record:

- [ ] stable error code
- [ ] HTTP status
- [ ] `failure_kind`
- [ ] retryability
- [ ] required user action
- [ ] preserved inspection state

Include at least:

- [ ] no usable content
- [ ] truncation
- [ ] refusal/filter
- [ ] invalid structured output exhaustion
- [ ] provider transport/status failure
- [ ] deadline exhaustion if P1-A
- [ ] stale revision/CAS failure if P2-B
- [ ] cancellation/supersession
- [ ] unsupported design
- [ ] generation failure
- [ ] canonical persistence failure where it reaches route boundary

## 13.2 Retryability representation

- [ ] Decide whether retryability is already explicit enough.
- [ ] If not, add a stable retryable/non-retryable field or equivalent contract.
- [ ] Do not require frontend parsing of prose.
- [ ] Do not require frontend inference from `error` object shape.

## 13.3 Route tests

- [ ] representative operational failure HTTP mapping
- [ ] representative non-retryable failure mapping
- [ ] failure response cannot be interpreted as success
- [ ] no secret/provider internal leakage

---

# Phase 14 — P14 comprehensive regression architecture

Create/extend tests at the narrowest realistic layer.

## Provider/client tests

- [ ] payload capability matrix
- [ ] completion normalization matrix
- [ ] schema-valid abnormal terminal outcome tests
- [ ] retry/no-replay tests

## Wizard orchestration tests

- [ ] repair budget preserved
- [ ] failure_kind preserved
- [ ] transition legality
- [ ] retry gates
- [ ] cancellation/supersession if implemented
- [ ] deadline behavior if implemented

## Persistence/restart tests

- [ ] canonical persistence failure
- [ ] debug best-effort failure
- [ ] legacy session compatibility
- [ ] in-flight restart semantics if implemented

## Concurrency tests

If P2-B:

- [ ] CAS mismatch
- [ ] stale completion
- [ ] duplicate completion
- [ ] concurrent edits

If P2-A:

- [ ] same-session serialization remains explicit

## Fake-time tests

If P1-A:

- [ ] monotonic fake clock
- [ ] no real sleeps
- [ ] exact deadline/attempt behavior

## HTTP tests

- [ ] stable error codes
- [ ] stable HTTP statuses
- [ ] failure_kind
- [ ] retryability representation

---

# Phase 15 — npm dependency-security disposition

Current permanent CI reports:

- 1 low
- 1 moderate
- 6 high npm audit findings

This batch must not silently ignore or silently remediate them.

- [ ] Record the findings as pre-existing.
- [ ] Determine whether findings are direct or transitive if an audit is performed.
- [ ] Record affected packages/paths if available.
- [ ] Decide whether remediation is safe within this batch.
- [ ] Do **not** run broad `npm audit fix` automatically.

Preferred disposition unless a narrow safe fix is required by this work:

- [ ] Mark frontend dependency remediation as a separate hardening batch.
- [ ] Do not modify frontend lockfile/dependencies in this LLM production-hardening batch.

---

# Phase 16 — Local acceptance gates

Run after all intended product changes are complete.

## Required Python gates

- [ ] `uv run --extra dev --extra web ruff check .`
- [ ] `uv run --extra dev --extra web ruff format --check .`
- [ ] `uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web`
- [ ] `uv run --extra dev --extra web python -m pytest tests/unit tests/web`

Record:

- [ ] test count
- [ ] pass count
- [ ] skip count/reasons
- [ ] coverage if collected
- [ ] Ruff result
- [ ] formatted-file count if emitted
- [ ] mypy source-file count if emitted

## Frontend gates

If frontend/API-client source changed:

- [ ] frontend lint
- [ ] frontend unit tests
- [ ] frontend production build
- [ ] committed bundle verification if repository policy requires it

If frontend did not change:

- [ ] Record frontend source unchanged; permanent CI still validates frontend.

---

# Phase 17 — Scope and dangerous-fallback audit

## 17.1 Diff scope guard

Compare implementation candidate to exact implementation-starting SHA.

- [ ] List every changed file.
- [ ] Verify no deterministic placement changes.
- [ ] Verify no orientation changes.
- [ ] Verify no wire-routing changes.
- [ ] Verify no PCB layout/routing changes.
- [ ] Verify no unrelated Circuit IR semantic changes.
- [ ] Verify no temporary helper/workflow remains.

## 17.2 Silent-failure/fallback audit

Search changed code for:

- [ ] broad `except Exception`
- [ ] exception swallowing
- [ ] warning-free best-effort failures
- [ ] invalid-config fallback to defaults
- [ ] capability guessing
- [ ] ambiguous POST replay
- [ ] stale last-writer-wins state publication
- [ ] success status after provider/persistence failure

For every intentional exception/fallback:

- [ ] document rationale
- [ ] add regression test where behavior is safety-relevant

---

# Phase 18 — Completion evidence

Create:

`docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_COMPLETION_2026-08-10.md`

The completion document must include:

## SHA chain

- [ ] planning baseline SHA
- [ ] spec/TODO documentation SHA(s)
- [ ] exact implementation-starting SHA
- [ ] implementation candidate SHA(s)
- [ ] permanent-CI accepting SHA
- [ ] final documentation SHA

## Architecture dispositions

- [ ] P1 deadline decision and behavior
- [ ] P2 concurrency decision and behavior
- [ ] P3 provider capability contract
- [ ] P4 completion normalization/generic-classifier status
- [ ] P5 retry/idempotency contract
- [ ] P6 cancellation/supersession contract
- [ ] P7 transition/state-machine disposition
- [ ] P8 crash-recovery disposition
- [ ] P9 observability contract
- [ ] P10 debug-artifact redaction/lifecycle contract
- [ ] P11 configuration-audit summary
- [ ] P12 security/trust-boundary summary
- [ ] P13 API error/retryability contract
- [ ] P14 test architecture summary
- [ ] P15 npm audit disposition

## Test/CI evidence

- [ ] local gate commands/results
- [ ] test counts/skips
- [ ] coverage result
- [ ] frontend/browser results
- [ ] KiCad integration result
- [ ] package smoke result
- [ ] permanent CI run ID
- [ ] permanent CI job IDs
- [ ] artifact IDs/names where available
- [ ] independent CI-status bridge result if available

## Scope evidence

- [ ] changed-file list
- [ ] deterministic schematic/PCB behavior untouched
- [ ] temporary helpers removed
- [ ] all deliberate deferrals listed

---

# Phase 19 — Permanent CI acceptance and exact-final-head closure

## 19.1 First normal acceptance candidate

- [ ] Commit/push cleaned product implementation and initial completion evidence on `webapp`.
- [ ] Ensure no temporary helper remains.
- [ ] Trigger/observe the normal permanent workflow.
- [ ] Require all five permanent jobs to succeed:
  - [ ] frontend
  - [ ] Python
  - [ ] package build/install smoke
  - [ ] browser smoke
  - [ ] KiCad integration
- [ ] Investigate/fix any failure; do not waive failing required jobs.

## 19.2 Record authoritative permanent evidence

- [ ] Python counts/coverage/Ruff/format/mypy
- [ ] browser counts/skips
- [ ] KiCad integration counts/version
- [ ] package smoke result
- [ ] frontend result
- [ ] artifact IDs
- [ ] CI-status bridge result where available

## 19.3 Final documentation closure

- [ ] Update completion evidence and this TODO with authoritative acceptance results.
- [ ] Commit/push final documentation closure.
- [ ] Require the **exact final documentation SHA** to pass permanent CI 5/5.
- [ ] Verify branch head still equals that final SHA.
- [ ] Verify independent CI-status bridge agrees where available.
- [ ] Do **not** create another commit solely to check a checkbox that says the current SHA passed CI; record that self-referential gate externally in the final report.

---

# Definition of done

The Ralph loop is complete only when:

- [ ] Phase 0 decisions are explicit and truthful.
- [ ] P1 does not claim a wall-clock guarantee unless a real absolute deadline exists.
- [ ] P2 does not release provider work from the session lock without stale-write protection.
- [ ] Provider capabilities are explicit for enabled providers.
- [ ] Recognized abnormal provider completions cannot silently succeed.
- [ ] Retry/idempotency behavior is explicit and ambiguous-delivery replay remains prohibited.
- [ ] Cancellation/supersession semantics match the chosen concurrency model.
- [ ] Wizard transition behavior is audited and mechanically regression-tested.
- [ ] Crash/restart behavior is explicit and never auto-replays an ambiguous provider operation.
- [ ] Structured observability is useful without leaking secrets/prompts/private paths by default.
- [ ] Debug-artifact redaction/lifecycle is explicit and canonical persistence remains fail-closed.
- [ ] LLM/web configuration is fully audited for type/range/empty/cross-field/capability correctness.
- [ ] Security/trust-boundary review is documented and no silent security downgrade exists.
- [ ] API failure/retryability behavior is stable and tested.
- [ ] Required provider/wizard/route/concurrency/deadline tests for the chosen architecture are green.
- [ ] npm audit findings have an explicit disposition.
- [ ] Local Ruff/format/mypy/unit+web gates are green.
- [ ] Scope audit proves no accidental deterministic schematic/PCB behavior change.
- [ ] Permanent CI is 5/5 green on the accepting SHA.
- [ ] Permanent CI is 5/5 green on the exact final documentation SHA.
- [ ] Completion evidence contains exact SHAs, run/job IDs, counts/skips, artifacts, and deferrals.

## Deferred-work discipline

A task may be marked deferred only when:

1. the governing decision explicitly permits deferral,
2. current behavior remains safe and truthful,
3. the completion document records the limitation and follow-up trigger, and
4. no test or documentation falsely claims the deferred behavior exists.

Do not mark a safety requirement complete by replacing it with prose alone when the selected architecture requires code and tests.

---

# Ralph-loop closure disposition — 2026-08-10

This section is the authoritative disposition of the planning checklist above. The original checkboxes are retained verbatim as historical planning detail, including mutually exclusive alternatives and conditional tasks that cannot all truthfully be checked. Completion status is determined by this disposition plus `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_COMPLETION_2026-08-10.md`.

## Exact accepted product state

- Implementation-starting SHA: `bdfaf93e4f955ba5dafc34e26ab2cae34a280725`.
- Baseline permanent CI: run `31419168095`, 5/5 green.
- Product-accepting SHA: `b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41`.
- Product-accepting permanent CI: run `31430879076`, 5/5 green.
- Product acceptance jobs:
  - Python: `93593786514` — success.
  - Frontend: `93593786559` — success.
  - Browser: `93597157519` — success.
  - KiCad integration: `93597157553` — success.
  - Wheel/sdist package smoke: `93597157574` — success.

## Phase disposition matrix

| Phase / concern | Disposition |
|---|---|
| Global rules | **Complete.** Work stayed on `webapp`; no PR; prior fail-closed protections preserved; deterministic schematic/PCB engine scope excluded. |
| Phase 0 | **Complete.** Exact baseline recorded and validated; P1-B and P2-A selected before product implementation; implementation notes committed before product code. |
| P1 / Phase 4 | **P1-B complete by explicit deferral.** No aggregate wall-clock guarantee is claimed. HTTPX timeout plus bounded retry-envelope semantics remain truthful. P1-A-only implementation/fake-clock tasks are N/A for this batch. |
| P2 / Phase 5 | **P2-A complete.** Per-session lock remains held across provider I/O/retries/repairs; same-session serialization and different-session lock independence are regression-tested. P2-B/CAS tasks are N/A for this batch. |
| P3 / Phase 1 | **Complete.** Typed provider capability contract implemented for OpenAI, llama-server, and Ollama; no model-name capability heuristics. |
| P4 / Phase 2 | **Complete.** Normalized terminal outcomes implemented; truncation/refusal/filter fail closed; Ollama length termination covered. Generic classifier intentionally retained as defense in depth, so deletion-gate tasks are intentionally not selected. |
| P5 / Phase 3 | **Complete.** HTTP/status retries, structured/semantic repair, and user retry remain distinct. Retryable statuses are explicit; ambiguous transport delivery is never automatically replayed; no fake idempotency support. |
| P6 / Phase 6 | **Deferred under P2-A.** Active cancellation/supersession was not invented without durable operation identity/revision. Current safety is session serialization. Follow-up requires P2-B-style durable operation semantics. |
| P7 / Phase 7 | **Audit/document path complete.** Transition mutation sites were inventoried and retained; broad transition-framework centralization is deliberately deferred to a dedicated refactor. |
| P8 / Phase 8 | **Explicit limitation/deferred.** No durable in-flight crash-recovery protocol exists or is claimed; ambiguous provider work is never auto-replayed after restart. Follow-up requires durable operation identity/revision. |
| P9 / Phase 9 | **Complete.** Safe structured request/retry/completion observability implemented; raw prompt/body/secret/private-path logging prohibited by contract. |
| P10 / Phase 10 | **Complete.** Debug capture remains default-off and now stores metadata/fingerprints rather than raw prompts/provider bodies; unsafe structures are WARNING-visible and dropped; lifecycle/retention and canonical fail-closed persistence preserved. |
| P11 / Phase 11 | **Complete.** Full settings table is in completion evidence; strict typing/ranges/cross-field validation added; TOML float-to-int truncation and non-finite float bypasses closed. |
| P12 / Phase 12 | **Complete.** Provider URL trust boundary hardened; credentials/query/fragment rejected; malformed URLs fail closed; redirects are not enabled; secret/provider-body leakage regressions covered. |
| P13 / Phase 13 | **Complete.** Stable explicit API retryability contract added and preserved through wizard wrapping without leaking provider internals. |
| P14 / Phase 14 | **Complete for selected architecture.** Provider, orchestration, persistence, HTTP, observability, config/security, and P2-A concurrency regressions are present. P1-A/P2-B/P6/P8 conditional tests are N/A because those architectures/features were not selected. |
| P15 / Phase 15 | **Disposition complete.** Accepting CI reports 1 low, 1 moderate, 6 high npm findings. No broad `npm audit fix` or frontend lockfile/dependency change was mixed into this LLM batch; remediation is a separate hardening concern. |
| Phase 16 | **Satisfied by authoritative exact-SHA permanent CI.** Connected execution had no separate networked local clone, so no false claim of an independent local run is made. Exact CI ran Ruff, format, mypy, unit/web tests, frontend tests/build, browser, KiCad integration, and package smoke. |
| Phase 17 | **Complete.** Exact diff from implementation start to product acceptance contains 20 files confined to wizard/LLM/settings/errors/tests/docs; no placement/orientation/routing/PCB-layout/frontend-source/unrelated Circuit-IR product changes; temporary format-probe workflow removed. |
| Phase 18 | **Complete.** Completion evidence records SHA chain, decisions, settings/security tables, tests/coverage, CI jobs, artifacts, scope, dangerous-fallback audit, and deferrals. |
| Phase 19.1–19.2 | **Complete.** Exact product SHA passed all five permanent jobs and authoritative evidence was captured. |
| Phase 19.3 | **Documentation commit created by this closure sequence.** The exact final documentation SHA must now pass permanent CI 5/5. Per the self-referential rule, that result is reported externally without another checkbox-only commit. |

## Authoritative acceptance metrics

- Python: 2,817 passed, 7 skipped, 2,824 collected; 90.85% coverage.
- Ruff: all checks passed.
- Ruff format: 467 files already formatted.
- mypy: no issues in 214 source files.
- Frontend: 143 tests passed in 15 files; production build and committed-bundle verification succeeded.
- Browser: 11 passed, 1 live-LLM test skipped because no provider is enabled in CI.
- KiCad integration: success.
- Wheel/sdist package smoke: success.

## Dangerous-fallback and silent-failure closure

The final implementation does not introduce any of the prohibited quiet degradations that motivated this batch:

- no model-name capability guessing;
- no invalid-explicit-config fallback to defaults;
- no ambiguous transport POST replay;
- no schema-valid acceptance of recognized truncated/refused/filtered completions;
- no opportunistic per-session lock release without stale-write protection;
- no raw prompt/provider-body debug persistence;
- no warning-free unsafe debug sanitization fallback;
- no canonical persistence downgrade from fatal to best-effort;
- no broad frontend dependency mutation hidden inside the LLM hardening work.

## Remaining deliberate deferrals

The following remain explicitly deferred and must not be described as implemented:

1. P1-A aggregate monotonic operation deadline.
2. P2-B out-of-lock provider execution with revision/CAS protection.
3. P6 active cancellation/supersession tied to durable operation identity.
4. P8 durable in-flight crash/restart recovery.
5. P7 full transition-framework centralization.
6. Frontend npm vulnerability remediation (current posture: 1 low, 1 moderate, 6 high).

These deferrals satisfy the checklist's deferred-work discipline: each is architecture-permitted, current behavior remains safe/truthful, the limitation and follow-up trigger are documented in completion evidence, and no test/documentation falsely claims the deferred behavior exists.

## Final closure gate

At the time this section is committed, the only remaining Ralph-loop gate is self-referential: **the exact final documentation SHA must pass permanent CI 5/5 and remain the `webapp` head**. The result is intentionally recorded in the external final Ralph-loop report rather than by creating another commit that would change the SHA being certified.
