# KiCad Web App Wizard/LLM Robustness — Answers to Review Questions — 2026-08-10

Responses to `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_QUESTIONS_2026-08-10.md`.
Every decision is folded into the revised
`docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md` (see "Resolved contracts") and
the revised TODO. This doc is the narrative rationale; the spec is authoritative.

Items marked **[proposed default]** are values chosen to remove ambiguity; they are safe to
adjust before implementation without changing the architecture.

A second round of review — `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_FOLLOWUP_2026-08-10.md`
and its duplicate `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_D4_REVIEW_QUESTIONS_2026-08-10.md`
— found one remaining issue in D4 after the first round of fixes. Section 11 below answers it.

---

## 1. Review baseline vs implementation starting SHA — accepted

Agreed. The spec now tracks three SHAs distinctly:

- **code-review baseline SHA**: `479465102f25f7dd85153477442c1c01b6dfbbe3` (where defects
  were observed);
- **planning/documentation head**: the spec/TODO/answers commits (not a code baseline);
- **implementation starting SHA**: webapp HEAD immediately before the first product-code
  change, recorded in the completion evidence as the true implementation head.

The completion doc records the true implementation head, not the review baseline.

---

## 2. D1 single, non-multiplicative retry budget — accepted (shared total)

1. **Shared total budget**, not separate per failure class.
2. **Max LLM invocations for one `generate_ir` = `ir_max_repair_rounds + 1`** (3 at the
   default of 2), regardless of whether an attempt failed on malformed JSON, schema-invalid
   output, no usable content, or semantic netlist validation. One attempt counter governs
   all classes — they must not multiply.
3. **Yes** — a regression test asserts that exact maximum call count for a fully-failing
   request.

Terminal mapping (adopted exactly as recommended):

- parseable IR that repeatedly fails **semantic** netlist validation → `ir_needs_repair`,
  preserving the last parsed IR;
- **malformed / schema-invalid / no-usable-content** exhaustion (no usable IR draft ever
  produced) → operational `failed` for `generate_ir`, with a typed structural error.

Implementation note: this requires restructuring the IR loop so the single counter wraps
both the structured-output call and the semantic-validation step — **not** setting
`max_repairs=ir_max_repair_rounds` (which would nest and multiply).

---

## 3. D2 classify outcomes; do not blanket-retry `ToolError` — accepted

1. Malformed/schema-invalid JSON → **repairable** within the structured-output budget. Yes.
2. `null`/empty content with no stronger provider reason → **repairable**, same bounded
   budget. Yes.
3. `finish_reason="length"` → **distinct terminal typed truncation**; we do **not** blindly
   re-request with the same `max_tokens`. (Auto-raising `max_tokens` and retrying is out of
   scope for this batch; the truncation is surfaced with a distinct message telling the
   operator to raise the limit.)
4. Provider refusal / content-filter stop → **terminal typed provider outcome**, not retried
   as transient.

Concern accepted: `content: null` is not always equivalent to a content-filter stop. The
classification separates "no usable content" (repairable) from "refusal/content-filter"
(terminal) using the provider `finish_reason` rather than lumping them.

Error classification adopted (spec table):

- structured JSON parse/schema failure — repairable;
- provider returned no usable content — repairable;
- response truncated (output limit) — terminal;
- provider refusal/content-filter — terminal;
- provider/transport/protocol failure (generic `ToolError`) — not handled by the repair loop.

Critically: the repair loop will **not** catch generic `ToolError`. The base client will
expose a narrower typed signal for "no usable content" so genuine transport/protocol errors
keep propagating and are not silently retried as structured-output problems.

Tests: all six cases in the review (malformed→valid, empty→valid, repeated no-content,
truncation, refusal, and "generic transport `ToolError` not absorbed") are required.

---

## 4. D3 explicit temperature policy — accepted (config mode, no heuristic)

1/2. The code determines applicability from an **explicit config setting**, not a model-name
heuristic and not a model capability registry (deferred). New `[llm]` key:

```text
temperature_mode = "send" | "omit"     # default "send"
```

- `send` **[proposed default]** — include `temperature` (current behavior; no silent change);
- `omit` — never include `temperature` in the payload (for reasoning-class models that reject
  non-default temperature).

An `auto` capability-registry mode is explicitly deferred to avoid an ad-hoc heuristic.

3. **Provenance**: `temperature_mode` is recorded in the LLM provenance / config revision so
   a session created under one policy is not silently reinterpreted under another (consistent
   with the existing provider/model/prompt provenance).

Tests assert exact payload dicts for both modes on both clients, provenance recording, and
rejection of unknown modes.

