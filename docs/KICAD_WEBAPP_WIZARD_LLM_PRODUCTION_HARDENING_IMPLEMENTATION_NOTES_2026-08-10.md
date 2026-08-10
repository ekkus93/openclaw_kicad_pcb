# KiCad Webapp Wizard LLM Production Hardening — Implementation Notes

Date: 2026-08-10
Branch: `webapp`
Specification: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_SPEC_2026-08-10.md`
TODO: `docs/KICAD_WEBAPP_WIZARD_LLM_PRODUCTION_HARDENING_TODO_2026-08-10.md`

## Phase 0 baseline and SHA discipline

- Planning baseline before the spec/TODO pair: `0af8061ac9de7c0344a43a58156b1612c685ba0a`.
- Prior accepted follow-up-hardening final SHA: `0af8061ac9de7c0344a43a58156b1612c685ba0a` (`docs: close follow-up hardening checklist`).
- Production-hardening specification commit: `4fdac562e590d7b8b85937963592db70b50d365a`.
- Production-hardening TODO / implementation-starting SHA: `bdfaf93e4f955ba5dafc34e26ab2cae34a280725`.
- Permanent baseline CI: run `31419168095`, 5/5 green:
  - Frontend lint/unit/build: success.
  - Python lint/types/unit/web: success.
  - Browser smoke: success.
  - KiCad integration: success.
  - Wheel/sdist package smoke: success.

The implementation-starting SHA is intentionally the product tree immediately before the first production-hardening product-code edit. This implementation-note commit is documentation-only and does not replace that SHA.

## Scope

Expected in-scope implementation areas include:

- `src/kicad_pcb_web/settings.py`
- `src/kicad_pcb_web/errors.py`
- `src/kicad_pcb_web/services/llm/**`
- `src/kicad_pcb_web/services/_wizard_llm.py`
- `src/kicad_pcb_web/services/_wizard_session_io.py`
- `src/kicad_pcb_web/services/wizard.py`
- `src/kicad_pcb_web/routes/api_wizard.py`
- targeted `tests/unit/**` and `tests/web/**`
- completion evidence / checklist updates

Explicitly out of scope unless a hardening change proves strictly necessary:

- deterministic symbol placement
- deterministic orientation
- deterministic wire routing
- PCB layout algorithms
- unrelated Circuit IR semantics
- unrelated frontend redesign

## Architecture decision P1 — no aggregate absolute deadline in this batch

Selected: **P1-B**.

Retain truthful HTTPX timeout semantics plus bounded retry-attempt and retry-sleep configuration. Do not describe the configured HTTPX timeout as a hard end-to-end wall-clock deadline: HTTPX timeout behavior is phase/inactivity oriented, and the current synchronous operation can include multiple HTTP attempts, retry sleeps, and structured-output repair attempts.

An aggregate operation deadline is deferred because implementing it correctly requires a monotonic operation budget propagated through every provider attempt, retry sleep, structured-output repair attempt, and semantic IR repair attempt. Adding a superficially named deadline without that propagation would create a false safety guarantee.

No product code may add or advertise an absolute operation deadline in this batch.

## Architecture decision P2 — retain serialized per-session synchronous execution

Selected: **P2-A**.

Keep the per-session mutation lock held across synchronous provider calls, retry sleeps, structured-output repair, semantic IR repair, and canonical session publication. Do not opportunistically release the lock around provider I/O.

Tradeoff: a long provider operation reduces same-session throughput and consumes a worker for the duration of the synchronous request. This is accepted for this hardening release because releasing the lock safely would require durable operation identity, revisions/CAS, stale-result rejection, duplicate-completion handling, cancellation/supersession semantics, and restart recovery as one coherent protocol.

Different sessions retain their independent session locks; this decision does not intentionally serialize unrelated sessions.

## Provider capability representation

Use an explicit typed provider-capability contract keyed by configured provider family (`openai`, `llama_server`, `ollama`). The contract must describe payload-affecting behavior such as temperature omission support, structured-JSON mode, token-limit field semantics, terminal-reason protocol, request-ID availability, and idempotency-key support.

Rules:

- no model-name substring heuristics;
- `provider=disabled` is exempt from enabled-provider payload capabilities;
- unsupported provider/capability combinations fail closed;
- existing `temperature_mode=send|omit` behavior remains truthful;
- capability selection is provider-family configuration, not inferred from a model name.

## Completion normalization migration

Keep the generic wizard finish-reason classifier during the migration. Provider clients will gain typed normalized terminal outcomes, including completed, truncated, refused, filtered, no-usable-content, and unknown-terminal-reason behavior.

The generic classifier may be deleted only after every enabled provider family has direct regression coverage proving equivalent or stronger fail-closed behavior. In particular, it must not be removed merely because OpenAI-compatible clients already classify some terminal reasons.

## Retry and idempotency decision

Keep three retry domains separate:

1. HTTP/status retry attempts inside the provider client.
2. Structured-output / semantic repair attempts in wizard orchestration.
3. A user-triggered retry of a failed wizard operation.

Automatic replay remains prohibited after transport failures where request delivery is ambiguous. Only explicitly enumerated retryable HTTP statuses may be replayed automatically within the HTTP retry budget.

No provider idempotency key will be fabricated or simulated. The explicit capability contract records idempotency-key support; this batch uses no idempotency header unless a provider family is deliberately implemented and tested for it.

## Cancellation scope

This release will not claim active interruption of an already-running synchronous HTTPX request. Cancellation/supersession hardening, where implemented, must instead guarantee that a cancelled or superseded operation cannot later publish stale success. Any limitation while provider I/O is already active must be explicit rather than silently pretending the HTTP request was cancelled.

## State-transition decision

Prefer a narrow centralized transition boundary for new/changed wizard transitions where it materially reduces ambiguous state writes. Do not perform a broad unrelated state-machine rewrite merely to satisfy the checklist. Existing direct transition sites must be audited, and any retained direct writes must have an explicit disposition.

## Crash-recovery posture

Do not claim durable in-flight crash recovery unless durable in-flight metadata and restart reconciliation are actually implemented and tested. In particular, never auto-replay a provider POST after restart when the prior delivery state may be ambiguous.

## npm audit disposition

Treat npm dependency-audit remediation as separate dependency-hardening work unless a narrowly required production-hardening change is identified. Do not mix broad dependency upgrades into the wizard/LLM correctness slice.

## Non-regression rules

The previously accepted F1–F8 protections remain binding. This batch must not:

- add broad `except Exception` recovery to keep a wizard operation moving;
- add silent capability fallbacks;
- add model-name heuristics;
- replay ambiguous-delivery POSTs;
- weaken canonical persistence failures;
- hide debug/observability failures that are required to remain WARNING-visible;
- broaden scope into placement/routing/layout or unrelated Circuit IR behavior.
