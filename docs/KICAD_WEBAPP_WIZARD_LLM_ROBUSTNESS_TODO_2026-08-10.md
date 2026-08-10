# KiCad Web App Wizard/LLM Robustness TODO — 2026-08-10

Implementation checklist for:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md
```

Review resolved by:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_ANSWERS_2026-08-10.md
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_QUESTIONS_2026-08-10.md
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_REVIEW_FOLLOWUP_2026-08-10.md
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_D4_REVIEW_QUESTIONS_2026-08-10.md
```

SHA discipline:

```text
code-review baseline SHA : 479465102f25f7dd85153477442c1c01b6dfbbe3
implementation starting  : b5855cc3f0e984f3a189b17fec37ac1cf8685de1
implementation commit    : e302d1b2b6e17bc6e62ae43c8f1100201f547e66
accepted predecessor     : 515f898b7a519cd9341eee605e18e9f1f5a9eed6
permanent CI             : run 31375490922 — 5/5 success
```

This batch fixes pre-existing wizard/LLM reliability defects. **No schematic placement,
orientation, wire routing, PCB layout, or unrelated Circuit IR semantics were modified.**
Targeted regression coverage was added for every defect class, and permanent CI passed on
the accepted implementation/evidence predecessor.

---

# Phase 0 — Baseline and scope

- [x] Confirm work is on `webapp`.
- [x] Confirm spec, this TODO, answers, and review-questions docs all cross-reference.
- [x] Record the true implementation starting SHA — `b5855cc3f0e984f3a189b17fec37ac1cf8685de1`.
- [x] Confirm all quality gates are green at the implementation starting SHA before editing — permanent run `31370387661`, 5/5 success.
- [x] Re-read the cited code for each defect and confirm the reviewed mechanisms still reproduce.

---

# Phase 1 — D1: IR structural-JSON repair, single shared budget

Files: `src/kicad_pcb_web/services/wizard.py`, `src/kicad_pcb_web/services/_wizard_llm.py`.

- [x] Reproduce the malformed-IR first-attempt hard failure from the baseline.
- [x] Restructure the IR loop so a **single attempt counter** governs structural and semantic failures — max LLM calls = `ir_max_repair_rounds + 1`.
- [x] Do not use nested `max_repairs=ir_max_repair_rounds`; structural + semantic failures share one counter.
- [x] Feed the specific parse/schema/no-content/netlist-validation failure into the next repair prompt.
- [x] Semantic exhaustion → `ir_needs_repair` with preserved last parseable IR; structural/no-content exhaustion → operational `failed` with a typed structural error.
- [x] Terminal state reflects the true final cause.

Acceptance:

- [x] Malformed-then-valid IR JSON recovers within budget.
- [x] Fully failing structural request makes exactly `ir_max_repair_rounds + 1` LLM calls.
- [x] Semantic-only repeated failure → `ir_needs_repair`; structural exhaustion → `failed`.

---

# Phase 2 — D2: completion-shape classification (typed, narrow)

Files: `services/_wizard_llm.py`, `services/llm/base.py`, `services/llm/openai_client.py`, `services/llm/llama_server_client.py`, web error types.

- [x] Reproduce the baseline no-content/`ToolError` escape.
- [x] Introduce narrow typed conditions/signals rather than catching generic `ToolError`:
  - structured JSON parse failure — repairable;
  - schema-invalid structured output — repairable;
  - provider returned no usable content — repairable;
  - `finish_reason="length"` — terminal typed truncation;
  - provider refusal/content-filter — terminal typed refusal;
  - generic provider/transport/protocol `ToolError` — not handled by the structured-output repair loop.
- [x] Repairable conditions consume the configured spec or shared-IR budget.
- [x] Truncation and refusal/content-filter surface distinct user-meaningful typed outcomes and are not blindly re-requested.
- [x] Spec and IR paths use the same structured-output classification contract.

Acceptance:

