# KiCad PCB Web App Post-Review Hardening TODO — 2026-08-09

Implementation checklist for:

```text
docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_SPEC_2026-08-09.md
```

Review baseline:

```text
branch: webapp
reviewed product SHA: 5e2e362880c1fe38b81a71e71a7cb924b893fd0d
baseline CI run: 30109333310
baseline CI result: success
```

This is a focused failure-semantics, state-integrity, configuration, diagnostics, and observability hardening pass.

**Do not redesign schematic component placement or wire routing in this TODO.**

---

# Phase 0 — Baseline, scope, and evidence

## 0.1 Confirm starting point

- [ ] Work on the `webapp` branch.
- [ ] Confirm the branch contains:
  - [ ] `docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_SPEC_2026-08-09.md`
  - [ ] this TODO file
- [ ] Record the implementation starting SHA.
- [ ] Confirm the reviewed baseline findings still reproduce or are still applicable before changing code.

## 0.2 Preserve explicit non-goals

Do **not** modify except where strictly necessary for a hardening fix:

- [ ] component placement algorithms
- [ ] symbol-position heuristics
- [ ] schematic block placement
- [ ] wire-routing algorithms
- [ ] orthogonal routing
- [ ] crossing minimization
- [ ] schematic spacing/whitespace optimization
- [ ] component orientation policy
- [ ] PCB placement/routing
- [ ] unrelated Circuit IR semantics
- [ ] unrelated UI styling

If a hardening change appears to require one of these areas, stop that subtask and document the dependency rather than folding the redesign into this batch.

## 0.3 Preserve good existing contracts

- [ ] Keep atomic canonical state writes.
- [ ] Keep malformed canonical persisted state fail-closed.
- [ ] Keep bounded cross-process mutation locking.
- [ ] Keep deterministic Circuit IR -> KiCad generation boundary.
- [ ] Keep bounded structured-output repair attempts.
- [ ] Keep optional preview failure non-fatal when project generation itself succeeds.
- [ ] Keep `PREVIEW_GENERATION_SKIPPED` visible in result warnings.

## 0.4 Baseline checks

Run and record baseline status before implementation where practical:

```bash
uv run --extra dev --extra web ruff check src tests
uv run --extra dev --extra web ruff format --check src tests
uv run --extra dev --extra web mypy src
uv run --extra dev --extra web pytest -q -rs
cd frontend && npm ci
cd frontend && npm run lint
cd frontend && npm run test:run
cd frontend && npm run build
```

- [ ] Record any pre-existing failures separately.
- [ ] Do not silently classify a pre-existing failure as caused by this batch.

---

# Phase 1 — Backend wizard state-transition contract (P0)

This phase establishes backend authority before frontend changes.

## 1.1 Define explicit transition helpers

Inspect:

```text
src/kicad_pcb_web/services/wizard.py
src/kicad_pcb_web/wizard_models.py
src/kicad_pcb_web/services/_wizard_session_io.py
```

- [ ] Enumerate every wizard mutation:
  - [ ] create session
  - [ ] send/revise spec message
  - [ ] approve spec
  - [ ] generate IR
  - [ ] clear IR
  - [ ] generate project
- [ ] Define allowed source states for each operation.
- [ ] Implement reusable state/condition validation helpers where this avoids duplicated ad-hoc checks.
- [ ] Illegal transitions raise typed `ConflictError`/`WIZARD_STATE_CONFLICT` or a more specific typed conflict.
- [ ] Do not use `assert` for user-reachable transition validation.
- [ ] Keep backend checks authoritative even if frontend buttons are disabled.

## 1.2 Tighten spec approval gate

- [ ] `approve_wizard_spec()` requires the intended review-ready source state.
- [ ] Spec must exist.
- [ ] `open_questions` must be empty.
- [ ] `unsupported_reasons` must be empty.
- [ ] Any custom-block/component requirements currently enforced by frontend must also be enforced by the backend if they are correctness requirements.
- [ ] Approval remains an explicit action.

