# KiCad Web App Wizard/LLM Robustness — Review Follow-up — 2026-08-10

## Purpose

This note captures the remaining issue found after re-reading the revised:

- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_TODO_2026-08-10.md`

The earlier review questions were substantially resolved. D1, D2, D3, D5, D6, D7, SHA discipline, and the associated regression-test contracts are now sufficiently deterministic for implementation.

One D4 issue remains: the revised spec/TODO currently overstate what the existing HTTPX timeout configuration can guarantee about total wall-clock duration.

No product-code changes are requested by this document.

---

## D4 — The proposed "hard wall-clock ceiling" is not actually guaranteed by the current HTTPX timeout model

The revised spec says to retain the synchronous client and per-session lock, add caps such as:

```text
0 < timeout_s <= 300
retry_base_delay_s <= retry_max_delay_s <= 60
1 <= retry_max_attempts <= 10
```

and then document/test a worst-case operation bound as:

```text
retry_max_attempts * timeout_s + sum(clamped backoff delays)
```

The configuration caps themselves are useful and should remain.

The problem is the claim that this formula is a guaranteed hard total wall-clock ceiling for one HTTP operation.

### Why this is a problem

The current client creates HTTPX with a scalar timeout:

```python
httpx.Client(..., timeout=self.timeout_s, ...)
```

HTTPX timeouts are network-operation/inactivity timeouts (connect/read/write/pool semantics), not a single absolute deadline for the entire HTTP request.

In particular, a read timeout limits how long HTTPX waits for the next response data chunk; it does not necessarily cap the total lifetime of a response that continues to make progress within the configured timeout interval.

Therefore, with the current transport contract, this expression:

```text
retry_max_attempts * timeout_s + sum(backoff delays)
```

is a useful configured timeout/backoff envelope, but it is not a mathematically guaranteed upper bound on total wall-clock duration.

As a result, the current D4 wording/test requirement risks creating a false correctness claim in the completion evidence.

---

## Questions for D4

1. Do we actually require a **hard total operation deadline**, or only bounded configured timeout/backoff values?
2. If only bounded configuration is required, can the spec/TODO stop calling the formula a guaranteed wall-clock ceiling?
3. Can the D4 regression tests assert the validated configuration envelope rather than attempting to prove an absolute runtime guarantee the transport does not provide?
4. If a true hard deadline is required, is that intentionally being expanded into a larger transport/deadline implementation in this batch?

---

## Recommended resolution for this batch

Prefer the smaller, truthful contract:

- keep the synchronous HTTP transport;
- keep the per-session mutation lock;
- do **not** release the lock around retries without revision/CAS protection;
- retain the new validation caps:
  - `0 < timeout_s <= 300`;
  - `retry_max_delay_s <= 60` and `retry_max_delay_s >= retry_base_delay_s`;
  - `1 <= retry_max_attempts <= 10`;
- preserve the existing retryable HTTP status set and ambiguous-delivery no-replay behavior;
- describe the resulting formula as the **maximum configured timeout/backoff envelope**, not a guaranteed total wall-clock deadline;
- explicitly note that HTTPX's network-operation timeout semantics do not constitute an overall request deadline;
- keep true operation deadlines and out-of-lock execution as future transport/concurrency work.

### Suggested wording concept

Instead of:

> hard wall-clock ceiling

use something like:

> bounded configured retry/timeout envelope

And instead of claiming:

```text
operation duration <= retry_max_attempts * timeout_s + sum(clamped delays)
```

say:

```text
configured per-attempt timeout values and scheduled retry sleeps are bounded by:
retry_max_attempts * timeout_s + sum(clamped delays)
```

with an explicit statement that this is **not an overall HTTP request deadline guarantee**.

---

## Recommended D4 test contract

The tests should verify:

1. `timeout_s <= 0` rejects.
2. `timeout_s > 300` rejects.
3. `retry_max_delay_s > 60` rejects.
4. `retry_max_delay_s < retry_base_delay_s` rejects.
5. `retry_max_attempts` remains bounded at 1..10.
6. The maximum **scheduled retry-sleep total** produced by the configured exponential/clamped backoff policy is bounded as documented.
7. The calculated configured timeout/backoff envelope is deterministic for a given valid configuration.
8. No test or documentation claims this value is a hard total HTTP wall-clock deadline unless a separate absolute-deadline mechanism is actually implemented.

This still materially improves D4 by preventing extreme operator-configured timeout/backoff values while preserving the session-lock correctness and current idempotency behavior.

---

## Alternative if a true hard deadline is required

If the product requirement is genuinely:

> every LLM operation must terminate within an absolute wall-clock deadline

then the implementation needs a separate total-deadline mechanism rather than relying solely on `httpx.Client(timeout=...)`.

That should be specified explicitly, including:

- where the deadline is created;
- whether it covers all HTTP retries plus repair loops;
- how remaining time is propagated into each HTTP request;
- what typed error is raised when the deadline expires;
- how debug artifacts/session failure state record deadline exhaustion;
- interaction with the session mutation lock;
- tests using a controllable clock/transport rather than flaky real-time sleeps.

That is a materially larger transport/concurrency change and is not recommended for this focused robustness batch unless a hard deadline is truly required.

---

## Requested spec/TODO adjustment before Ralph-loop implementation

Please make one explicit D4 decision:

### Preferred

Revise D4 to require a **bounded configured timeout/backoff envelope**, retain the proposed caps, and remove the unsupported assertion of a guaranteed total wall-clock ceiling.

### Or

Explicitly expand D4 to implement a true absolute operation deadline with a separate deadline mechanism and corresponding tests.

Once that D4 point is resolved, I have no remaining blocking design question for the planned Ralph loop.
