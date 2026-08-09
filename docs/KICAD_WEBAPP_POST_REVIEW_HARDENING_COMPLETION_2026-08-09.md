# KiCad Web App Post-Review Hardening Completion Evidence — 2026-08-09

## Purpose

This document records completion evidence for:

- `docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_SPEC_2026-08-09.md`
- `docs/KICAD_WEBAPP_POST_REVIEW_HARDENING_TODO_2026-08-09.md`

The hardening pass intentionally did **not** redesign schematic component placement, orientation, wire routing, routing heuristics, or schematic-layout algorithms.

## Source and acceptance revisions

- Reviewed product baseline: `5e2e362880c1fe38b81a71e71a7cb924b893fd0d`
- Ralph-loop implementation start after CI-bridge setup: `ff980449541ca76c336b6474cd3283fce24631cd`
- Exact implementation acceptance SHA: `bb77b4985c124dcf947b5f2da4ab3b4b486b2798`
- Implementation acceptance CI run: `31341391124`
- Implementation acceptance CI URL: `https://github.com/ekkus93/openclaw_kicad_pcb/actions/runs/31341391124`

The commit containing this evidence document is a documentation-only successor to the implementation acceptance SHA. Permanent CI attached to that exact documentation head must also pass before the Ralph loop is declared complete; the final conversation/report and CI status bridge identify that final exact SHA/run without requiring another self-referential documentation edit.

## Implementation acceptance result

Permanent CI for `bb77b4985c124dcf947b5f2da4ab3b4b486b2798` completed successfully across all required jobs:

| Job | Job ID | Result |
| --- | ---: | --- |
| Python lint, types, unit and web tests | `93315656758` | PASS |
| Frontend lint, unit tests and production build | `93315656755` | PASS |
| Build and install wheel/sdist | `93317250675` | PASS |
| Browser smoke tests | `93317250722` | PASS |
| KiCad integration tests | `93317250666` | PASS |

### Python quality and regression evidence

The permanent Python job reported:

- Ruff lint: PASS (`All checks passed!`)
- Ruff format: PASS (`460 files already formatted`)
- mypy: PASS (`Success: no issues found in 213 source files`)
- workflow configuration guard: PASS
- generated-tree guard: PASS
- pytest collection: `2728` tests
- pytest result: `2721 passed, 7 skipped`
- total coverage: `90.84%`
- required coverage threshold: `70.0%`, satisfied

The skips are explicit environment/optional-feature skips; they are not treated as manual smoke evidence.

### Browser evidence

The permanent Playwright job reported:

- `11 passed`
- `1 skipped`
- direct JSON failure remains visible and does not navigate as success: PASS
- failed wizard IR regeneration blocks stale generation and successful retry recovers to the Generate step: PASS
- normal direct JSON validation/generation smoke coverage: PASS

The skipped browser case is the live-LLM wizard smoke test, which remains opt-in/environment-gated. It is not represented as having run.

### Packaging and KiCad integration

- wheel/sdist build: PASS
- installed-wheel smoke test: PASS
- KiCad 9 integration job: PASS
- no placement/routing golden-output churn was accepted as incidental hardening work

## Review findings closed

### Wizard state and checkpoint correctness

- Backend transition/readiness rules are authoritative rather than relying on the presence of stale fields.
- Failed IR regeneration cannot make preserved prior IR actionable.
- Failed spec revision preserves the last-known-good checkpoint for inspection/recovery but cannot masquerade as current approved state.
- Replacement state is committed only when the replacement operation succeeds.
- Backend spec approval rejects unresolved review state rather than relying on frontend-only disabling.
- Project generation requires an explicitly generation-ready, approved, valid current session state.
- Frontend gating and canonical-step logic mirror the backend rules.
- A successful IR retry advances to the canonical Generate step; a failed retry does not unlock generation.

### LLM provenance and process configuration

- LLM-backed wizard revisions persist provider/model/prompt/config provenance.
- LLM-backed continuation fails closed when revision provenance is missing or no longer matches current provider/model/prompt/config identity.
- Read-only access to persisted sessions remains available across configuration drift.
- Runtime settings are loaded and validated as a stable process snapshot rather than silently reloaded per request.
- An explicitly configured missing/unreadable/invalid config file fails startup.
- Unknown/stale configuration keys fail explicitly instead of being accepted as no-ops.
- Fake `network_probe_enabled` behavior was removed rather than advertised as implemented.
- Inert web host/port settings were removed; Uvicorn launch arguments remain authoritative.

### Direct synchronous job correctness

- Failed synchronous `/api/jobs/from-netlist` generation persists the failed job for diagnostics but returns non-2xx HTTP semantics.
- The frontend does not navigate to a job as if generation succeeded when the request failed.
- Unexpected failures allocate one correlation/error ID and carry it through logs, persisted job state, and the public structured error.
- Public errors omit traceback/private-path/raw unexpected exception details.
- Process-restart reconciliation converts stranded nonterminal synchronous jobs to an explicit interrupted terminal failure rather than leaving them indefinitely `running`.

### Filesystem and public-error boundaries

- Job-relative path derivation no longer falls back to an absolute path when containment fails.
- Persisted absolute workspace paths are not trusted as authority for artifact access.
- Public request-validation errors omit arbitrary rejected Pydantic `input` values and unsafe context.
- Path redaction covers supported Unix, Windows-drive, and UNC-like forms.

