# LLM_DESIGN_TODO.md

# LLM-Assisted Circuit Design Wizard TODO

This TODO covers the work required to add an LLM-assisted wizard workflow to the
web app so a user can:

1. discuss the desired electronic circuit in a guided conversation,
2. produce an approved structured circuit specification,
3. convert that specification into canonical Circuit IR JSON,
4. validate and repair the IR deterministically, and
5. generate a KiCad project using the existing deterministic pipeline.

The LLM layer must be optional and must not replace the existing validated
Circuit IR generation flow.

Provider constraints for this work:

- Supported provider modes: `openai`, `ollama`, `llama_server`, and `disabled`.
- Do not use LiteLLM.
- Provider calls must be implemented directly in repo code, preferably with
  `httpx`.
- Provider configuration must be server-side and file-backed first; web UI
  settings can come later.

Current state snapshot:

- File-backed LLM settings groundwork exists in `src/kicad_pcb_web/settings.py`.
- No LLM provider client exists yet.
- No wizard routes, services, persistence, or UI exist yet.
- No spec schema exists yet.
- No spec-to-IR conversion workflow exists yet.

---

## Task 0: Lock the implementation boundaries

Status: DONE

### 0.1 Preserve the deterministic generation pipeline as the system of record

Status: DONE

Do not let the LLM write KiCad files directly.

The supported flow must remain:

```text
conversation -> circuit spec -> Circuit IR JSON -> validate/fix -> generate project
```

The existing deterministic validation and generation path must remain the only
supported route into schematic generation.

### 0.2 Separate human-facing spec from machine-facing Circuit IR

Status: DONE

Add a first-class `Circuit Spec` model instead of asking the LLM to emit final
Circuit IR as the primary artifact.

Reason:

- makes review easier,
- makes clarification loops explicit,
- reduces invalid or hallucinated IR,
- gives the user a stable artifact to approve before generation.

### 0.3 Define the initial scope of supported design intent

Status: DONE

Start with a constrained MVP rather than arbitrary free-form electronics.

Initial recommended scope:

- passive resistor/capacitor networks,
- op-amp stages,
- timer-based circuits,
- simple transistor/FET stages,
- connectors, power rails, and decoupling,
- single-sheet or modest multi-block circuits.

Explicitly mark out-of-scope behaviors for v1:

- PCB layout intent capture,
- multi-board systems,
- firmware generation,
- SPICE simulation synthesis,
- unconstrained natural-language generation straight to KiCad files.

---

## Task 1: Finalize the configuration model

Status: DONE

### 1.1 Keep file-backed configuration as the first control surface

Status: DONE

Current config file target:

```text
./kicad_pcb_web.toml
```

Override path:

```text
KICAD_PCB_WEB_CONFIG_FILE
```

### 1.2 Finalize the LLM config schema

Status: DONE

Confirm and document the full set of settings required for runtime use.

Required settings:

- `provider`
- `model`
- `base_url`
- `api_key`
- `timeout_s`
- `temperature`
- `max_tokens`

Likely additional settings:

- `system_prompt_version`
- `spec_max_repair_rounds`
- `ir_max_repair_rounds`
- `enable_streaming`
- `request_log_redaction`

### 1.3 Decide provider-specific required vs optional fields

Status: DONE

Expected rules:

- `openai`: requires `model`, normally requires `api_key`, may use default base URL.
- `ollama`: requires `model`, usually requires `base_url`, no API key by default.
- `llama_server`: requires `model` and `base_url`; API key optional depending on deployment.
- `disabled`: no runtime client should be constructed.

### 1.4 Validate invalid config early at startup

Status: DONE

Reject:

- unsupported provider names,
- negative timeouts,
- invalid token limits,
- malformed base URLs,
- impossible combinations such as `provider = "openai"` with no model.

### 1.5 Add doctor output for LLM readiness

Status: DONE

Extend the doctor endpoint to report:

- configured provider,
- whether required fields are present,
- whether the provider is disabled,
- whether a network probe is supported and whether it is enabled.

Do not leak secrets in doctor output.

---

## Task 2: Add the LLM provider abstraction

