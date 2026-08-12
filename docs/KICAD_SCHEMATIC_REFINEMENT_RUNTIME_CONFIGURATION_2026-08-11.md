# KiCad schematic refinement runtime configuration

Date: 2026-08-11

This document describes the runtime/configuration contract for the bounded schematic visual-refinement service. It is intentionally narrower than the implementation specification: it records the production-facing feature gate, resource bounds, provenance requirements, evidence policy, project ownership boundary, and interface constraints.

## Feature gate

Iterative refinement is **disabled by default**.

The following environment variables are recognized:

| Variable | Default | Valid values / bounds |
| --- | ---: | --- |
| `KICAD_WEBAPP_REFINEMENT_ENABLED` | `false` | exactly `0`, `1`, `false`, or `true` |
| `KICAD_WEBAPP_REFINEMENT_MAX_ROUNDS` | `3` | integer `1..20` |
| `KICAD_WEBAPP_REFINEMENT_MAX_OPERATIONS_PER_ROUND` | `4` | integer `1..32` |
| `KICAD_WEBAPP_REFINEMENT_MAX_TOTAL_ACCEPTED_OPERATIONS` | `8` | integer `1..128` |
| `KICAD_WEBAPP_REFINEMENT_MAX_CANDIDATE_REJECTIONS` | `2` | integer `1..20` |
| `KICAD_WEBAPP_REFINEMENT_MAX_CRITIC_REPAIRS` | `0` | integer `0..8` |
| `KICAD_WEBAPP_REFINEMENT_MAX_PLANNER_REPAIRS` | `0` | integer `0..8` |

Integer values must use canonical base-10 text. Whitespace, a leading `+`, leading zeroes, floating-point text, and other coercive forms are rejected. Unknown environment variables beginning with `KICAD_WEBAPP_REFINEMENT_` are also rejected so a misspelled enable flag or bound cannot silently fall back to a default. Unrelated process environment variables are ignored by the refinement loader.

`src/kicad_pcb_web/main.py` loads this configuration when the application is composed. When disabled, the refinement router is empty and `/api/refinement/run` is not exposed. Invalid refinement configuration therefore fails application composition rather than silently producing an unintended runtime policy.

Enabling the feature flag does not create a new mutation implementation. The production path remains:

`HTTP route -> run_wizard_refinement_request() -> run_configured_refinement_request() -> run_configured_refinement() -> refine_schematic()`

## Logical model-call bound

The refinement-layer logical call limit is:

```text
max_rounds * (2 + max_critic_repairs + max_planner_repairs)
```

The two base calls are the critic and planner calls for a round. Structured-output repairs add at most the configured repair counts.

This is deliberately **not** described as a provider HTTP-attempt or wall-clock deadline. Provider transport retries occur inside an `LlmClient.complete()` call and retain their existing HTTPX/provider semantics.

## Runtime provenance

Iterative `refine_schematic()` requires explicit `RefinementProvenance` containing at least:

- provider identity;
- model identity.

Product version and implementation Git SHA may also be supplied when the composition layer knows them. Provider/model identity is never guessed by inspecting an `LlmClient` implementation or parsing a model-name heuristic.

`build_refinement_runtime()` receives the authoritative `CircuitIR`, KiCad adapter, LLM client, work/evidence directories, operation policy, and explicit provenance. Production HTTP composition supplies:

- the request-scoped configured `LlmClient` from `get_llm_client()`;
- provider/model identity from validated `WebSettings`;
- a server-created `KicadCliAdapter()` using the configured process environment rather than any request-selected executable;
- the authoritative `CircuitIR` reconstructed from trusted persisted wizard/job state;
- server-derived work/evidence directories.

API keys, authorization headers, provider payloads, and unrelated absolute paths are not provenance fields.

## Production wizard/project ownership boundary

The mounted HTTP path treats `RefinementRunRequest.session_id` as the **wizard session ID** whose current generated project is eligible for refinement. The request does not contain a job ID or path.

Before model dispatch or candidate mutation, `resolve_wizard_refinement_target()` requires all of the following:

