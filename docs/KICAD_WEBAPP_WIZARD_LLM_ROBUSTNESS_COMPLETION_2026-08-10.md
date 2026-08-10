# KiCad Web App Wizard/LLM Robustness — Completion Evidence — 2026-08-10

## Status

Implementation of `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_TODO_2026-08-10.md` is complete. The implementation was validated first by a bounded pre-commit acceptance workflow and then by the permanent `ci.yml` workflow on a normal repository SHA containing the cleaned implementation and this evidence predecessor.

## SHA discipline

- Code-review baseline SHA: `479465102f25f7dd85153477442c1c01b6dfbbe3`
- True implementation starting SHA: `b5855cc3f0e984f3a189b17fec37ac1cf8685de1`
- Implementation commit SHA: `e302d1b2b6e17bc6e62ae43c8f1100201f547e66`
- Permanent-CI implementation/evidence accepting SHA: `515f898b7a519cd9341eee605e18e9f1f5a9eed6`

The implementation starting SHA was captured before the first product-code change. Permanent baseline CI run `31370387661` passed all five jobs on that exact SHA before implementation began.

Because recording a CI run necessarily creates a successor documentation SHA, the final exact documentation head is validated externally by the final Ralph-loop report and the repository CI-status bridge rather than by recursively committing its own run ID forever.

## Bounded implementation validation

The successful bounded validation/publication workflow was:

- Run: `31374614117`
- Job: `93410932389` (`apply-and-validate`)
- Result: success

Before the implementation commit was created, that job completed all of the following successfully:

- targeted D1-D7 plus existing wizard/client regressions: `58 passed, 2 skipped` in the selected suite;
- Ruff formatting of permanent Python sources/tests;
- Ruff check: clean;
- Ruff format check: clean (`456 files already formatted` at that bounded runner revision);
- mypy: `Success: no issues found in 213 source files`;
- full `tests/unit tests/web` suite: reached 100% with no failures;
- scope-boundary guard: clean;
- `git diff --cached --check`: clean.

All temporary bounded-helper files and workflows were deleted in the same implementation commit. At the cleaned implementation/evidence SHA, `.github/workflows/` contains only permanent `ci.yml`, and there is no `.github/scripts/` directory.

## Permanent CI acceptance

Permanent workflow run:

- Run: `31375490922`
- Head SHA: `515f898b7a519cd9341eee605e18e9f1f5a9eed6`
- Branch: `webapp`
- Result: **5/5 required jobs successful**

Jobs:

1. Python lint, types, unit and web tests — `93413662913` — success
2. Frontend lint, unit tests and production build — `93413662944` — success
3. Build and install wheel/sdist — `93417287256` — success
4. KiCad integration tests — `93417287263` — success
5. Browser smoke tests — `93417287277` — success

### Python evidence

The permanent Python job recorded:

- Ruff: all checks passed;
- Ruff format: `461 files already formatted`;
- mypy: `Success: no issues found in 213 source files`;
- workflow-config guard: pass;
- generated-tree guard: pass;
- pytest collected: `2753`;
- result: **`2746 passed, 7 skipped`**;
- coverage: **`90.85%`**, above the 70% gate.

### Browser evidence

Playwright ran 12 tests:

- **11 passed**;
- **1 skipped**.

The skipped test is the explicitly opt-in live-LLM wizard flow. `frontend/e2e/wizard.spec.ts` skips it unless `RUN_E2E_WIZARD_TESTS=1` is supplied; permanent CI does not enable that provider-dependent test. The browser suite still exercises the deterministic wizard failure/retry hardening flow and direct JSON generation flow.

### Permanent artifacts

Run `31375490922` produced all five expected permanent artifacts:

- `package-smoke-31375490922` — artifact `9058190026`;
- `python-coverage-31375490922` — artifact `9058178652`;
- `frontend-coverage-31375490922` — artifact `9057747969`;
- `built-spa-31375490922` — artifact `9057747133`;
- `ruff-report-31375490922` — artifact `9057740334`.

## D1 — IR structural-output repair: fixed

