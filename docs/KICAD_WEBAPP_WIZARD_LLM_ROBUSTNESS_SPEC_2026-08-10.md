# KiCad Web App Wizard/LLM Robustness Spec — 2026-08-10

## Purpose

This is a targeted reliability pass on the LLM wizard service layer. It follows the
post-hardening closure batch (`KICAD_WEBAPP_POST_HARDENING_CLOSURE_*`), which was a
documentation/configuration pass and did not touch product code.

A focused correctness review of the LLM client and wizard orchestration found several
pre-existing defects that turn recoverable transient conditions into hard session
failures, plus lower-severity robustness and hygiene gaps. This spec closes those
defects. It does **not** reopen schematic placement, orientation, wire routing, PCB
layout, or Circuit IR semantics.

## Starting point

- Branch: `webapp`
- Starting SHA: `479465102f25f7dd85153477442c1c01b6dfbbe3`
- Review basis: static review of `src/kicad_pcb_web/services/llm/*`,
  `services/wizard.py`, `services/_wizard_llm.py`, `services/_wizard_session_io.py`,
  and `settings.py`.
- All existing quality gates were green at the starting SHA (ruff, ruff format, mypy,
  `pytest tests/unit`).

## Confirmed defects

Severity is the reviewer's estimate; the mechanism of each was confirmed by reading the
cited code.

### D1 — IR generation gets zero structural-JSON repair (Med)

`services/wizard.py` runs the IR loop `ir_max_repair_rounds + 1` times but calls
`_call_llm_for_json(..., max_repairs=0)` inside it (`wizard.py:507-521`). The inner
`try/except` only catches `UserError` from `prepare_netlist_dict` — i.e. *semantic*
netlist validation. A malformed-JSON or schema-invalid IR completion raises
`UpstreamProviderError` (`_wizard_llm.py:331`) that escapes the loop and hard-fails the
session. So `ir_max_repair_rounds` protects against semantic netlist errors but **not**
transient bad JSON, even though the identically-named `spec_max_repair_rounds` gives spec
drafting exactly that protection. A single flaky JSON completion aborts the session.

### D2 — Null / non-string completion content bypasses the repair loop (Med)

`_coerce_text_content` raises `ToolError` when a provider returns content that is not a
string or content-part list (`base.py:334`) — which is exactly what OpenAI returns
(`content: null`) on content-filter stops and some `finish_reason="length"` cases
(`openai_client.py:52`). The structured-JSON repair loop only catches
`(json.JSONDecodeError, ValidationError)` (`_wizard_llm.py:299`), so `ToolError` escapes
with no repair attempt and hard-fails. Related: a *truncated* JSON string does retry, but
re-requests with the identical `max_tokens`, so it typically re-truncates and burns every
repair round.

### D3 — `temperature` is always sent to OpenAI / llama-server (Med/Low)

`openai_client.py:21` and `llama_server_client.py:23` unconditionally send
`temperature`. Current OpenAI reasoning-class models reject any `temperature != 1` with a
400, which becomes a non-retryable `ToolError` (400 ∉ the retryable status set) and a hard
failure. The code already special-cased `max_tokens` → `max_completion_tokens` for this
exact compatibility reason (`openai_client.py:25`); temperature is the remaining exposure.

### D4 — Blocking retry sleep holds the mutation lock and a threadpool worker (Low/Med)

`base.py:257` calls `time.sleep(delay_s)` synchronously inside a wizard call dispatched to
FastAPI's bounded threadpool, while the per-session mutation lock is held. With
`retry_max_attempts` up to 10 and `retry_max_delay_s` up to 8s (both operator-configurable),
a provider outage can pin workers for tens of seconds and stall unrelated endpoints.

### D5 — `debug_artifact_capture` artifacts accumulate unbounded (Low)

