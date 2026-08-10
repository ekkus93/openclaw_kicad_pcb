# KiCad Web App Wizard/LLM Follow-up Hardening — Review Questions and Issues

Date: 2026-08-10
Branch: `webapp`

Reviewed documents:

- `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SPEC_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_TODO_2026-08-10.md`

This note records questions/issues found while reviewing the follow-up hardening plan against the current implementation. No product-code changes are proposed here beyond clarifying what the spec/TODO should require.

## Summary

The follow-up review is useful and most of F1–F8 is well scoped. I would not begin implementation yet, however, because two items are substantive blockers:

1. **F2 is internally contradictory about where finish-reason/refusal classification belongs and whether the current wizard-layer branch is actually dead.**
2. **F6 proposes catching the wrong exception type and therefore does not actually guarantee that debug-artifact failures are best-effort end-to-end.**

The remaining items are smaller contract clarifications rather than blockers.

---

## 1. F2 — finish-reason/refusal classification contract is internally contradictory

### Current implementation

`ollama_client.py` currently maps Ollama's `done_reason` directly into `LlmCompletion.finish_reason`.

`_wizard_llm.py` then performs generic classification after `llm_client.complete()` returns:

- `finish_reason == "length"` → `LlmCompletionTruncatedError`
- `finish_reason in {"content_filter", "refusal"}` → `LlmCompletionRefusedError`

For `OpenAiLlmClient` and `LlamaServerLlmClient`, equivalent typed errors are already raised inside the provider parser before an `LlmCompletion` is returned, so the wizard-layer branch is redundant for those provider families.

However, that does **not** make the wizard-layer branch universally dead. It is currently live for Ollama and for fake/test clients that return raw `LlmCompletion` objects.

### Test contradiction

The existing D2 test `test_d2_terminal_finish_reasons_do_not_repair` uses `ScriptedClient`, bypasses `OpenAiLlmClient._parse_completion()`, returns a raw `LlmCompletion(finish_reason="length"|"content_filter")`, and expects `_call_llm_for_json()` itself to raise the typed error.

Therefore these TODO requirements cannot both be true:

- remove the wizard-layer finish-reason/refusal branch;
- keep all existing `test_d2_*` tests green **unmodified**.

Likewise, the statement that there is "no behavior change" for Ollama is not correct if the generic branch is removed. Ollama currently depends on that branch for any OpenAI-like `done_reason` value that happens to match it.

### Question for Claude Code

Which architecture is intended?

#### Option A — provider-owned classification (preferred if documented honestly)

- OpenAI/llama-server classify provider-specific terminal outcomes in their provider parser.
- Ollama remains explicitly unclassified for truncation/refusal in this batch.
- Remove the wizard-layer finish-reason/refusal mapping.
- Update the D2 fake tests so the fake client raises the typed provider error instead of relying on a raw `finish_reason` to be translated by `_wizard_llm.py`.
- Explicitly acknowledge that this is a behavior change/limitation for Ollama rather than calling the branch fully dead.

#### Option B — shared classifier

- Extract the normalized finish-reason mapping into one shared helper.
- Call that helper from the provider/parser boundary and/or generic completion boundary as appropriate.
- Keep one source of truth while retaining generic-client behavior.

I slightly prefer **Option A**, because provider-specific finish semantics belong with the provider adapter, but the spec/TODO must state the behavioral consequences accurately.

### Required spec/TODO corrections

Please remove or revise claims that:

- the wizard branch is "fully dead";
- all existing `test_d2_*` tests must remain unmodified after removing it;
- Ollama behavior is unchanged.

Those three claims are incompatible with the current implementation and tests.

---

## 2. F6 — proposed exception handling does not satisfy the best-effort guarantee

### Current implementation

The proposed fix says to wrap:

```python
atomic_write_json(artifact_path, payload)
```

with `except OSError`.

But `atomic_write_json()` calls the shared atomic persistence layer, which converts filesystem failures into `PersistenceError`. Serialization failures are also converted into `PersistenceError`.

Therefore realistic failures such as:

- disk full,
- permission failure,
- failed fsync,
- failed rename/replace,
- serialization failure,

will generally **not** reach the debug writer as raw `OSError`.

A test that monkeypatches `atomic_write_json` to raise `OSError` would test an exception shape that the real function intentionally translates away.

### Additional uncovered failure path

`_ensure_private_directory(artifact_dir)` runs before `atomic_write_json()`.

That helper may fail during:

- directory creation (`OSError`), or
- POSIX permission hardening (`PersistenceError`).

Those failures currently escape the writer too.

Therefore the current proposed F6 implementation still does not satisfy the Definition of Done statement:

