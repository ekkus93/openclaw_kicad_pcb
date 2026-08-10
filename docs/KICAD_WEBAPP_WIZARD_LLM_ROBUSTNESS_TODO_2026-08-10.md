# KiCad Web App Wizard/LLM Robustness TODO — 2026-08-10

Implementation checklist for:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md
```

Review resolved by:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_ANSWERS_2026-08-10.md
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_QUESTIONS_2026-08-10.md
```

SHA discipline:

```text
code-review baseline SHA : 479465102f25f7dd85153477442c1c01b6dfbbe3
implementation starting  : record webapp HEAD immediately before the first product-code change
```

This batch fixes pre-existing wizard/LLM reliability defects. **Do not modify schematic
placement, orientation, wire routing, PCB layout, or Circuit IR semantics.** Each fixed
defect requires targeted regression tests with the explicit assertions below. All gates must
pass before commit.

---

# Phase 0 — Baseline and scope

- [ ] Confirm work is on `webapp`.
- [ ] Confirm spec, this TODO, answers, and review-questions docs all cross-reference.
- [ ] Record the **implementation starting SHA** (webapp HEAD before first code change) —
      distinct from the code-review baseline `479465102...`.
- [ ] Confirm all quality gates are green at the implementation starting SHA before editing.
- [ ] Re-read the cited code for each defect to confirm it still reproduces.

---

# Phase 1 — D1: IR structural-JSON repair, single shared budget

Files: `src/kicad_pcb_web/services/wizard.py`, `src/kicad_pcb_web/services/_wizard_llm.py`.

- [ ] Reproduce: fake LLM returns malformed IR JSON once → session hard-fails today.
- [ ] Restructure the IR loop so a **single attempt counter** governs both structural and
      semantic failures — max LLM calls per `generate_ir` = `ir_max_repair_rounds + 1`.
- [ ] Do **not** just set `max_repairs=ir_max_repair_rounds` (that multiplies to 9 at
      default 2). Structural + semantic failures must share one counter.
- [ ] Feed the specific failure (parse/schema/no-content/netlist `UserError`) back into the
      next prompt.
- [ ] Terminal mapping: parseable-but-semantically-invalid (exhausted) → `ir_needs_repair`
      with preserved `prior_ir_json`; structural/no-content exhaustion → operational
      `failed` with a typed structural error.
- [ ] Ensure the terminal state reflects the true final cause (no mislabeling).

Acceptance:

- [ ] Malformed-then-valid IR JSON recovers within budget.
- [ ] A fully-failing request makes **exactly** `ir_max_repair_rounds + 1` LLM calls (assert
      the exact count).
- [ ] Semantic-only repeated failure → `ir_needs_repair`; structural exhaustion → `failed`.

---

# Phase 2 — D2: completion-shape classification (typed, narrow)

Files: `services/_wizard_llm.py`, `services/llm/base.py`, `services/llm/openai_client.py`,
`services/llm/llama_server_client.py`, error types module.

- [ ] Reproduce: fake client returns `content=None` → uncaught `ToolError` escapes today.
- [ ] Introduce distinct typed conditions / signals so the repair loop can classify without
      catching generic `ToolError`:
      - structured JSON parse failure — repairable;
      - schema-invalid structured output — repairable;
      - provider returned no usable content (`null`/empty) — repairable;
      - `finish_reason="length"` truncation — **terminal**, distinct typed error;
      - provider refusal / content-filter stop — **terminal**, distinct typed error;
      - generic provider/transport/protocol `ToolError` — **not** handled by repair loop.
- [ ] Repairable conditions retry within the shared budget (spec: `spec_max_repair_rounds+1`;
      IR: the Phase 1 shared budget).
- [ ] Truncation and refusal/content-filter surface distinct user-meaningful messages; do not
      re-request truncation with the same `max_tokens`.
- [ ] Identical behavior for spec and IR paths.

Acceptance (separate tests each):

