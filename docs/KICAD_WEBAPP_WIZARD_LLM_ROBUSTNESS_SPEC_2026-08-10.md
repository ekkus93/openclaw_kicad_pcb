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

This revision (2026-08-10) incorporates two rounds of review:
`docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_QUESTIONS_2026-08-10.md` and the D4
follow-up in `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_FOLLOWUP_2026-08-10.md` /
`docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_D4_REVIEW_QUESTIONS_2026-08-10.md`. Every open
decision those reviews raised is resolved into an exact contract below so implementation
does not make architecture/failure-semantics decisions opportunistically. Point-by-point
answers are in `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_ANSWERS_2026-08-10.md`.

## Starting point and SHA discipline

Three SHAs are tracked distinctly:

- **Code-review baseline SHA** (where the defects were observed):
  `479465102f25f7dd85153477442c1c01b6dfbbe3`
- **Planning/documentation head** (spec/TODO/answers commits): recorded in the completion
  doc; not a code baseline.
- **Implementation starting SHA**: whatever `webapp` points to immediately before the
  first product-code change. The completion evidence records this true implementation head,
  **not** the older review baseline.

All existing quality gates were green at the code-review baseline (ruff, ruff format, mypy,
`pytest tests/unit`). The implementer must re-confirm green at the implementation starting
SHA before changing code.

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
drafting exactly that protection.

### D2 — Null / non-string completion content bypasses the repair loop (Med)

`_coerce_text_content` raises `ToolError` when a provider returns content that is not a
string or content-part list (`base.py:334`) — which is exactly what OpenAI returns
(`content: null`) on content-filter stops and some `finish_reason="length"` cases
(`openai_client.py:52`). The structured-JSON repair loop only catches
`(json.JSONDecodeError, ValidationError)` (`_wizard_llm.py:299`), so `ToolError` escapes
with no repair attempt and hard-fails. Related: a *truncated* JSON string does retry, but
re-requests with the identical `max_tokens`, so it typically re-truncates.

### D3 — `temperature` is always sent to OpenAI / llama-server (Med/Low)

`openai_client.py:21` and `llama_server_client.py:23` unconditionally send `temperature`.
Current OpenAI reasoning-class models reject any `temperature != 1` with a 400, which
becomes a non-retryable `ToolError` (400 ∉ the retryable status set) and a hard failure.
There is currently **no way to express "do not send temperature"**: `LlmSettings.temperature`
is a required float defaulting `0.2`, the factory always propagates it, and both clients
always put it in the payload.

### D4 — Blocking retry sleep holds the mutation lock and a threadpool worker (Low/Med)

`base.py:257` calls `time.sleep(delay_s)` synchronously inside a wizard call dispatched to
FastAPI's bounded threadpool, while the per-session mutation lock is held. With
`retry_max_attempts` up to 10 and `retry_max_delay_s` up to 8s, a provider outage can pin
workers for tens of seconds. Note: `mutation_lock_timeout_s` bounds only how long a *second*
caller waits to acquire the lock — it does **not** bound how long the current operation
holds it. `timeout_s` and `retry_max_delay_s` currently have no useful hard upper bound.

### D5 — `debug_artifact_capture` artifacts accumulate unbounded (Low)

`_wizard_session_io.py:157-161` writes a new uuid-suffixed file on every attempt with no
cap or pruning. Only relevant when the default-off flag is enabled, but an enabled session
grows `debug_artifacts/` indefinitely. These files are intentionally un-redacted.

### D6 — Soft `failed` state is ambiguous with operational failure (Low)

When the model returns `next_state="failed"` (unsupported design), the session is written
with `status="failed"` and `error=None` (`wizard.py:290-292, 372-373`), overloading the
terminal `failed` status for two very different meanings distinguished only by whether
`error` is a dict. `_failure_operation` returns `None` for the soft case, so retry gates
silently treat it as non-retryable.

### D7 — `settings.py` minor correctness/consistency issues (Low)

- Redundant `base_url` validation at `settings.py:345-346` then again at `355-357`.
- `jobs_dir.mkdir(...)` at load time (`settings.py:400`) can raise a bare `OSError`,
  inconsistent with the loader's `ValueError` contract.
- Empty-string env values treated as real: `KICAD_PCB_WEB_DATA_DIR=""` silently resolves to
  CWD (`_read_setting` only checks `is not None`).

---

## Resolved contracts (scope)

### 1. IR structural-JSON repair — single shared budget (D1)

**Decision: one shared total repair budget, non-multiplicative.**