Note: default `send` means reasoning-class models still require the operator to set
`omit`. We keep `send` as default specifically to avoid any behavior change for existing
working configs; making `omit`/`auto` the default is the main open preference if you'd
rather protect reasoning models out of the box.

---

## 5. D4 keep lock, bound worst case — accepted (do not release lock)

1. **No**, this batch does **not** introduce revision/CAS semantics — so releasing the lock
   around the retry sleep is explicitly rejected (it would create the lost-update race you
   described).
2. **Yes** — the selected fix keeps the lock and imposes a hard, validated worst-case bound.
3. **Yes** — cross-field/range validation is added.

Adopted caps:

- `0 < timeout_s <= 300` **[proposed default ceiling]**;
- `retry_base_delay_s <= retry_max_delay_s <= 60` **[proposed default ceiling]**;
- existing `1 <= retry_max_attempts <= 10` retained.

Documented worst case per operation:
`retry_max_attempts * timeout_s + sum(clamped backoff delays)` — finite and knowable under
the caps. Accepted point: `mutation_lock_timeout_s` bounds only the *waiter*, not the holder;
that is why the holder-side wall-clock cap is the actual fix.

Out-of-lock provider execution + CAS is deferred to a future concurrency design. The D4 test
asserts the enforced bound/cross-field validation, not a single backoff calc.

---

## 6. D5 exact retention policy — accepted with concrete values

1. Per-session **per-stage file-count cap = 20** **[proposed default]**.
2. **Yes**, also a per-session **total-byte cap = 25 MiB** **[proposed default]**.
3. **Oldest-first** deletion.
4. Retention applies **separately by stage** (`spec_*` and `ir_*`), so one stage's burst
   cannot evict the other's.
5. **Oversize single artifact**: the newest just-written artifact is always retained even if
   it alone exceeds the byte cap; pruning never deletes the newest file.
6. **Pruning/deletion failure**: logged at WARNING with the path, does not raise (capture is
   best-effort and must never fail the wizard op), and is never silently swallowed.

Because these files hold unredacted prompts/completions, the "never silently swallow a
pruning failure" requirement is explicit.

---

## 7. D6 explicit discriminator, not error-shape — accepted

1. **Yes**, a new explicit discriminator.
2. A **new field**, not a new frontend-visible `WizardStatus` value.
3. **Legacy behavior**: the field is `Optional`, default `None`, so old persisted sessions
   deserialize. A read helper interprets legacy `failed` + `failure_kind is None` via the
   historical rule (`error is None` ⇒ `unsupported_design`; `error is dict` ⇒ `operational`)
   for reads only; new writes always set the field. This is tested, not guessed.
4. **Yes** — retry gates key off `failure_kind` directly, not `_failure_operation()`
   inferring from `error` shape.

