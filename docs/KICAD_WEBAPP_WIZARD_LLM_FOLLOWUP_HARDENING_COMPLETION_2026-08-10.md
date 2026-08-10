# KiCad Web App Wizard/LLM Follow-up Hardening — Completion Evidence — 2026-08-10

## Status

Implementation of
`docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_TODO_2026-08-10.md` is complete and has
passed the bounded pre-publication validation described below. Permanent-CI acceptance is
performed on the normal documentation/evidence successor SHA so GitHub Actions evaluates
the cleaned implementation tree after the temporary bounded helper has removed itself.

## SHA discipline

- Code-review baseline SHA: `3f088f7ad09a6ce254c2294d1b714faa20db9b43`.
- Final planning head before bounded helper setup:
  `179f8d2b3552e9b022fe63bcb003f2479f30e0d9`.
- True implementation starting SHA (last helper-only head immediately before product-code
  application): `0158581bb3f62cbc6eec7a6360b3489508e51ca2`.
- Validated implementation commit:
  `a680403e4df3b3feff70b398c063bde65beb81bc`
  (`fix: harden wizard LLM follow-up reliability`).
- Bounded implementation/validation run: `31386619457`, job `93448364333`, success.
- Permanent-CI accepting SHA/run: pending this evidence commit and its permanent run.

The temporary helper/workflow files were deleted in the implementation commit. Comparing
`179f8d2b3552e9b022fe63bcb003f2479f30e0d9` to
`a680403e4df3b3feff70b398c063bde65beb81bc` leaves exactly nine product/test files in the
net diff; no helper file remains.

## F1 — Ollama temperature-mode compatibility

Completed per contract.

- `provider=ollama` combined with `temperature_mode=omit` now fails closed during settings
  validation with a clear `ValueError` rather than silently sending a temperature anyway.
- Ollama `temperature_mode=send` remains the default and retains the existing nested
  `options.temperature` payload behavior.
- OpenAI and llama-server `send`/`omit` behavior is unchanged.
- `provider=disabled` remains exempt because it sends no LLM request.
- The settings comment documents that `omit` is implemented for OpenAI/llama-server and
  not for Ollama.

Targeted regressions cover the rejected Ollama `omit` combination and unchanged Ollama
`send` payload.

## F2 — retained generic classifier as interim Ollama protection (Option B)

Completed per the final second-review decision.

- The generic wizard-layer `finish_reason` classifier was **not removed**.
- A code comment records why it is intentionally retained: OpenAI-compatible clients
  classify terminal outcomes in their provider adapter before returning, while Ollama and
  other self-unclassified clients still require the generic fallback for fail-closed
  behavior.
- Existing D2 tests were not weakened or rewritten to hide the duplicate-layer state.
- A new real-Ollama regression drives `OllamaLlmClient` through `httpx.MockTransport` with
  schema-valid JSON content and `done_reason="length"`, then through the wizard structured
  output path. It asserts `LlmCompletionTruncatedError` before schema-valid content can be
  silently accepted and asserts a single provider call.

Deferred deliberately: an Ollama-owned `done_reason` classifier and subsequent removal of
the generic fallback. The batch does not fabricate undocumented Ollama refusal/content
filter reason mappings.

## F3 — real provider classification coverage

Completed.

Direct `httpx.MockTransport` regressions exercise both `OpenAiLlmClient` and
`LlamaServerLlmClient` for:

- `finish_reason="length"` -> `LlmCompletionTruncatedError`;
- `finish_reason="content_filter"` -> `LlmCompletionRefusedError`;
- `message.refusal=true` with `finish_reason="stop"` -> `LlmCompletionRefusedError`;
- `content=null` with `finish_reason="length"` -> truncation is classified before content
  coercion can raise a generic provider error.

## F4 — web-layer typed completion failures

Completed.

New route-level regressions prove that:

- no usable content -> HTTP 502 / `LLM_NO_USABLE_CONTENT` /
  `failure_kind="operational"`;
- truncation -> HTTP 502 / `LLM_COMPLETION_TRUNCATED` /
  `failure_kind="operational"`, exactly one LLM call;
- refusal -> HTTP 502 / `LLM_COMPLETION_REFUSED` /
  `failure_kind="operational"`, exactly one LLM call.

The existing `LLM_INVALID_STRUCTURED_OUTPUT` route regression remains intact.

## F5 — actual retry-gate regression coverage

Completed with real second invocations.

- After an operational `generate_ir` truncation failure, a second `generate_wizard_ir`
  invocation on the same session is accepted and reaches a successful
  `ir_ready_for_generation` state.
- After a `generation` failure produced by `generate_wizard_project`, an actual subsequent
  `generate_wizard_ir` invocation is rejected before the LLM client is called, proving
  operation identity is part of the retry gate.