Status: DONE

### 2.1 Create a small internal provider interface

Status: DONE

Add a focused service layer under the web app for LLM requests.

Recommended structure:

```text
src/kicad_pcb_web/services/llm/
    __init__.py
    base.py
    factory.py
    openai_client.py
    ollama_client.py
    llama_server_client.py
```

The abstraction must expose domain-level operations rather than raw vendor
payloads.

### 2.2 Implement direct OpenAI API client support

Status: DONE

Requirements:

- use direct `httpx` requests,
- support chat-style completions needed by the wizard,
- normalize response payloads into internal result objects,
- map provider failures into domain-specific errors.

### 2.3 Implement direct Ollama support

Status: DONE

Requirements:

- use direct `httpx` requests,
- support local deployment defaults,
- handle model-not-found and server-unreachable cases clearly,
- normalize output into the same internal result shape.

### 2.4 Implement direct llama-server support

Status: DONE

Requirements:

- use direct `httpx` requests,
- document which API contract is assumed,
- isolate any compatibility differences from the shared wizard logic.

### 2.5 Add a provider factory driven by web settings

Status: DONE

The factory must:

- return no client when provider is `disabled`,
- construct the correct provider client for enabled modes,
- reject unsupported combinations early.

### 2.6 Add provider-level retry and timeout behavior

Status: DONE

Implement bounded retry behavior for transient network failures only.

Do not retry:

- malformed requests,
- authentication failures,
- deterministic provider validation errors.

---

## Task 3: Define the Circuit Spec schema

Status: DONE

### 3.1 Create a Pydantic schema for the user-approved circuit spec

Status: DONE

The schema should capture design intent without requiring KiCad-level symbol and
pin details.

Suggested sections:

- project name,
- purpose,
- supply rails,
- inputs,
- outputs,
- required functional blocks,
- required components or part families,
- packaging preferences,
- constraints,
- assumptions,
- open questions,
- acceptance criteria.

### 3.2 Define a normalized block model for common circuit stages

Status: DONE

Examples:

- gain stage,
- voltage divider,
- filter stage,
- bias network,
- power input,
- output driver,
- timer oscillator.

### 3.3 Add explicit ambiguity markers to the spec schema

Status: DONE

The spec should be able to represent:

- unresolved values,
- unresolved topology choices,
- missing package preferences,
- unconfirmed operating conditions.

This prevents the LLM from silently guessing.

### 3.4 Define approval semantics for the spec

Status: DONE

The user must explicitly approve the spec before IR generation.

Approval state should be tracked separately from draft state.

---

## Task 4: Build the conversation and wizard state model

Status: DONE

### 4.1 Define wizard session persistence

Status: DONE

Decide where wizard sessions live.

Recommended first approach:

- store under the existing web app data directory,
- persist conversation transcript,
- persist current spec draft,
- persist approval state,
- persist generated IR drafts and validation outcomes.

### 4.2 Define the wizard state machine

Status: DONE

Proposed states:

- `drafting_spec`
- `awaiting_user_clarification`
- `spec_ready_for_review`
- `spec_approved`
- `drafting_ir`
- `ir_needs_repair`
- `ir_ready_for_generation`
- `generation_started`
- `completed`
- `failed`

### 4.3 Define transition rules between states

Status: DONE

Examples:

- do not allow IR generation before explicit spec approval,
- do not allow project generation while IR validation is failing,
- allow users to go back from approved spec to spec editing,
- allow repair loops with bounded retry counts.

### 4.4 Define auditability requirements for wizard sessions

Status: DONE

Persist enough detail to explain:

- what the user asked for,
- what the model proposed,
- what spec was approved,
- what IR was generated,
- what repairs were applied,
- what final project was built.

---

## Task 5: Implement prompt contracts and structured LLM outputs

Status: DONE

### 5.1 Create a system prompt for spec elicitation

Status: DONE

The prompt must instruct the model to:

- ask clarifying questions when key information is missing,
- avoid inventing component details unless stated or explicitly assumed,
- surface assumptions separately from confirmed facts,
- stay within supported circuit categories.

