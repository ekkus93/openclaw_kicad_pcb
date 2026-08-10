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
docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SECOND_REVIEW_QUESTIONS_2026-08-10.md
```

F2 and F6 below were revised across two review rounds. First round: F2's original "remove
the dead branch, all D2 tests stay unmodified" claim was internally contradictory (the
branch is not dead for Ollama or for `ScriptedClient`-based fakes), and F6's original
"catch `OSError`" fix would not have caught the actual `PersistenceError` shape the code
raises. Second round: F2's *corrected* removal plan (Option A) was itself found to
introduce a new silent-acceptance regression for Ollama — removing the branch without a
provider-owned Ollama replacement would let a truncated/refused-but-schema-valid Ollama
response be silently accepted. F2 now adopts Option B: retain the generic classifier as
documented interim technical debt rather than remove it. All corrections are reflected in
the contracts below.

SHA discipline:

```text
code-review baseline SHA : 3f088f7ad09a6ce254c2294d1b714faa20db9b43
implementation starting  : 0158581bb3f62cbc6eec7a6360b3489508e51ca2
implementation commit    : a680403e4df3b3feff70b398c063bde65beb81bc
accepted predecessor     : 6e9b07a7db58fc2c9644756778b06f89262c24a3
permanent CI             : 31387498004 — 5/5 green
```

---

# Phase 0 — Baseline and scope

- [x] Confirm work is on `webapp`.
- [x] Confirm spec and this TODO cross-reference and both are read in full before editing.
- [x] Record the true implementation starting SHA.
- [x] Confirm all quality gates are green at the implementation starting SHA before
      editing (ruff, ruff format, mypy, `pytest tests/unit tests/web`).
- [x] Re-read the cited code for F1–F8 and confirm the reviewed mechanisms still
      reproduce (line numbers may have drifted since the review).

---

# Phase 1 — F1: reject `temperature_mode=omit` for providers that don't honor it

File: `src/kicad_pcb_web/settings.py`.

- [x] Reproduce the baseline: `provider=ollama, temperature_mode=omit` currently passes
      settings validation and the Ollama payload still includes `temperature`.
- [x] At settings-load time, reject `provider=ollama` combined with
      `temperature_mode=omit` with a `ValueError` naming both fields and stating that
      `omit` is not implemented for the `ollama` provider.
- [x] `provider=ollama, temperature_mode=send` (default) is unaffected.
- [x] `provider=openai`/`llama_server` with either mode is unaffected.
- [x] Document in the `temperature_mode` field's docstring/comment which providers
      currently honor `omit` (`openai`, `llama_server`) and that `ollama` does not.

Acceptance:

- [x] `provider=ollama, temperature_mode=omit` rejects at settings load.
- [x] `provider=ollama, temperature_mode=send` continues to load and the built payload is
      unchanged (`temperature` present).
- [x] Existing D3 tests for `openai`/`llama_server` remain green, unmodified.

---

# Phase 2 — F2: retain the generic classifier as documented interim protection (Option B)

Files: `src/kicad_pcb_web/services/_wizard_llm.py`,
`tests/unit/test_wizard_llm_robustness.py`.

- [x] Confirm the `finish_reason == "length"` / `finish_reason in {"content_filter",
      "refusal"}` branch in `_call_llm_for_json_once` is unreachable for
      `openai`/`llama_server` clients (the exception already fires inside
      `OpenAiLlmClient._parse_completion` before a completion object is returned), and
      confirm `ollama_client.py` has no `finish_reason`/`done_reason` classification of
      its own — the generic branch is currently the **only** protection against a
      truncated/refused-but-schema-valid Ollama response being silently accepted.
- [x] Make **no code change** to the branch itself — do not remove it.
- [x] Add a code comment at the branch explaining why it is intentionally retained: dead
      code for `openai`/`llama_server`, but the sole fail-closed protection for
      `ollama_client.py` and any other self-unclassified client, pending a future
      Ollama-owned classifier. Label this as documented, intentional, temporary technical
      debt — not an oversight.
- [x] Do not implement Ollama-side finish-reason/refusal classification in this batch
      (explicitly out of scope per spec contract 2; recorded as a deliberately deferred
      item in the completion evidence).
- [x] Add a new test driving the real `OllamaLlmClient.complete()` via
      `httpx.MockTransport` with a raw `/api/chat` response carrying `"done_reason":
      "length"`, through `_call_llm_for_json`/`_call_llm_for_json_once`, asserting the
      generic classifier still raises `LlmCompletionTruncatedError` end-to-end through
      the real Ollama parsing path. The regression uses schema-valid JSON content so it
      exercises the exact silent-success risk the retained classifier prevents.

Acceptance:

- [x] No existing D2 test changes; `test_d2_terminal_finish_reasons_do_not_repair` and
      all other `test_d2_*` tests pass completely unmodified.
- [x] The new Ollama-specific `MockTransport` test passes, proving the real (not just
      `ScriptedClient`-faked) Ollama code path is still protected end-to-end.
- [x] No claim in code comments, this TODO, or the completion evidence states that
      Ollama has its own provider-owned classifier, or that the two-layer classification
      state is anything other than documented interim technical debt.

---

# Phase 3 — F3: real-provider-client classification tests

Files: `tests/web/test_web_llm_clients.py`.

- [x] Add an `httpx.MockTransport` test asserting `finish_reason: "length"` raises
      `LlmCompletionTruncatedError` from `OpenAiLlmClient.complete()` directly.
- [x] Add a test asserting `finish_reason: "content_filter"` raises
      `LlmCompletionRefusedError`.
- [x] Add a test asserting `message.refusal: true` with `finish_reason: "stop"` raises
      `LlmCompletionRefusedError` (the `message.get("refusal")` branch specifically).
- [x] Add a test asserting `content: null` with `finish_reason: "length"` raises
      `LlmCompletionTruncatedError` (not a content-coercion `ToolError`).
- [x] Parametrize or duplicate the above for `LlamaServerLlmClient` if its payload
      construction differs enough to warrant separate assertions; a shared parametrized
      test now covers both client classes.

Acceptance:

- [x] All four cases pass for `OpenAiLlmClient`.
- [x] The same cases pass for `LlamaServerLlmClient`.
- [x] No existing `test_web_llm_clients.py` test is weakened or removed.

---

# Phase 4 — F4: end-to-end web-layer tests for the remaining typed completion errors

Files: `tests/web/test_web_wizard.py`.

- [x] Add a test with a scripted/fake client always returning no usable content; assert
      the wizard route surfaces `LLM_NO_USABLE_CONTENT` with `failure_kind=operational`.
- [x] Add a test with a scripted/fake client raising truncation; assert the route
      surfaces `LLM_COMPLETION_TRUNCATED` with `failure_kind=operational` and that no
      retry/repair attempt occurred (exactly one LLM call).
- [x] Add a test with a scripted/fake client raising refusal/content-filter; assert the
      route surfaces `LLM_COMPLETION_REFUSED` with `failure_kind=operational` and exactly
      one LLM call.

Acceptance:

- [x] All three new tests pass.
- [x] Existing `LLM_INVALID_STRUCTURED_OUTPUT` end-to-end test remains green and
      unmodified.

---

# Phase 5 — F5: end-to-end retry-gate regression

Files: `tests/unit/test_wizard_llm_robustness.py` and/or `tests/web/test_web_wizard.py`.

- [x] Positive case: after an `operational` `generate_ir` failure, perform an actual
      second `generate_wizard_ir` call on the same session; assert it is accepted and
      proceeds (no "not retryable" rejection).
- [x] Negative case: after a `generation` failure (from `generate_wizard_project`),
      perform an actual `generate_wizard_ir` call; assert the operation-identity rule
      gates it correctly (i.e. failure kind alone is not sufficient — operation identity
      matters too).
- [x] Negative case: construct a legacy session with no `failure_kind` and `error is
      None` (legacy `unsupported_design` interpretation); assert the operational retry
      gate does **not** treat it as retryable.

Acceptance:

- [x] All three cases pass via real second invocations, not just post-failure field
      assertions.
- [x] Existing D6 tests remain green, unmodified.

---

# Phase 6 — F6: bound the debug-artifact write path with the exception types it actually raises

File: `src/kicad_pcb_web/services/_wizard_session_io.py`.

- [x] Wrap the full body of `writer()` — `_ensure_private_directory(artifact_dir)`,
      `atomic_write_json(artifact_path, payload)`, and the subsequent
      `_prune_debug_artifacts` call — in a single `try/except (OSError,
      PersistenceError)`, logging at WARNING with the artifact path, stage, and error
      type, matching the existing `_unlink_debug_artifact` logging pattern in style.
- [x] Confirm this also covers `_ensure_private_directory`'s own unwrapped
      `path.mkdir(..., mode=0o700)` call, which raises a bare `OSError` directly (in
      addition to its existing `chmod`-originated `PersistenceError`).
- [x] Do not use a bare `except Exception`.
- [x] Confirm the wizard operation completes/returns normally when a simulated failure
      occurs at any of the three stages (does not propagate as a hard failure).

Acceptance:

- [x] A simulated `atomic_write_json` failure raising `PersistenceError` (its real
      translated exception shape) logs a WARNING and the wizard operation still
      completes normally.
- [x] A simulated `_ensure_private_directory` failure — both the raw `mkdir` `OSError`
      case and the `chmod`-originated `PersistenceError` case — logs a WARNING and the
      wizard operation still completes normally.
- [x] All existing D5 pruning/retention tests remain green, unmodified.

---

# Phase 7 — F7: type-safety and consistency cleanup

Files: `src/kicad_pcb_web/services/_wizard_llm.py`, `src/kicad_pcb_web/wizard_models.py`,
`src/kicad_pcb_web/services/wizard.py`.

- [x] Change `_raise_structured_output_exhausted`'s return type from `-> None` to
      `-> NoReturn`.
- [x] Replace `wizard_models.py`'s inline `Literal["send", "omit"] | None` for
      `temperature_mode` with `Optional[LlmTemperatureMode]` imported from `settings.py`;
      confirm no import cycle is introduced.
- [x] Route `generate_wizard_project`'s failed-job branch through the existing
      `_set_error` helper using this exact mechanism: call `_set_error(...)` first, then
      apply `.model_copy(update={"latest_job_id": job.id})` to its result. Do not expand
      `_set_error`'s signature with a generic extra-updates parameter for this one caller.

Acceptance:

- [x] mypy passes with the `NoReturn` annotation in place.
- [x] A test asserts `WizardLlmProvenance.temperature_mode` still accepts only
      `"send"`/`"omit"`/`None` after the import change (guards against an accidental type
      widening).
- [x] All existing D3/D6 tests remain green, unmodified; `generate_wizard_project`'s
      failure behavior (status, failure_kind, error payload, latest_job_id) is unchanged.

---

# Phase 8 — F8: residual settings-hygiene gaps

File: `src/kicad_pcb_web/settings.py`.

- [x] `_resolve_config_path`: reject a non-string `data_dir` TOML value explicitly (raise
      `ValueError` naming the field and the received type) instead of silently
      `str()`-coercing it.
- [x] Audit whether any other path-valued TOML setting flows through
      `_resolve_config_path` or an equivalent coercion and apply the same type check if
      so; disposition: `web.data_dir` is the path-valued TOML caller of this resolver and
      no additional path-valued TOML caller requiring this fix was found.
- [x] Add a targeted regression test for `provider="disabled"` combined with an invalid
      `base_url` value, asserting the current (correct, fail-closed) `ValueError`
      behavior at load. This sub-item documents and locks down an existing fail-closed
      policy — it is not a defect repair.

Acceptance:

- [x] `data_dir = 123` (or any non-string TOML value) rejects with a clear `ValueError`
      (this sub-item is a genuine defect repair).
- [x] `provider="disabled"` with an invalid `base_url` rejects at load; behavior is now
      documented and tested rather than an untested side effect (this sub-item locks down
      existing behavior — no functional change).
- [x] Existing settings tests remain green.

---

# Phase 9 — Tests and gates

- [x] F1–F8 have targeted regressions per the Acceptance bullets above.
- [x] No existing test was weakened to hide a failure.
- [x] Ruff check — clean.
- [x] Ruff format check — clean (461 files already formatted in permanent CI).
- [x] mypy — clean (213 source files in permanent CI).
- [x] `tests/unit tests/web` — full permanent suite passed: 2776 collected, 2769 passed,
      7 skipped; total coverage 90.95%. Skip count is environment/opt-in dependent and is
      recorded in the completion evidence rather than treated as a universal invariant.
- [x] Frontend conditional rebuild — **NOT APPLICABLE** to the implementation scope;
      confirmed no frontend source or committed SPA bundle changed. The repository's
      normal permanent CI nevertheless reran frontend lint/unit/build successfully.

---

# Phase 10 — Completion evidence

- [x] Create `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_COMPLETION_2026-08-10.md`.
- [x] Record review baseline, true implementation start, implementation commit, accepted
      predecessor, F1–F8 dispositions/decisions, local/bounded results, permanent CI
      run/job IDs, Python counts/coverage, and artifacts.
- [x] Confirm no placement/routing/layout/IR-semantics code was modified and no
      already-confirmed D1–D7 contract was altered.
- [x] Record deliberately deferred items: Ollama-side `temperature_mode=omit` support and
      an Ollama-owned finish-reason classifier/removal of the generic interim fallback.

---

# Definition of Done

- [x] F1 — `temperature_mode=omit` never a silent no-op for any **enabled** provider;
      rejected at load for `ollama`. `provider=disabled` is exempt (no LLM request).
- [x] F2 — the generic wizard-layer classifier is retained unmodified (Option B) as
      documented interim protection for Ollama and other self-unclassified clients; no
      silent-acceptance regression is introduced; a new `MockTransport`-backed test
      proves the real `OllamaLlmClient` path is still protected end-to-end; no existing
      D2 test changes.
- [x] F3 — real OpenAI/llama-server classification code has direct `MockTransport`
      regression coverage for truncation, content-filter, and message-level refusal.
- [x] F4 — `LLM_NO_USABLE_CONTENT`, `LLM_COMPLETION_TRUNCATED`, `LLM_COMPLETION_REFUSED`
      each have an end-to-end web-layer regression test.
- [x] F5 — the D6 retry gate has real positive-and-negative end-to-end retry regressions.
- [x] F6 — debug-artifact capture cannot hard-fail a wizard operation at any stage
      (secure directory, write, prune, stat, or delete), catching both `OSError` and
      `PersistenceError` — the exception shapes the code actually raises.
- [x] F7 — `NoReturn` typing, single-source `temperature_mode` literal, and
      `_set_error`-routed failure writes (via `_set_error` + `model_copy` for
      `latest_job_id`) are all in place.
- [x] F8 — non-string `data_dir` is fixed and tested (genuine defect repair);
      `disabled`+invalid-`base_url` is documented and tested as existing fail-closed
      policy (not described as a defect repair).
- [x] Every fix has targeted regression evidence; no failure was hidden by weakening
      tests.
- [x] Ruff / format / mypy / `tests/unit tests/web` pass.
- [x] No schematic/PCB placement, routing, layout, deterministic-engine, or unrelated IR
      semantic code modified; no already-confirmed D1–D7 contract altered.
- [x] True implementation starting SHA recorded, and accepted predecessor SHA
      `6e9b07a7db58fc2c9644756778b06f89262c24a3` passed permanent CI 5/5 green in run
      `31387498004`, all recorded in the completion evidence document.