## 1.3 Tighten project-generation gate

Update `generate_wizard_project()` so project generation requires at minimum:

- [ ] `session.status == "ir_ready_for_generation"`
- [ ] `session.spec_approved is True`
- [ ] `session.ir_json is not None`
- [ ] `session.ir_validation is not None`
- [ ] `session.ir_validation.valid is True`

- [ ] Any failed, drafting, repair-needed, clarification, spec-review, or otherwise non-ready state returns 409.
- [ ] Do not infer readiness from field presence alone.

## 1.4 Add state-transition backend tests

Add parameterized tests where useful.

- [ ] approve-spec rejected outside review-ready state
- [ ] generate-IR rejected without approved spec
- [ ] generate-project rejected from `drafting_ir`
- [ ] generate-project rejected from `ir_needs_repair`
- [ ] generate-project rejected from `failed`
- [ ] generate-project rejected from `spec_approved` before valid IR
- [ ] generate-project succeeds only from generation-ready state with valid current IR

---

# Phase 2 — Fix stale IR after failed regeneration (P0)

## 2.1 Reproduce the reviewed failure sequence in a test first

Create a regression test for:

```text
create/approve spec
-> generate valid IR A
-> attempt regeneration B
-> provider/operation B fails
-> old IR A remains somewhere in persisted state
-> attempt generate project
```

Required assertion:

```text
generate project => HTTP 409
no new generation job is created
```

- [ ] Test must fail against the old behavior before the fix.

## 2.2 Make replacement IR non-actionable until success

When `generate_wizard_ir()` begins:

- [ ] mark an explicit in-progress state,
- [ ] preserve last-known-good state if needed for recovery/audit,
- [ ] do not allow preserved old IR to satisfy generation readiness,
- [ ] do not clear the previous successful job relationship solely because a replacement attempt started.

On successful new IR:

- [ ] validate/auto-fix through the deterministic pipeline,
- [ ] commit the new IR,
- [ ] commit the matching validation object,
- [ ] set `ir_ready_for_generation`,
- [ ] clear the active old `latest_job_id` relationship because a new IR is now current,
- [ ] clear stale operation error state.

On failed new IR/provider operation:

- [ ] persist an explicit error,
- [ ] leave the session in a state that cannot generate a project,
- [ ] preserve prior checkpoint data only as non-current recovery/audit state,
- [ ] keep retry possible.

## 2.3 Do not solve this with a dangerous fallback

- [ ] Do not say “if new IR fails, use previous valid IR.”
- [ ] Do not silently set status back to `ir_ready_for_generation`.
- [ ] Do not let frontend field checks bypass backend status checks.

## 2.4 Regression tests

- [ ] valid A -> failed B -> generate => 409
- [ ] valid A -> failed B -> retry B -> valid B -> generate succeeds
- [ ] valid A -> successful B -> `latest_job_id` from A is no longer active
- [ ] in-progress replacement cannot generate
- [ ] repair-needed replacement cannot generate

---

# Phase 3 — Transactional spec-revision semantics (P0)

## 3.1 Stop destructively invalidating the last good checkpoint before upstream success

Inspect `post_wizard_message()`.

Current destructive pre-provider behavior around these fields must be redesigned:

```text
spec_approved
spec_approved_at
ir_json
ir_validation
latest_job_id
```

Required approach:

- [ ] persist the user's new message safely,
- [ ] mark the revision operation as pending/in progress,
- [ ] preserve the prior known-good checkpoint until a replacement spec succeeds,
- [ ] make the prior checkpoint non-actionable while the requested revision is unresolved.

Do not silently erase the prior checkpoint merely because the provider call started.

## 3.2 Successful revision commit

Only after a valid replacement spec is received:

- [ ] replace the active spec,
- [ ] update assumptions/open questions/unsupported reasons,
- [ ] clear previous approval,
- [ ] clear old active IR and IR validation,
- [ ] clear old active generation-result relationship,
- [ ] update canonical status,
- [ ] clear operation error.