- [x] Malformed JSON → valid recovery.
- [x] Empty/no-content → valid recovery.
- [x] Repeated no-content → typed `LLM_NO_USABLE_CONTENT` exhaustion.
- [x] `finish_reason="length"` → distinct terminal truncation disposition.
- [x] Content-filter/refusal → distinct terminal disposition.
- [x] Generic transport/provider `ToolError` is not absorbed by the structured-output repair loop.

---

# Phase 3 — D3: temperature capability contract

Files: `settings.py`, `services/llm/factory.py`, `services/llm/base.py`, `services/llm/openai_client.py`, `services/llm/llama_server_client.py`, provenance writer.

- [x] Add `[llm] temperature_mode = send | omit`, default `send`, plus `KICAD_PCB_WEB_LLM_TEMPERATURE_MODE`; reject unknown values.
- [x] `send` preserves current behavior; `omit` removes the `temperature` field entirely.
- [x] OpenAI and llama-server payload builders honor the mode.
- [x] Record `temperature_mode` in LLM provenance/config revision.
- [x] Preserve existing configurations through default `send`.
- [x] Do not implement an `auto`/model-name heuristic.

Acceptance:

- [x] Payload tests assert exact inclusion/omission behavior for both clients.
- [x] Provenance changes with the mode; unknown mode rejects at settings load.

---

# Phase 4 — D4: bounded retry/timeout configuration envelope (keep lock)

Files: `settings.py`, `services/llm/base.py`, `services/wizard.py`.

The implemented contract deliberately bounds configuration/backoff rather than claiming an
absolute end-to-end HTTP deadline. HTTPX retains connect/read/write/pool inactivity timeout
semantics.

- [x] Keep synchronous transport and the per-session mutation lock; do not release the lock during retry sleeps without revision/CAS protection.
- [x] Validate `0 < timeout_s <= 300`, `retry_base_delay_s <= retry_max_delay_s <= 60`, and `1 <= retry_max_attempts <= 10`.
- [x] Document the maximum scheduled retry-sleep total as a configured retry/backoff bound, not a total HTTP wall-clock deadline.
- [x] Preserve the retryable-status set and no-replay behavior after ambiguous POST delivery.

Acceptance:

- [x] `timeout_s <= 0` rejects.
- [x] `timeout_s > 300` rejects.
- [x] `retry_max_delay_s > 60` rejects.
- [x] `retry_max_delay_s < retry_base_delay_s` rejects.
- [x] `retry_max_attempts` remains constrained to 1..10.
- [x] Maximum scheduled retry-sleep total is deterministic for a valid configuration; maximum configured conservative bound recorded as `(10 - 1) * 60 = 540s`.
- [x] Existing retryable statuses and ambiguous-delivery no-replay behavior remain unchanged.
- [x] No test/code/doc claims an absolute total HTTP deadline.

---

# Phase 5 — D5: deterministic debug-artifact retention

File: `services/_wizard_session_io.py`.

- [x] Retention applied separately by stage (`spec_*` and `ir_*`).
- [x] Per-session/per-stage file-count cap = `20`; oldest-first pruning.
- [x] Per-session total-byte cap = `25 MiB` across stages.
- [x] Newest artifact is retained even if it alone exceeds the byte cap.
- [x] Pruning/stat/deletion failure logs at WARNING with path/error information and does not fail the wizard operation; no silent swallow.
- [x] Preserve default-off capture and private-directory guard.

Acceptance:

- [x] Oldest-first pruning at the count boundary.
- [x] Byte-cap pruning and newest-always-retained oversize behavior.
- [x] Simulated deletion failure warns without raising.

---

# Phase 6 — D6: failure-kind discriminator

Files: `services/wizard.py`, `wizard_models.py`.

- [x] Add optional persisted `failure_kind`: `unsupported_design | operational | generation`.
- [x] Set `failure_kind` on every new `status="failed"` write.
- [x] Keep wire `status="failed"`; no new frontend-visible `WizardStatus` value.
- [x] Legacy `failed` session without `failure_kind` uses the documented read fallback (`error is None` → unsupported; `error is dict` → operational).
- [x] Retry gates use effective failure kind plus operation identity rather than error-shape inference for new writes.
- [x] Frontend rebuild for a status-enum change — **NOT APPLICABLE**; no frontend-consumed status enum changed and no frontend source/SPA bundle was modified.

