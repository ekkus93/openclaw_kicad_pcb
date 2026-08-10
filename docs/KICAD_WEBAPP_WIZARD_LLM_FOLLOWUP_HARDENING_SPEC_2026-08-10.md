# KiCad Web App Wizard/LLM Follow-up Hardening Spec — 2026-08-10

## Purpose

This is a second, smaller reliability/hygiene pass on the LLM wizard service layer. It
follows the closed `KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_*` batch (implementation commit
`e302d1b2b6e17bc6e62ae43c8f1100201f547e66`, accepted predecessor
`515f898b7a519cd9341eee605e18e9f1f5a9eed6`, permanent CI run `31375490922` 5/5 green).

A post-implementation code review of that batch — five independent verification passes
plus direct source reads, each checking the D1–D7 contracts against the real code rather
than trusting the TODO's checkmarks — confirmed every D1–D7 contract is genuinely
implemented. It also surfaced one real functional bug the D1–D7 batch did not cover, plus
several design-debt and test-coverage gaps left behind by that implementation pass. This
spec closes those. It does **not** reopen schematic placement, orientation, wire routing,
PCB layout, or unrelated Circuit IR semantics, and it does **not** revisit any D1–D7
contract that was confirmed correct.

## Starting point and SHA discipline

- **Code-review baseline SHA** (where these follow-up defects were observed):
  `3f088f7ad09a6ce254c2294d1b714faa20db9b43`.
- **Implementation starting SHA**: whatever `webapp` points to immediately before the
  first product-code change for this batch. The completion evidence records this true
  implementation head, not the review baseline above.

All quality gates were green at the code-review baseline (ruff, ruff format, mypy,
`pytest tests/unit tests/web` — 2753 collected, 2752 passed, 1 skipped locally; see
`KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_COMPLETION_2026-08-10.md` for the permanent-CI count).
The implementer must re-confirm green at the implementation starting SHA before changing
code.

## Confirmed defects

Severity is the reviewer's estimate; the mechanism of each was confirmed by reading the
cited code directly (not inferred from the D1–D7 completion evidence).

### F1 — `temperature_mode=omit` is a silent no-op for the Ollama provider (Med)

D3 added `temperature_mode` (`send | omit`) and wired it into `openai_client.py:23-24`
and `llama_server_client.py`. `ollama_client.py:22` was never touched by that batch and
unconditionally sends `"options": {"temperature": self._effective_temperature(request)}`.
An operator can set `provider=ollama, temperature_mode=omit`; settings validation accepts
it (the enum check only validates the value is `send`/`omit`, not that the active
provider honors it), and the payload still includes `temperature` — silently doing nothing.
This directly contradicts the D1–D7 spec's own failure-semantics principle: "Provider-
parameter incompatibility is handled deliberately (`temperature_mode`), never a silent
no-op." `temperature_mode` is untested for the Ollama provider, which is why this shipped
unnoticed.

### F2 — Duplicated, partially dead finish-reason/refusal classification (Low/Med)

D2's truncation/refusal classification is implemented twice. The authoritative copy is
`openai_client.py:52-67`, which raises `LlmCompletionTruncatedError` /
`LlmCompletionRefusedError` directly inside `_parse_completion` (called from
`.complete()`, before a completion object is ever returned). A second, functionally
identical copy exists in `_wizard_llm.py:326-337`, re-normalizing
`completion.finish_reason` after `.complete()` returns. For `openai`/`llama_server`
(which subclasses `OpenAiLlmClient`) the second copy is unreachable dead code — the
exception already fired upstream. It is only live for `ollama_client.py`, which forwards
`done_reason` verbatim and was never given its own classification logic, so an Ollama
refusal or truncation is not actually distinguished — it just looks like ordinary content
and falls through to normal JSON parsing. Two copies of the same logic that can silently
drift out of sync, with no test exercising the divergence, is exactly the kind of
half-applied duplication automated patch passes leave behind.

### F3 — The real provider-client classification code has no test coverage (Med)

Every D2 regression test (`test_d2_terminal_finish_reasons_do_not_repair`,
`test_d2_no_content_*` in `tests/unit/test_wizard_llm_robustness.py`) uses
`ScriptedClient`, a fake that returns a pre-built `LlmCompletion` directly and bypasses
`BaseHttpLlmClient`/`OpenAiLlmClient` entirely. `tests/web/test_web_llm_clients.py` (the
file that does exercise `OpenAiLlmClient._parse_completion` against raw JSON payloads via
`httpx.MockTransport`) only ever uses `finish_reason: "stop"`. No test sends
`finish_reason: "length"`, `finish_reason: "content_filter"`, `message.refusal: true`, or
`content: null` through the real HTTP-parsing path (`openai_client.py:52-67`). A
regression there — wrong JSON key, wrong casing, a broken `message.get("refusal")` check,
or an exception-ordering change relative to `_coerce_text_content` — would not be caught
by anything in the suite.