### Preview capability and degraded mode

- Doctor preview readiness matches the actual dependency contract: `kicad-cli` **and** `rsvg-convert`.
- ImageMagick `convert` is not counted as a capability the runtime does not use.
- Missing optional preview tooling may leave core health usable while the preview capability is explicitly unavailable.
- The allowed success-preserving fallback is narrowed to genuine preview-specific export/conversion/tooling failure.
- A missing core generated schematic is fatal and cannot be reclassified as preview-only degradation.
- Allowed preview degradation remains visible through structured `PREVIEW_GENERATION_SKIPPED` warnings.
- Public preview warnings do not expose workspace paths.

### Retry and debug-artifact hardening

- Retryable HTTP responses use bounded attempts with exponential backoff/jitter and valid `Retry-After` handling.
- Ambiguous POST transport failures are not blindly replayed, avoiding silent duplicate/billable inference risk.
- Retry behavior is represented by validated, effective settings rather than hidden/no-op knobs.
- Raw wizard debug capture remains opt-in and disabled by default.
- Debug capture is documented as sensitive local data that may include raw prompts/completions and is not sanitized by request-log redaction.
- Debug storage uses a restrictive local directory boundary where supported.

### Wizard persistence semantics

- `wizard.json` remains the authoritative canonical state.
- Derived sidecar failure is explicit about canonical state already having committed; callers are not told a false rollback occurred.
- The unused unlocked compatibility mutation path was removed rather than retained as a lock bypass.

## Regression coverage added or strengthened

Focused hardening coverage includes:

- stale IR after failed regeneration
- failed spec-revision checkpoint preservation
- exact wizard action/transition gates
- failed-state retry behavior
- backend approval gates
- LLM provenance mismatch/missing-provenance behavior
- non-2xx direct synchronous generation failure with persisted job diagnostics
- shared correlation-ID propagation
- interrupted-job restart reconciliation
- explicit config-file startup failures
- immutable process settings
- unknown/no-op config rejection
- containment and persisted-path trust
- request-validation input sanitization
- doctor preview dependency matrix
- retry/backoff and ambiguous transport behavior
- preview-specific nonfatal behavior versus fatal primary-generation failure
- wizard authoritative-state/sidecar failure semantics
- frontend generation gating and retry state
- Playwright direct-generation failure behavior
- Playwright wizard IR failure/recovery behavior

## Silent-failure and fallback audit

Touched hardening paths were reviewed for broad catches, empty catches, default-return patterns, and fallback behavior.

### Removed or prohibited

- **No stale-IR fallback:** failure of a replacement IR does not permit old IR to act as current generation input.
- **No absolute-path containment fallback:** containment violations are failures, not path disclosure.
- **No explicit-config fallback:** an explicitly requested bad config does not silently behave as an absent default config.
- **No fake network-probe success/no-op:** the unimplemented setting/check was removed.
- **No HTTP 2xx for failed synchronous generation.**
- **No raw Pydantic rejected-input echo** in public validation responses.
- **No ambiguous transport replay fallback** that can silently duplicate an LLM POST.
- **No missing-primary-schematic preview fallback.**

### Intentional and justified

The only success-preserving generation degradation retained by this hardening pass is preview-specific failure after the primary deterministic project is otherwise valid. It is justified because preview is an optional derived artifact, and it is not silent:

- the project/job can succeed,
- `schematic_preview.png` may be absent,
- structured warning code `PREVIEW_GENERATION_SKIPPED` is retained,
- doctor reports preview capability truthfully,
- non-preview generation failures remain fatal.

Broad exception handling that remains in job/wizard operation boundaries terminalizes or persists failure and exposes a safe error/correlation record; it does not convert unexpected failure into apparent success.

Frontend mutation catches that suppress rethrow into the event handler are paired with React Query mutation error state plus authoritative session refetch/rendering; they do not create a success state or silently navigate after failure.

## Manual smoke disposition

Automated tests are **not** substituted for manual validation.

| Manual smoke item | Status | Disposition |
| --- | --- | --- |
| Direct JSON flow | **NOT PERFORMED** | Permanent API/frontend/Playwright coverage passed, but no separate human-driven browser smoke was executed. |
| Wizard happy path | **NOT PERFORMED** | The live-LLM Playwright wizard test is environment-gated/skipped; no live provider manual session was executed. |
| Wizard failure/retry path | **NOT PERFORMED** | Provider-independent Playwright failure/retry coverage passed, but no separate human-driven manual scenario was executed. |

These P1 manual checks are explicitly dispositioned rather than represented as PASS.

## Scope confirmation

The hardening diff was reviewed against the baseline. Changes are confined to web-app state/provenance, job/error behavior, settings/startup, LLM transport/logging, filesystem containment, doctor/preview behavior, frontend state handling, tests, documentation, and generated SPA assets.

No schematic component-placement algorithm, orientation algorithm, wire-routing algorithm, routing heuristic, or schematic-layout redesign was included.

## Closure rule

Implementation acceptance is green at `bb77b4985c124dcf947b5f2da4ab3b4b486b2798` / run `31341391124`.

The Ralph loop is complete only after permanent CI for the commit containing this evidence document also finishes successfully across all five required jobs. If that documentation-head CI fails, the failure must be fixed and exact-SHA validation repeated before closure is claimed.
