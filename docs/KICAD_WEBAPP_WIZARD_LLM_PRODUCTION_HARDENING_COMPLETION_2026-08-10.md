# KiCad Webapp Wizard LLM Production Hardening Completion Evidence

Date: 2026-08-10
Branch: `webapp`
Specification: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_SPEC_2026-08-10.md`
TODO: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_TODO_2026-08-10.md`
Implementation notes: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_IMPLEMENTATION_NOTES_2026-08-10.md`

## Result

The wizard/LLM production-hardening batch is product-accepted on exact SHA:

`b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41`

Permanent CI run:

`31430879076`

Run URL:

`https://github.com/ekkus93/openclaw_kicad_pcb/actions/runs/31430879076`

All five permanent jobs passed on that exact SHA.

This batch did not change deterministic placement, orientation, wire routing, PCB layout/routing, frontend source, or unrelated Circuit IR semantics.

## SHA chain

| Purpose | SHA |
|---|---|
| Prior accepted follow-up-hardening baseline | `0af8061ac9de7c0344a43a58156b1612c685ba0a` |
| Production-hardening spec commit | `4fdac562e590d7b8b85937963592db70b50d365a` |
| Exact implementation-starting SHA | `bdfaf93e4f955ba5dafc34e26ab2cae34a280725` |
| Architecture/implementation-notes decision record | `50f3e5573a210c6918dbab0cdeddf997dbbed2a2` |
| First provider-contract implementation | `7db762dbd7a28af3172da50f356da44ca128ccc8` |
| Privacy/retryability implementation | `afcb290cf623d31f58c2e64b875ab91e6b8a8067` |
| Configuration/trust-boundary implementation | `d37d5cd817050d35ab78ca48958ccb4c8bb96a1a` |
| Observability/serialization implementation | `eb738b50333cf6b1c53a8267bddbd4d86cb973ec` |
| Final product-accepting SHA | `b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41` |

The exact final documentation SHA cannot truthfully be embedded in its own contents without creating a new SHA. It is therefore recorded externally in the Ralph-loop final report after the exact documentation commit passes permanent CI, as explicitly permitted by the TODO's self-referential closure rule.

## Baseline discipline

The implementation-starting product tree was `bdfaf93e4f955ba5dafc34e26ab2cae34a280725`.

Its permanent baseline CI run `31419168095` was 5/5 green before product-code changes. The production-hardening work therefore started from a validated baseline rather than treating a documentation/helper commit as the implementation baseline.

## Architecture dispositions

### P1 — absolute deadline: P1-B selected

No aggregate wall-clock deadline was invented.

The implementation retains truthful HTTPX timeout semantics plus bounded retry configuration. `llm.timeout_s` remains the HTTPX timeout value; it is not documented or tested as an end-to-end operation deadline. Retry backoff is independently bounded by retry-attempt and retry-delay settings.

Reason for deferral: a real operation deadline would need monotonic deadline ownership, remaining-budget propagation through HTTP attempts, retry sleeps, structured-output repair, semantic IR repair, and explicit deadline-exhaustion state/error semantics. Implementing only a scalar timeout and calling it a total deadline would be misleading.

Follow-up trigger: implement P1-A only as a dedicated change that provides the complete monotonic remaining-budget contract and fake-clock regressions.

### P2 — concurrency: P2-A selected

Per-session mutations remain serialized. The mutation lock remains held across provider calls, HTTP retry sleeps, and repair work.

Tradeoff: a long provider call occupies the worker and blocks other mutations for the same session. This is intentionally preferred over releasing the lock without durable operation identity/revision/CAS stale-write protection.

A regression proves that a held same-session lock blocks competing same-session mutation while a different session remains independently lockable.

Follow-up trigger: move provider work out of lock only in a dedicated P2-B implementation with durable operation ID/revision, stale-completion rejection, duplicate-completion handling, cancellation/supersession rules, and restart behavior.

### P3 — explicit provider capability contract

Capabilities are represented by the typed `LlmProviderCapabilities` contract and selected from configured provider family only. Model names do not influence capability selection.