### F4 — New typed completion errors have no end-to-end web-layer regression (Low/Med)

Only `LLM_INVALID_STRUCTURED_OUTPUT` is asserted end-to-end through the real FastAPI route
(`tests/web/test_web_wizard.py:902-928`). `LlmNoUsableContentError`,
`LlmCompletionTruncatedError`, and `LlmCompletionRefusedError` are exercised only at the
unit level via `ScriptedClient`, never through the actual HTTP route →
`502`/persisted-session path a real client would hit.

### F5 — D6's "retry gate driven correctly (tested)" acceptance claim is overstated (Low)

The D1–D7 TODO's D6 acceptance bullet claims the retry gate (`wizard.py:500-503`,
`672-675`) is tested for unsupported/operational/generation outcomes. In fact no test
re-invokes `generate_wizard_ir` or `generate_wizard_project` a second time on a failed
session to prove the gate actually permits or blocks the retry; only
`_failure_operation(...)` and `failure_kind` values are asserted after the first failure.
The underlying gate logic was confirmed correct by direct code trace and by manually
exercising a live retry, but there is no positive test that a retry succeeds and no
negative test that a mismatched-operation or non-retryable `failure_kind` blocks one (e.g.
a `"generation"` failure blocking a subsequent `generate_wizard_ir` call).

### F6 — Debug-artifact "best-effort, never fails the operation" guarantee doesn't cover the initial write (Low/Med)

D5's contract states debug capture "is best-effort and must never fail the wizard
operation." The pruning/stat/deletion paths correctly honor this
(`_wizard_session_io.py:153-201`, WARNING-logged, non-raising). The *initial* artifact
write inside `writer()` (`_wizard_session_io.py:216-222`,
`atomic_write_json(artifact_path, payload)`) is unguarded: if it raises (disk full,
permission error, etc.), the exception propagates up through `_call_llm_for_json`/
`_call_llm_for_json_once` into the wizard operation's outer exception handling and **does**
hard-fail the operation — for an opt-in, default-off debugging feature. This contradicts
the stated rationale even though it is outside the literal D5 checklist wording (which
named only "pruning/stat/deletion failure").

### F7 — Minor type-safety and consistency gaps left by the D1–D7 implementation (Low)

- `_raise_structured_output_exhausted` (`_wizard_llm.py:253`) is annotated `-> None`
  though every branch raises; it should be `-> NoReturn` (the codebase already uses this
  convention for `_persist_and_raise_failure` in `wizard.py`), so mypy can verify the
  unreachable-after-call invariant both call sites (`_wizard_llm.py:445`,
  indirectly `wizard.py`) rely on.
- `wizard_models.py:132` redeclares `temperature_mode: Literal["send", "omit"] | None`
  instead of importing `LlmTemperatureMode` from `settings.py` (`settings.py:13`) — a DRY
  violation that will silently drift if a future `auto` mode is added to one but not the
  other.
- `generate_wizard_project`'s failed-job branch (`wizard.py:730-738`) hand-constructs the
  `status`/`failure_kind`/`error`/`updated_at` update inline instead of routing through
  the `_set_error` helper (`wizard.py:253` and its other call sites use it) — not
  incorrect, but it undercuts the point of having that helper and is a second place a
  future failure-kind change must remember to update.

### F8 — Residual settings-hygiene gaps outside the D7 audit's exact scope (Low)

The D7 audit was correctly scoped to empty-string handling and found/fixed a real bug
(`data_dir=""` silently resolving to CWD). Two adjacent gaps were disclosed by the
follow-up review but not fixed:

- `_resolve_config_path` (`settings.py:381-385`) calls `Path(str(raw_value))` — a
  non-string TOML `data_dir` (e.g. `data_dir = 123`) is silently coerced via `str()`
  rather than rejected, producing a directory literally named `123` with no type error.
  This is the same class of "silently accept a degenerate config value" defect D7 fixed
  for empty strings, just for a different malformed input shape.
