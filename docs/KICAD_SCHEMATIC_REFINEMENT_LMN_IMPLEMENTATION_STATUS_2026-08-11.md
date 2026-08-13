# KiCad schematic refinement L/M/N implementation status

Date: 2026-08-11

This note records the implementation state of the bounded iterative schematic-refinement work. It is intentionally conservative: an item is described as complete only when the production path actually uses it.

## Phase L — bounded iterative refine

Implemented in the service layer:

- conservative loop defaults and strict integer bounds;
- per-round operation cap and total accepted-operation cap;
- candidate-rejection cap;
- refinement-layer logical model-call budget, separate from provider HTTP retry semantics;
- deterministic best-known accepted-state retention using non-regressing Pareto replacement rather than a weighted aggregate;
- distinct raw schematic hash and presentation/layout fingerprint;
- layout fingerprint canonicalization independent of file formatting and reversed wire point direction;
- exact/repeated layout-state oscillation detection for accepted and rejected candidates;
- bounded structured prior-decision context for critic/planner anti-oscillation guidance;
- explicit stop codes for max rounds, no actionable issues, no operations, no meaningful deterministic improvement, rejection limit, oscillation, and operation budget;
- hard failures remain exceptions and do not silently degrade to a successful stop;
- explicit user cancellation is not implemented and is not claimed.

The accepted schematic remains the best-known accepted state. A later mixed-tradeoff or equal-quality candidate cannot silently displace it.

## Phase M — evidence and observability

Implemented:

- atomic per-iteration evidence bundles for accepted, rejected, and no-op rounds;
- atomic session bundle with `manifest.json` and human-readable `summary.md`;
- explicit provider/model provenance rather than client introspection;
- prompt/schema/service/layout-fingerprint/evidence version identifiers;
- configured bounds and logical model-call accounting in session evidence;
- candidate hash and candidate layout fingerprint in iteration evidence;
- cryptographic session references to iteration manifests;
- semantic cross-check of referenced iteration ID, reason code, accepted hashes, candidate hash, and candidate layout fingerprint;
- sanitized summary of starting metrics, issue categories/counts, operation counts, electrical status, and numeric metric deltas;
- no raw prompt or provider-payload retention;
- candidate files and post-edit render scratch are transaction-scoped and not durably accumulated;
- durable iteration evidence is bounded by the configured `max_rounds` for one session;
- hard-failure session finalization attempts to preserve the original machine-readable failure code;
- a secondary evidence-finalization failure does not replace the original runtime/verification failure.

### Session-ID reservation hardening

`src/kicad_pcb/refinement/session_reservation.py` implements an atomic ownership-token reservation for a session evidence namespace. It rejects:

- an already-reserved session ID;
- an existing completed or failed session bundle;
- an orphan/pre-existing iteration bundle for the session ID across the full supported round namespace, independent of a later request's smaller `max_rounds` value;
- unsafe session IDs or invalid round bounds;
- release of a missing, foreign, or tampered reservation.

A stale reservation is intentionally fail-closed: it blocks reuse rather than allowing a new session to overwrite or ambiguously extend old evidence. Reservation acquisition uses exclusive creation, so concurrent same-session contenders cannot both become owners.

The reservation is acquired by `refine_schematic()` before `_run_refinement_loop()`. Therefore a conflicting finalized, active, stale, or orphan-evidence session is rejected before any refinement model dispatch or candidate mutation. The service keeps the reservation through terminal evidence publication. It releases the reservation only after durable completed or failed session evidence has been published. If terminal evidence publication itself fails, the reservation is deliberately retained to block ambiguous replay.

Regression coverage proves completed-session reuse, active conflicts, stale reservations, failed-session finalization, terminal-evidence failure retention, concurrent single ownership, and orphan iteration evidence across the full supported round namespace.

## Phase N — feature/config/API/CLI integration

Implemented:

- dedicated `RefinementFeatureConfig`, disabled by default;
- strict canonical boolean/integer environment parsing;
- unknown `KICAD_WEBAPP_REFINEMENT_*` variables are rejected instead of silently falling back;
- configured loop limits reuse the same `RefinementLoopLimits` validation as the service;
- external request accepts only a safe session ID;
- request cannot override accepted path, wizard/job workspace, work/evidence paths, provider/model, API key, KiCad executable, operation policy, or resource bounds;
- external response omits absolute evidence paths;
- explicit runtime builder requires provider/model provenance;
- route-side malformed/invalid request handling is generic and does not reflect rejected paths/credentials;
- controlled refinement `UserError` and trusted-resolver `WebServiceError` responses preserve the machine/status code but not exception text/details;
- production CLI accepts only `--session-id` and cannot override paths/providers/credentials/limits;
- production CLI dispatches through the same trusted wizard/current-job composition path as HTTP;
- CLI controlled, unexpected configuration, and unexpected service failures return fixed generic messages without reflecting exception text/details;
- CLI client-cleanup failure is warning-visible without replacing the command result or logging exception text;
- runtime configuration and retention semantics are documented in `docs/KICAD_SCHEMATIC_REFINEMENT_RUNTIME_CONFIGURATION_2026-08-11.md`.

