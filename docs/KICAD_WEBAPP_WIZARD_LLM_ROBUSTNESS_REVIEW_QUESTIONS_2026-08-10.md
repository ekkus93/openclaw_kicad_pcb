# KiCad Web App Wizard/LLM Robustness — Review Questions and Issues — 2026-08-10

## Purpose

This document captures follow-up questions and concerns after reviewing:

- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_SPEC_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_ROBUSTNESS_TODO_2026-08-10.md`

The overall robustness batch looks sound, and the cited D1-D7 mechanisms were checked against the reviewed code baseline. The items below should be resolved or explicitly dispositioned before implementation begins so the Ralph loop does not have to make architecture/state-machine decisions implicitly while editing code.

No product code changes are requested by this document.

---

## 1. Clarify the review baseline vs implementation starting SHA

The spec/TODO currently call:

```text
479465102f25f7dd85153477442c1c01b6dfbbe3
```

the starting SHA.

However, the `webapp` branch now points to:

```text
c6e73643ef81d46f79d6a67eb10081c5afb4d817
```

which is the documentation commit that added the robustness spec/TODO, and its parent is `4794651...`.

### Question

Can we distinguish these explicitly as:

- **code-review baseline SHA:** `479465102f25f7dd85153477442c1c01b6dfbbe3`
- **planning/documentation head:** `c6e73643ef81d46f79d6a67eb10081c5afb4d817`
- **implementation starting SHA:** whatever `webapp` points to immediately before the first product-code change

The completion evidence should record the true implementation starting head, not an older review baseline.

---

## 2. D1 needs one exact, non-multiplicative retry-budget contract

The current IR generation path already has an outer semantic repair loop of:

```text
ir_max_repair_rounds + 1
```

LLM calls, while each `_call_llm_for_json()` invocation uses:

```text
max_repairs=0
```

If implementation simply changes `max_repairs=0` to `max_repairs=ir_max_repair_rounds`, the structural and semantic loops become multiplicative.

With the default:

```text
ir_max_repair_rounds = 2
```

that could permit up to 9 LLM calls instead of 3.

### Questions

1. Is `ir_max_repair_rounds` intended to be one **shared total repair budget**, or a separate budget for structural and semantic failures?
2. What is the exact maximum number of LLM invocations allowed for one `generate_ir` operation?
3. Should a regression test assert that exact maximum call count?

### Recommended contract

Use **one shared total repair budget**:

```text
maximum LLM calls = ir_max_repair_rounds + 1
```

regardless of whether each failed attempt was:

- malformed JSON,
- schema-invalid structured output,
- empty/unusable structured content, or
- semantically invalid Circuit IR/netlist.

This prevents a hidden multiplicative retry explosion.

### Terminal-state question

Please also define the terminal mapping explicitly:

- **parseable IR that repeatedly fails semantic netlist validation** → `ir_needs_repair`, preserving the last parsed IR for inspection/repair;
- **malformed/schema-invalid/no-usable-content exhaustion** → operational `failed` for `generate_ir`, because no usable IR draft exists to place into `ir_needs_repair`.

Is that the intended distinction?

---

## 3. D2 should distinguish repairable malformed output from refusal/content-filter/truncation

The current spec groups several cases under transient structured-output failure:

- malformed JSON,
- schema-invalid JSON,
- `content=None`,
- empty content,
- `finish_reason="length"`,
- content-filter/refusal-like outcomes.

These do not necessarily have the same retry semantics.

### Questions

1. Should malformed/schema-invalid JSON be repairable within the structured-output budget? I assume **yes**.
2. Should `null`/empty content with no stronger provider reason be repairable? I assume **yes**, within the same bounded budget.
3. Should `finish_reason="length"` be treated as a distinct typed truncation condition rather than blindly issuing the same request again with the same token limit?
4. Should a provider refusal/content-filter termination be treated as a terminal typed provider outcome rather than automatically retried as though it were transient malformed JSON?

### Concern

The spec says `content: null` can occur for content-filter stops, but elsewhere treats null content as a transient provider condition. Those are not always equivalent.

### Recommended error classification

At minimum, distinguish:

- structured JSON parse/schema failure;
- provider returned no usable content;
- provider response truncated because of output limit;
- provider refusal/content-filter stop;
- actual provider/transport/protocol failure.

Do **not** catch generic `ToolError` in the repair loop and retry all of it. `ToolError` is also used for genuine provider/transport/protocol errors, so the repair path should use a narrower typed error or an explicit classification.

### Test additions requested

Besides `content=None`, please consider targeted tests for:

- malformed JSON → valid JSON recovery;
- empty content → valid recovery;
- repeated no-content exhaustion;
- `finish_reason="length"` disposition;
- content-filter/refusal disposition;
- generic provider/transport `ToolError` does **not** get accidentally treated as a structured-output repair.

---

## 4. D3 needs an explicit temperature capability/configuration contract

The current code has no way to express "do not send temperature":

- `LlmRequest.temperature` is optional;
- `_effective_temperature()` falls back to the configured default;
- `LlmSettings.temperature` is a required float with default `0.2`;
- the factory always propagates it;
- OpenAI and llama-server always put it into the payload.

The spec says temperature should be omitted when not applicable while preserving behavior for providers/models that accept it.

### Questions

1. How exactly will the code determine that temperature is not applicable?
2. Is this meant to be:
   - an explicit config value/mode,
   - a provider capability flag,
   - a model capability registry,
   - or a model-name heuristic?
3. What should be persisted in LLM provenance/config revision so a session created under one parameter policy cannot silently mutate under another?

### Recommendation

Avoid an ad-hoc model-name heuristic buried inside `openai_client.py`.

Prefer an explicit, validated contract such as a temperature policy/capability, for example conceptually:

```text
auto / send / omit
```

or another explicit provider/model capability mechanism.

Whatever mechanism is chosen should have payload-builder tests for both inclusion and omission.

---

## 5. D4: releasing the wizard lock around retry sleeps is not a safe local change by itself

The current wizard mutation holds `resource_lock()` around the provider call, and the synchronous HTTP retry loop performs `time.sleep()` inside that operation.

The problem is real, but releasing the per-session mutation lock only for the retry delay creates a new race:

1. operation A releases the lock while waiting;
2. operation B mutates the same wizard session;
3. operation A reacquires/continues and may persist state derived from an older session snapshot;
4. newer user/session state can be overwritten unless there is revision/CAS protection.

### Questions

1. Is this batch intended to introduce revision/CAS semantics for wizard-session mutations? If not, releasing the lock should probably not be the selected small fix.
2. Is the preferred bounded fix instead to keep the lock and impose a hard total retry/wall-clock budget?
3. Should `timeout_s`, `retry_max_attempts`, retry delays, and `mutation_lock_timeout_s` get cross-field validation so the operational worst case is actually bounded?

### Important detail

`mutation_lock_timeout_s` only bounds how long a **second caller waits to acquire the lock**. It does not bound how long the current operation holds it.

Also, `retry_max_attempts` is capped, but `timeout_s` and `retry_max_delay_s` currently do not appear to have a useful hard upper bound, so simply documenting a "worst case" is not enough unless those values are constrained.

### Recommendation for this batch

Keep the existing synchronous transport and keep the session lock for correctness, but impose/document an explicit maximum retry/wall-clock policy. Defer lock release/out-of-lock provider execution to a future concurrency design that has revision/CAS protection.

### Test request

The D4 test should prove the actual enforced total bound/cross-field validation, not merely test one backoff-delay calculation.

---

## 6. D5 needs an exact debug-artifact retention policy

The current problem is clear: every captured attempt writes another timestamp/UUID JSON file, with no pruning.

The spec currently permits "per-session count and/or total size cap," which leaves too much implementation discretion.

### Questions

1. What exact per-session file-count limit should be enforced?
2. Should there also be a byte-size cap?
3. Is pruning oldest-first?
4. Does retention apply across both `spec_*` and `ir_*` artifacts together, or separately by stage?
5. What happens when a **single artifact** is larger than the total byte-size cap?
6. What happens if pruning/deletion fails?

### Recommendation

Define deterministic retention before implementation, preferably:

- per-session cap;
- oldest-first deletion;
- optionally a total-byte cap as well;
- deterministic behavior for an oversize single artifact;
- pruning/security failures are explicitly surfaced/logged and are not silently swallowed.

Because these files intentionally contain unredacted prompts/completions, silent retention/pruning failures would be particularly undesirable.

---

## 7. D6 currently contains a requirement/solution contradiction

The spec says the two meanings of `failed` must be distinguishable **without inspecting `error`**.

But one permitted solution is:

```text
error is None => model-declared unsupported design
error is dict => operational failure
```

That still requires inspecting `error`.

### Questions

1. Do we actually want a new explicit discriminator?
2. If so, should it be a new field rather than a new frontend-visible status enum?
3. What is the migration/compatibility behavior for previously persisted wizard sessions that do not contain the new field?
4. Should retry gates use the new discriminator directly rather than `_failure_operation()` inferring meaning from the shape of `error`?

### Recommendation

Prefer an explicit field such as a conceptual:

```text
terminal_reason / failure_kind / outcome_kind
```

rather than adding another frontend-visible `WizardStatus` value unless a wire-enum change is genuinely needed.

That would let the existing status remain `failed` while distinguishing, for example:

- model-declared unsupported design;
- provider/operational failure;
- project-generation failure.

The exact names are not important; the explicit contract is.

Please also define how older persisted sessions with no discriminator are interpreted. That behavior should be tested rather than silently guessed.

---

## 8. D7 is largely implementation-ready, but tighten the loader-error contract

The three D7 findings appear straightforward:

- redundant OpenAI `base_url` re-validation;
- raw `OSError` from `jobs_dir.mkdir()`;
- empty-string `data_dir` resolving to the current working directory.

### Questions / requested tightening

1. Can the expected settings-loader exception be fixed specifically to `ValueError` instead of leaving "ValueError or established error type" open-ended?
2. Can tests cover **both**:
   - `KICAD_PCB_WEB_DATA_DIR=""`; and
   - `data_dir = ""` in TOML?
3. During the requested audit of other settings, can any other empty-string path/string field be explicitly dispositioned in the completion evidence?

---

## 9. Strengthen the regression-test requirements before implementation

The existing testing plan is good, but the following assertions should be explicit so implementation cannot satisfy the checklist while retaining hidden bad behavior.

### D1

Assert the exact maximum LLM call count for a completely failing IR generation request.

### D2

Test truncation and provider refusal/content-filter separately from generic no-content, and prove generic provider/transport failures are not accidentally retried by the structured-output repair loop.

### D3

Assert exact payload dictionaries for temperature inclusion and omission under the chosen policy.

### D4

Assert the actual retry/wall-clock bound or cross-field validation contract, not only a single calculated backoff.

### D5

Assert deterministic pruning order and behavior at the retention boundary.

### D6

Test:

- unsupported/model-declared terminal outcome;
- operational failure;
- retry-gate behavior for each;
- legacy persisted session behavior if a new discriminator is introduced.

### D7

Test empty env and TOML values separately.

---

## 10. Requested spec/TODO updates before Ralph-loop implementation

Before starting implementation, please update or explicitly answer the spec/TODO for these decisions:

1. review baseline SHA vs true implementation starting SHA;
2. one exact D1 total retry budget and terminal-state mapping;
3. D2 finish-reason/content classification and which cases are repairable vs terminal;
4. D3 explicit temperature capability/configuration policy;
5. D4 lock/concurrency decision and actually bounded worst-case policy;
6. D5 exact retention policy;
7. D6 explicit discriminator contract and legacy-session behavior;
8. D7 exact loader exception contract;
9. strengthened targeted regression-test assertions above.

Once these are resolved, the robustness batch should be sufficiently deterministic to implement autonomously without making architecture or failure-semantics decisions opportunistically during the Ralph loop.
