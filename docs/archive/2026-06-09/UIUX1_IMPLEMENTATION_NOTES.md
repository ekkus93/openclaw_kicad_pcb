# Wizard UI/UX Redesign Implementation Notes

## Scope

This note records where the redesigned wizard step model lives and what was
intentionally deferred.

## Frontend Ownership

Primary files:

- `src/kicad_pcb_web/templates/wizard.html`
- `src/kicad_pcb_web/static/wizard.js`
- `src/kicad_pcb_web/static/app.css`

## Step Model

The frontend step model is encoded in `deriveWizardUiState(...)` inside
`src/kicad_pcb_web/static/wizard.js`.

Visible steps:

1. `describe`
2. `spec`
3. `ir`
4. `generate`

Representative backend status mapping:

- `drafting_spec` and `awaiting_user_clarification` -> `describe`
- `spec_ready_for_review` -> `spec`
- `spec_approved`, `drafting_ir`, and `ir_needs_repair` -> `ir`
- `ir_ready_for_generation`, `generation_started`, and `completed` -> `generate`
- `failed` -> `spec` or `generate` depending on whether the failure happened before or after IR generation

The same function also owns:

- step completion and blocked-state flags
- banner copy and semantic class
- primary action label and action id
- optional secondary action for revision notes
- whether the composer remains visible

## Rendering Model

The page is rendered as:

- hero
- persistent action rail
- current-step focus banner
- one visible stage panel at a time

Supporting panels remain mounted but hidden unless active so the step model can
switch state without reloading the page.

## Advanced Disclosure

Advanced technical detail intentionally remains secondary:

- raw Circuit IR JSON is behind `details`
- raw generation payload is behind `details`
- readable stats, warnings, and fixes lead the default view

## Intentionally Deferred

The redesign deliberately did not add:

- a separate CSS bundle just for the wizard
- screenshot-based documentation assets
- automated JavaScript unit tests for `deriveWizardUiState(...)`
- optimistic session polling or streaming token output
- a settings UI for provider configuration

## Follow-up Candidates

Good next UX candidates if the wizard keeps growing:

1. Add browser-level automated tests for the frontend state model.
2. Add explicit loading spinners or elapsed-time indicators for long provider calls.
3. Add richer human summaries for Circuit IR topology beyond counts/fixes/warnings.
4. Add job-result next-step shortcuts for artifacts that matter most.