Make this one coherent persisted transition under the existing mutation lock.

## 3.3 Failed revision behavior

If provider/structured-output processing fails:

- [ ] persist the failure visibly,
- [ ] retain the user's revision message in the transcript,
- [ ] retain prior checkpoint data for recovery/audit,
- [ ] do not allow project generation from the old checkpoint as though the revision succeeded,
- [ ] provide a supported retry/recovery transition,
- [ ] do not require manual JSON edits to recover.

## 3.4 Regression tests

Test a session that already has:

```text
approved spec
valid IR
successful latest job
```

Then:

- [ ] failed spec revision preserves the old checkpoint data
- [ ] failed revision does not allow stale generation
- [ ] failed revision keeps the new user message
- [ ] successful retry commits new spec and invalidates old approval/IR/job relationship
- [ ] no intermediate persisted state incorrectly claims the revision succeeded

---

# Phase 4 — Frontend wizard gating and recovery (P0)

Inspect:

```text
frontend/src/routes/wizard/useWizardController.ts
frontend/src/routes/wizard/wizardStepLogic.ts
frontend/src/routes/WizardPage.tsx
frontend/src/queries/wizardQueries.ts
```

## 4.1 Make Generate require exact readiness

Change `canGenerateProject` so it mirrors backend invariants.

At minimum require:

- [ ] `session.status === 'ir_ready_for_generation'`
- [ ] `session.spec_approved === true`
- [ ] `session.ir_validation?.valid === true`
- [ ] current IR exists if represented in frontend types

## 4.2 Fix failed-state canonical routing

- [ ] Do not route a failed session to Generate solely because `ir_json` or `ir_validation` exists.
- [ ] Route based on failed operation/recovery state or another explicit recovery rule.
- [ ] Preserve read-only visibility of old data where useful without making it actionable.

## 4.3 Recovery controls

- [ ] Failed IR generation presents an IR retry/repair path.
- [ ] Failed spec revision presents a spec revision retry/recovery path.
- [ ] Failed project generation permits retry only if backend state still satisfies the intended recovery invariant.
- [ ] Do not expose a button that the backend will always reject unless that rejection is deliberate UX.

## 4.4 Preserve good mutation error behavior

- [ ] Keep `onError` authoritative session invalidation/refetch.
- [ ] Keep mutation errors visible.
- [ ] `catch {}` blocks remain only where React Query mutation state owns and renders the error.
- [ ] Do not add `console.error` as a substitute for user-visible failure state.

## 4.5 Frontend tests

- [ ] `canGenerateProject` false for failed session with old valid IR
- [ ] `canGenerateProject` false for drafting/repair states
- [ ] `canGenerateProject` true for valid `ir_ready_for_generation`
- [ ] canonical step for failed IR replacement is recovery-oriented, not implicitly Generate
- [ ] retry controls map to allowed backend operations

---

# Phase 5 — Synchronous direct-generation HTTP failure contract (P0)

Inspect:

```text
src/kicad_pcb_web/services/netlists.py
src/kicad_pcb_web/routes/api_jobs.py
src/kicad_pcb_web/services/wizard.py
frontend/src/api/jobs.ts
frontend/src/routes/JsonGeneratePage.tsx
```

## 5.1 Preserve failed job state but return non-2xx

- [ ] Keep failed job persistence.
- [ ] Do not change a failed job to success to simplify routing.
- [ ] Convert failed synchronous generation into a typed API error.
- [ ] Include `job_id` in safe structured error details.

Recommended status mapping:

- [ ] validation/user generation failure -> 422
- [ ] required tool unavailable -> 503
- [ ] unexpected internal failure -> 500

- [ ] Share mapping with wizard project-generation failure where practical.

## 5.2 Frontend direct JSON behavior