`_wizard_session_io.py:157-161` writes a new uuid-suffixed file on every attempt with no
cap or pruning. Only relevant when the default-off flag is enabled, but an enabled session
grows `debug_artifacts/` indefinitely. These files are intentionally un-redacted (they hold
circuit specs/IR + model output, not the API key), so unbounded growth compounds a
confidentiality surface.

### D6 — Soft `failed` state is ambiguous with operational failure (Low)

When the model returns `next_state="failed"` (unsupported design), the session is written
with `status="failed"` and `error=None` (`wizard.py:290-292, 372-373`), overloading the
terminal `failed` status for two very different meanings distinguished only by whether
`error` is a dict. `_failure_operation` returns `None` for the soft case, so retry gates
silently treat it as non-retryable.

### D7 — `settings.py` minor correctness/consistency issues (Low)

- Redundant `base_url` validation: validated for all providers at `settings.py:345-346`,
  then re-validated in the openai branch at `355-357` (dead code).
- `jobs_dir.mkdir(...)` runs at settings-load time (`settings.py:400`); a read-only-FS
  failure raises a bare `OSError`, inconsistent with the `ValueError` contract used
  everywhere else in the loader.
- Empty-string env values are treated as real values: `_read_setting` only checks
  `is not None`, so `KICAD_PCB_WEB_DATA_DIR=""` silently resolves to CWD instead of
  erroring. (Critical string fields are caught by `_require_non_empty`; `data_dir` is the
  one silent case.)

## Scope

### 1. IR structural-JSON repair (D1)

Give IR generation the same structural-JSON repair budget as spec drafting.

Requirements:

- IR JSON generation must retry malformed/schema-invalid completions up to a bounded
  number of rounds derived from `ir_max_repair_rounds`, feeding the parse/validation error
  back into the next prompt — the same mechanism spec drafting already uses.
- Semantic netlist repair (the existing `prepare_netlist_dict` → `UserError` outer loop)
  must be preserved; structural-JSON repair is additive, not a replacement.
- The combined loop must remain strictly bounded (no unbounded retry) and must terminate in
  a well-defined terminal state (`ir_needs_repair` / `failed`) when the budget is exhausted.
- Exhausting structural-JSON repair must not be reported as a semantic netlist failure and
  vice versa; the terminal state/error must reflect the actual cause.

### 2. Non-string / truncated completion handling (D2)

Treat an unusable completion shape as a repairable structured-output failure, not a hard
error.

Requirements:

- A `null` / non-string / empty content completion during structured-JSON generation must
  be handled inside the repair loop (retry within budget), not escape as an uncaught
  `ToolError`.
- The behavior must be identical for spec and IR generation.
- When the budget is exhausted, the failure must surface as a typed, user-meaningful error
  identifying "provider returned no usable content", distinct from "invalid JSON".
- Optional (document decision if not implemented): on a `finish_reason="length"` truncation,
  do not silently re-request with the same `max_tokens` and expect a different result — at
  minimum surface the truncation cause distinctly.

### 3. Provider parameter compatibility (D3)

Do not send request parameters that a configured provider is known to reject as a
non-retryable 400.

Requirements:

- `temperature` must only be sent when it is meaningful for the configured model/provider;
  a way to omit it (mirroring the existing `max_completion_tokens` handling) must exist.
- The chosen approach must be explicit and validated, not a silent fallback: either omit
  `temperature` when unset/not-applicable, or document the exact condition under which it is
  sent.
- No behavior change for providers/models that accept `temperature` today.

### 4. Retry sleep must not monopolize shared resources (D4)

Reduce the blast radius of a provider outage.

Requirements:

- The retry backoff wait must not hold the per-session mutation lock across the full sleep,
  **or** the interaction between `retry_max_attempts`, `retry_max_delay_s`, `timeout_s`, and
  `mutation_lock_timeout_s` must be documented as a bounded, understood worst case.
- Any change must preserve the existing idempotency guarantee (POSTs are not replayed on
  ambiguous delivery) and the existing retryable-status set.
- Prefer the smallest safe change; do not convert the synchronous client to async as part
  of this batch.