| Provider | Temperature omit | JSON mode | Token limit semantics | Terminal protocol | Request ID | Idempotency key |
|---|---:|---|---|---|---:|---:|
| OpenAI | yes | `openai_json_object` | `max_completion_tokens` | `openai_chat` | yes | no |
| llama-server | yes | `openai_json_object` | `max_tokens` | `openai_chat` | yes | no |
| Ollama | no | `ollama_json` | `ollama_num_predict` | `ollama_chat` | no | no |

Unsupported enabled-provider capability sets fail closed. `provider=disabled` remains outside enabled-provider payload capability selection.

### P4 — normalized completion outcomes

Normalized outcomes are:

- `completed`
- `truncated`
- `refused`
- `filtered`
- `no_usable_content`
- `unknown_terminal_reason`

Recognized truncation/refusal/filter outcomes fail closed even when response content happens to be schema-valid JSON. Ollama `done_reason="length"` is treated as truncation rather than accepted content.

The generic wizard classifier remains intentionally in place as defense in depth. It may be removed only after every enabled provider path is proven to have equivalent abnormal-terminal protection.

### P5 — retry/idempotency/delivery-state contract

The three retry domains remain separate:

1. HTTP/status retry budget.
2. Structured-output / semantic repair budget.
3. User-triggered operation retry.

Automatic HTTP retries are limited to response statuses:

`408, 429, 500, 502, 503, 504`

Default HTTP retry settings are 3 total attempts, 0.5 s base delay, 8.0 s maximum delay, and 0.25 s jitter.

`Retry-After` is honored but capped by `retry_max_delay_s`.

Transport errors before a response are treated as ambiguous delivery and are **not automatically replayed**. The error metadata states `ambiguous_delivery=true`, `automatic_retry=false`, and `retryable=false`.

No fake idempotency-key support is emitted: all current provider capability entries explicitly report `idempotency_key_supported=False`.

### P6 — cancellation/supersession

Deferred for this batch under P2-A.

There is no new active-cancellation API and no claim that synchronous HTTPX provider work can be safely interrupted and durably superseded. Inventing a cancellation state without durable operation identity/revision would risk late stale publication or misleading recovery guarantees.

Current safety posture is serialization: the session lock prevents concurrent same-session mutations from racing publication.

Follow-up trigger: cancellation/supersession belongs with P2-B durable operation identity/revision semantics.

### P7 — wizard transition/state-machine disposition

Transition sites were audited rather than broadly rewritten. State mutation remains concentrated in the wizard service and its failure helper; existing explicit retry gates are retained.

A large transition-framework rewrite was deliberately avoided because it would broaden product risk without being required to close the identified production-hardening defects.

Follow-up trigger: centralize transitions only as a dedicated state-machine refactor with a complete legal-transition matrix and legacy-session migration coverage.

### P8 — crash recovery

No durable in-flight operation protocol was added, and no crash-recovery guarantee is claimed.

A process crash can still leave an operation-state snapshot such as `drafting_ir` or `generation_started` without a durable record proving whether the provider POST was delivered. The application therefore does **not** auto-replay ambiguous provider work on restart.

Follow-up trigger: durable crash recovery requires the P2-B operation-ID/revision protocol and explicit abandoned-operation recovery semantics.

### P9 — structured observability

Provider requests now emit safe structured metadata including provider, endpoint, attempt, elapsed time, configured timeout, payload byte count, prompt fingerprint, retry decision/reason, normalized completion outcome, finish reason, and provider request ID when available.

Raw prompts, raw user design text, authorization data, API keys, raw provider bodies, and private artifact paths are not default log fields.

### P10 — debug artifact privacy and lifecycle

Debug capture remains opt-in/default-off, but enabled capture is now metadata/fingerprint-only rather than a license to persist raw prompts or provider content.

Persisted safe fields include counts, lengths, provider/model identifiers, normalized metadata, and short SHA-256 fingerprints. Raw `messages`, raw completion objects/bodies, authorization headers, API keys, parsed user-design bodies, and private paths are omitted/rejected at the writer boundary.

Unsupported diagnostic structures are dropped with WARNING visibility instead of being silently persisted.

