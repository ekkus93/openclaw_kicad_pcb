# KiCad Web App Wizard/LLM Robustness — D4 Follow-up Review Questions — 2026-08-10

## Purpose

This document captures the remaining issue found after re-reading the updated:

- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_TODO_2026-08-10.md`

The previously raised questions around D1, D2, D3, D5, D6, D7, SHA discipline, and targeted regression assertions now appear resolved clearly enough for implementation. The remaining concern is limited to D4.

No product-code change is requested by this document. The purpose is to tighten the D4 contract before the Ralph loop so the completion evidence does not claim a timing guarantee the current HTTP client does not actually provide.

---

## 1. D4 — The current "hard wall-clock ceiling" claim is too strong

The revised D4 contract makes an important and correct concurrency decision:

- keep the synchronous transport;
- keep the per-session mutation lock;
- do **not** release the lock during retry sleeps;
- defer revision/CAS semantics and out-of-lock provider execution to a future concurrency design.

That avoids the lost-update race described in the earlier review.

The updated spec then proposes these configuration caps:

```text
0 < timeout_s <= 300
retry_base_delay_s <= retry_max_delay_s <= 60
1 <= retry_max_attempts <= 10
```

and describes the worst-case bound for one operation as:

```text
retry_max_attempts * timeout_s + sum(clamped backoff delays)
```

The concern is that the current client constructs HTTPX with a scalar timeout:

```python
httpx.Client(..., timeout=self.timeout_s, ...)
```

A scalar HTTPX timeout configures the underlying connect/read/write/pool timeout behavior. In particular, the read timeout is an inactivity timeout while receiving response data, not an absolute deadline for the complete HTTP request from start to finish.

Therefore, the formula above is useful as a **configured retry/timeout envelope**, but it is not a mathematically guaranteed total wall-clock deadline for the whole wizard operation.

### Why this matters

With the proposed maxima:

```text
retry_max_attempts = 10
timeout_s = 300
retry_max_delay_s = 60
```

it is tempting to say the operation is absolutely bounded by something on the order of:

```text
10 * 300 + bounded retry delays
```

However, that conclusion requires `timeout_s` to be an absolute deadline for each full request. The current HTTPX timeout model does not provide that guarantee by itself.

A server can, for example, continue sending response data often enough to avoid a read-inactivity timeout while still taking longer than `timeout_s` overall. The exact behavior also depends on which timeout phase is active.

So the spec/TODO should not require a regression test asserting a guaranteed total wall-clock deadline unless the implementation introduces a separate total-deadline mechanism.

---

## 2. Questions for D4

### Q1 — Is an actual absolute operation deadline required?

Do we need a hard guarantee that one LLM provider operation cannot exceed a fixed elapsed wall-clock duration?

If **yes**, the current scalar HTTPX timeout plus retry-delay caps are insufficient. The implementation would need an explicit overall deadline mechanism independent of HTTPX's per-operation/inactivity timeouts.

If **no**, the D4 wording should be changed so it does not claim a hard total deadline.

### Q2 — Can D4 instead define a bounded configuration envelope?

For this robustness batch, is the intended contract actually:

- `timeout_s` is capped at 300 seconds;
- retry delay is capped at 60 seconds;
- retry attempts are capped at 10;
- scheduled retry sleeps therefore have a deterministic maximum;
- the HTTP client still uses HTTPX's normal timeout semantics;
- the resulting operation is operationally constrained, but **not claimed to have a strict absolute elapsed-time deadline**?

This appears to be the smallest correct change for the current batch.

### Q3 — What should the D4 regression test prove?

The current TODO says:

```text
Test asserts the enforced total wall-clock bound / cross-field contract
```

Should that be changed to explicit assertions such as:

1. `timeout_s <= 0` rejects;
2. `timeout_s > 300` rejects;
3. `retry_max_delay_s > 60` rejects;
4. `retry_max_delay_s < retry_base_delay_s` rejects;
5. `retry_max_attempts` remains constrained to 1..10;
6. the maximum scheduled retry-sleep sum is bounded by the configured/capped retry policy;
7. existing retryable-status behavior and ambiguous-delivery no-replay behavior remain unchanged?

Those assertions are true properties of the proposed implementation and do not depend on pretending HTTPX supplies an absolute request deadline.

---

## 3. Recommended resolution for this batch

Prefer the smaller, truthful contract:

### Keep

- synchronous LLM transport;
- per-session mutation lock held across the operation;
- no lock release during retry sleep;
- existing retryable HTTP status set;
- no replay after ambiguous POST delivery;
- configuration caps:
  - `0 < timeout_s <= 300`;
  - `retry_base_delay_s <= retry_max_delay_s <= 60`;
  - `1 <= retry_max_attempts <= 10`.

### Change the wording

Replace language like:

```text
hard wall-clock ceiling
```

and:

```text
the worst-case bound for one operation is
retry_max_attempts * timeout_s + sum(clamped backoff delays)
```

with wording such as:

```text
The configuration now places hard bounds on the provider timeout values,
retry-attempt count, and scheduled retry backoff. This bounds the configured
retry/backoff envelope and prevents arbitrarily large operator-configured waits.
HTTPX timeout_s retains its native connect/read/write/pool timeout semantics and
is not treated as an absolute end-to-end request deadline.
```

The completion evidence can still calculate and record the maximum configured retry-sleep budget, but it should not call the entire LLM operation absolutely wall-clock bounded unless a separate deadline mechanism is implemented.

---

## 4. Alternative if a true hard deadline is required

If the product requirement really is:

> one LLM-backed wizard mutation must terminate within a guaranteed elapsed wall-clock duration

then D4 needs a stronger design than the current spec describes.

That would require an explicit total-deadline mechanism that applies across:

- connection establishment;
- request write;
- response reading;
- all HTTP retries;
- all retry sleeps;
- structured-output repair attempts where applicable.

That is a larger transport/orchestration change and should be designed carefully. It may be better treated as its own concurrency/deadline hardening batch rather than quietly folded into this reliability pass.

If this alternative is chosen, the spec should define:

- the exact absolute deadline;
- where the deadline clock starts;
- how remaining time is propagated into each HTTP attempt;
- how retry sleeps are truncated by remaining time;
- the typed error returned when the total deadline is exhausted;
- interaction with the session mutation lock;
- tests using a controllable/fake clock rather than timing-sensitive wall-clock sleeps.

---

## 5. Requested spec/TODO edits before Ralph-loop implementation

Please resolve D4 by choosing one of these two contracts explicitly.

### Option A — Recommended for this batch

**Bounded configuration/retry envelope, no absolute wall-clock guarantee.**

Update the spec/TODO to:

- retain the proposed numeric configuration caps;
- retain the session lock;
- remove the claim that `retry_max_attempts * timeout_s + delays` is a guaranteed total operation deadline;
- describe `timeout_s` according to the underlying HTTPX timeout semantics;
- test range/cross-field validation and bounded retry-sleep scheduling rather than an absolute elapsed runtime.

### Option B — Larger change

**Introduce a true total operation deadline.**

If selected, define the deadline mechanism and its tests explicitly before implementation.

---

## 6. Readiness after this issue is resolved

Apart from this D4 timing-contract issue, the revised robustness spec/TODO appear sufficiently deterministic for a Ralph loop:

- D1 has a single shared non-multiplicative IR repair budget;
- D2 has narrow typed completion-outcome classification;
- D3 uses explicit `temperature_mode = send | omit` rather than model-name heuristics;
- D5 defines concrete count/size/pruning rules;
- D6 defines an explicit persisted `failure_kind` plus legacy behavior;
- D7 defines the exact settings-loader error contract;
- SHA discipline and targeted regression assertions are explicit.

Once the D4 wording/test contract is corrected or explicitly defended with a true-deadline design, there are no remaining review questions blocking implementation.