1. the wizard session exists;
2. the wizard is in `completed` state;
3. the wizard has current valid `ir_json` and `ir_validation`;
4. the wizard has a `latest_job_id`;
5. that job exists and has `status="succeeded"` with result metadata;
6. the job's persisted generation request `netlist_json` exactly matches the wizard's current `ir_json`;
7. the job's canonical server-owned `input/circuit_ir.json` also exactly matches that wizard IR and validates as `CircuitIR`;
8. the job result contains a workspace-relative `.kicad_sch` reference;
9. resolving that reference remains inside the canonical job workspace and points to an existing regular schematic file.

Any mismatch fails closed before the refinement service is dispatched. In particular, regenerating or revising Circuit IR cannot silently reuse an older generated schematic merely because the old job still exists on disk.

`JobRecord` paths are canonicalized from the trusted location of `job.json`; persisted absolute path fields are not used as authority. The job's relative `schematic_path` is resolved under that canonical work directory and rechecked for containment.

Production refinement work/evidence directories are derived from trusted IDs:

```text
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/work
<data_dir>/wizard_sessions/<wizard-session-id>/refinement/<latest-job-id>/evidence
```

The job-specific namespace means a later successful regeneration can have a distinct refinement evidence root while preserving one-shot idempotency within the particular generated project.

The wizard cross-process mutation lock is held across target resolution, provider/KiCad refinement work, terminal evidence publication, and derived-artifact refresh. The synchronous refinement path is executed through Starlette's threadpool from the async route so long-running provider/KiCad work does not run directly on the FastAPI event loop.

## Vision capability boundary

The mounted production path requires:

- an enabled configured LLM provider;
- an explicit configured model;
- a request-scoped LLM client;
- `vision_enabled=true` in the validated LLM runtime configuration.

If any of these is absent, refinement fails with `VISION_CAPABILITY_UNAVAILABLE` before target resolution/model dispatch. Provider/model names are not used as vision heuristics and there is no text-only fallback that masquerades as visual critique.

## HTTP contract

`RefinementRunRequest` accepts only:

```json
{"session_id": "wiz_20260811_120000_abcd1234"}
```

The request cannot override:

- accepted schematic path;
- wizard/job workspace;
- work/evidence directories;
- provider or model;
- API key;
- KiCad executable;
- loop/resource limits;
- operation policy.

When enabled, the route obtains `WebSettings` and the configured request-scoped LLM client through normal FastAPI dependencies and forwards the sanitized request through the trusted wizard composition boundary above.

Request-body validation is intentionally generic: malformed JSON, unknown fields, path attempts, credentials, and bound overrides return `REFINEMENT_INVALID_REQUEST` without reflecting rejected values. Lower-layer `UserError` responses preserve only the machine-readable code plus a generic refinement failure message; internal exception text/details are not reflected.

The HTTP response exposes hashes, stop reason, counts, model-call accounting, and an `evidence_available` boolean. It does not expose the absolute session-evidence directory.

## CLI contract

`execute_refinement_cli()` follows the canonical configured refinement service and accepts only:

```text
--session-id <safe-session-id>
```

The CLI adapter itself cannot override paths, provider/model, credentials, or bounds. Production CLI composition with the same wizard/current-job ownership resolver remains a separate integration step; a standalone arbitrary-path mutation CLI must not be introduced.

## Evidence and retention

Each completed iteration writes an atomic sanitized evidence directory. Accepted, rejected, and no-op rounds are all visible. Each session writes an atomic session bundle containing `manifest.json` and `summary.md`.

The session manifest records:

- session/evidence schema versions;
- provider/model and optional product/implementation identity;
- authoritative and accepted hashes;
- layout fingerprints;
- prompt/schema versions;
- configured bounds;
- model-call accounting;
- per-iteration evidence directory names and manifest SHA-256 hashes;
- final stop reason and hard-failure code when applicable;
- final and best accepted hashes.

The recorded retention policy is bounded per session:

- rejected candidate files: not retained after the iteration transaction;
- post-edit candidate render scratch: not retained after the iteration transaction;
- durable render copies: only inside iteration evidence;
- durable iteration bundles: at most `max_rounds` for the session;
- raw prompts: not retained;
- raw provider payloads: not retained;
- optional debug artifacts: not retained.