- The single consolidated `base_url` validation (`settings.py:365-366`, D7's fix for the
  previously-redundant duplicate check) now runs unconditionally, including when
  `provider="disabled"`. A stray/misconfigured `base_url` env var will fail settings load
  even for a fully disabled LLM provider. This is arguably correct fail-closed behavior
  and is a small, deliberate improvement over the prior state — but it is an undocumented
  behavior change with no regression test covering the `disabled` + invalid-`base_url`
  combination.

---

## Resolved contracts (scope)

### 1. Ollama temperature-mode compatibility (F1)

**Decision: make the incompatibility explicit and fail-closed rather than fixing it
silently by wiring temperature into the Ollama payload.** Ollama was never in scope for
D3's `send | omit` payload contract, and speculatively wiring it in now without a real
capability model would repeat the same "ad-hoc heuristic" pattern D3 explicitly rejected.

- At settings-load time, reject `provider=ollama` combined with `temperature_mode=omit`
  with a clear `ValueError` explaining that `temperature_mode=omit` is not currently
  implemented for the `ollama` provider.
- `provider=ollama` combined with `temperature_mode=send` (the default) continues to work
  unchanged — this is a validation-time rejection of the one combination that is
  currently a silent no-op, not a behavior change for existing configurations.
- Document in the `temperature_mode` setting's docstring/comment which providers
  currently honor it (`openai`, `llama_server`) and that `ollama` does not yet support
  `omit`.
- Extending `omit` support to `ollama_client.py` itself is explicitly **out of scope**
  for this batch (Ollama's temperature is nested under `"options"`, and its own
  refusal/finish-reason semantics per F2 are also unresolved) and is noted as a possible
  future extension alongside D3's deferred `auto` capability registry.

### 2. Consolidate finish-reason/refusal classification into one layer (F2)

**Decision: keep the provider-side classification in `openai_client.py` /
`llama_server_client.py` as authoritative; remove the now-fully-dead duplicate branch in
`_wizard_llm.py` rather than maintaining two copies.**

- Delete the `finish_reason == "length"` / `finish_reason in {"content_filter",
  "refusal"}` re-checks in `_wizard_llm.py` (`_wizard_llm.py:326-337`) — for
  `openai`/`llama_server` these are unreachable because `_parse_completion` already
  raised before a completion object exists.