> debug-artifact capture cannot hard-fail a wizard operation at any stage (write, prune, stat, or delete).

### Recommended contract

Treat the **entire expected filesystem/persistence portion of the debug writer** as best-effort:

1. secure/create debug directory;
2. write artifact;
3. prune/stat/delete artifacts.

Catch only the expected filesystem/persistence error classes, at minimum:

- `OSError`
- `PersistenceError`

WARNING-log the failure and return without changing the wizard operation outcome.

Do **not** use a broad `except Exception`, because that would hide programming bugs.

### Required tests

Please add tests that exercise realistic exception shapes:

1. `atomic_write_json` raising `PersistenceError` → WARNING, wizard operation still succeeds.
2. directory creation/security failure (`OSError` or `PersistenceError`) → WARNING, wizard operation still succeeds.
3. existing prune/stat/delete failure tests remain green.

The existing suggestion to test only a monkeypatched raw `OSError` from `atomic_write_json` is insufficient.

---

## 3. F1 — "every provider" should mean every enabled provider

The Definition of Done says:

> `temperature_mode=omit` is either honored or explicitly rejected at load for every provider.

`disabled` is itself an allowed provider, but no request payload exists when the provider is disabled. It is therefore neither meaningful to "honor" nor necessary to reject `temperature_mode=omit` in that state.

### Question / recommendation

Please change this to one of:

- "for every **enabled provider**", or
- explicitly state that `provider=disabled` is exempt because no LLM request is generated.

I do **not** recommend adding a new failure for `provider=disabled, temperature_mode=omit` merely to satisfy the current wording.

---

## 4. F7 — type-sharing dependency direction

The proposal replaces the inline `Literal["send", "omit"]` in `wizard_models.py` by importing `LlmTemperatureMode` from `settings.py`.

This appears cycle-safe today, so it is not a blocker. However, it makes a persisted/wire model depend on the runtime settings module only to share a type alias.

### Question

Is that dependency direction intentional?

For this small batch, importing from `settings.py` is acceptable if intentional. A future cleanup could move shared LLM type aliases to a neutral module if more cross-layer types accumulate.

No separate refactor is required for this batch unless Claude sees an immediate import-cycle or layering problem.

---

## 5. F7 — `_set_error` should preserve `latest_job_id` without unnecessarily expanding the helper

`_set_error()` currently updates:

- `status`
- `failure_kind`
- `error`
- `updated_at`

The `generate_wizard_project` failed-job branch additionally sets `latest_job_id`.

The TODO says to route the branch through `_set_error` while keeping `latest_job_id` unchanged, but it does not specify how.

### Recommended implementation shape

Prefer:

1. call `_set_error(...)`;
2. then `model_copy(update={"latest_job_id": job.id})`.

I would **not** expand `_set_error` with generic extra-update arguments only for this one caller unless there is another concrete use case. That would enlarge a simple helper's API unnecessarily.

Please make the intended mechanism explicit in the TODO so the refactor remains behavior-preserving.

---

## 6. F8 — disabled-provider invalid `base_url` is contract coverage, not really a defect

Current validation checks `base_url` before returning early for `provider="disabled"`. Therefore an explicitly malformed `base_url` still fails settings load even though the provider is disabled.

I agree this is a defensible fail-closed contract: explicitly malformed configuration should not silently become accepted merely because another setting currently disables its use.

### Recommendation

Keep the behavior and add the proposed regression test, but describe this F8 sub-item accurately as:

- **documenting and locking down an existing fail-closed policy**, not
- repairing a functional defect.

This distinction matters for completion evidence and future reviews.

---

## Items I otherwise agree with

The following parts of the follow-up plan look good as written once the issues above are resolved:

- F1: rejecting the currently silent `ollama + temperature_mode=omit` combination rather than pretending it works;
- F3: direct `httpx.MockTransport` coverage of the real OpenAI/llama-server parser path;
- F4: route-level coverage for `LLM_NO_USABLE_CONTENT`, `LLM_COMPLETION_TRUNCATED`, and `LLM_COMPLETION_REFUSED`;
- F5: actual second-call retry-gate tests instead of only inspecting persisted fields;
- F7: `NoReturn` correction and behavior-preserving failed-project cleanup;
- F8: rejecting non-string TOML `data_dir` rather than silently coercing it with `str()`;
- preserving the existing non-goals around deterministic-engine, placement/routing/layout, unrelated IR semantics, CAS/concurrency, and absolute LLM deadlines.

## Readiness recommendation

I recommend updating the spec/TODO before implementation, with **F2 and F6 treated as blockers**. Once those two contracts are corrected and the smaller wording/implementation clarifications above are resolved, the follow-up batch should be ready for a Ralph loop.