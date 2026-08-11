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

`src/kicad_pcb/refinement/session_reservation.py` now implements an atomic ownership-token reservation for a session evidence namespace. It rejects:

- an already-reserved session ID;
- an existing completed session bundle;
- an orphan/pre-existing iteration bundle for the requested session ID;
- unsafe session IDs or invalid round bounds;
- release of a missing, foreign, or tampered reservation.

A stale reservation is intentionally fail-closed: it blocks reuse rather than allowing a new session to overwrite or ambiguously extend old evidence.

**Current integration status:** the reservation primitive is implemented and tested, but it has not yet been wired into `refine_schematic()` before the first model call/mutation. Therefore this specific hardening item remains open until the service acquires the reservation before execution and releases it only after successful completed/failed session-evidence finalization.

## Phase N — feature/config/API/CLI integration

Implemented building blocks:

- dedicated `RefinementFeatureConfig`, disabled by default;
- strict environment parsing with no coercive fallback;
- unknown `KICAD_WEBAPP_REFINEMENT_*` variables fail instead of being silently ignored;
- configured loop limits reuse the same `RefinementLoopLimits` validation as the service;
- one configured facade delegates only to the canonical transactional `refine_schematic()` implementation;
- external request accepts only a safe session ID;
- request cannot override accepted path, work/evidence paths, provider/model, API key, operation policy, or resource bounds;
- external response omits absolute evidence paths;
- explicit server-side runtime builder requires provider/model provenance;
- default-off FastAPI router factory returns no refinement endpoint when disabled;
- FastAPI route obtains accepted path/runtime only from server-owned dependency providers;
- route-side request validation returns a generic sanitized 422 rather than FastAPI validation details that could reflect a submitted path or credential;
- route-side service errors omit internal `UserError.details`;
- CLI adapter accepts only `--session-id` and cannot override paths/providers/credentials/limits;
- CLI service errors likewise omit internal `UserError.details`;
- runtime configuration and retention semantics are documented in `docs/KICAD_SCHEMATIC_REFINEMENT_RUNTIME_CONFIGURATION_2026-08-11.md`.

### Production mounting status

The HTTP router/installer and CLI adapter are intentionally app-neutral. They are **not yet claimed as production-mounted entry points**. The remaining integration must identify the existing webapp's canonical project/session ownership boundary and inject:

1. the currently accepted schematic path;
2. the matching authoritative `CircuitIR`;
3. the existing KiCad adapter;
4. the existing configured LLM client;
5. explicit provider/model provenance from server configuration;
6. server-owned work/evidence directories.

The request must never select those values. The final mounting change must preserve a single mutation path through `run_configured_refinement_request()` → `run_configured_refinement()` → `refine_schematic()`.

Until that composition point is verified, leaving the router unmounted is safer than inventing a second project-selection or writable path.

## Validation status

Focused tests and static-analysis commands have been prepared/expanded for the refinement modules, but this note does not claim GitHub Actions status. CI monitoring remains outside this implementation loop unless explicitly requested.

## Next implementation actions

1. Wire `RefinementSessionReservation` into `refine_schematic()` before any model call or candidate mutation; release only after durable session finalization.
2. Add service-level tests proving reused/stale session IDs cannot dispatch a model call or mutate accepted bytes.
3. Inspect and use the existing webapp project/session composition to mount the default-off router without introducing request-selected paths.
4. Register the CLI adapter only through an existing project/runtime composition path; do not create a standalone arbitrary-path mutation command.
5. Continue the remaining validation/security/release phases and reconcile TODO checkboxes only after the implemented production path is verified.