Existing lifecycle rules remain: private debug directory, per-stage retention, per-session byte retention, deterministic pruning, newest-artifact preservation, and best-effort debug failures that do not convert a successful canonical wizard operation into failure. Canonical session persistence remains fail-closed.

### P11 — configuration audit

Configuration precedence remains:

`environment override > TOML > default`

Unknown top-level, `[web]`, and `[llm]` keys fail closed. Explicit malformed values do not silently fall back to defaults.

| TOML key | Environment override | Type | Default | Principal constraints / behavior |
|---|---|---|---|---|
| `web.data_dir` | `KICAD_PCB_WEB_DATA_DIR` | string/path | `data` | non-empty string; relative TOML path resolves against config-file directory |
| `web.mutation_lock_timeout_s` | `KICAD_PCB_WEB_MUTATION_LOCK_TIMEOUT_S` | float | `2.0` | finite and `> 0` |
| `llm.provider` | `KICAD_PCB_WEB_LLM_PROVIDER` | enum string | `disabled` | `disabled/openai/ollama/llama_server` |
| `llm.model` | `KICAD_PCB_WEB_LLM_MODEL` | optional string | `None` | required non-empty for enabled provider |
| `llm.base_url` | `KICAD_PCB_WEB_LLM_BASE_URL` | optional string/URL | `None` | HTTP(S), hostname required, no credentials/query/fragment; required for Ollama/llama-server |
| `llm.api_key` | `KICAD_PCB_WEB_LLM_API_KEY` | optional string | `None` | required non-empty for OpenAI; never logged |
| `llm.timeout_s` | `KICAD_PCB_WEB_LLM_TIMEOUT_S` | float | `60.0` | finite, `> 0`, `<= 300` |
| `llm.temperature` | `KICAD_PCB_WEB_LLM_TEMPERATURE` | float | `0.2` | finite, `0..2` |
| `llm.temperature_mode` | `KICAD_PCB_WEB_LLM_TEMPERATURE_MODE` | enum string | `send` | `send/omit`; Ollama rejects `omit` |
| `llm.max_tokens` | `KICAD_PCB_WEB_LLM_MAX_TOKENS` | optional int | `None` | positive if set; TOML floats rejected rather than truncated |
| `llm.system_prompt_version` | `KICAD_PCB_WEB_LLM_SYSTEM_PROMPT_VERSION` | string | `v1` | non-empty |
| `llm.spec_max_repair_rounds` | `KICAD_PCB_WEB_LLM_SPEC_MAX_REPAIR_ROUNDS` | int | `2` | `>= 0`; TOML floats/bools rejected |
| `llm.ir_max_repair_rounds` | `KICAD_PCB_WEB_LLM_IR_MAX_REPAIR_ROUNDS` | int | `2` | `>= 0`; TOML floats/bools rejected |
| `llm.enable_streaming` | `KICAD_PCB_WEB_LLM_ENABLE_STREAMING` | bool | `false` | `true` rejected because streaming is not implemented |
| `llm.request_log_redaction` | `KICAD_PCB_WEB_LLM_REQUEST_LOG_REDACTION` | bool | `true` | `false` rejected; provider request logs are always redacted |
| `llm.debug_artifact_capture` | `KICAD_PCB_WEB_LLM_DEBUG_ARTIFACT_CAPTURE` | bool | `false` | opt-in; metadata-only capture |
| `llm.retry_max_attempts` | `KICAD_PCB_WEB_LLM_RETRY_MAX_ATTEMPTS` | int | `3` | `1..10` |
| `llm.retry_base_delay_s` | `KICAD_PCB_WEB_LLM_RETRY_BASE_DELAY_S` | float | `0.5` | finite and `>= 0` |
| `llm.retry_max_delay_s` | `KICAD_PCB_WEB_LLM_RETRY_MAX_DELAY_S` | float | `8.0` | finite, `>= base`, `<= 60` |
| `llm.retry_jitter_s` | `KICAD_PCB_WEB_LLM_RETRY_JITTER_S` | float | `0.25` | finite, `>= 0`, `<= max delay` |