The accepted-render work directory is a fixed scratch location and is overwritten rather than accumulated; durable render evidence is stored in the bounded iteration bundles.

Session references are hash-bound to iteration manifests and are cross-checked against iteration ID, reason code, accepted hashes, candidate hash, and candidate layout fingerprint before the final session bundle is published.

## Session idempotency and reservation

A refinement `session_id` is a one-shot evidence/mutation namespace within its job-specific evidence root. `refine_schematic()` atomically reserves that namespace before entering the refinement loop, which is before any refinement model dispatch or candidate mutation.

A request fails closed before refinement execution when any of the following already exists for the same ID in that evidence root:

- a completed or failed session evidence bundle;
- an active reservation;
- a stale/orphan reservation;
- an orphan iteration evidence bundle anywhere in the supported round namespace.

A stale reservation is never automatically stolen, expired, or overwritten. Recovery of an ambiguous orphan reservation therefore requires an explicit operator decision outside the automatic refinement path.

The reservation remains held while the session runs and while terminal evidence is being published. On normal completion or a runtime failure, it is released only after the completed/failed session bundle has been durably published. Consequently, another same-ID request always encounters either the reservation or the durable terminal session bundle.

If terminal session-evidence publication fails, the reservation is deliberately retained. The service does not remove the reservation and invite an ambiguous replay after model calls or accepted-state mutation may already have occurred.

## Derived project artifacts after refinement

The job's `.kicad_sch` inside the generated project is the canonical accepted artifact. `project.zip` and `schematic_preview.png` are derived download/preview artifacts and must not silently remain stale after canonical schematic mutation.

After a refinement service result is committed:

1. the schematic preview is regenerated;
2. the downloadable project ZIP is regenerated from the current project tree;
3. job result metadata is refreshed with a sanitized refinement summary.

Preview generation remains an explicitly optional capability inherited from initial project generation. Refinement renders the new preview in a private same-filesystem scratch directory and only then atomically replaces the public `schematic_preview.png`. If generation fails with the known `PreviewGenerationError`, any old preview is removed, the failure is warning-visible, and `preview_warning` is updated. A stale or partially generated preview is not presented as current. Scratch cleanup failure is error-visible but does not change the truth of an already published preview.

Unexpected preview failures, ZIP refresh failures, or job-metadata refresh failures raise `REFINEMENT_DERIVED_STATE_REFRESH_FAILED` with `authoritative_committed=true`. This explicitly states that the canonical schematic/refinement evidence may already be committed and must not be replayed as though nothing happened. If ZIP refresh fails, the stale archive is removed before returning the failure.

`create_project_zip()` rejects symbolic links and any archive member whose resolved path escapes the generated project root. A new ZIP is fully written and fsynced inside a private `artifacts/.staging/` directory that the flat artifact API neither lists nor addresses; only then is it atomically moved into place as `project.zip`. Concurrent readers therefore see either the previous complete archive or the new complete archive, never the in-progress staging file or a partially written replacement.

## Stop and failure semantics

Machine-readable normal stop reasons include:

- `REFINEMENT_STOP_MAX_ROUNDS`;
- `REFINEMENT_STOP_NO_ACTIONABLE_ISSUES`;
- `REFINEMENT_STOP_NO_OPERATIONS`;
- `REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT`;
- `REFINEMENT_STOP_REJECTION_LIMIT`;
- `REFINEMENT_STOP_OSCILLATION`;
- `REFINEMENT_STOP_OPERATION_BUDGET`.

Hard runtime/provider/verification failures are re-raised. The service attempts to publish a failed session manifest with `REFINEMENT_STOP_HARD_FAILURE` and the original machine-readable failure code. If evidence finalization also fails, the original failure remains primary and the secondary evidence failure is attached as exception context/note rather than replacing the original cause. When that terminal evidence finalization fails, the session reservation remains in place to block ambiguous replay.

Explicit user cancellation is **not implemented** and must not be claimed by API, CLI, UI, or documentation.