### Production HTTP mounting — implemented

The refinement route is now installed from the real FastAPI composition point in `src/kicad_pcb_web/main.py` through `install_refinement_routes(app, config=load_refinement_feature_config())`.

The feature remains default-off. With the default configuration the installed router is empty and `/api/refinement/run` is absent. Invalid or unknown refinement environment configuration fails application composition instead of silently selecting defaults.

When enabled, production HTTP composition is:

`/api/refinement/run` → `run_wizard_refinement_request()` → `run_configured_refinement_request()` → `run_configured_refinement()` → `refine_schematic()`

No second layout-mutation implementation was added.

The route receives validated `WebSettings` and the request-scoped configured `LlmClient` through normal FastAPI dependencies. The synchronous LLM/KiCad/refinement operation is dispatched through Starlette's threadpool so it does not run directly on the FastAPI event loop.

### Trusted wizard/current-job target binding — implemented

`src/kicad_pcb_web/services/wizard_refinement.py` resolves the production mutation target from existing authoritative file-backed state. The submitted `session_id` identifies the wizard session, not a request-selected filesystem location.

Before dispatch, the composition layer requires:

- wizard exists and has `status="completed"`;
- current `ir_json` exists and has valid `ir_validation`;
- current `latest_job_id` exists;
- referenced job is successful and has result metadata;
- referenced job carries a private wizard-session owner marker that exactly equals the requesting wizard session;
- job generation request IR exactly equals current wizard IR;
- canonical job `input/circuit_ir.json` exactly equals current wizard IR and validates as `CircuitIR`;
- persisted job `schematic_path` is relative, resolves beneath the canonical job workspace, has `.kicad_sch` suffix, and exists.

`JobRecord` canonicalization derives workspace paths from the trusted `job.json` location rather than accepting persisted absolute path authority. The private wizard-owner marker is retained in canonical job state but removed from public `JobDetail.request` serialization. A stale wizard IR/current-job mismatch, cross-session/unowned job reference, or escaped schematic reference fails before refinement/model dispatch. Jobs created before the owner marker existed are intentionally not grandfathered into refinement; regenerating the current wizard project creates the required binding.

Shared wizard-session persistence also rejects parent-like IDs, a symlinked `wizard_sessions` root, per-session symlink aliases, and a persisted `wizard.json` whose ID does not equal the requested session ID.

Work/evidence roots are server-derived beneath:

```text
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/work
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/evidence
```

The job-specific evidence root preserves one-shot session reservation within a particular generated project without allowing an older generated job to become the target of a later wizard revision.

The wizard cross-process mutation lock remains held across target resolution, refinement, terminal evidence publication, and derived-artifact refresh.

### Vision/provenance gate — implemented

Production HTTP and CLI refinement require an enabled configured LLM provider, explicit model, configured client, and `vision_enabled=true`. Missing image capability fails with `VISION_CAPABILITY_UNAVAILABLE`; provider/model names do not imply vision and no text-only fallback is used.

Runtime provider/model provenance comes directly from validated process settings. `KicadCliAdapter()` is process-created; neither HTTP nor CLI can select a KiCad executable.

### Derived project-artifact synchronization — implemented

The generated project's `.kicad_sch` is canonical. After successful refinement execution, production composition refreshes the derived `schematic_preview.png`, `project.zip`, and sanitized job-result refinement metadata.

The refreshed preview is generated in private same-filesystem scratch and atomically replaces the public preview only after successful rendering. An explicitly optional preview-generation failure removes the old preview and is warning-visible. Unexpected preview failures, archive-refresh failures, or job-metadata refresh failures raise `REFINEMENT_DERIVED_STATE_REFRESH_FAILED` with `authoritative_committed=true`, making it explicit that canonical mutation/evidence may already be durable and must not be replayed automatically.

A failed ZIP refresh removes stale `project.zip`. Project archive publication is atomic and private: the new ZIP is written and fsynced beneath `artifacts/.staging/`, which the flat artifact API neither lists nor addresses, and only then replaces `project.zip`. Archive generation rejects symbolic links and resolved entries outside the project root, preventing archive refresh from reading arbitrary external files through a tampered project tree.

### Production HTTP regression coverage

Added/updated tests cover:

- disabled router has no refinement routes;
- enabled router uses request-scoped server dependencies and the threadpool boundary;
- request-side path/job/provider/model/credential/KiCad/bound/operation-policy/reservation override attempts fail before service dispatch;
- malformed bodies are sanitized;
- controlled `UserError` and trusted-resolver `WebServiceError` path/secret/session canaries are not reflected;
- wizard/current-job/IR/schematic binding succeeds only for current trusted state;
- cross-session and legacy unowned job references fail closed even when Circuit IR is identical;
- stale wizard IR cannot dispatch or mutate;
- parent-like/symlink-alias wizard-session paths and persisted-ID mismatch are rejected;
- traversal-style persisted schematic references are rejected;
- missing vision capability refuses before dispatch;
- successful composition supplies authoritative IR and configured provenance and refreshes derived artifacts/job metadata;
- archive refresh failure removes stale ZIP and reports committed-state truthfully;
- atomic project ZIP failure preserves the previous complete archive and successful publication replaces it;
- project ZIP staging files are not exposed by the artifact listing API;
- symlink-based archive escape attempts fail closed without replacing the prior complete archive.

### CLI production composition — implemented

`src/kicad_pcb_web/refinement_cli.py` composes the CLI through `run_wizard_refinement_request()` rather than accepting a pre-resolved schematic path/runtime. `RefinementCliContext` contains only process-owned `WebSettings`, configured `LlmClient`, refinement feature configuration, and output streams.

The installed package exposes:

```text
kicad-refine --session-id <wizard-session-id>
```

`main()` loads validated web settings and refinement feature configuration, builds the configured LLM client, executes the request through the trusted wizard/current-job resolver, and closes the client afterward. The CLI therefore inherits the same current-wizard/current-job/IR/path containment checks, mutation lock, vision/provenance gate, evidence namespace, and derived-artifact refresh behavior as HTTP.

No CLI flag can select an accepted schematic path, job ID, work/evidence path, provider, model, API key, KiCad executable, operation policy, alternate refinement reservation ID, or loop/resource bound. Invalid arguments fail before dispatch. Controlled `UserError` and `WebServiceError` failures return only a machine-readable code plus a generic message. Unexpected configuration/request failures return `REFINEMENT_CLI_INTERNAL_ERROR` with fixed text while logging only exception type. Client cleanup failures are warning-visible without replacing the command result or logging provider exception text.

Focused tests cover trusted-composition dispatch, rejection of path/job/provider/model/credential/KiCad/bound/policy/reservation flags, unsafe session IDs, process-owned configuration/client composition, client cleanup, generic controlled/configuration/unexpected-error output, and sanitization of both core `UserError` and wizard-resolver `WebServiceError` failures. Package smoke verifies that the wheel emits the `kicad-refine` console entry point and that the installed CLI module is importable.

Implementation commits:

- `17d72f9a62b21696ea5945e1728607065f387efc` — trusted CLI production composition and focused tests;
- `f0636cb6d700487788658d5d87fad3f6ad6796dd` — packaged console-entry-point smoke assertion.

### Final HTTP/CLI adversarial production-boundary audit — completed

The final adversarial production-boundary pass is recorded in:

`docs/KICAD_SCHEMATIC_REFINEMENT_HTTP_CLI_ADVERSARIAL_PRODUCTION_BOUNDARY_AUDIT_2026-08-13.md`

The audit explicitly covers arbitrary filesystem selection, provider/model/credential/KiCad/bound/policy selection, reservation bypass, stale/foreign project mutation, wizard path aliases, persisted identity mismatch, controlled and unexpected error reflection, and the absence of an alternate public low-level mutation route.

## Validation status

Local validation for the adversarial pass used the available sandbox Python environment. The expanded boundary/configuration/session/job matrix passed (`76 passed, 2 skipped`), shared wizard-generation/job regressions passed (`41 passed, 3 skipped`), and the available web regression sweep passed (`327 passed, 6 skipped`) while the current CLI sanitization slice also passed. `python3 -m compileall -q src/kicad_pcb_web` and changed-file syntax/whitespace checks passed.

Ruff and mypy were not installed/cached in the sandbox, so this status does **not** claim those repository-wide gates locally. Permanent CI monitoring remains outside this implementation loop unless explicitly requested.

## Next implementation actions

1. Reconcile the main refinement TODO/status checkboxes against actual implementation evidence.
2. Continue the remaining experimental evaluation corpus work.
3. Run the broader Phase O unsafe-fallback/silent-failure audit across the full refinement implementation, not just the HTTP/CLI production boundary.
4. Complete remaining operator documentation, full static/test/KiCad/package validation, and exact-SHA release closure.