Strict coercion changes close two silent-configuration hazards: TOML floats can no longer be truncated into integer settings, and non-finite floats (`nan`/`inf`) cannot bypass range checks. Boolean TOML numeric values are rejected, and strings are type-checked rather than blindly passed through `str(...)`.

### P12 — security/trust boundary

Provider base URLs must use `http` or `https`, include a hostname, and may not include embedded username/password credentials, query strings, or fragments. Malformed ports/URLs fail closed without echoing the supplied URL value in the validation error.

HTTP redirect following is not enabled by the provider client and redirect behavior is covered by regression tests. No permissive URL compatibility fallback was added.

API keys and authorization headers are never included in frontend errors or debug artifacts. Provider error bodies and raw prompt content are not propagated into the stable API error payload.

### P13 — stable API error/retryability contract

Public error payloads now carry an explicit typed retryability bit rather than requiring the frontend to parse prose or infer retryability from object shape.

Provider-originated retryability is preserved through wizard error wrapping while provider bodies/internal text remain sanitized.

Representative terminal completion failures remain non-success HTTP responses and persist operational failure state as appropriate.

### P14 — regression architecture

New/focused tests cover:

- provider capability matrices and exact payload behavior;
- provider terminal normalization including schema-valid abnormal completion cases;
- exact HTTP retry/no-replay behavior;
- config type/range/cross-field/provider validation;
- URL/redirect/security rules;
- structured/redacted observability;
- metadata-only debug artifact content and retention;
- stable route retryability/error behavior;
- same-session serialization and different-session lock independence.

Existing repair-budget and user-triggered-retry tests remain the source of truth rather than duplicating parallel test machinery.

### P15 — npm audit disposition

Permanent CI on the accepting SHA reported the pre-existing frontend dependency posture during `npm ci`:

- 1 low
- 1 moderate
- 6 high

No broad `npm audit fix`, dependency upgrade, or `frontend/package-lock.json` change was mixed into this wizard/LLM production-hardening batch. Those findings remain a separate frontend dependency-hardening concern unless a future focused audit identifies a narrow urgent fix.

## Exact product acceptance evidence

Permanent CI run `31430879076` on `b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41`:

| Job | Job ID | Result |
|---|---:|---|
| Python lint, types, unit and web tests | `93593786514` | success |
| Frontend lint, unit tests and production build | `93593786559` | success |
| Browser smoke tests | `93597157519` | success |
| KiCad integration | `93597157553` | success |
| Wheel and sdist package smoke | `93597157574` | success |

### Python gates

- Ruff: all checks passed.
- Ruff format: 467 files already formatted.
- mypy: success, no issues in 214 source files.
- pytest: 2,817 passed, 7 skipped; 2,824 collected.
- Python coverage: 90.85% (70% required threshold).

The permanent CI workflow executed the authoritative exact-SHA forms of the required Ruff/format/mypy/unit+web gates. A separate networked local clone was not available in the connected-GitHub execution environment, so this evidence does not falsely claim a second independent local invocation.

### Frontend

- ESLint: success.
- Vitest: 15 test files passed, 143 tests passed.
- Frontend line coverage: 70.47% in the emitted report.
- Production build: success.
- Committed SPA bundle diff verification: success.
- Frontend source was unchanged by this hardening diff; permanent CI still validated it.
- Informational React `act(...)` warnings in existing `CopyButton` tests remain non-failing baseline test noise and were not introduced by this batch.

### Browser

Playwright Chromium smoke suite:

- 11 passed.
- 1 skipped: live LLM wizard flow requiring an enabled provider.

The browser job also logged the existing non-fatal schematic-preview skip when `rsvg-convert` is unavailable; this is unrelated to the LLM production-hardening changes.

### KiCad integration and package smoke

Both permanent jobs succeeded on the exact accepting SHA. No waiver or manual bypass was used.

## Artifacts from accepting run