- A legacy failed session with `failure_kind=None` and `error=None` is interpreted as
  `unsupported_design` and is not admitted by the operational retry gate.

## F6 — debug-artifact capture is best-effort end-to-end

Completed without a broad catch.

The full debug writer filesystem/persistence block now catches exactly
`(OSError, PersistenceError)` around secure-directory creation, artifact writing, and
pruning. Failures are WARNING-logged with path, stage, and error type and do not fail the
wizard operation. Unrelated programming exceptions are still allowed to propagate.

Regressions prove:

- a realistic `atomic_write_json` `PersistenceError` on a debug-artifact path is warning
  visible while canonical wizard persistence continues and the operation succeeds;
- `_ensure_private_directory` failures in both raw-`OSError` and wrapped
  `PersistenceError` shapes are warning visible and non-fatal to the wizard operation;
- the pre-existing D5 prune/stat/delete behavior remains green.

Canonical session persistence remains fail-closed; only the explicitly optional debug
capture subsystem is best-effort.

## F7 — type safety and failure-write consistency

Completed.

- `_raise_structured_output_exhausted` is annotated `-> NoReturn`.
- `WizardLlmProvenance.temperature_mode` uses the shared `LlmTemperatureMode` alias from
  settings; the cross-layer dependency is the intentionally accepted small-batch design.
- A Pydantic regression confirms `send`, `omit`, and `None` remain accepted while `auto`
  remains rejected.
- The failed-job branch of `generate_wizard_project` now uses `_set_error(...)` and then a
  narrow `.model_copy(update={"latest_job_id": job.id})`, preserving status,
  `failure_kind`, public error payload, timestamp semantics, and job identity without
  expanding `_set_error`'s API.

## F8 — settings hygiene and documented disabled-provider policy

Completed.

- A non-string TOML `web.data_dir` now fails closed instead of being silently coerced via
  `str(...)`; the error names `web.data_dir` and the received type.
- Audit result: `web.data_dir` is the path-valued TOML setting routed through
  `_resolve_config_path`; no additional path-valued TOML caller requiring the same fix was
  found.
- `provider="disabled"` plus an invalid explicit `base_url` remains fail-closed at settings
  load. A regression now locks down this already-existing policy; this item is documented
  as contract coverage, not as a functional defect repair.

## Bounded validation evidence

Temporary bounded workflow run `31386619457`, job `93448364333`, succeeded from the true
implementation-starting helper head and performed all of the following before publication:

1. Reconfirmed the implementation-starting baseline with Ruff, Ruff format, mypy, and the
   complete `tests/unit tests/web` suite.
2. Applied F1-F8.
3. Normalized imports/formatting and reran Ruff/format checks.
4. Ran the targeted follow-up suite:
   `tests/unit/test_wizard_llm_robustness.py`,
   `tests/web/test_web_llm_clients.py`,
   `tests/web/test_web_wizard.py`, and
   `tests/web/test_web_settings.py` — success.
5. Reran the final required gates — Ruff success; 461 files formatted; mypy success in
   213 source files; complete `tests/unit tests/web` suite success.
6. Enforced an explicit changed-file allowlist and succeeded.
7. Deleted all temporary helper/workflow files, committed the validated implementation,
   and pushed it to `webapp`.

The first bounded attempt failed before tests/commit because a temporary patch helper
omitted a `json` import in a newly added test. That orchestration-only error was corrected;
no product commit was published from the failed attempt. The successful bounded run above
repeated the full baseline and final validation rather than reusing a prior runner state.

## Scope audit

Net implementation changes relative to the final planning head are limited to:

- `src/kicad_pcb_web/services/_wizard_llm.py`
- `src/kicad_pcb_web/services/_wizard_session_io.py`
- `src/kicad_pcb_web/services/wizard.py`
- `src/kicad_pcb_web/settings.py`
- `src/kicad_pcb_web/wizard_models.py`
- `tests/unit/test_wizard_llm_robustness.py`
- `tests/web/test_web_llm_clients.py`
- `tests/web/test_web_settings.py`
- `tests/web/test_web_wizard.py`

No schematic placement, component orientation, wire routing, routing heuristic,
crossing-minimization, spacing/layout, PCB placement/routing, deterministic `kicad_pcb`
engine, frontend source/SPA bundle, or unrelated Circuit IR semantic code was modified.
No already-confirmed D1-D7 contract was intentionally altered.

## Permanent acceptance

Pending permanent CI on the normal evidence successor SHA. Final closure will record the
exact accepting SHA, permanent run/job IDs, Python counts/coverage, browser result, and
artifacts after that SHA reaches 5/5 green.
