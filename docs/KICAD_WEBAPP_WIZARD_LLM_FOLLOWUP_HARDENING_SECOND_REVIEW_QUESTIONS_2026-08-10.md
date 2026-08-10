# KiCad Web App Wizard/LLM Follow-up Hardening — Second Review Questions — 2026-08-10

This note records the one remaining issue found after re-reading the **revised** follow-up hardening spec and TODO against the current `webapp` implementation. The earlier F2/F6 review round was largely resolved correctly. F1 and F3–F8 now look ready. The remaining concern is confined to **F2**.

Authoritative planning documents reviewed:

- `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_SPEC_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_TODO_2026-08-10.md`
- `docs/KICAD_WEBAPP_WIZARD_LLM_FOLLOWUP_HARDENING_ANSWERS_2026-08-10.md`

Relevant current source inspected:

- `src/kicad_pcb_web/services/_wizard_llm.py`
- `src/kicad_pcb_web/services/llm/ollama_client.py`

## Remaining blocker — F2 still overstates the post-removal Ollama failure behavior

The revised F2 contract correctly acknowledges that removing the generic wizard-layer `finish_reason` classifier is an intentional behavior change for Ollama. It also correctly moves ownership of provider-specific finish semantics to provider adapters.

However, the current wording still says that after removal an unclassified Ollama truncation/refusal:

> falls through to ordinary structured-output parsing (repairable `invalid_structured_output`, eventually exhausting to `LLM_INVALID_STRUCTURED_OUTPUT`)

That outcome is **not guaranteed by the code path**.

Today `ollama_client.py` returns an `LlmCompletion` with:

- `content` from `message.content`
- `finish_reason` copied from Ollama `done_reason`

The wizard layer currently then classifies selected `finish_reason` strings before parsing JSON. The revised F2 plan removes those checks and intentionally does not add equivalent Ollama-side classification.

After that removal, the remaining generic path is effectively:

```python
completion = llm_client.complete(request)

if not completion.content.strip():
    ... no-usable-content handling ...

parsed = json.loads(completion.content)
validated = response_model.model_validate(parsed)
```

Therefore an Ollama completion whose `done_reason` is unclassified can have several outcomes:

1. empty content → no-usable-content handling;
2. malformed/schema-invalid content → repairable structured-output failure, eventually `LLM_INVALID_STRUCTURED_OUTPUT` if the repair budget is exhausted;
3. **syntactically valid and schema-valid content → accepted as a successful structured result, regardless of the unclassified `done_reason`.**

The revised spec/TODO currently describe only case 2 as though it were inevitable. That is technically incorrect and could hide a new silent-acceptance path.

## Why this matters

The generic wizard-level classifier currently prevents acceptance for exact recognized reasons such as `"length"` regardless of whether the partial content happens to be valid JSON. If that classifier is removed before Ollama gets a provider-owned replacement, an Ollama response can potentially be accepted even when its `done_reason` indicates a terminal condition that the adapter has not classified.

That is weaker fail-closed behavior than the current implementation.

This also conflicts with wording elsewhere in the revised spec such as:

> Classification logic exists in exactly one place per provider family

and the Definition of Done's equivalent “exactly one place per client family” statement.

Under the current revised F2 design, OpenAI/llama-server have one provider-owned classifier, but Ollama has **zero** classifiers because Ollama classification is explicitly deferred.

## Questions for Claude Code

1. Is the intended F2 contract really that Ollama `done_reason` becomes advisory/unclassified metadata for this batch?
2. If yes, is it acceptable that schema-valid Ollama output can be accepted even when `done_reason` would previously have triggered a terminal wizard-layer classification?
3. If that acceptance is intentional, can the spec/TODO state it explicitly instead of promising inevitable `LLM_INVALID_STRUCTURED_OUTPUT` exhaustion?
4. If that acceptance is **not** intentional, what mechanism will preserve fail-closed behavior before the generic wizard classifier is removed?
5. Should the “classification exists in exactly one place per provider/client family” wording be changed to accurately represent the temporary Ollama state if Ollama classification remains deferred?

## Preferred resolution

I recommend **not removing the generic Ollama protection until a deliberate provider-owned replacement exists**.

There are two clean ways to do that.

### Option A — finish the provider-ownership migration for Ollama in this batch

If Ollama's `done_reason` vocabulary can be mapped confidently from documented/observed semantics:

- implement the relevant terminal classification inside `ollama_client.py`;
- add direct Ollama adapter regression tests;
- then remove the generic wizard-layer classifier;
- keep OpenAI/llama-server/Ollama classification fully provider-owned.

This is the cleanest end state.

### Option B — retain the generic classifier temporarily for unclassified providers

If Ollama's reason vocabulary is not sufficiently well-defined to map safely in this batch:

- keep the current generic wizard-level protection for Ollama/raw unclassified completions;
- explicitly document the duplication as temporary technical debt;
- still add the real OpenAI/llama-server provider-path tests from F3;
- defer full classifier ownership migration until Ollama semantics are designed and tested.

This is less architecturally pure, but it does **not** create a new silent-success path while provider parity is incomplete.

## Less-preferred but technically honest resolution

If the project intentionally accepts the weaker temporary Ollama behavior, then the spec/TODO should say exactly what happens:

- Ollama `done_reason` is not interpreted by the wizard after F2;
- malformed/schema-invalid content follows structured-output repair/exhaustion;
- empty content follows no-usable-content handling;
- **schema-valid content may be accepted even when `done_reason` is non-`stop` or otherwise indicates a condition the adapter does not classify**;
- Ollama has no finish-reason/refusal classifier in this batch, so “exactly one classifier per provider family” must be rewritten accordingly.

I do not recommend this option because it weakens the fail-closed behavior that the rest of the robustness work has consistently tried to preserve.

## Requested spec/TODO correction before implementation

Before Ralph-loop implementation, please resolve F2 so the authoritative documents answer all of the following deterministically:

- whether Ollama terminal `done_reason` values are classified in this batch;
- whether schema-valid content is allowed to succeed when `done_reason` is unclassified;
- where classification ownership lives for Ollama;
- whether the generic wizard classifier remains temporarily or is removed only after a provider-owned replacement exists;
- what exact regression test proves the intended Ollama behavior;
- wording that accurately reflects the number/location of classifiers per provider family.

Once this F2 contract is corrected, I do not currently have another blocker in F1 or F3–F8.