- Retain the `if not completion.content.strip(): raise _RepairableStructuredOutputError`
  check (empty-content-without-a-typed-exception is still a legitimate condition for
  clients that don't self-classify).
- For `ollama_client.py`, this batch does **not** implement Ollama-side truncation/refusal
  classification (Ollama's `done_reason` values do not cleanly map to OpenAI's
  `finish_reason` semantics, and designing that mapping is a larger provider-parity
  change). Instead: document explicitly (code comment + this spec) that an Ollama
  truncation/refusal currently surfaces as ordinary content that fails structured-output
  parsing (i.e. `invalid_structured_output`, repairable, eventually exhausting to
  `LLM_INVALID_STRUCTURED_OUTPUT`) rather than as a distinct terminal
  truncation/refusal outcome. This is a known, disclosed limitation, not a silent gap.
- Spec and IR paths continue to share the same (now single-copy) classification contract.

### 3. Add real-provider-client classification tests (F3)

**Decision: add `httpx.MockTransport`-based tests that exercise the actual
`OpenAiLlmClient._parse_completion` code path**, matching the existing style in
`tests/web/test_web_llm_clients.py`, for:

- `finish_reason: "length"` → asserts `LlmCompletionTruncatedError` raised from
  `OpenAiLlmClient.complete()` directly (not via the wizard layer).
- `finish_reason: "content_filter"` → asserts `LlmCompletionRefusedError`.
- `message.refusal: true` with `finish_reason: "stop"` → asserts
  `LlmCompletionRefusedError` (the `message.get("refusal")` branch, independent of
  `finish_reason`).
- `content: null` with `finish_reason: "length"` → asserts the truncation error fires
  before any content-coercion `ToolError` would.
- The same four cases for `LlamaServerLlmClient` (subclass of `OpenAiLlmClient`; a
  parametrized test is sufficient if the payload shape is identical).

### 4. Add end-to-end web-layer tests for the remaining typed completion errors (F4)

**Decision: extend `tests/web/test_web_wizard.py` with one HTTP-level regression per
error type**, mirroring the existing `LLM_INVALID_STRUCTURED_OUTPUT` test
(`tests/web/test_web_wizard.py:902-928`):

- A scripted/fake client that always returns no usable content → asserts the route
  surfaces `LLM_NO_USABLE_CONTENT` with `failure_kind=operational`.
- A scripted/fake client that raises truncation → asserts the route surfaces
  `LLM_COMPLETION_TRUNCATED` with `failure_kind=operational` (do not retry).
- A scripted/fake client that raises refusal/content-filter → asserts the route surfaces
  `LLM_COMPLETION_REFUSED` with `failure_kind=operational` (do not retry).

### 5. Add an end-to-end retry-gate regression test (F5)

**Decision: add a test that actually performs a second operation call after a failure**,
not just an assertion on the persisted `failure_kind`/`_failure_operation` value:

- Positive case: after an `operational` `generate_ir` failure, a second
  `generate_wizard_ir` call on the same session is accepted and proceeds (does not raise
  a "not retryable" error).
- Negative case: after a `generation` failure (from `generate_wizard_project`), a
  subsequent `generate_wizard_ir` call is still gated correctly per the operation-identity
  rule (i.e. the gate keys off both failure kind and operation, not just failure kind).
- Negative case: a legacy session with no `failure_kind` and `error is None` (interpreted
  as `unsupported_design` per the D6 legacy rule) is **not** treated as retryable by the
  operational retry gate.

### 6. Bound the initial debug-artifact write within the same best-effort contract (F6)

**Decision: catch and WARNING-log a failure of the initial artifact write itself,
consistent with the existing prune/stat/delete handling — do not let an opt-in debugging
feature hard-fail a wizard operation.**

- Wrap the `atomic_write_json(artifact_path, payload)` call inside `writer()`
  (`_wizard_session_io.py:216-222`) in a `try/except OSError`, logging at WARNING with the
  path and error type, matching the existing `_unlink_debug_artifact` pattern
  (`_wizard_session_io.py:153-162`) exactly in style.
- On write failure, skip the subsequent `_prune_debug_artifacts` call for that write
  (nothing to prune around) rather than raising.
- This must not change behavior for the success path or for any existing D5 pruning
  test.

### 7. Type-safety and consistency cleanup (F7)

- Change `_raise_structured_output_exhausted`'s return type from `-> None` to
  `-> NoReturn` in `_wizard_llm.py`.
- Replace `wizard_models.py`'s inline `Literal["send", "omit"] | None` for
  `temperature_mode` with an import of `LlmTemperatureMode` from `settings.py` (verify no
  import cycle — `settings.py` does not import `wizard_models`), keeping the field
  `Optional[LlmTemperatureMode]`.
- Route `generate_wizard_project`'s failed-job branch (`wizard.py:730-738`) through the
  existing `_set_error` helper instead of constructing the session update inline, keeping
  behavior (including the `latest_job_id` field it currently sets) identical.

### 8. Close the two residual settings-hygiene gaps (F8)

- `_resolve_config_path`: reject a non-string `data_dir` TOML value explicitly (raise
  `ValueError` naming the field and the received type) instead of silently `str()`-
  coercing it. Apply the same check pattern to any other path-valued TOML setting that
  currently flows through `_resolve_config_path` or an equivalent coercion, if any exist.
- Add a targeted regression test for `provider="disabled"` combined with an invalid
  `base_url` value, asserting the current (correct, fail-closed) behavior — a `ValueError`
  at load — so this is a documented, tested contract rather than an untested side effect
  of the D7 consolidation.

---

## Explicit non-goals

Do not modify:

- schematic component placement, orientation, wire routing, routing heuristics, crossing
  minimization, spacing/layout algorithms;
- PCB placement/routing;
- unrelated Circuit IR semantics;
- the deterministic engine (`kicad_pcb`);
- any D1–D7 contract already confirmed correct by this review (D1, D3's `send`/`omit`
  behavior for `openai`/`llama_server`, D4, D5's pruning logic, D6's failure-kind field
  and legacy fallback rule, D7's `data_dir=""`/`jobs_dir.mkdir` fixes);
- adding Ollama-side `temperature_mode=omit` support or Ollama-side finish-reason
  classification (both explicitly deferred by contract 1 and 2 above);
- an `auto` temperature capability registry (already out of scope per the original spec);
- revision/CAS session concurrency or an absolute LLM operation deadline (already
  deferred by D4);
- unrelated UI design; no frontend source or SPA bundle changes are expected.

If a fix requires one of these areas, document the dependency instead of folding it in.

## Failure semantics

- `temperature_mode=omit` is rejected at settings load for providers that do not honor it
  (currently `ollama`), rather than silently doing nothing — extending the same
  "no silent no-op" principle D3 established to a provider D3 didn't originally cover.
- Classification logic exists in exactly one place per provider family; no dead/duplicate
  branch is left for future edits to drift out of sync with.
- Debug-artifact capture (writes, prunes, and deletions alike) is best-effort end-to-end:
  no failure in that subsystem raises out of a wizard operation.
- The retry gate's operation-identity + failure-kind behavior is proven by an actual
  second call, not only by inspecting persisted fields after the first failure.
- Settings continue to fail closed on malformed input (wrong type, not just wrong/empty
  string).

## Testing requirements

Every fixed defect gets targeted regression tests (`tests/unit/`, `tests/web/`; real fakes
and `httpx.MockTransport` over mocks, matching existing project style):

- **F1**: `provider=ollama, temperature_mode=omit` rejects at settings load with a clear
  message; `provider=ollama, temperature_mode=send` (default) continues to pass and the
  Ollama payload is unchanged.
- **F2**: after removing the dead duplicate branch, all existing D2 tests
  (`test_d2_*` in `tests/unit/test_wizard_llm_robustness.py`) still pass unmodified —
  proving the removal was genuinely dead code, not a behavior change.
- **F3**: four new `httpx.MockTransport`-backed tests (length, content_filter,
  message.refusal, content:null+length) asserting the exact typed error raised by
  `OpenAiLlmClient.complete()` / `LlamaServerLlmClient.complete()` directly.
- **F4**: three new `tests/web/test_web_wizard.py` HTTP-level tests, one per typed error
  (`LLM_NO_USABLE_CONTENT`, `LLM_COMPLETION_TRUNCATED`, `LLM_COMPLETION_REFUSED`), each
  asserting the response code and `failure_kind=operational`.
- **F5**: one positive end-to-end retry test and two negative gating tests as specified in
  contract 5 above — actual second calls, not just field inspection.
- **F6**: a simulated initial-write failure (e.g. monkeypatched `atomic_write_json` raising
  `OSError`) asserts a WARNING log and that the wizard operation still completes/returns
  normally, matching the existing `test_d5_prune_delete_failure_warns_without_raising`
  style.
- **F7**: no new test required beyond existing D1–D7 tests continuing to pass and mypy
  passing with the `NoReturn` annotation in place; add one assertion that
  `WizardLlmProvenance.temperature_mode` still accepts only `"send"`/`"omit"`/`None` after
  the import change (guards against the DRY fix accidentally widening the type).
- **F8**: a non-string TOML `data_dir` (e.g. `data_dir = 123`) rejects with a clear
  `ValueError`; `provider="disabled"` with an invalid `base_url` rejects at load
  (documented, tested contract for the F8/D7 interaction).

No existing test may be weakened to pass.

## Definition of Done

Closure is complete when:

- F1–F8 are each implemented per the exact contracts above (or a deviation is explicitly
  documented with rationale in the completion evidence);
- `temperature_mode=omit` is either honored or explicitly rejected at load for every
  provider — never a silent no-op;
- finish-reason/refusal classification exists in exactly one place per client family, with
  the Ollama limitation explicitly documented rather than silently unhandled;
- the real OpenAI/llama-server classification code path (not just the wizard-layer fake)
  has direct regression coverage for truncation, refusal, and message-level refusal;
- the three newer typed completion errors (`LLM_NO_USABLE_CONTENT`,
  `LLM_COMPLETION_TRUNCATED`, `LLM_COMPLETION_REFUSED`) each have an end-to-end web-layer
  regression test;
- the D6 retry gate has a real positive-and-negative end-to-end retry regression, not just
  a post-failure field assertion;
- debug-artifact capture cannot hard-fail a wizard operation at any stage (write, prune,
  stat, or delete);
- the two residual settings-hygiene gaps (non-string `data_dir`, `disabled`+invalid
  `base_url`) are resolved and tested;
- each fix has targeted regression tests with the explicit assertions above; no test
  weakened;
- ruff, ruff format, mypy, and `pytest tests/unit tests/web` all pass;
- no schematic/PCB placement, routing, layout, deterministic-engine, or unrelated IR
  semantic code was modified, and no already-confirmed D1–D7 contract was altered;
- the implementation starting SHA (not the review baseline) and an accepting SHA that
  passes permanent CI 5/5 green are recorded in the completion evidence document.
