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
- lower-layer refinement `UserError` responses preserve the machine code but not exception text/details;
- CLI adapter accepts only `--session-id` and cannot override paths/providers/credentials/limits;
- CLI service errors likewise omit internal `UserError.details`;
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
- job generation request IR exactly equals current wizard IR;
- canonical job `input/circuit_ir.json` exactly equals current wizard IR and validates as `CircuitIR`;
- persisted job `schematic_path` is relative, resolves beneath the canonical job workspace, has `.kicad_sch` suffix, and exists.

`JobRecord` canonicalization derives workspace paths from the trusted `job.json` location rather than accepting persisted absolute path authority. A stale wizard IR/current-job mismatch or escaped schematic reference fails before refinement/model dispatch.

Work/evidence roots are server-derived beneath:

```text
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/work
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/evidence
```

The job-specific evidence root preserves one-shot session reservation within a particular generated project without allowing an older generated job to become the target of a later wizard revision.

The wizard cross-process mutation lock remains held across target resolution, refinement, terminal evidence publication, and derived-artifact refresh.

### Vision/provenance gate — implemented

Production HTTP refinement requires an enabled configured LLM provider, explicit model, request-scoped client, and `vision_enabled=true`. Missing image capability fails with `VISION_CAPABILITY_UNAVAILABLE`; provider/model names do not imply vision and no text-only fallback is used.

Runtime provider/model provenance comes directly from validated server settings. `KicadCliAdapter()` is server-created; the request cannot select a KiCad executable.

### Derived project-artifact synchronization — implemented

The generated project's `.kicad_sch` is canonical. After successful refinement execution, production composition refreshes the derived `schematic_preview.png`, `project.zip`, and sanitized job-result refinement metadata.

The refreshed preview is generated in private same-filesystem scratch and atomically replaces the public preview only after successful rendering. An explicitly optional preview-generation failure removes the old preview and is warning-visible. Unexpected preview failures, archive-refresh failures, or job-metadata refresh failures raise `REFINEMENT_DERIVED_STATE_REFRESH_FAILED` with `authoritative_committed=true`, making it explicit that canonical mutation/evidence may already be durable and must not be replayed automatically.

A failed ZIP refresh removes stale `project.zip`. Project archive publication is atomic and private: the new ZIP is written and fsynced beneath `artifacts/.staging/`, which the flat artifact API neither lists nor addresses, and only then replaces `project.zip`. Archive generation rejects symbolic links and resolved entries outside the project root, preventing archive refresh from reading arbitrary external files through a tampered project tree.

### Production HTTP regression coverage

Added/updated tests cover:

- disabled router has no refinement routes;
- enabled router uses request-scoped server dependencies and the threadpool boundary;
- request-side path/bound override attempts fail before service dispatch;
- malformed bodies are sanitized;
- lower-layer path/secret-bearing errors are not reflected;
- wizard/current-job/IR/schematic binding succeeds only for current trusted state;
- stale wizard IR cannot dispatch or mutate;
- traversal-style persisted schematic references are rejected;
- missing vision capability refuses before dispatch;
- successful composition supplies authoritative IR and configured provenance and refreshes derived artifacts/job metadata;
- archive refresh failure removes stale ZIP and reports committed-state truthfully;
- atomic project ZIP failure preserves the previous complete archive and successful publication replaces it;
- project ZIP staging files are not exposed by the artifact listing API;
- symlink-based archive escape attempts fail closed without replacing the prior complete archive.

### CLI production composition — still open

The CLI parser/service boundary is hardened, but the CLI is not yet production-composed through the wizard/current-job resolver. That remains the next integration task. It must reuse the same trusted ownership rules rather than adding an arbitrary-path command.

## Validation status

The production mounting changes and focused regression tests are committed on `webapp`. Local checkout/verification from this ChatGPT sandbox remains unavailable because the environment cannot resolve `github.com`, and Ruff is not installed/cached locally. This note therefore does **not** claim a green Ruff/mypy/pytest or GitHub Actions result. CI monitoring remains outside this implementation loop unless explicitly requested.

## Next implementation actions

1. Reconcile any concrete Ruff/format/type/test failures reported for the mounted HTTP production path without weakening its fail-closed contracts.
2. Compose the CLI through the same wizard/current-job ownership resolver; do not create a standalone arbitrary-path mutation command.
3. Run the final HTTP/CLI adversarial production-boundary pass: no arbitrary filesystem access, provider/model/credential/bound selection, reservation bypass, stale-project mutation, or secret reflection.
4. Reconcile the main refinement TODO/status checkboxes against actual implementation evidence.
5. Complete the final full validation/release closure after the exact accepting SHA is green.
