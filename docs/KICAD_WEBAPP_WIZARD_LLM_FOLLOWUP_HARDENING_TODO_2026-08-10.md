# KiCad Web App Wizard/LLM Follow-up Hardening TODO — 2026-08-10

Implementation checklist for:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SPEC_2026-08-10.md
```

This batch follows the closed `KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_*` batch (implementation
commit `e302d1b2b6e17bc6e62ae43c8f1100201f547e66`, accepted predecessor
`515f898b7a519cd9341eee605e18e9f1f5a9eed6`, permanent CI run `31375490922` 5/5 green). It
fixes one real functional bug (F1) and several design-debt/test-coverage gaps (F2–F8)
found by a five-agent post-implementation review of that batch. **No schematic placement,
orientation, wire routing, PCB layout, or unrelated Circuit IR semantics may be modified.
No already-confirmed D1–D7 contract may be altered.**

Review resolved by:

```text
docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_ANSWERS_2026-08-10.md
docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_REVIEW_QUESTIONS_2026-08-10.md
```

F2 and F6 below were revised from their initial versions: the review found F2's original
"remove the dead branch, all D2 tests stay unmodified" claim was internally contradictory
(the branch is not dead for Ollama or for `ScriptedClient`-based fakes), and F6's original
"catch `OSError`" fix would not have caught the actual `PersistenceError` shape the code
raises. Both are corrected in the contracts below.

SHA discipline:

```text
code-review baseline SHA : 3f088f7ad09a6ce254c2294d1b714faa20db9b43
implementation starting  : <fill in — webapp HEAD immediately before first product-code change>
implementation commit    : <fill in>
accepted predecessor     : <fill in>
permanent CI             : <fill in — run ID and 5/5 result>
```

---

# Phase 0 — Baseline and scope

- [ ] Confirm work is on `webapp`.
- [ ] Confirm spec and this TODO cross-reference and both are read in full before editing.
- [ ] Record the true implementation starting SHA.
- [ ] Confirm all quality gates are green at the implementation starting SHA before
      editing (ruff, ruff format, mypy, `pytest tests/unit tests/web`).
- [ ] Re-read the cited code for F1–F8 and confirm the reviewed mechanisms still
      reproduce (line numbers may have drifted since the review).

---

# Phase 1 — F1: reject `temperature_mode=omit` for providers that don't honor it

File: `src/kicad_pcb_web/settings.py`.

- [ ] Reproduce the baseline: `provider=ollama, temperature_mode=omit` currently passes
      settings validation and the Ollama payload still includes `temperature`.
- [ ] At settings-load time, reject `provider=ollama` combined with
      `temperature_mode=omit` with a `ValueError` naming both fields and stating that
      `omit` is not implemented for the `ollama` provider.
- [ ] `provider=ollama, temperature_mode=send` (default) is unaffected.
- [ ] `provider=openai`/`llama_server` with either mode is unaffected.
- [ ] Document in the `temperature_mode` field's docstring/comment which providers
      currently honor `omit` (`openai`, `llama_server`) and that `ollama` does not.

Acceptance:

- [ ] `provider=ollama, temperature_mode=omit` rejects at settings load.
- [ ] `provider=ollama, temperature_mode=send` continues to load and the built payload is
      unchanged (`temperature` present).
- [ ] Existing D3 tests for `openai`/`llama_server` remain green, unmodified.

---

# Phase 2 — F2: provider-owned classification; remove wizard-layer branch (Option A)

Files: `src/kicad_pcb_web/services/_wizard_llm.py`,
`tests/unit/test_wizard_llm_robustness.py`.

- [ ] Confirm the `finish_reason == "length"` / `finish_reason in {"content_filter",
      "refusal"}` branch in `_call_llm_for_json_once` is unreachable for
      `openai`/`llama_server` clients (the exception already fires inside
      `OpenAiLlmClient._parse_completion` before a completion object is returned), but
      is live for `ollama_client.py` and for any `ScriptedClient`-style fake returning a
      raw `LlmCompletion` with `finish_reason` set.
- [ ] Remove that branch; keep the `if not completion.content.strip():` empty-content
      check (still reachable for clients that don't self-classify).
- [ ] Add a code comment at the removal site (or in `ollama_client.py`) documenting —
      as a **behavior change**, not a preserved status quo — that Ollama
      truncation/refusal is no longer distinguished: it now surfaces as ordinary content
      that fails structured-output parsing (repairable `invalid_structured_output`,
      eventually `LLM_INVALID_STRUCTURED_OUTPUT` on exhaustion) instead of the terminal
      classification it previously received by coincidence via the removed branch.
- [ ] Do not implement Ollama-side finish-reason/refusal classification in this batch
      (explicitly out of scope per spec contract 2).
- [ ] Rewrite `test_d2_terminal_finish_reasons_do_not_repair` (and any other D2 test
      relying on `ScriptedClient` + raw `finish_reason` translation) so `ScriptedClient`
      is constructed with the typed exception directly
      (`ScriptedClient([LlmCompletionTruncatedError(...)])` /
      `ScriptedClient([LlmCompletionRefusedError(...)])`) instead of a raw completion
      carrying `finish_reason`.

Acceptance:

- [ ] The rewritten `test_d2_terminal_finish_reasons_do_not_repair` passes, asserting the
      same terminal/no-repair/single-call behavior under the new fake-client shape.
- [ ] All other, unrelated `test_d2_*` tests continue to pass unmodified.
- [ ] No claim in code comments, this TODO, or the completion evidence states the removal
      is behaviorally inert for Ollama, or that all D2 tests remained unmodified.

---

# Phase 3 — F3: real-provider-client classification tests

Files: `tests/web/test_web_llm_clients.py`.

- [ ] Add an `httpx.MockTransport` test asserting `finish_reason: "length"` raises
      `LlmCompletionTruncatedError` from `OpenAiLlmClient.complete()` directly.
- [ ] Add a test asserting `finish_reason: "content_filter"` raises
      `LlmCompletionRefusedError`.
- [ ] Add a test asserting `message.refusal: true` with `finish_reason: "stop"` raises
      `LlmCompletionRefusedError` (the `message.get("refusal")` branch specifically).
- [ ] Add a test asserting `content: null` with `finish_reason: "length"` raises
      `LlmCompletionTruncatedError` (not a content-coercion `ToolError`).
- [ ] Parametrize or duplicate the above for `LlamaServerLlmClient` if its payload
      construction differs enough to warrant separate assertions; otherwise a shared
      parametrization over both client classes is sufficient.

Acceptance:

- [ ] All four cases pass for `OpenAiLlmClient`.
- [ ] The same cases pass for `LlamaServerLlmClient`.
- [ ] No existing `test_web_llm_clients.py` test is weakened or removed.

---

# Phase 4 — F4: end-to-end web-layer tests for the remaining typed completion errors

Files: `tests/web/test_web_wizard.py`.

- [ ] Add a test with a scripted/fake client always returning no usable content; assert
      the wizard route surfaces `LLM_NO_USABLE_CONTENT` with `failure_kind=operational`.
- [ ] Add a test with a scripted/fake client raising truncation; assert the route
      surfaces `LLM_COMPLETION_TRUNCATED` with `failure_kind=operational` and that no
      retry/repair attempt occurred (exactly one LLM call).
- [ ] Add a test with a scripted/fake client raising refusal/content-filter; assert the
      route surfaces `LLM_COMPLETION_REFUSED` with `failure_kind=operational` and exactly
      one LLM call.

Acceptance:

- [ ] All three new tests pass.
- [ ] Existing `LLM_INVALID_STRUCTURED_OUTPUT` end-to-end test
      (`tests/web/test_web_wizard.py:902-928` or its current location) remains green,
      unmodified.

---

# Phase 5 — F5: end-to-end retry-gate regression

Files: `tests/unit/test_wizard_llm_robustness.py` and/or `tests/web/test_web_wizard.py`.

- [ ] Positive case: after an `operational` `generate_ir` failure, perform an actual
      second `generate_wizard_ir` call on the same session; assert it is accepted and
      proceeds (no "not retryable" rejection).
- [ ] Negative case: after a `generation` failure (from `generate_wizard_project`),
      perform an actual `generate_wizard_ir` call; assert the operation-identity rule
      gates it correctly (i.e. failure kind alone is not sufficient — operation identity
      matters too).
- [ ] Negative case: construct a legacy session with no `failure_kind` and `error is
      None` (legacy `unsupported_design` interpretation); assert the operational retry
      gate does **not** treat it as retryable.

Acceptance:

- [ ] All three cases pass via real second invocations, not just post-failure field
      assertions.
- [ ] Existing D6 tests remain green, unmodified.

---

# Phase 6 — F6: bound the debug-artifact write path with the exception types it actually raises

File: `src/kicad_pcb_web/services/_wizard_session_io.py`.

- [ ] Wrap the full body of `writer()` — `_ensure_private_directory(artifact_dir)`,
      `atomic_write_json(artifact_path, payload)`, and the subsequent
      `_prune_debug_artifacts` call — in a single `try/except (OSError,
      PersistenceError)`, logging at WARNING with the artifact path, stage, and error
      type, matching the existing `_unlink_debug_artifact` logging pattern in style.
- [ ] Confirm this also covers `_ensure_private_directory`'s own unwrapped
      `path.mkdir(..., mode=0o700)` call, which raises a bare `OSError` directly (in
      addition to its existing `chmod`-originated `PersistenceError`).
- [ ] Do not use a bare `except Exception`.
- [ ] Confirm the wizard operation completes/returns normally when a simulated failure
      occurs at any of the three stages (does not propagate as a hard failure).

Acceptance:

- [ ] A simulated `atomic_write_json` failure raising `PersistenceError` (its real
      translated exception shape) logs a WARNING and the wizard operation still
      completes normally.
- [ ] A simulated `_ensure_private_directory` failure — both the raw `mkdir` `OSError`
      case and the `chmod`-originated `PersistenceError` case — logs a WARNING and the
      wizard operation still completes normally.
- [ ] All existing D5 pruning/retention tests remain green, unmodified.

---

# Phase 7 — F7: type-safety and consistency cleanup

Files: `src/kicad_pcb_web/services/_wizard_llm.py`, `src/kicad_pcb_web/wizard_models.py`,
`src/kicad_pcb_web/services/wizard.py`.

- [ ] Change `_raise_structured_output_exhausted`'s return type from `-> None` to
      `-> NoReturn`.
- [ ] Replace `wizard_models.py`'s inline `Literal["send", "omit"] | None` for
      `temperature_mode` with `Optional[LlmTemperatureMode]` imported from `settings.py`;
      confirm no import cycle is introduced.
- [ ] Route `generate_wizard_project`'s failed-job branch through the existing
      `_set_error` helper using this exact mechanism: call `_set_error(...)` first, then
      apply `.model_copy(update={"latest_job_id": job.id})` to its result. Do not expand
      `_set_error`'s signature with a generic extra-updates parameter for this one caller.

Acceptance:

- [ ] mypy passes with the `NoReturn` annotation in place.
- [ ] A test asserts `WizardLlmProvenance.temperature_mode` still accepts only
      `"send"`/`"omit"`/`None` after the import change (guards against an accidental type
      widening).
- [ ] All existing D3/D6 tests remain green, unmodified; `generate_wizard_project`'s
      failure behavior (status, failure_kind, error payload, latest_job_id) is unchanged.

---

# Phase 8 — F8: residual settings-hygiene gaps

File: `src/kicad_pcb_web/settings.py`.

- [ ] `_resolve_config_path`: reject a non-string `data_dir` TOML value explicitly (raise
      `ValueError` naming the field and the received type) instead of silently
      `str()`-coercing it.
- [ ] Audit whether any other path-valued TOML setting flows through
      `_resolve_config_path` or an equivalent coercion and apply the same type check if
      so; record the disposition in the completion evidence.
- [ ] Add a targeted regression test for `provider="disabled"` combined with an invalid
      `base_url` value, asserting the current (correct, fail-closed) `ValueError`
      behavior at load. This sub-item documents and locks down an existing fail-closed
      policy — it is not a defect repair; do not describe it as one in the completion
      evidence.

Acceptance:

- [ ] `data_dir = 123` (or any non-string TOML value) rejects with a clear `ValueError`
      (this sub-item is a genuine defect repair).
- [ ] `provider="disabled"` with an invalid `base_url` rejects at load; behavior is now
      documented and tested rather than an untested side effect (this sub-item locks down
      existing behavior — no functional change).
- [ ] Existing settings tests remain green.

---

# Phase 9 — Tests and gates

- [ ] F1–F8 have targeted regressions per the Acceptance bullets above.
- [ ] No existing test was weakened to hide a failure.
- [ ] Ruff check — clean.
- [ ] Ruff format check — clean.
- [ ] mypy — clean.
- [ ] `tests/unit tests/web` — full suite passes; record exact local pass/skip counts and
      skip reasons (environment-dependent per CLAUDE.md — do not hard-code universal
      counts).
- [ ] Frontend conditional rebuild — expected **NOT APPLICABLE**; confirm no frontend
      source changed before skipping it.

---

# Phase 10 — Completion evidence

- [ ] Create `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_COMPLETION_2026-08-10.md`.
- [ ] Record review baseline, true implementation start, implementation commit, accepted
      predecessor, F1–F8 dispositions/decisions, local/bounded results, permanent CI
      run/job IDs, Python counts/coverage, and artifacts.
- [ ] Confirm no placement/routing/layout/IR-semantics code was modified and no
      already-confirmed D1–D7 contract was altered.
- [ ] Record any deliberately deferred items (e.g. Ollama-side `omit` support, Ollama-side
      finish-reason classification — both explicitly out of scope for this batch).

---

# Definition of Done

- [ ] F1 — `temperature_mode=omit` never a silent no-op for any **enabled** provider;
      rejected at load for `ollama`. `provider=disabled` is exempt (no LLM request).
- [ ] F2 — finish-reason/refusal classification is provider-owned (Option A); the
      wizard-layer branch is removed as an explicit, documented behavior change for
      Ollama and for raw-completion fakes — not claimed as dead-code-only or
      test-unmodified. `test_d2_terminal_finish_reasons_do_not_repair` is updated to
      construct `ScriptedClient` with the typed exception directly.
- [ ] F3 — real OpenAI/llama-server classification code has direct `MockTransport`
      regression coverage for truncation, content-filter, and message-level refusal.
- [ ] F4 — `LLM_NO_USABLE_CONTENT`, `LLM_COMPLETION_TRUNCATED`, `LLM_COMPLETION_REFUSED`
      each have an end-to-end web-layer regression test.
- [ ] F5 — the D6 retry gate has real positive-and-negative end-to-end retry regressions.
- [ ] F6 — debug-artifact capture cannot hard-fail a wizard operation at any stage
      (secure directory, write, prune, stat, or delete), catching both `OSError` and
      `PersistenceError` — the exception shapes the code actually raises.
- [ ] F7 — `NoReturn` typing, single-source `temperature_mode` literal, and
      `_set_error`-routed failure writes (via `_set_error` + `model_copy` for
      `latest_job_id`) are all in place.
- [ ] F8 — non-string `data_dir` is fixed and tested (genuine defect repair);
      `disabled`+invalid-`base_url` is documented and tested as existing fail-closed
      policy (not described as a defect repair).
- [ ] Every fix has targeted regression evidence; no failure was hidden by weakening
      tests.
- [ ] Ruff / format / mypy / `tests/unit tests/web` pass.
- [ ] No schematic/PCB placement, routing, layout, deterministic-engine, or unrelated IR
      semantic code modified; no already-confirmed D1–D7 contract altered.
- [ ] True implementation starting SHA recorded, and an accepting SHA passes permanent CI
      5/5 green, both recorded in the completion evidence document.
