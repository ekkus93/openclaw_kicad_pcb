# KiCad Web App Wizard/LLM Follow-up Hardening — Answers — 2026-08-10

Responses to
`docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_REVIEW_QUESTIONS_2026-08-10.md`, point by
point. Both blocking items are accepted as correct and have been resolved into
`docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SPEC_2026-08-10.md` and
`docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_TODO_2026-08-10.md`.

## 1. F2 — finish-reason/refusal classification contract

Confirmed by direct source read: `test_d2_terminal_finish_reasons_do_not_repair`
(`tests/unit/test_wizard_llm_robustness.py:180-189`) constructs `ScriptedClient` with a
raw `_completion(..., finish_reason=reason)` and expects `_call_llm_for_json` — i.e. the
`_wizard_llm.py` branch — to translate that into the typed terminal error. Removing the
branch while claiming that test stays green unmodified was wrong.

**Resolution: Option A, as recommended.** The spec/TODO now state plainly that:

- the wizard-layer branch is being removed as an intentional ownership decision
  (provider-specific finish semantics belong in the provider adapter), not because it was
  dead code everywhere;
- this is an explicit behavior change for Ollama (previously classified by coincidence
  when `done_reason` happened to match a checked string; after this fix, unclassified —
  falls through to ordinary structured-output parsing) and is documented as such, not
  described as "no behavior change";
- `test_d2_terminal_finish_reasons_do_not_repair` is rewritten so `ScriptedClient` raises
  the typed exception directly, modeling a client whose provider layer already
  classified — consistent with Option A's ownership split — rather than relying on
  `_wizard_llm.py` to translate a raw `finish_reason`.

Both spec contract 2 and TODO Phase 2 were rewritten to state this exactly; the previous
"all D2 tests pass unmodified" and "no behavior change for Ollama" claims were removed.

## 2. F6 — debug-artifact write exception handling

Confirmed by direct source read: `atomic_io.py`'s `atomic_write_bytes` (called by
`atomic_write_json`) catches `Exception` broadly and re-raises as `PersistenceError`
(`except PersistenceError: raise` / `except Exception as exc: raise PersistenceError(...)
from exc`), and JSON-serialization failures in `atomic_write_json` itself raise
`PersistenceError` directly. Separately, `_ensure_private_directory`
(`_wizard_session_io.py:134-144`) has two failure paths: an unwrapped `path.mkdir(...)`
raising a bare `OSError`, and a `chmod` failure wrapped as `PersistenceError`. A handler
that only caught `OSError` would not catch any of the realistic failure shapes.

**Resolution: adopted the recommended contract exactly.** Spec contract 6 and TODO
Phase 6 now specify wrapping the entire `writer()` body — directory security, write, and
the subsequent prune call — in `try/except (OSError, PersistenceError)`, explicitly
covering `_ensure_private_directory`'s unwrapped `mkdir` path, WARNING-logging without a
bare `except Exception`. Testing requirements now call for a `PersistenceError`-raising
simulation (the real shape) rather than a monkeypatched raw `OSError`, plus a separate
`_ensure_private_directory`-failure test covering both its `OSError` and `PersistenceError`
paths.

## 3. F1 — "every provider" wording

Agreed. Changed the Definition of Done (spec and TODO) from "every provider" to "every
**enabled** provider," with an explicit note that `provider=disabled` is exempt (no LLM
request is generated) and that this batch does not add a new rejection for
`provider=disabled, temperature_mode=omit` solely to satisfy the wording.

## 4. F7 — type-sharing dependency direction

Agreed this is not a blocker. Added one sentence to spec contract 7 acknowledging the
`wizard_models.py` → `settings.py` dependency direction is intentional for this small
batch, with a note that a future cleanup may move shared LLM type aliases to a neutral
module if more cross-layer types accumulate. No separate refactor added to this batch.

## 5. F7 — `_set_error` mechanism for `latest_job_id`

Agreed with the recommended shape. Spec contract 7 and TODO Phase 7 now specify the exact
mechanism: call `_set_error(...)` first, then apply
`.model_copy(update={"latest_job_id": job.id})` to its result — `_set_error`'s signature is
not expanded with a generic extra-updates parameter for this one caller.

## 6. F8 — disabled-provider invalid `base_url` framing

Agreed. Reframed throughout (Confirmed Defects F8, contract 8, TODO Phase 8, and both
Definitions of Done) as: the non-string `data_dir` fix is a genuine defect repair, while
the `disabled`+invalid-`base_url` regression test documents and locks down an existing,
correct fail-closed policy — not a defect repair. The distinction is now preserved
explicitly so completion evidence and future reviews don't conflate the two.

## Items accepted as-is

F1's core decision (reject rather than silently accept `ollama + temperature_mode=omit`),
F3, F4, F5, F7's `NoReturn` correction, F8's non-string-`data_dir` fix, and all existing
non-goals were confirmed acceptable as originally written and required no changes.

## Resolution applied

All corrections above are reflected directly in the current revisions of
`KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SPEC_2026-08-10.md` and
`KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_TODO_2026-08-10.md`. No open questions remain
from this review round; the batch is ready for implementation.