IR generation now uses a single shared attempt counter across both structural-output failures and semantic netlist failures.

Contract implemented:

- maximum LLM invocations for one `generate_ir` operation = exactly `ir_max_repair_rounds + 1`;
- structural and semantic failures consume the same budget and cannot multiply into nested retry loops;
- malformed/schema-invalid/no-content repair context is fed into the next IR prompt;
- semantic netlist validation failures continue to feed the exact validation context into the next prompt;
- parseable semantic exhaustion ends as `ir_needs_repair` with the last parsed IR preserved;
- structural/no-usable-content exhaustion ends as operational `failed` with a typed error.

Targeted regressions assert malformed-then-valid recovery, the exact call bound, structural exhaustion, and semantic exhaustion with preserved IR.

## D2 — Completion outcome classification: fixed

Structured-output handling no longer blanket-retries generic `ToolError`.

Implemented classifications:

- JSON parse failure — repairable within budget;
- schema validation failure — repairable within budget;
- null/empty provider content with no stronger finish reason — repairable within budget;
- `finish_reason="length"` — terminal `LLM_COMPLETION_TRUNCATED` outcome;
- refusal/content-filter outcome — terminal `LLM_COMPLETION_REFUSED` outcome;
- generic provider/transport/protocol `ToolError` — not absorbed by the structured-output repair loop.

Structural exhaustion surfaces the more specific `LLM_INVALID_STRUCTURED_OUTPUT` or `LLM_NO_USABLE_CONTENT` code instead of collapsing everything into generic `LLM_PROVIDER_FAILED`.

## D3 — Explicit temperature compatibility: fixed

Added explicit LLM configuration:

```toml
temperature_mode = "send" # send | omit
```

Contract implemented:

- default is `send`, preserving existing behavior;
- `send` includes `temperature` in OpenAI and llama-server payloads;
- `omit` removes the `temperature` key entirely;
- env override: `KICAD_PCB_WEB_LLM_TEMPERATURE_MODE`;
- unknown values fail settings validation;
- `temperature_mode` is included in wizard LLM provenance/config revision, so changing the policy changes the reproducibility identity.

No model-name heuristic or automatic capability registry was added.

## D4 — Retry/timeout configuration envelope: fixed to the reviewed contract

The synchronous transport and per-session mutation lock are retained. The lock is deliberately **not** released during retry backoff because doing so without revision/CAS protection would create a lost-update race.

Validated configuration bounds now include:

- `0 < timeout_s <= 300`;
- `1 <= retry_max_attempts <= 10`;
- `retry_base_delay_s <= retry_max_delay_s <= 60`;
- existing retry-jitter constraints remain enforced.

The client exposes the maximum scheduled retry-sleep total as a bound on its own sleep scheduling. With the maximum valid values, the conservative maximum is:

```text
(10 - 1) * 60 = 540 seconds
```

This is **not** an absolute HTTP wall-clock deadline. HTTPX keeps its native connect/read/write/pool inactivity-timeout semantics. A true end-to-end deadline mechanism remains out of scope.

The retryable HTTP status set and the no-replay rule after ambiguous POST delivery remain unchanged.

## D5 — Debug-artifact retention: fixed

When raw debug capture is enabled:

- per-session/per-stage file-count cap: `20` for `spec_*` and `20` for `ir_*`;
- per-session total byte cap across stages: `25 MiB`;
- pruning is oldest-first;
- the newest just-written artifact is retained even if it alone exceeds the byte cap;
- deletion/stat failures are logged at WARNING with the path/error type and do not fail the wizard operation;
- pruning failures are never silently swallowed;
- capture remains default-off and the existing private-directory guard remains in place.

Raw debug artifacts remain intentionally unredacted; this batch bounds retention rather than changing that explicit debugging contract.

## D6 — Failure-state discriminator: fixed

Wizard sessions now persist optional `failure_kind` with values:

- `unsupported_design`;
- `operational`;
- `generation`.

The frontend-visible `status` enum is unchanged; terminal sessions still use `status="failed"`.