| Artifact | ID | SHA-256 digest |
|---|---:|---|
| `ruff-report-31430879076` | `9079067819` | `b9a3d1701318205115faf5ae70d33c4fd351a02a350c869a58503ad7274b783d` |
| `built-spa-31430879076` | `9079069645` | `eae75ff76f2905e34c41caa9e42aa842c26fd0e71f303f7e26c4275bb54985bd` |
| `frontend-coverage-31430879076` | `9079070094` | `4435dced044ba65eb1a7eb5141bd73faa135c17f291f1b7cf740369158d6ccbb` |
| `python-coverage-31430879076` | `9079438474` | `7ff482ee2cba65e4b652c4eb096bc38d703d2216569ce6a5e44fbbbca8a37b3c` |
| `package-smoke-31430879076` | `9079446728` | `8284ab58f36e5dfe1c54b653651bae9f20b173e011cf844b2be8c36c2fce70a1` |

## Scope evidence

Comparison range:

`bdfaf93e4f955ba5dafc34e26ab2cae34a280725..b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41`

There are 20 changed files over 19 commits:

1. `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_IMPLEMENTATION_NOTES_2026-08-10.md`
2. `src/kicad_pcb_web/errors.py`
3. `src/kicad_pcb_web/services/_wizard_session_io.py`
4. `src/kicad_pcb_web/services/llm/__init__.py`
5. `src/kicad_pcb_web/services/llm/base.py`
6. `src/kicad_pcb_web/services/llm/capabilities.py`
7. `src/kicad_pcb_web/services/llm/factory.py`
8. `src/kicad_pcb_web/services/llm/llama_server_client.py`
9. `src/kicad_pcb_web/services/llm/ollama_client.py`
10. `src/kicad_pcb_web/services/llm/openai_client.py`
11. `src/kicad_pcb_web/services/wizard.py`
12. `src/kicad_pcb_web/settings.py`
13. `tests/unit/test_wizard_llm_robustness.py`
14. `tests/web/test_web_llm_config_production_hardening.py`
15. `tests/web/test_web_llm_observability.py`
16. `tests/web/test_web_llm_production_hardening.py`
17. `tests/web/test_web_wizard.py`
18. `tests/web/test_web_wizard_concurrency_hardening.py`
19. `tests/web/test_web_wizard_error_contract.py`
20. `tests/web/test_web_wizard_persistence_hardening.py`

No placement, orientation, wire-routing, PCB-layout/routing, frontend-source, or unrelated Circuit-IR product file is in the final implementation diff. The temporary Ruff-format probe workflow used during diagnosis was removed before the accepted product SHA.

## Dangerous fallback / silent-failure audit

The final changed product code was reviewed specifically for the failure modes called out by this hardening batch.

- No model-name-based capability guessing remains in the new capability path.
- No unsupported explicit configuration silently falls back to a default.
- No ambiguous transport delivery is automatically replayed.
- No provider terminal truncation/refusal/filter is accepted solely because content parses as valid JSON.
- No per-session lock is opportunistically released around provider work without CAS/revision protection.
- Debug-artifact sanitization rejects/drops unsafe structures with WARNING visibility rather than silently persisting them.
- Canonical session persistence remains fail-closed.
- Provider/internal/raw-body detail is not surfaced as a successful API result.
- No broad dependency fix or unrelated deterministic-engine fallback was introduced.

## Deliberate deferrals / known limitations

The following are explicit and are **not** represented as implemented features:

1. Aggregate monotonic end-to-end operation deadline (P1-A).
2. Out-of-lock provider execution with durable revision/CAS protection (P2-B).
3. Active cancellation/supersession and late-completion rejection protocol tied to durable operation IDs (P6).
4. Durable in-flight crash/restart recovery (P8).
5. Full transition-framework centralization (P7 refactor option).
6. Frontend npm vulnerability remediation; current CI reports 1 low, 1 moderate, 6 high findings and dependency remediation remains separate work.

Each deferral preserves a truthful fail-closed posture: the code does not claim the missing guarantee, and it does not add a weaker silent fallback in its place.

## Product acceptance conclusion

The production-hardening implementation is accepted on `b2ee1f64ceeb735e14b1ecd4b6b861bf3cc8ea41` with permanent CI 5/5 green. The remaining Ralph-loop step is documentation closure: update the TODO disposition, commit the final docs state, and require permanent CI 5/5 on that exact documentation SHA. That final self-referential SHA/result is reported externally after CI without creating another checkbox-only commit.
