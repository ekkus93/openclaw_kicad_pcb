# KiCad Web App Wizard/LLM Robustness TODO — 2026-08-10

Implementation checklist for:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md
```

Starting SHA:

```text
479465102f25f7dd85153477442c1c01b6dfbbe3
```

This batch fixes pre-existing wizard/LLM reliability defects. **Do not modify schematic
placement, orientation, wire routing, PCB layout, or Circuit IR semantics.** Each fixed
defect requires a targeted regression test. All gates must pass before commit.

---

# Phase 0 — Baseline and scope

- [ ] Confirm work is on `webapp`.
- [ ] Confirm spec and this TODO exist and reference each other.
- [ ] Record exact starting SHA (`479465102f25f7dd85153477442c1c01b6dfbbe3`).
- [ ] Confirm all quality gates are green at the starting SHA before changing code.
- [ ] Re-read the cited code for each defect to confirm it still reproduces.

---

# Phase 1 — D1: IR structural-JSON repair parity

Files: `src/kicad_pcb_web/services/wizard.py`, `src/kicad_pcb_web/services/_wizard_llm.py`.

- [ ] Reproduce: fake LLM returns malformed IR JSON once → session hard-fails today.
- [ ] Give IR generation a bounded structural-JSON repair budget derived from
      `ir_max_repair_rounds` (feed parse/validation error back into the next prompt).
- [ ] Preserve the existing semantic netlist repair loop (`prepare_netlist_dict` →
      `UserError`) — structural repair is additive.
- [ ] Ensure the combined loop is strictly bounded (no unbounded retry).
- [ ] Ensure budget exhaustion lands in a well-defined terminal state
      (`ir_needs_repair` / `failed`) with a cause that reflects structural vs semantic
      failure correctly.
- [ ] Decide whether `ir_max_repair_rounds` splits across structural + semantic or applies
      to each; document the decision in the completion doc.

Acceptance:

- [ ] Malformed-then-valid IR JSON recovers within budget.
- [ ] Malformed-every-round yields a deterministic terminal state, not an uncaught error.

---

# Phase 2 — D2: Non-string / truncated completion handling

Files: `src/kicad_pcb_web/services/_wizard_llm.py`, `services/llm/base.py`,
`services/llm/openai_client.py`.

- [ ] Reproduce: fake client returns `content=None` → uncaught `ToolError` escapes repair
      loop today (for both spec and IR paths).
- [ ] Handle null/non-string/empty content as a repairable structured-output failure inside
      the repair loop, within budget.
- [ ] Make behavior identical for spec and IR generation.
- [ ] On budget exhaustion, surface a typed "provider returned no usable content" error,
      distinct from "invalid JSON".
- [ ] Disposition the truncation (`finish_reason="length"`) re-request behavior: fix or
      explicitly document why re-requesting with the same `max_tokens` is acceptable.

Acceptance:

- [ ] `content=None` is retried, not hard-failed on first occurrence.
- [ ] Exhaustion error type is distinguishable from a JSON-decode failure.

---

# Phase 3 — D3: Provider parameter compatibility (temperature)

Files: `services/llm/openai_client.py`, `services/llm/llama_server_client.py`,
`services/llm/base.py`.

- [ ] Add a way to omit `temperature` from the payload (mirroring the existing
      `max_completion_tokens` handling).
- [ ] Choose and implement an explicit contract: omit when unset/not-applicable, or document
      the exact condition under which `temperature` is sent.
- [ ] No silent fallback — the decision is validated/asserted, not implicit.
- [ ] Confirm no behavior change for providers/models that accept `temperature` today.

Acceptance:

- [ ] Payload-builder test asserts `temperature` presence/absence per the contract.

---

# Phase 4 — D4: Retry sleep resource monopolization

Files: `services/llm/base.py`, `services/wizard.py` (lock scope).

- [ ] Establish the worst-case bound of `retry_max_attempts` × `retry_max_delay_s` vs
      `timeout_s` and `mutation_lock_timeout_s`.
- [ ] Either release the per-session mutation lock across the retry wait, or document the
      bounded worst case as understood and acceptable.
- [ ] Preserve idempotency (no POST replay on ambiguous delivery) and the retryable-status
      set.
- [ ] Do not convert the synchronous client to async in this batch.

Acceptance:

- [ ] Test asserting the bound, or that the lock is not held across the retry wait.

---

# Phase 5 — D5: Debug-artifact retention bound

Files: `services/_wizard_session_io.py`.

- [ ] Enforce a retention bound (per-session count and/or total size) when
      `debug_artifact_capture` is enabled.
- [ ] Preserve default-off behavior and the `0o700` private-directory guard.
- [ ] Leave un-redacted content as-is (out of scope); only bound accumulation.

Acceptance:

- [ ] Enabling capture over N attempts respects the bound.

---

# Phase 6 — D6: Failure-state clarity

Files: `services/wizard.py`, `services/wizard_models.py`, and frontend status handling
**only if** a wire enum value changes.

- [ ] Make "model-declared unsupported design" vs "operational failure" distinguishable
      without inspecting `error` — new terminal state/discriminator, or a documented+tested
      `error is None` invariant.
- [ ] Make the retry-gate behavior follow explicitly from that contract.
- [ ] If any frontend-consumed status enum value changes, update the SPA and rebuild/commit
      `src/kicad_pcb_web/static/spa/` in the same change.

Acceptance:

- [ ] Test distinguishes soft-failed from operational-failed and asserts retry-gate outcome.

---

# Phase 7 — D7: Settings hygiene

File: `src/kicad_pcb_web/settings.py`.

- [ ] Remove the redundant `base_url` re-validation in the openai branch (lines ~355-357).
- [ ] Wrap the load-time `jobs_dir.mkdir` failure as a typed `ValueError` (loader error
      contract) instead of a bare `OSError`.
- [ ] Reject empty-string `data_dir` explicitly (and audit any other field that silently
      resolves to CWD/default on empty string).

Acceptance:

- [ ] Empty `data_dir` env/config rejects with a clear error.
- [ ] Redundant validation removed with no behavior change (existing settings tests pass).

---

# Phase 8 — Tests and gates

- [ ] Each of D1–D7 has a targeted regression test in `tests/unit/` (real fakes over mocks).
- [ ] No existing test weakened to pass.
- [ ] `uv run --extra dev --extra web ruff check .` — clean.
- [ ] `uv run --extra dev --extra web ruff format --check .` — clean.
- [ ] `uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web` — clean.
- [ ] `uv run --extra dev --extra web python -m pytest tests/unit tests/web` — pass
      (record pass/skip counts and skip reasons).
- [ ] If frontend changed (D6 only): `npm --prefix frontend run lint`, `test:run`, `build`
      pass and the built SPA bundle is committed.

---

# Phase 9 — Completion evidence

- [ ] Create `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_COMPLETION_2026-08-10.md`.
- [ ] Record: starting SHA, accepting SHA, per-defect disposition (fixed / documented),
      the decisions made for D1/D3/D4/D6, local test pass/skip counts, and permanent CI
      run/job IDs.
- [ ] Confirm no placement/routing/layout/IR-semantics code was modified.
- [ ] Record any residual observation intentionally deferred.

---

# Definition of Done

- [ ] D1 — IR generation recovers from transient malformed/empty JSON within budget.
- [ ] D2 — null/non-string content is repairable, not an uncaught hard failure.
- [ ] D3 — provider-parameter compatibility handled deliberately (no silent non-retryable 400).
- [ ] D4 — retry wait does not monopolize the lock, or worst case is documented.
- [ ] D5 — debug-artifact output is bounded when enabled.
- [ ] D6 — the two `failed` meanings are unambiguous by contract and tested.
- [ ] D7 — settings hygiene items resolved.
- [ ] Every fix has a targeted regression test; no test weakened.
- [ ] ruff / format / mypy / `pytest tests/unit tests/web` all pass.
- [ ] No schematic/PCB placement, routing, or layout code modified.
- [ ] Permanent CI is 5/5 green on the accepting SHA and recorded in the completion doc.