Acceptance:

- [x] Unsupported, operational, and generation outcomes set the correct failure kind and retry behavior.
- [x] Legacy failed session without the field is interpreted per the documented compatibility rule.

---

# Phase 7 — D7: settings hygiene, exact loader contract

File: `src/kicad_pcb_web/settings.py`.

- [x] Remove redundant OpenAI `base_url` re-validation.
- [x] Wrap load-time `jobs_dir.mkdir` failure specifically as `ValueError` including the path.
- [x] Reject empty-string `data_dir` for env and TOML forms.
- [x] Audit other path/string settings for silent empty-string behavior and record disposition in the completion evidence.

Acceptance:

- [x] `KICAD_PCB_WEB_DATA_DIR=""` rejects.
- [x] TOML `data_dir = ""` rejects separately.
- [x] Existing settings behavior/tests remain green after redundant validation removal.

---

# Phase 8 — Tests and gates

- [x] D1-D7 have targeted regressions, primarily in `tests/unit/test_wizard_llm_robustness.py`, with one existing web regression strengthened for the new typed structural error.
- [x] No existing test was weakened to hide a failure; the affected web assertion was made more specific (`LLM_INVALID_STRUCTURED_OUTPUT`, `failure_kind=operational`).
- [x] Ruff check — clean.
- [x] Ruff format check — clean; permanent acceptance reports `461 files already formatted`.
- [x] mypy — clean across `213 source files`.
- [x] `tests/unit tests/web` — permanent acceptance: **2753 collected, 2746 passed, 7 skipped**, coverage **90.85%**.
- [x] Frontend conditional rebuild — **NOT APPLICABLE as an implementation requirement**; no frontend source changed. Permanent CI nevertheless ran the normal frontend job successfully.
- [x] Browser smoke — **11 passed, 1 skipped**; the one skip is the explicitly opt-in live-LLM wizard test requiring `RUN_E2E_WIZARD_TESTS=1`.

---

# Phase 9 — Completion evidence

- [x] Create `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_COMPLETION_2026-08-10.md`.
- [x] Record review baseline, true implementation start, implementation commit, accepted predecessor, D1-D7 dispositions/decisions, local/bounded results, permanent CI run/job IDs, Python counts/coverage, browser counts, and artifacts.
- [x] Confirm no placement/routing/layout/IR-semantics code was modified.
- [x] Record deliberately deferred items: revision/CAS concurrency, automatic temperature capability registry, absolute total-deadline mechanism, and raw debug-artifact redaction.

---

# Definition of Done

- [x] D1 — single shared bounded IR repair budget and correct terminal mapping.
- [x] D2 — distinct typed completion outcomes; no blanket generic `ToolError` repair.
- [x] D3 — explicit `temperature_mode`, provenance, default `send` compatibility.
- [x] D4 — retry/timeout configuration envelope bounded and documented truthfully; lock retained.
- [x] D5 — deterministic retention when debug capture is enabled.
- [x] D6 — persisted `failure_kind` disambiguation with tested legacy behavior.
- [x] D7 — settings hygiene with exact `ValueError` loader contract.
- [x] Every fix has targeted regression evidence; no failure was hidden by weakening tests.
- [x] Ruff / format / mypy / `tests/unit tests/web` pass.
- [x] No schematic/PCB placement, routing, layout, deterministic-engine, or unrelated IR semantic code modified.
- [x] True implementation starting SHA recorded and implementation/evidence predecessor `515f898b7a519cd9341eee605e18e9f1f5a9eed6` passed permanent CI run `31375490922` **5/5 green**.

## Final external exact-head gate

- [ ] Permanent CI for the exact documentation head containing this finalized checklist must complete 5/5 green. This is intentionally closed by the final Ralph-loop report and the repository CI-status bridge rather than another self-referential documentation commit.