### 5.2 Create a system prompt for spec-to-IR conversion

Status: DONE

The prompt must instruct the model to:

- use only the approved spec as source truth,
- emit structured JSON only,
- avoid prose in machine outputs,
- avoid hidden assumptions,
- surface unresolved items explicitly.

### 5.3 Define strict structured response contracts

Status: DONE

Do not parse free-form mixed prose when a structured object is expected.

Preferred outputs:

- spec draft object,
- clarification-question object,
- IR draft object,
- repair rationale object.

### 5.4 Add prompt versioning

Status: DONE

Track prompt versions in persisted wizard session data so behavior changes are
auditable and reproducible.

### 5.5 Bound LLM repair loops

Status: DONE

Limit the number of iterative repair attempts for:

- spec clarification,
- spec normalization,
- IR repair after validation failures.

When the cap is reached, the UI must stop and ask the user for intervention.

---

## Task 6: Implement spec-to-IR conversion and repair orchestration

Status: DONE

### 6.1 Build a service that converts approved spec into draft Circuit IR

Status: DONE

This service must return:

- draft IR JSON,
- assumptions,
- unresolved fields,
- confidence warnings.

### 6.2 Pass draft IR through deterministic validation immediately

Status: DONE

Use the existing validation pipeline as soon as the draft is produced.

### 6.3 Integrate deterministic `fix-netlist` behavior before LLM repair

Status: DONE

Repair order should be:

1. schema validation,
2. deterministic auto-fix,
3. re-validate,
4. only then, optional targeted LLM repair.

### 6.4 Add a targeted IR repair prompt fed by concrete validator errors

Status: DONE

If IR still fails after deterministic repair, feed only the minimum required
context back to the model:

- approved spec,
- current IR draft,
- exact validator errors,
- allowed repair rules.

### 6.5 Stop using the LLM once IR is valid

Status: DONE

The LLM must not participate in the actual schematic generation step.

---

## Task 7: Add web API routes for the wizard

Status: IN PROGRESS

### 7.1 Define wizard API schemas

Status: DONE

Add request/response models for:

- create wizard session,
- send user message,
- fetch current wizard state,
- approve spec,
- request IR generation,
- start project generation from approved IR.

### 7.2 Add wizard API routes

Status: DONE

Recommended route family:

```text
/api/wizard/sessions
/api/wizard/sessions/{id}
/api/wizard/sessions/{id}/messages
/api/wizard/sessions/{id}/approve-spec
/api/wizard/sessions/{id}/generate-ir
/api/wizard/sessions/{id}/generate-project
```

### 7.3 Reuse existing job generation APIs where possible

Status: DONE

Avoid duplicating generation logic.

Once IR is valid and approved, use or wrap the existing job creation flow.

### 7.4 Add server-side authorization boundaries if remote exposure is later enabled

Status: DONE

Even if the app remains local-first in v1, document the future boundary now.

---

## Task 8: Add the wizard UI

Status: DONE

### 8.1 Add a new entry point on the home page

Status: DONE

Provide a clear choice between:

- raw Circuit IR workflow,
- LLM-guided wizard workflow.

### 8.2 Build a step-based wizard page

Status: DONE

Suggested steps:

- conversation,
- spec review,
- spec approval,
- IR preview,
- validation results,
- generation.

### 8.3 Keep the UI concise and avoid scroll-heavy control placement

Status: DONE

The critical controls for moving through the wizard should remain visible without
requiring the user to scroll to find the next action.

### 8.4 Show structured review surfaces instead of raw JSON first

Status: DONE

For the spec review step, prefer tables/cards over raw JSON.

For the IR review step, show both:

- a readable component/net summary,
- the raw JSON for advanced users.

### 8.5 Surface validation and repair outcomes clearly

Status: DONE

The UI must show:

- validator errors,
- deterministic fixes applied,
- any LLM repair attempts,
- the final approved IR used for generation.

---

## Task 9: Add privacy, security, and safety controls

Status: DONE

### 9.1 Keep all provider credentials server-side

Status: DONE

Do not expose API keys to browser JavaScript.

### 9.2 Redact secrets from logs and persisted artifacts