Field: `failure_kind ∈ {unsupported_design, operational, generation}` **[proposed names]**.
Wire `status` stays `failed`. The contradiction you flagged (the spec said "without
inspecting `error`" but then offered an error-shape rule) is resolved: new sessions never
require inspecting `error`; the error-shape rule is used only to interpret legacy rows.

---

## 8. D7 tighten loader contract — accepted

1. **Yes** — the loader raises **`ValueError`** specifically (not "ValueError or established
   type"). The `jobs_dir.mkdir` failure is wrapped as `ValueError` with the path.
2. **Yes** — separate tests for `KICAD_PCB_WEB_DATA_DIR=""` and TOML `data_dir = ""`.
3. **Yes** — the empty-string audit of every path/string setting is dispositioned in the
   completion evidence (current expectation: `model`/`base_url`/`api_key` already fail via
   `_require_non_empty`; `system_prompt_version` via its own check; numeric fields fail
   coercion on `""`; `provider=""` is an invalid enum; `data_dir` is the one silent case
   being fixed — but each is confirmed and recorded, not assumed).

---

## 9. Strengthened regression assertions — accepted

All adopted into the spec's Testing requirements and the TODO acceptance criteria:

- **D1**: assert exact max LLM call count for a fully-failing IR request.
- **D2**: truncation and refusal tested separately from generic no-content; prove generic
  transport `ToolError` is not absorbed by the repair loop.
- **D3**: assert exact payload dicts for inclusion and omission, both clients.
- **D4**: assert the enforced total wall-clock bound / cross-field validation.
- **D5**: assert deterministic pruning order and boundary behavior.
- **D6**: unsupported/operational/generation outcomes, retry-gate behavior for each, and
  legacy-session interpretation.
- **D7**: empty env and TOML values tested separately.

---

## 10. Spec/TODO update status

All nine decision points are now resolved in the spec ("Resolved contracts") and TODO:

1. SHA distinction — done (spec "Starting point and SHA discipline").
2. D1 shared total budget + terminal mapping — done (Resolved contract 1).
3. D2 classification, repairable vs terminal — done (Resolved contract 2).
4. D3 `temperature_mode` policy + provenance — done (Resolved contract 3).
5. D4 keep-lock + validated configuration caps (bounded envelope, not a wall-clock
   deadline — see section 11) — done (Resolved contract 4).
6. D5 exact retention policy — done (Resolved contract 5).
7. D6 `failure_kind` discriminator + legacy rule — done (Resolved contract 6).
8. D7 exact `ValueError` loader contract — done (Resolved contract 7).
9. Strengthened test assertions — done (spec Testing requirements + TODO acceptance).

Open **preferences** (not blockers) flagged for the owner:

- D3: default `temperature_mode` = `send` (no behavior change) vs `omit`/`auto` (protect
  reasoning models by default).
- D5: the concrete caps (20 files/stage, 25 MiB/session).
- D4: the concrete ceilings (`timeout_s <= 300`, `retry_max_delay_s <= 60`).
- D6: the field/value names (`failure_kind`, `unsupported_design`/`operational`/`generation`).

With these resolved, the batch is deterministic enough to implement without making
architecture or failure-semantics decisions during the Ralph loop.

---

## 11. D4 follow-up: "hard wall-clock ceiling" was overstated — accepted, corrected

This is a valid catch. Confirmed directly against the code: `base.py:113` constructs
`httpx.Client(..., timeout=self.timeout_s, ...)` — a scalar HTTPX timeout, which sets
connect/read/write/pool *inactivity* timeouts. It is not an absolute end-to-end deadline for
the whole HTTP request; a server that keeps sending data within each inactivity window can
still exceed `timeout_s` in total elapsed time. My first-round D4 wording
("hard wall-clock ceiling", "worst-case bound for one operation is
`retry_max_attempts * timeout_s + sum(clamped backoff delays)`") therefore overstated what
the current transport guarantees, and the corresponding test requirement risked asserting a
timing property the implementation cannot actually prove.

### Q1 — Is an absolute operation deadline required?

**No.** This batch does not require a hard guarantee that one LLM provider operation cannot
exceed a fixed elapsed duration. **Option A** (bounded configuration/retry envelope) is
adopted, not Option B (a true deadline mechanism).

### Q2 — Bounded configuration envelope instead?

**Yes**, exactly as proposed: `timeout_s` capped at 300s, retry delay capped at 60s, retry
attempts capped at 1..10, so scheduled retry sleeps have a deterministic maximum — while
HTTPX retains its normal connect/read/write/pool timeout semantics and the operation is
**not** claimed to have a strict absolute elapsed-time deadline.

### Q3 — What should the D4 regression tests prove?

Adopted your suggested explicit assertions exactly:

1. `timeout_s <= 0` rejects;
2. `timeout_s > 300` rejects;
3. `retry_max_delay_s > 60` rejects;
4. `retry_max_delay_s < retry_base_delay_s` rejects;
5. `retry_max_attempts` stays constrained to 1..10;
6. the maximum scheduled retry-sleep sum from the configured/clamped backoff policy is
   deterministic and bounded as documented for a given valid configuration;
7. existing retryable-status behavior and ambiguous-delivery no-replay behavior are
   unchanged;
8. no test or documentation claims this is a hard total HTTP wall-clock deadline.

### Resolution applied

Both the spec (Resolved contract 4, its Testing requirements bullet, and its Definition of
Done bullet) and the TODO (Phase 4 and its Definition of Done bullet) were rewritten to:

- keep the synchronous transport, the per-session lock, and no lock release during retry
  sleeps (unchanged from round 1 — the lost-update-race concern that motivated keeping the
  lock still stands);
- retain the exact numeric caps (`0 < timeout_s <= 300`,
  `retry_base_delay_s <= retry_max_delay_s <= 60`, `1 <= retry_max_attempts <= 10`);
- rename the concept from "worst-case wall-clock bound" to **"configured retry/timeout
  envelope"** / **"maximum scheduled retry-sleep total"** throughout;
- add an explicit scope note in both docs quoting the exact `httpx.Client(...)` call and
  stating HTTPX's inactivity-timeout semantics do not constitute an end-to-end request
  deadline;
- explicitly list an absolute total-deadline mechanism (deadline clock across connect/write/
  read/all retries/all sleeps, remaining-time propagation, typed deadline-exhausted error,
  lock interaction, fake-clock tests) as **out of scope**, to be its own follow-up spec if
  ever required — not folded into this batch;
- replace the D4 test requirement with your eight explicit assertions.

No further D4 ambiguity remains: the batch keeps the smaller, truthful contract (Option A)
rather than expanding into a deadline-mechanism implementation.