- The maximum number of LLM invocations for one `generate_ir` operation is exactly
  `ir_max_repair_rounds + 1`, **regardless** of whether each failed attempt was malformed
  JSON, schema-invalid structured output, empty/unusable content, or semantically invalid
  netlist. Structural and semantic failures draw from the **same** counter — they must not
  multiply.
- Implementation must restructure the IR loop so a single attempt counter governs all
  failure classes (do **not** simply change `max_repairs=0` to
  `max_repairs=ir_max_repair_rounds`, which would make the loops multiplicative — up to 9
  calls at the default of 2).
- On each repairable failure, feed the specific error (parse error, schema error,
  no-content, or netlist `UserError` message) back into the next prompt.

**Terminal-state mapping (exact):**

- **Parseable IR that repeatedly fails semantic netlist validation** → `ir_needs_repair`,
  preserving the last parsed IR (`prior_ir_json`) for inspection/repair.
- **Malformed / schema-invalid / no-usable-content exhaustion** (no usable IR draft ever
  produced) → operational `failed` for `generate_ir`, with a typed error describing the
  structural cause.
- The terminal state/error must reflect the actual final cause; a structural exhaustion must
  not be reported as `ir_needs_repair`, and a semantic exhaustion must not be reported as an
  operational structural failure.

### 2. Completion-shape classification — narrow, typed, not blanket-retry (D2)

**Decision: introduce an explicit outcome classification; do not catch generic `ToolError`
in the repair loop.** `ToolError` also covers genuine provider/transport/protocol errors,
so the repair path must key off narrower typed conditions.

Classify provider completion outcomes into these distinct conditions:

| Condition | Repairable within budget? | Terminal disposition on exhaustion / immediately |
|-----------|---------------------------|---------------------------------------------------|
| Structured JSON parse failure | Yes | typed "invalid structured output" |
| Schema-invalid structured output | Yes | typed "invalid structured output" |
| Provider returned no usable content (`null`/empty, no stronger reason) | Yes | typed "provider returned no usable content" |
| Response truncated (`finish_reason="length"`) | **No** — terminal typed condition | typed "response truncated; raise max_tokens" (do **not** blindly re-request with the same `max_tokens`) |
| Provider refusal / content-filter stop | **No** — terminal typed provider outcome | typed "provider refused / content-filtered" |
| Provider / transport / protocol failure (generic `ToolError`) | No — not handled by repair loop | propagates; subject only to the existing HTTP-layer retry |

Requirements:

- The base client must expose enough information (finish reason and a distinct exception or
  return type for "no usable content") that the repair loop can classify without catching
  generic `ToolError`.
- Repairable conditions (parse/schema/no-content) retry within the same shared budget
  defined for that operation (spec: `spec_max_repair_rounds + 1`; IR: the D1 shared budget).
- Truncation and refusal/content-filter are **terminal typed** outcomes, surfaced with
  distinct, user-meaningful messages — not retried as if transient.
- Behavior is identical for spec and IR generation.

### 3. Temperature capability contract (D3)

**Decision: explicit config-driven policy, no model-name heuristic.**

- Add an `[llm]` setting `temperature_mode` with values `send | omit`.
  - `send` (**default**) — include `temperature` in the payload (current behavior; no silent
    change for models that accept it).
  - `omit` — never include `temperature` in the payload (for reasoning-class models that
    reject non-default temperature).
- `temperature_mode` is validated like other enum settings (reject unknown values) and is
  recorded in LLM provenance / config revision so a session created under one policy is not
  silently reinterpreted under another.
- Both `openai_client` and `llama_server_client` payload builders honor the mode. When
  `omit`, `temperature` is absent from the dict entirely (not sent as `null`).
- An `auto` capability-registry mode is explicitly **out of scope** for this batch and noted
  as a possible future extension; we avoid an ad-hoc name heuristic.

### 4. Retry configuration envelope — keep lock, bound the configuration (D4)

**Decision: keep the synchronous transport and the per-session lock. Do not release the
lock around retry sleeps** (that would create a lost-update race without revision/CAS
protection, which is out of scope). Instead, place hard caps on the operator-configurable
retry/timeout values.

**Important scope correction:** the current client constructs HTTPX with a scalar timeout
(`base.py:113`, `httpx.Client(..., timeout=self.timeout_s, ...)`). HTTPX's timeout model
configures connect/read/write/pool *inactivity* timeouts, not an absolute end-to-end
deadline for the whole HTTP request — a server that keeps sending data within each
inactivity window can still exceed `timeout_s` in total elapsed time. Therefore this batch
does **not** claim a guaranteed total wall-clock ceiling for one LLM operation. It only
bounds the *configuration* so operators cannot set arbitrarily large timeout/retry values.

