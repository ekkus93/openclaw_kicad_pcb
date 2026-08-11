# KiCad schematic refinement runtime configuration

Date: 2026-08-11

This document describes the runtime/configuration contract for the bounded schematic visual-refinement service. It is intentionally narrower than the implementation specification: it records the production-facing feature gate, resource bounds, provenance requirements, evidence policy, and interface constraints.

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

Integer values must use canonical base-10 text. Whitespace, a leading `+`, leading zeroes, floating-point text, and other coercive forms are rejected. Unknown environment variables beginning with `KICAD_WEBAPP_REFINEMENT_` are also rejected so a misspelled bound cannot silently fall back to a default.

Enabling the feature flag does not create a new mutation implementation. `run_configured_refinement()` dispatches only to the canonical transactional `refine_schematic()` service.

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

`build_refinement_runtime()` is the preferred server-side composition helper. It receives the authoritative `CircuitIR`, KiCad adapter, LLM client, work/evidence directories, provider/model identity, and optional implementation identity explicitly.

API keys, authorization headers, provider payloads, and unrelated absolute paths are not provenance fields.

## HTTP contract

`RefinementRunRequest` accepts only:

```json
{"session_id": "session-001"}
```

The request cannot override:

- accepted schematic path;
- work/evidence directories;
- provider or model;
- API key;
- loop/resource limits;
- operation policy.

When the feature is disabled, `build_refinement_router()` returns an empty router, so `/api/refinement/run` is not exposed. When enabled, the route obtains the accepted path and `RefinementRuntime` from server-owned dependency providers and forwards the sanitized request through `run_configured_refinement_request()`.

The HTTP response exposes hashes, stop reason, counts, model-call accounting, and an `evidence_available` boolean. It does not expose the absolute session-evidence directory.

## CLI contract

`execute_refinement_cli()` follows the same service path and accepts only:

```text
--session-id <safe-session-id>
```

Accepted schematic path, runtime, provider/model, and limits are injected by the composition layer. CLI flags that attempt to override paths, providers, credentials, or limits are invalid arguments and never dispatch refinement.

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

## Stop and failure semantics

Machine-readable normal stop reasons include:

- `REFINEMENT_STOP_MAX_ROUNDS`;
- `REFINEMENT_STOP_NO_ACTIONABLE_ISSUES`;
- `REFINEMENT_STOP_NO_OPERATIONS`;
- `REFINEMENT_STOP_NO_MEANINGFUL_IMPROVEMENT`;
- `REFINEMENT_STOP_REJECTION_LIMIT`;
- `REFINEMENT_STOP_OSCILLATION`;
- `REFINEMENT_STOP_OPERATION_BUDGET`.

Hard runtime/provider/verification failures are re-raised. The service attempts to publish a failed session manifest with `REFINEMENT_STOP_HARD_FAILURE` and the original machine-readable failure code. If evidence finalization also fails, the original failure remains primary and the secondary evidence failure is attached as exception context/note rather than replacing the original cause.

Explicit user cancellation is **not implemented** and must not be claimed by API, CLI, UI, or documentation.