- [ ] `createJobFromNetlist()` rejects on failed generation through normal `requestJson()` error handling.
- [ ] `JsonGeneratePage` does not unconditionally navigate on a failed generation.
- [ ] Display the structured error.
- [ ] If `job_id` is provided, optionally show an explicit “View failed job” link.
- [ ] Do not make the failed-job link look like successful generation.

## 5.3 Tests

Backend:

- [ ] failed deterministic generation returns non-2xx
- [ ] failed job remains readable by ID
- [ ] public error contains job ID
- [ ] successful generation remains 2xx

Frontend:

- [ ] failed response displays error and remains on page
- [ ] success navigates to job page
- [ ] failed-job link behavior test if implemented

---

# Phase 6 — Remove explicit config-file fallback (P0)

Inspect `src/kicad_pcb_web/settings.py`.

## 6.1 Distinguish default optional config from explicit config

- [ ] If `KICAD_PCB_WEB_CONFIG_FILE` is unset and default file is absent -> allowed.
- [ ] If `KICAD_PCB_WEB_CONFIG_FILE` is set and target file is absent -> raise.
- [ ] Explicit unreadable config -> raise.
- [ ] Invalid TOML -> raise.
- [ ] Invalid field values -> raise.

## 6.2 Tests

- [ ] no explicit config + no default file -> defaults work
- [ ] explicit missing path -> failure
- [ ] explicit unreadable/invalid file -> failure
- [ ] valid explicit file -> loads
- [ ] environment overrides config-file values as documented

---

# Phase 7 — Startup configuration snapshot and fail-fast behavior (P0)

## 7.1 Load settings at application startup

Inspect:

```text
src/kicad_pcb_web/main.py
src/kicad_pcb_web/deps.py
src/kicad_pcb_web/settings.py
```

- [ ] Introduce startup/lifespan configuration loading.
- [ ] Validate the full runtime config during startup.
- [ ] Store one immutable `WebSettings` snapshot for the process lifetime.
- [ ] `get_settings()` returns the startup snapshot rather than re-reading env/TOML on every request.
- [ ] Preserve dependency overrides for tests.

## 7.2 Startup failure tests

- [ ] invalid provider config prevents app startup
- [ ] explicit missing config prevents app startup
- [ ] valid config starts successfully
- [ ] requests see one stable settings snapshot

## 7.3 Documentation

- [ ] README accurately says configuration is validated at startup.
- [ ] Remove any statement implying dynamic per-request config reload unless deliberately retained.

---

# Phase 8 — Wizard LLM provenance integrity (P0)

## 8.1 Persist model provenance

Inspect `WizardSessionDetail` and wizard creation.

Ensure persisted session includes:

- [ ] `llm_provider`
- [ ] `llm_model`
- [ ] `prompt_version`

- [ ] Never persist API keys.

## 8.2 Validate provenance before LLM-backed continuation

Before:

- [ ] spec revision
- [ ] IR generation/retry

compare persisted session provenance with current startup configuration.

If provider/model/prompt version differ:

- [ ] reject with typed conflict
- [ ] explain safely that session provenance no longer matches runtime configuration
- [ ] keep read-only session access working
- [ ] do not rewrite persisted provenance
- [ ] do not silently continue with a different provider/model

## 8.3 Tests

- [ ] same provenance -> continuation works
- [ ] provider mismatch -> conflict
- [ ] model mismatch -> conflict
- [ ] prompt-version mismatch -> conflict
- [ ] historical session still readable after mismatch
- [ ] no secret appears in response/persisted provenance

---

# Phase 9 — Remove job-relative absolute-path fallback (P0)

Inspect `_job_relative_path()` in `src/kicad_pcb_web/services/netlists.py`.

## 9.1 Enforce containment

- [ ] Path expected inside job workspace resolves inside `job.work_dir`.
- [ ] Containment failure raises a typed internal error.
- [ ] Do not `return str(path)` as fallback.
- [ ] Do not expose external absolute path in public payload.
- [ ] Required path failures fail the job/request.
- [ ] Optional path omission, if any, is explicit and warned rather than silent.