- [ ] malformed JSON → valid recovery;
- [ ] empty/`null` content → valid recovery;
- [ ] repeated no-content → typed "no usable content" exhaustion;
- [ ] `finish_reason="length"` → terminal truncation disposition (distinct);
- [ ] content-filter/refusal → terminal disposition (distinct);
- [ ] generic transport `ToolError` is NOT absorbed by the structured-output repair loop.

---

# Phase 3 — D3: temperature capability contract

Files: `settings.py`, `services/llm/factory.py`, `services/llm/base.py`,
`services/llm/openai_client.py`, `services/llm/llama_server_client.py`, provenance writer.

- [ ] Add `[llm] temperature_mode` = `send | omit`, default `send`; add to `_LLM_CONFIG_KEYS`
      and the `KICAD_PCB_WEB_LLM_TEMPERATURE_MODE` env override; reject unknown values.
- [ ] `send` = current behavior (temperature present); `omit` = temperature absent from the
      payload dict entirely (not `null`).
- [ ] Both openai and llama-server payload builders honor the mode.
- [ ] Record `temperature_mode` in LLM provenance / config revision.
- [ ] No behavior change for existing configs (default `send`).
- [ ] Do NOT implement an `auto`/model-name heuristic (explicitly deferred).

Acceptance:

- [ ] Payload-builder tests assert exact dicts: temperature present under `send`, absent
      under `omit`, for both clients.
- [ ] Provenance records the mode; unknown mode rejects at settings load.

---

# Phase 4 — D4: bounded retry worst case (keep lock)

Files: `settings.py` (validation), `services/llm/base.py`, `services/wizard.py`.

- [ ] Keep the synchronous transport and the per-session mutation lock. Do **not** release
      the lock around retry sleeps (avoids a lost-update race without CAS — deferred).
- [ ] Add range/cross-field validation: `0 < timeout_s <= 300`;
      `retry_base_delay_s <= retry_max_delay_s <= 60`; keep `1 <= retry_max_attempts <= 10`.
- [ ] Document the worst-case wall-clock bound
      (`retry_max_attempts * timeout_s + sum(clamped delays)`) in the completion evidence.
- [ ] Preserve idempotency (no POST replay on ambiguous delivery) and the retryable-status
      set.

Acceptance:

- [ ] Tests assert out-of-range `timeout_s` and `retry_max_delay_s` reject.
- [ ] Test asserts the enforced total wall-clock bound / cross-field contract (not just one
      backoff-delay calc).

---

# Phase 5 — D5: deterministic debug-artifact retention

Files: `services/_wizard_session_io.py`.

- [ ] Retention applied **separately by stage** (`spec_*` vs `ir_*` prefixes).
- [ ] Per-session per-stage file-count cap = **20** (single tunable constant); prune
      oldest-first when exceeded.
- [ ] Per-session total-byte cap = **25 MiB** across stages; after count prune, delete
      oldest-first until under cap.
- [ ] Newest just-written artifact is always retained even if it alone exceeds the byte cap.
- [ ] Pruning/deletion failure logs at WARNING with the path and does not raise; never
      silently swallowed (no bare `except: pass`).
- [ ] Preserve default-off behavior and the `0o700` private-directory guard.

Acceptance:

- [ ] Test: oldest-first pruning at the count boundary;
- [ ] Test: byte-cap boundary pruning + newest-always-retained on oversize;
- [ ] Test: simulated deletion failure logs without raising.

---

# Phase 6 — D6: failure-kind discriminator

Files: `services/wizard.py`, `wizard_models.py` (top-level package, defines `WizardStatus`
and the session model); frontend only if a wire enum value changes (it should not).

- [ ] Add optional `failure_kind` to the session model: `unsupported_design | operational |
      generation`; default `None` for backward-compatible deserialization.
- [ ] Set `failure_kind` on every `status="failed"` write (soft unsupported, operational,
      generation).