All new failed writes set `failure_kind` explicitly. Legacy persisted sessions with no field remain readable and are interpreted only on the legacy read path using the historical invariant:

- `error is None` -> `unsupported_design`;
- `error is dict` -> `operational`.

Retry gating uses the effective failure kind and operation identity instead of relying on the error shape for newly written sessions.

## D7 — Settings hygiene: fixed

Implemented:

- removed the redundant OpenAI `base_url` re-validation branch;
- `jobs_dir.mkdir(...)` `OSError` is wrapped as `ValueError` with the jobs path;
- empty `KICAD_PCB_WEB_DATA_DIR=""` rejects;
- TOML `data_dir = ""` rejects.

### Empty-string settings audit

- `KICAD_PCB_WEB_CONFIG_FILE`: already rejects an explicitly empty value.
- `web.data_dir`: previously silent/CWD-resolving case; now rejects explicitly.
- `web.mutation_lock_timeout_s`: empty value fails numeric coercion.
- `llm.provider`: empty value is not a valid provider enum and rejects.
- `llm.model`: when a provider is enabled, empty/whitespace fails `_require_non_empty`; when the provider is disabled it is operationally unused.
- `llm.base_url`: an explicitly empty string fails URL validation; enabled Ollama/llama-server additionally require it non-empty.
- `llm.api_key`: enabled OpenAI requires non-empty; for providers that do not require an API key it may be absent/unused by design.
- `llm.system_prompt_version`: empty/whitespace rejects.
- `llm.temperature_mode`: empty is not `send|omit` and rejects.
- numeric retry/token/temperature fields: empty strings either fail numeric coercion or, for `max_tokens`, preserve the existing documented optional/unset behavior.
- boolean fields: empty strings fail boolean coercion.

No additional silent-CWD path field was found.

## Scope verification

The net implementation diff from `b5855cc3...` to `e302d1b2...` contains only:

- `README.md`;
- `src/kicad_pcb_web/errors.py`;
- `src/kicad_pcb_web/settings.py`;
- `src/kicad_pcb_web/wizard_models.py`;
- `src/kicad_pcb_web/services/_wizard_llm.py`;
- `src/kicad_pcb_web/services/_wizard_session_io.py`;
- `src/kicad_pcb_web/services/llm/base.py`;
- `src/kicad_pcb_web/services/llm/factory.py`;
- `src/kicad_pcb_web/services/llm/openai_client.py`;
- `src/kicad_pcb_web/services/llm/llama_server_client.py`;
- `src/kicad_pcb_web/services/wizard.py`;
- `tests/unit/test_wizard_llm_robustness.py`;
- `tests/web/test_web_wizard.py`.

No `src/kicad_pcb/`, frontend, generated SPA, schematic placement/orientation, wire routing, routing-heuristic, PCB placement/routing, or unrelated Circuit IR semantic code changed.

## Deliberately deferred / residual observations

The following remain explicit non-goals rather than silent fallbacks:

1. **Revision/CAS session concurrency and out-of-lock provider execution** — deferred. The lock remains held for correctness.
2. **Automatic/model-capability temperature policy** — deferred. Operators must explicitly choose `temperature_mode="omit"` for models that reject temperature; default `send` preserves compatibility with existing configurations.
3. **Absolute end-to-end LLM operation deadline** — deferred. D4 bounds configuration/backoff, not total HTTP elapsed time.
4. **Raw debug-artifact redaction** — unchanged by design; raw capture stays default-off/private and is now retention-bounded.
5. **Frontend dependency audit** — `npm ci` continues to report 8 vulnerabilities (1 low, 1 moderate, 6 high). This batch does not change frontend dependencies and permanent CI does not currently gate on `npm audit`; no claim is made that these advisories are fixed or product-exploitable without separate dependency/advisory analysis.

## Final exact-head gate

The permanent implementation/evidence predecessor `515f898b...` is accepted 5/5 green. This finalized evidence document and the finalized TODO form a documentation-only successor. Permanent CI for that **final exact documentation head** is intentionally closed by the final Ralph-loop report and the repository CI-status bridge, avoiding an infinite self-referential commit/run cycle.