## 9.2 Tests

- [ ] normal job-local path becomes relative path
- [ ] external path raises
- [ ] external absolute path absent from API error
- [ ] job failure is persisted visibly

---

# Phase 10 — Fix doctor preview capability reporting (P0)

Inspect `src/kicad_pcb_web/services/doctor.py`.

## 10.1 Match actual preview implementation

Actual preview requires:

```text
kicad-cli AND rsvg-convert
```

- [ ] Replace `rsvg-convert OR convert OR kicad-cli` logic.
- [ ] Do not count ImageMagick `convert` unless preview generation actually uses it.
- [ ] Add one clear preview-capability check or equivalent truthful checks.
- [ ] Detail identifies missing dependency/dependencies.

Preview remains optional:

- [ ] missing preview tooling may leave overall doctor `ok == true` if required core checks pass
- [ ] individual preview check must be `ok == false`

## 10.2 Doctor matrix tests

- [ ] both dependencies present -> preview true
- [ ] only kicad-cli -> preview false
- [ ] only rsvg-convert -> preview false
- [ ] neither -> preview false

---

# Phase 11 — Remove fake/no-op network probe (P1)

Preferred implementation for this batch: remove it until a real probe is needed.

Inspect:

```text
src/kicad_pcb_web/settings.py
src/kicad_pcb_web/services/doctor.py
README.md
kicad_pcb_web.toml examples/tests
```

## 11.1 Preferred removal path

- [ ] Remove `network_probe_enabled` from active LLM settings.
- [ ] Remove env/config parsing for it.
- [ ] Remove `llm_network_probe` doctor entry that says probing is unimplemented.
- [ ] Remove/update README config examples.
- [ ] Update tests.

## 11.2 Alternative only if deliberately implemented

If keeping the flag instead:

- [ ] implement a real bounded provider-specific probe
- [ ] use no completion-generation request for health if avoidable
- [ ] enforce short timeout
- [ ] sanitize response/errors
- [ ] add provider-specific tests
- [ ] make doctor result reflect actual probe result

Do **not** leave the current no-op behavior.

---

# Phase 12 — Correlation IDs for direct-job unexpected failures (P0)

Inspect unexpected exception handling in `generate_project_from_netlist_job()`.

## 12.1 Allocate and propagate ID

For unexpected failures:

- [ ] call `new_error_id()` once
- [ ] log with:
  - [ ] error ID
  - [ ] job ID
  - [ ] exception type
  - [ ] stack trace
- [ ] persist safe job error with the same `error_id`
- [ ] direct API error returns the same ID

## 12.2 Do not leak internals

- [ ] no raw traceback in API
- [ ] no raw exception message unless explicitly sanitized/safe
- [ ] no private filesystem paths

## 12.3 Tests

- [ ] force unexpected generation exception
- [ ] assert job error has `error_id`
- [ ] assert API error has same `error_id`
- [ ] assert public payload omits injected sensitive exception string/path

---

# Phase 13 — Sanitize request-validation errors (P1)

Inspect `validation_error_to_payload()` in `src/kicad_pcb_web/errors.py`.

## 13.1 Stop returning raw `exc.errors()` input

Build a public validation-error representation containing only safe/useful fields.

Retain as needed:

- [ ] `loc`
- [ ] `type`
- [ ] `msg`

Remove/sanitize:

- [ ] `input`
- [ ] unsafe `ctx`
- [ ] embedded private absolute paths
- [ ] arbitrary rejected object content

## 13.2 Tests

Submit a deliberately invalid request containing sentinel sensitive text:

```text
DO_NOT_ECHO_SECRET_12345
```

- [ ] response is 422
- [ ] response explains validation failure
- [ ] sentinel input does not appear anywhere in response JSON
- [ ] safe location/message metadata remains useful

---

# Phase 14 — LLM retry/backoff hardening (P1)