- [ ] Keep wire `status="failed"`; do not add a new frontend-visible `WizardStatus` value.
- [ ] Legacy read helper: `failed` + `failure_kind is None` → interpret via historical rule
      (`error is None` ⇒ `unsupported_design`; `error is dict` ⇒ `operational`) for reads
      only. All new writes set the field.
- [ ] Retry gates key off `failure_kind` directly, not `error` shape.
- [ ] If any frontend-consumed status enum value changes (should not), update SPA and
      rebuild/commit `src/kicad_pcb_web/static/spa/`.

Acceptance:

- [ ] Tests: unsupported vs operational vs generation set correct `failure_kind` and drive
      the retry gate correctly.
- [ ] Test: legacy `failed` session with no `failure_kind` interpreted per the documented
      rule.

---

# Phase 7 — D7: settings hygiene, exact loader contract

File: `src/kicad_pcb_web/settings.py`.

- [ ] Remove the redundant openai `base_url` re-validation (lines ~355-357).
- [ ] Wrap load-time `jobs_dir.mkdir` failure specifically as **`ValueError`** (with path in
      the message).
- [ ] Reject empty-string `data_dir` explicitly (`ValueError`) for both env and TOML forms.
- [ ] Audit other path/string settings for empty-string-silently-accepted; disposition each
      in the completion evidence.

Acceptance:

- [ ] Test: `KICAD_PCB_WEB_DATA_DIR=""` rejects.
- [ ] Test: TOML `data_dir = ""` rejects (separate test).
- [ ] Redundant validation removed; existing settings tests still pass.

---

# Phase 8 — Tests and gates

- [ ] Each of D1–D7 has targeted regression tests in `tests/unit/` with the explicit
      assertions above (real fakes over mocks).
- [ ] No existing test weakened to pass.
- [ ] `uv run --extra dev --extra web ruff check .` — clean.
- [ ] `uv run --extra dev --extra web ruff format --check .` — clean.
- [ ] `uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web` — clean.
- [ ] `uv run --extra dev --extra web python -m pytest tests/unit tests/web` — pass
      (record pass/skip counts and skip reasons).
- [ ] If frontend changed (D6 only, unlikely): `npm --prefix frontend run lint`, `test:run`,
      `build` pass and the built SPA bundle is committed.

---

# Phase 9 — Completion evidence

- [ ] Create `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_COMPLETION_2026-08-10.md`.
- [ ] Record: code-review baseline SHA, true implementation starting SHA, accepting SHA,
      per-defect disposition (fixed / documented deviation), the D1 budget + terminal mapping,
      D2 classification, D3 `temperature_mode` + provenance, D4 worst-case bound, D5 retention
      constants, D6 discriminator + legacy rule, D7 empty-string field audit, local test
      pass/skip counts, and permanent CI run/job IDs.
- [ ] Confirm no placement/routing/layout/IR-semantics code was modified.
- [ ] Record any residual observation intentionally deferred (CAS concurrency, `auto`
      temperature registry).

---

# Definition of Done

- [ ] D1 — IR recovers within a single shared bounded budget (`ir_max_repair_rounds + 1`
      calls max); terminal mapping correct.
- [ ] D2 — completion outcomes classified into distinct typed conditions; generic `ToolError`
      never blanket-retried.
- [ ] D3 — `temperature_mode` controls payload inclusion; provenance recorded; default `send`
      unchanged.
- [ ] D4 — retry worst case bounded by validated caps and documented; lock retained.
- [ ] D5 — debug-artifact retention deterministic when enabled.
- [ ] D6 — `failure_kind` disambiguates terminal meanings; legacy behavior tested.
- [ ] D7 — settings hygiene resolved with exact `ValueError` loader contract.
- [ ] Every fix has targeted regression tests with explicit assertions; no test weakened.
- [ ] ruff / format / mypy / `pytest tests/unit tests/web` all pass.
- [ ] No schematic/PCB placement, routing, or layout code modified.
- [ ] Implementation starting SHA and an accepting SHA (permanent CI 5/5 green) recorded in
      the completion doc.