### 5. Debug-artifact retention (D5)

Bound `debug_artifact_capture` output.

Requirements:

- When enabled, artifact writing must enforce a retention bound (per-session count and/or
  total size cap) so a long/repeatedly-repaired session cannot grow the directory without
  limit.
- Default-off behavior and the existing `0o700` private-directory guard must be preserved.
- Un-redacted content remains by design; this item only bounds accumulation.

### 6. Failure-state clarity (D6)

Make the two `failed` meanings distinguishable without inspecting `error`.

Requirements:

- Either introduce a distinct terminal state / explicit discriminator for
  "model-declared unsupported design" vs "operational failure", or document the invariant
  (`error is None` ⇒ soft/unsupported; `error is dict` ⇒ operational) as a deliberate,
  tested contract.
- Retry-gate behavior must follow from that contract explicitly, not incidentally.
- No change to the wire status enum values consumed by the frontend unless the frontend is
  updated in the same change and rebuilt.

### 7. Settings hygiene (D7)

- Remove the redundant `base_url` re-validation in the openai branch.
- Wrap the load-time `jobs_dir.mkdir` failure as a typed `ValueError` (or the loader's
  established error type) rather than a bare `OSError`.
- Reject empty-string `data_dir` (and any other silently-CWD-resolving field) explicitly.

## Explicit non-goals

Do not modify:

- schematic component placement, orientation, wire routing, routing heuristics, crossing
  minimization, spacing/layout algorithms;
- PCB placement/routing;
- unrelated Circuit IR semantics;
- the deterministic engine (`kicad_pcb`) except where a wizard fix genuinely requires it;
- the synchronous→async transport model of the LLM client;
- unrelated UI design.

If a fix requires one of these areas, document the dependency instead of folding it into
this batch.

## Failure semantics

- A transient provider condition (malformed JSON, null content, truncation) must be
  retried within its configured budget before the session hard-fails.
- Budgets must remain strictly bounded; no fix may introduce an unbounded loop.
- Provider-parameter incompatibility must not be a silent no-op; either the parameter is
  omitted deliberately or the rejection is surfaced as a typed error.
- Idempotency is not weakened: POSTs are still not replayed on ambiguous delivery.
- Fail-closed configuration posture is preserved (unknown/removed keys still reject).

## Testing requirements

Per project policy, every fixed defect gets a targeted regression test that would have
caught it (`tests/unit/`, real fakes over mocks):

- D1: fake LLM client returning malformed then valid IR JSON → session recovers within
  `ir_max_repair_rounds`; malformed on every round → deterministic terminal state.
- D2: fake client returning `content=None` → handled as repairable, not an uncaught
  `ToolError`; budget-exhaustion yields the typed "no usable content" error.
- D3: payload builder omits/includes `temperature` per the chosen contract (assert on the
  built payload dict).
- D4: assert the worst-case bound, or that the lock is released across the retry wait.
- D5: enabling capture over N attempts respects the retention bound.
- D6: soft-failed vs operational-failed are distinguishable and drive the retry gate as
  specified.
- D7: empty `data_dir` rejects; redundant validation removed without behavior change.

No existing test may be weakened to pass.

## Definition of Done

Closure is complete when:

- D1–D7 are each implemented or explicitly dispositioned with a documented decision;
- IR generation recovers from transient malformed/empty completions within its bounded
  budget and no longer hard-fails on a single flaky JSON/null response;
- provider-parameter compatibility is handled deliberately (no silent non-retryable 400);
- debug-artifact output is bounded when enabled;
- the two `failed` meanings are unambiguous by contract and tested;
- settings hygiene items are resolved;
- each fix has a targeted regression test; no test was weakened;
- ruff, ruff format, mypy, and `pytest tests/unit tests/web` all pass;
- no schematic/PCB placement, routing, or layout code was modified;
- an accepting SHA passes permanent CI 5/5 green and the result is recorded in a completion
  evidence document.