Inspect `src/kicad_pcb_web/services/llm/base.py`.

## 14.1 Replace 50–100 ms retry loop

For retryable HTTP status responses:

- [ ] bounded total attempts
- [ ] respect valid `Retry-After` where practical
- [ ] otherwise exponential backoff + jitter
- [ ] sane maximum delay
- [ ] log attempt/status/timing without prompt contents

## 14.2 Classify transport failures conservatively

- [ ] distinguish connection-establishment errors from ambiguous post-send/read failures where possible
- [ ] retry connect failures if appropriate
- [ ] avoid blindly retrying errors where provider may already be processing the POST
- [ ] if ambiguous retries are intentionally retained, document duplicate inference/billing risk

## 14.3 Test without slow sleeps

- [ ] isolate/inject sleep or delay calculation
- [ ] unit tests do not wait real multi-second delays
- [ ] test 429 handling
- [ ] test `Retry-After`
- [ ] test 5xx backoff
- [ ] test connect failure retry policy
- [ ] test ambiguous read/transport failure policy
- [ ] test max-attempt exhaustion

---

# Phase 15 — Debug artifact confidentiality (P1)

Inspect:

```text
src/kicad_pcb_web/services/_wizard_llm.py
src/kicad_pcb_web/services/_wizard_session_io.py
README.md
docs/LLM_WIZARD_OPERATOR_GUIDE.md
```

## 15.1 Preserve safe default

- [ ] `debug_artifact_capture` remains false by default.

## 15.2 Make confidentiality contract explicit

Document that debug artifacts may contain:

- [ ] full user prompts/messages
- [ ] full model completions
- [ ] structured parsed results
- [ ] parse/repair context

Document that:

- [ ] `request_log_redaction` does not redact debug artifact files
- [ ] debug artifact directories must be treated as sensitive local data

## 15.3 Keep debug files private from normal artifact API

- [ ] normal `/api/jobs/.../artifacts` cannot expose wizard debug artifacts
- [ ] no new route exposes debug captures by default

## 15.4 Optional permissions hardening

- [ ] Consider restrictive directory/file permissions on supported POSIX systems
- [ ] Do not add fragile cross-platform permission code without tests

---

# Phase 16 — Preserve and re-test non-fatal preview behavior (P0 regression gate)

This fallback is intentionally allowed.

- [ ] Missing `kicad-cli` for preview only is surfaced as warning when core generation does not require it.
- [ ] Missing `rsvg-convert` is surfaced as warning.
- [ ] Preview conversion failure is surfaced as warning.
- [ ] Project ZIP still exists when core generation succeeds.
- [ ] `schematic_preview.png` is absent when preview failed.
- [ ] `PREVIEW_GENERATION_SKIPPED` remains in structured warnings.
- [ ] Non-preview generation failures still fail the job.

Do not “fix” this by making every preview failure fatal.

---

# Phase 17 — Backend/web regression suite expansion (P0)

Add focused tests rather than relying solely on broad happy-path suites.

## 17.1 Wizard failure-state test module(s)

Cover:

- [ ] stale IR after failed regeneration
- [ ] failed spec revision checkpoint preservation
- [ ] exact transition matrix
- [ ] failed-state recovery
- [ ] provenance mismatch
- [ ] generation-ready gate

## 17.2 Job/API failure contract tests

Cover:

- [ ] non-2xx failed direct generation
- [ ] failed job persisted
- [ ] job ID returned safely
- [ ] correlation ID propagation
- [ ] no path leakage

## 17.3 Settings/startup tests

Cover:

- [ ] explicit missing config
- [ ] optional default absent
- [ ] startup validation
- [ ] immutable process snapshot

## 17.4 Doctor tests

Replace shallow “check exists” coverage with capability assertions.

- [ ] preview dependency matrix
- [ ] network probe removed or actually exercised

## 17.5 Error sanitization tests

- [ ] validation input sentinel omitted
- [ ] filesystem paths remain redacted
- [ ] useful public error structure preserved