Status: DONE

Never write:

- API keys,
- raw auth headers,
- provider secrets,
- full unredacted error payloads containing secrets.

### 9.3 Bound prompt inputs and outputs

Status: DONE

Protect against oversized prompts and responses that could destabilize the local
app or fill the job/session store.

### 9.4 Add explicit unsupported-use handling

Status: DONE

The wizard should refuse or redirect requests that are outside supported
electronics-design scope rather than improvising.

### 9.5 Review prompt-injection handling around user-supplied text

Status: DONE

Treat all user text as untrusted input.

The wizard should isolate:

- system instructions,
- tool context,
- user content,
- validator feedback.

---

## Task 10: Add observability and diagnostics

Status: DONE

### 10.1 Add structured logs for wizard lifecycle events

Status: DONE

Track:

- session created,
- provider selected,
- prompt version used,
- spec approved,
- IR validated,
- generation started,
- generation completed or failed.

### 10.2 Add safe diagnostics for provider failures

Status: DONE

Return actionable errors for:

- provider unavailable,
- authentication failure,
- timeout,
- malformed provider response,
- unsupported provider/model combination.

### 10.3 Add optional debug artifact capture for local development

Status: DONE

Persist sanitized prompt/response snapshots behind an explicit debug setting.

This must default to off.

---

## Task 11: Test the full stack

Status: DONE

### 11.1 Unit-test settings and provider selection

Status: DONE

Cover:

- config loading,
- env override precedence,
- disabled mode,
- invalid provider config.

### 11.2 Unit-test provider clients with mocked HTTP responses

Status: DONE

Cover:

- success paths,
- malformed payloads,
- timeouts,
- auth errors,
- retry behavior.

### 11.3 Unit-test spec schema validation

Status: DONE

Cover:

- complete spec,
- ambiguous spec,
- unsupported spec,
- approval gating.

### 11.4 Unit-test wizard state transitions

Status: DONE

Ensure invalid transitions are rejected.

### 11.5 Integration-test the wizard API flow

Status: DONE

Cover:

- create session,
- conversation turn,
- approve spec,
- generate IR,
- repair invalid IR,
- generate project.

### 11.6 Add fixture-based golden tests for spec-to-IR conversion

Status: DONE

Use a small curated set of circuit examples with expected spec and expected IR.

### 11.7 Add regression tests for failure cases

Status: DONE

Examples:

- hallucinated symbol names,
- invalid pin names,
- contradictory user constraints,
- missing supply rails,
- unresolvable topology ambiguity.

---

## Task 12: Document the workflow

Status: DONE

### 12.1 Update README with the LLM wizard architecture

Status: DONE

Document:

- what the wizard does,
- what it does not do,
- supported providers,
- config file format,
- security boundaries.

### 12.2 Add a dedicated design doc for prompt and state architecture

Status: DONE

Include:

- provider abstraction,
- session persistence,
- spec schema,
- prompt contracts,
- repair loop rules.

### 12.3 Add an operator guide for local deployment

Status: DONE

Cover:

- running with OpenAI,
- running with Ollama,
- running with llama-server,
- debugging misconfiguration,
- interpreting wizard failures.

---

## Task 13: Define the implementation order

Status: DONE

### 13.1 Recommended implementation sequence

Status: DONE

Implement in this order:

1. finalize config and startup validation,
2. add provider abstraction and direct clients,
3. add spec schema and wizard state model,
4. add conversation and spec approval API,
5. add spec-to-IR conversion orchestration,
6. add UI flow,
7. add observability and diagnostics,
8. add wider integration/regression coverage,
9. update docs.

### 13.2 Recommended MVP cut

Status: DONE

The smallest useful MVP should include:

- file-backed provider config,
- one provider enabled end-to-end,
- wizard session persistence,
- structured spec drafting,
- explicit spec approval,
- spec-to-IR draft generation,
- deterministic validation plus auto-fix,
- manual handoff to existing project generation.

This MVP does not need:

- multi-provider UI switching,
- prompt tuning controls in the UI,
- streaming chat tokens,
- advanced collaboration features.