- Add cross-field / range validation on the retry/timeout **configuration**:
  - `timeout_s`: `0 < timeout_s <= 300`.
  - `retry_max_delay_s`: `retry_base_delay_s <= retry_max_delay_s <= 60`.
  - Existing `1 <= retry_max_attempts <= 10` retained.
- Document and test the maximum **scheduled retry-sleep total** produced by the configured
  exponential/clamped backoff policy — this quantity (`sum(clamped backoff delays)` across
  up to `retry_max_attempts - 1` sleeps) is deterministic given a valid configuration and
  can be asserted exactly. It is a bound on the code's own sleep scheduling, not on HTTPX's
  network-timeout behavior.
- Do **not** describe `retry_max_attempts * timeout_s + sum(clamped backoff delays)` as a
  guaranteed total operation deadline anywhere in the spec, TODO, code comments, or
  completion evidence. It is, at most, the *configured retry/timeout envelope*: the caps
  prevent pathological operator configuration, but HTTPX's per-phase inactivity timeouts do
  not themselves guarantee the overall request finishes within that envelope.
- Introducing a true absolute total-deadline mechanism (deadline clock spanning connect,
  write, read, all HTTP retries, and all retry sleeps; remaining-time propagation into each
  attempt; a typed deadline-exhausted error; interaction with the session lock; tests on a
  controllable/fake clock) is explicitly **out of scope** for this batch — it is a
  materially larger transport/concurrency change and, if ever required, should be its own
  follow-up spec.
- Introducing revision/CAS semantics and out-of-lock provider execution is explicitly
  **deferred** to a future concurrency design.

### 5. Debug-artifact retention policy (D5)

**Decision: deterministic per-session, per-stage retention with oldest-first pruning.**

- Retention is applied **separately by stage** (the `spec_*` and `ir_*` filename prefixes),
  so a burst in one stage cannot evict the other's artifacts.
- Per-session, per-stage **file-count cap: 20** (proposed default; single constant, easily
  tunable). When writing the 21st, delete oldest-first until at most 20 remain.
- Additional per-session **total-byte cap: 25 MiB** across all stages; after the count prune,
  delete oldest-first (across stages) until under the byte cap.
- **Oversize single artifact**: the newest artifact is always retained even if it alone
  exceeds the byte cap; pruning never deletes the just-written newest file.
- **Pruning/deletion failure** is logged at WARNING with the path and does not raise — debug
  capture is best-effort and must never fail the wizard operation — but it is never silently
  swallowed (no bare `except: pass`).
- Default-off behavior and the `0o700` private-directory guard are preserved.

### 6. Failure-kind discriminator (D6)

**Decision: add an explicit persisted discriminator field; do not add a new
frontend-visible `WizardStatus` enum value, and do not rely on inspecting `error` shape.**

- Add an optional field `failure_kind` to the wizard session model, set whenever
  `status == "failed"`. Values:
  - `unsupported_design` — model-declared unsupported design (the current soft-failed case);
  - `operational` — provider/transport/validation operational failure;
  - `generation` — project-generation failure.
- The wire `status` remains `failed`; the frontend may ignore the new field.
- **Legacy sessions** persisted before this field: `failure_kind` is `Optional` with default
  `None` so old JSON still deserializes. A read helper interprets a legacy `failed` session
  with `failure_kind is None` using the historical rule (`error is None` ⇒
  `unsupported_design`; `error is dict` ⇒ `operational`) **for reads only**; all new writes
  set `failure_kind` explicitly. This legacy interpretation is tested, not guessed.
- Retry gates (`_failure_operation` and callers) key off `failure_kind` directly rather than
  inferring meaning from the shape of `error`.

### 7. Settings hygiene — exact loader contract (D7)