---

# Phase 18 — Frontend tests and browser smoke coverage (P0)

## 18.1 Unit tests

- [ ] exact generation gating
- [ ] canonical route after failed IR regeneration
- [ ] canonical route after failed spec revision
- [ ] retry controls
- [ ] direct generation non-2xx behavior
- [ ] persisted error rendering

## 18.2 Playwright/browser tests

Add or extend smoke tests for:

### Direct JSON

- [ ] valid IR validates and generates
- [ ] failed generation remains visibly failed
- [ ] failed generation does not navigate as successful

### Wizard

- [ ] happy path still completes
- [ ] invalid IR repair path still works
- [ ] forced provider failure after previously valid IR does not enable Generate
- [ ] retry after failure can recover

- [ ] Capture screenshots/traces on browser-test failure using existing CI conventions.

---

# Phase 19 — Documentation updates (P1)

Update active docs to match final behavior.

## 19.1 README

- [ ] startup-time config validation
- [ ] explicit missing-config failure
- [ ] immutable runtime settings snapshot
- [ ] wizard provider/model/prompt provenance rule
- [ ] direct failed-job HTTP semantics
- [ ] actual preview dependency requirement
- [ ] remove/update network probe setting
- [ ] debug artifact sensitivity

## 19.2 Wizard docs

Update as needed:

```text
docs/LLM_WIZARD_DESIGN.md
docs/LLM_WIZARD_OPERATOR_GUIDE.md
```

- [ ] transition/readiness semantics
- [ ] failure/retry behavior
- [ ] no stale IR generation
- [ ] provenance mismatch behavior

## 19.3 Job execution docs

Update `docs/JOB_EXECUTION_MODEL.md` as needed:

- [ ] failed synchronous generation persists job but returns non-2xx
- [ ] correlation/job identifiers available for diagnostics

## 19.4 Keep future schematic redesign out

- [ ] Do not add placement/wire-routing redesign requirements to these hardening docs.

---

# Phase 20 — Full automated validation (P0)

Run the repository's complete permanent validation set on the final implementation SHA.

## 20.1 Python quality gates

```bash
uv run --extra dev --extra web ruff check src tests
uv run --extra dev --extra web ruff format --check src tests
uv run --extra dev --extra web mypy src
```

- [ ] Ruff lint PASS
- [ ] Ruff format PASS
- [ ] mypy PASS

## 20.2 Python tests

Run the same effective suite/coverage invocation used by CI.

- [ ] unit tests PASS
- [ ] web tests PASS
- [ ] coverage threshold PASS
- [ ] new failure-state regressions PASS

## 20.3 Frontend

```bash
cd frontend
npm ci
npm run lint
npm run test:run
npm run build
```

- [ ] frontend lint PASS
- [ ] frontend unit tests PASS
- [ ] production build PASS
- [ ] committed SPA bundle verification PASS using repository CI procedure

## 20.4 KiCad integration

- [ ] KiCad 9 integration suite PASS
- [ ] no placement/routing output changes are accepted merely as incidental hardening churn
- [ ] if generated fixture output changes unexpectedly, investigate rather than update golden files blindly

## 20.5 Packaging

- [ ] wheel/sdist build PASS
- [ ] installed-wheel smoke test PASS

## 20.6 Browser

- [ ] Playwright smoke tests PASS

---

# Phase 21 — Manual smoke validation (P1)

Record one of `PASS`, `FAIL`, or `NOT PERFORMED` for each section. Never imply an unperformed manual test passed.

## 21.1 Direct JSON flow

Status: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`

If performed:

- [ ] open direct JSON page
- [ ] validate valid Circuit IR
- [ ] generate successful project
- [ ] force/observe failed generation path if practical
- [ ] failure remains visible and does not look successful
- [ ] successful artifacts download correctly

## 21.2 Wizard happy path

Status: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`

If performed:

- [ ] create session
- [ ] review/approve spec
- [ ] generate IR
- [ ] generate project
- [ ] review artifacts

## 21.3 Wizard failure/retry path

Status: `[ ] PASS  [ ] FAIL  [ ] NOT PERFORMED`

If performed with a controllable/mock/local provider:

- [ ] establish valid IR
- [ ] force replacement IR failure
- [ ] verify Generate disabled/rejected
- [ ] retry
- [ ] recover to generation-ready state

---

# Phase 22 — Silent-failure and fallback audit (P0 final review)

Before declaring completion, search the touched code for newly introduced or retained patterns such as:

```text
except Exception
except:
pass
return []
return None
or default
.catch(() => ...)
catch {}
ignore_errors=True
assert <user-reachable condition>
```

For every occurrence in touched paths:

- [ ] classify it as expected/justified or defect
- [ ] ensure unexpected failures are logged or raised
- [ ] ensure fallbacks cannot convert invalid/corrupt state into apparent success
- [ ] ensure optional degradation is visible to users/operators
- [ ] document intentional fallback behavior in code/tests where non-obvious

Special checks:

- [ ] no “use old IR if new IR fails” behavior
- [ ] no absolute-path containment fallback
- [ ] no explicit-config missing-file fallback
- [ ] no fake network-probe success
- [ ] no HTTP 2xx for failed synchronous generation
- [ ] no public raw Pydantic `input` echo

---

# Phase 23 — Final exact-SHA CI evidence (P0)

- [ ] Commit all implementation, tests, frontend bundle, and docs.
- [ ] Record the exact final SHA.
- [ ] Push the exact final SHA to `webapp`.
- [ ] Wait only for the permanent CI attached to that exact SHA; do not infer green from an earlier commit.
- [ ] Record CI run URL/ID.
- [ ] Confirm every required job is successful:
  - [ ] Python lint/types/unit/web tests
  - [ ] frontend lint/unit/build
  - [ ] KiCad integration tests
  - [ ] package build/install
  - [ ] Playwright browser smoke tests
- [ ] If final CI fails, fix the failure and repeat exact-SHA validation.
- [ ] Do not mark this TODO complete while final CI is pending or failed.

---

# Phase 24 — Completion evidence (P0)

Create/update a completion/evidence document if that is the repository convention for this batch.

Record:

- [ ] starting SHA
- [ ] final SHA
- [ ] concise summary of each fixed review finding
- [ ] tests added
- [ ] intentional preview fallback justification
- [ ] manual smoke status
- [ ] CI run ID/URL
- [ ] confirmation that schematic placement/wire routing were not redesigned

---

# Definition of Done

Do not mark this TODO complete until all P0 items are satisfied and all P1 items are either completed or explicitly dispositioned with a documented reason.

Final required outcomes:

- [ ] stale IR cannot be generated after failed regeneration
- [ ] backend uses explicit wizard transition/readiness rules
- [ ] frontend mirrors those rules
- [ ] last-known-good checkpoints are not destructively discarded before replacement succeeds
- [ ] failed replacement state cannot masquerade as current success
- [ ] failed synchronous direct generation returns non-2xx and preserves failed job diagnostics
- [ ] explicit missing config fails
- [ ] runtime configuration validates at startup and is stable for the process lifetime
- [ ] wizard LLM provider/model/prompt provenance cannot silently drift
- [ ] job containment failure never exposes absolute path fallback
- [ ] doctor preview capability is truthful
- [ ] no fake/no-op network probe remains
- [ ] unexpected direct-job failures carry correlation IDs
- [ ] request validation does not echo arbitrary rejected input values
- [ ] LLM retries use bounded defensible backoff/retry semantics
- [ ] debug artifact sensitivity is explicit and capture remains off by default
- [ ] intentional preview degradation remains non-fatal and visible
- [ ] new regression tests cover all reviewed failure sequences
- [ ] full final CI passes on the exact final SHA
- [ ] no schematic component-placement or wire-routing redesign is included