- Remove the redundant `base_url` re-validation in the openai branch (`settings.py:355-357`).
- Wrap the load-time `jobs_dir.mkdir` failure specifically as **`ValueError`** (not "an
  established error type" — `ValueError` exactly) with a clear message including the path.
- Reject empty-string `data_dir` explicitly (raise `ValueError`), for both the
  `KICAD_PCB_WEB_DATA_DIR=""` env form and the TOML `data_dir = ""` form.
- Audit every other path/string setting for the same empty-string-silently-accepted issue
  and record each field's disposition in the completion evidence.

---

## Explicit non-goals

Do not modify:

- schematic component placement, orientation, wire routing, routing heuristics, crossing
  minimization, spacing/layout algorithms;
- PCB placement/routing;
- unrelated Circuit IR semantics;
- the deterministic engine (`kicad_pcb`) except where a wizard fix genuinely requires it;
- the synchronous→async transport model of the LLM client (D4 keeps it synchronous);
- revision/CAS session concurrency (explicitly deferred by D4);
- an `auto` temperature capability registry (explicitly deferred by D3);
- unrelated UI design.

If a fix requires one of these areas, document the dependency instead of folding it in.

## Failure semantics

- A repairable transient condition (malformed JSON, schema-invalid, no usable content) is
  retried within its configured budget before the session hard-fails.
- Truncation and refusal/content-filter are terminal typed outcomes, not retried as
  transient.
- Budgets are strictly bounded and non-multiplicative; no fix introduces an unbounded loop.
- Provider-parameter incompatibility is handled deliberately (`temperature_mode`), never a
  silent no-op.
- Idempotency is not weakened: POSTs are still not replayed on ambiguous delivery, and the
  session lock is retained (no lock-release race introduced).
- Fail-closed configuration posture is preserved (unknown/removed keys still reject;
  `temperature_mode` rejects unknown values).

## Testing requirements

Every fixed defect gets targeted regression tests (`tests/unit/`, real fakes over mocks).
Assertions must be explicit enough that the checklist cannot pass while hidden bad behavior
remains:

- **D1**: fake client failing every IR round asserts the **exact** maximum LLM call count
  (`ir_max_repair_rounds + 1`, i.e. 3 at default); malformed-then-valid recovers; semantic
  repeated-failure lands in `ir_needs_repair` with preserved IR; structural exhaustion lands
  in operational `failed`.
- **D2**: separate tests for malformed→valid recovery, empty-content→valid recovery,
  repeated no-content exhaustion (typed error), `finish_reason="length"` truncation
  (terminal, distinct), content-filter/refusal (terminal, distinct), and a proof that a
  generic provider/transport `ToolError` is **not** absorbed by the structured-output repair
  loop.
- **D3**: assert exact built-payload dicts for `temperature_mode=send` (temperature present)
  and `temperature_mode=omit` (temperature absent) for both openai and llama-server clients;
  assert `temperature_mode` provenance is recorded; assert unknown mode rejects.
- **D4**: assert `timeout_s <= 0` rejects; `timeout_s > 300` rejects;
  `retry_max_delay_s > 60` rejects; `retry_max_delay_s < retry_base_delay_s` rejects;
  `retry_max_attempts` stays constrained to 1..10; the maximum scheduled retry-sleep total
  produced by the configured backoff policy is deterministic and bounded as documented for a
  given valid configuration; and existing retryable-status / no-replay-on-ambiguous-delivery
  behavior is unchanged. No test or documentation may assert this is a guaranteed total HTTP
  wall-clock deadline — only the configured retry/timeout envelope.
- **D5**: assert deterministic oldest-first pruning at the per-stage count boundary and the
  byte-cap boundary, newest-always-retained on oversize, and that a simulated deletion
  failure logs without raising.
- **D6**: unsupported/model-declared vs operational vs generation outcomes each set the
  right `failure_kind` and drive the retry gate correctly; a legacy `failed` session with no
  `failure_kind` is interpreted per the documented rule.
- **D7**: reject `KICAD_PCB_WEB_DATA_DIR=""` and TOML `data_dir=""` separately; redundant
  validation removed with existing settings tests still green.

No existing test may be weakened to pass.

## Definition of Done

Closure is complete when:

- D1–D7 are each implemented per the exact contracts above (or a deviation is explicitly
  documented with rationale in the completion evidence);
- IR generation recovers from transient malformed/empty completions within a single shared
  bounded budget and never hard-fails on one flaky JSON/null response;
- completion-shape outcomes are classified into distinct typed conditions with the specified
  repairable/terminal semantics, and generic `ToolError` is never blanket-retried;
- `temperature_mode` gives deliberate control with recorded provenance and no behavior change
  at its `send` default;
- the retry/timeout configuration envelope is bounded by validated caps and documented as a
  configured envelope, not claimed as an absolute HTTP wall-clock deadline;
- debug-artifact output obeys the deterministic retention policy when enabled;
- `failure_kind` makes the terminal meanings unambiguous, with tested legacy behavior;
- settings hygiene items are resolved with the exact `ValueError` loader contract;
- each fix has targeted regression tests with the explicit assertions above; no test weakened;
- ruff, ruff format, mypy, and `pytest tests/unit tests/web` all pass;
- no schematic/PCB placement, routing, or layout code was modified;
- the implementation starting SHA (not the review baseline) and an accepting SHA that passes
  permanent CI 5/5 green are recorded in the completion evidence document.
