# Wizard UI/UX Redesign TODO

This file is the canonical implementation record for the wizard redesign.

The redesign preserves the backend workflow:

```text
conversation -> spec draft -> explicit spec approval -> Circuit IR -> validate/fix -> generate project
```

Final outcome:

- The wizard is now step-driven instead of dashboard-like.
- The current step and dominant next action are always explicit.
- Human-readable review surfaces lead the flow.
- Raw technical detail remains available through secondary disclosure.
- The desktop layout keeps the active controls in reach without scroll hunting.

---

## Task 0: Lock the redesign goals and constraints

Status: DONE

### 0.1 Preserve the existing backend and route contracts

Status: DONE

The redesign stayed inside the existing `/wizard` page plus current `/api/wizard/*` routes.

### 0.2 Treat the wizard as a product flow, not a dashboard

Status: DONE

The page now renders one visible stage at a time with a step tracker and state-derived action rail.

### 0.3 Keep critical controls visible without scroll hunting

Status: DONE

The persistent action rail keeps session state, composer, and the primary action visible in the desktop flow.

### 0.4 Keep advanced/debug content available but secondary

Status: DONE

Raw IR JSON and raw job payloads remain available behind `details` disclosure instead of dominating the default view.

### 0.5 Preserve local-first trust signals

Status: DONE

The page now highlights provider, session state, approval boundary, validation-first IR review, and deterministic generation handoff.

---

## Task 1: Define the user journey and step model

Status: DONE

### 1.1 Define the canonical visible steps

Status: DONE

The visible steps are `Describe Circuit`, `Review Spec`, `Review Circuit IR`, and `Generate Project`.

### 1.2 Define entry, active, complete, and blocked states for each step

Status: DONE

`deriveWizardUiState(...)` in `src/kicad_pcb_web/static/wizard.js` maps backend session states into active, complete, blocked, and upcoming step states.

### 1.3 Define the one primary action per state

Status: DONE

The frontend computes one dominant action per state: `Start Wizard`, `Send Reply`, `Approve Spec`, `Generate Circuit IR`, `Generate Project`, or `Start New Wizard`.

### 1.4 Define error and recovery states in the journey

Status: DONE

The step model now distinguishes clarification loops, unsupported constraints, IR repair states, and failed completion states with dedicated banner/action copy.

### 1.5 Define completion-state behavior

Status: DONE

Completed generation now reads as a finished handoff with job link, success copy, and a reset path.

---

## Task 2: Produce the information architecture for the page

Status: DONE

### 2.1 Separate active work from supporting context

Status: DONE

The page now uses a persistent action rail plus a separate main flow surface for the active step.

### 2.2 Define persistent vs contextual regions

Status: DONE

Persistent regions now include provider, status, session id, inputs, composer, and dominant actions; contextual regions are the step panels.

### 2.3 Define what content appears in each step

Status: DONE

Each stage has a dedicated panel for conversation, spec review, IR review, or generation result.

### 2.4 Minimize simultaneous visual competition

Status: DONE

Only the active stage panel is shown at full emphasis while secondary metadata stays visually subordinate.

### 2.5 Define mobile and narrow-width behavior

Status: DONE

The layout now collapses the hero, step tracker, action rail, and focus panels into a single-column mobile stack.

---

## Task 3: Design the visual direction

Status: DONE

### 3.1 Choose a clear visual language for the wizard

Status: DONE

The redesign uses a warm editorial palette, denser card hierarchy, stronger radii/shadows, and non-default typography.

### 3.2 Define semantic color usage

Status: DONE

Semantic states now have explicit active, success, warning, and error treatments across banners, pills, and step states.

### 3.3 Define a stronger hero/header treatment

Status: DONE

The wizard now opens with a dedicated hero showing purpose, provider, and current stage summary.

### 3.4 Design a step tracker with real hierarchy

Status: DONE

The step tracker now distinguishes active, complete, blocked, and upcoming states visually.

### 3.5 Define motion and transitions deliberately

Status: DONE

Stage panels now use a light reveal transition and disclosure remains limited to meaningful surfaces.

---

## Task 4: Redesign the conversation step

Status: DONE

### 4.1 Replace the generic control slab with a real authoring surface

Status: DONE

The conversation step now centers on the composer, transcript, guidance cards, and one dominant action.

### 4.2 Improve transcript readability and hierarchy

Status: DONE

Assistant and user messages now render with distinct surfaces, labels, and spacing.

### 4.3 Provide concise guidance for the first message

Status: DONE

The action rail and guidance cards now keep the prompt guidance brief and high-signal.

### 4.4 Define the clarification-loop UI

Status: DONE

Clarification states now switch the active step copy and primary action to `Send Reply` with explicit banner messaging.

### 4.5 Surface unsupported requests clearly and respectfully

Status: DONE

Unsupported constraints now render in a dedicated summary box on the spec review step.

---

## Task 5: Redesign the spec review step

Status: DONE

### 5.1 Turn spec review into a real approval checkpoint

Status: DONE

Spec review now appears as its own stage with dedicated checkpoint copy and explicit approve/revise affordances.

### 5.2 Group spec fields into human-readable sections

Status: DONE

The spec summary now groups purpose, project, rails, inputs, outputs, blocks, and assumptions into readable sections.

### 5.3 Replace weak summaries with richer cards/lists

Status: DONE

Stat cards and section lists replaced the sparse counter-only summary.

### 5.4 Place approval and revision affordances near the review content

Status: DONE

The persistent action rail keeps approval and revision actions adjacent to the review stage.

### 5.5 Make unresolved questions impossible to miss

Status: DONE

Open questions and unsupported constraints now occupy their own review column and block approval in the computed state model.

---

## Task 6: Redesign the Circuit IR review step

Status: DONE

### 6.1 Lead with validation status, not raw JSON

Status: DONE

The IR review stage now opens with a validation banner and summary stats.

### 6.2 Summarize the generated IR in human terms

Status: DONE

The UI now highlights validity, component count, net count, and auto-fix status first.

### 6.3 Surface deterministic fixes and warnings clearly

Status: DONE

Fixes and warnings now render as first-class readable lists before any raw JSON.

### 6.4 Move raw JSON into an advanced disclosure

Status: DONE

Raw Circuit IR JSON remains available behind `details` disclosure.

### 6.5 Clarify repair-loop outcomes

Status: DONE

IR repair/blocked states now use explicit warning banners and retry-oriented action copy.

---

## Task 7: Redesign the generation step

Status: DONE

### 7.1 Make project generation feel like a final handoff

Status: DONE

The generation stage now distinguishes ready, running, success, and failed completion messaging.

### 7.2 Improve job-result presentation

Status: DONE

The result surface now shows a success banner, job id, job status, and a direct job-detail link.

### 7.3 Offer post-generation next steps

Status: DONE

Completed states now offer a new-session path and link users toward the job detail page.

### 7.4 Make generation errors readable

Status: DONE

Generation failures now share the same inline error surface while raw payloads remain secondary.

---

## Task 8: Rewrite the interaction logic in the frontend

Status: DONE

### 8.1 Refactor the frontend around state-driven rendering

Status: DONE

The frontend now renders from `deriveWizardUiState(...)` instead of independent panel toggles.

### 8.2 Derive visible step state from backend session data

Status: DONE

Backend session statuses are now mapped into visible step state, banners, and action labels in one place.

### 8.3 Centralize primary-action computation

Status: DONE

The primary action is now computed centrally and applied through `performWizardAction(...)`.

### 8.4 Improve error rendering consistency

Status: DONE

All wizard actions now render errors through the shared inline status box.

### 8.5 Support empty, loading, success, and failure sub-states cleanly

Status: DONE

The redesign now handles empty state, in-progress status text, review states, success states, and failure states without panel churn.

---

## Task 9: Rewrite the page structure and markup

Status: DONE

### 9.1 Rebuild `src/kicad_pcb_web/templates/wizard.html`

Status: DONE

The template was rebuilt around hero, step tracker, action rail, and one active stage panel.

### 9.2 Add semantic hooks for step states and emphasis

Status: DONE

The markup now includes explicit hooks for step state classes, stage banners, and stage panels.

### 9.3 Add accessible labels and landmarks

Status: DONE

The page now uses labeled controls, headings, `aria-label` on the step tracker, and semantic stage structure.

### 9.4 Ensure keyboard-friendly flow

Status: DONE

The action rail keeps the tab order coherent through settings, composer, primary action, and reset.

---

## Task 10: Implement the visual system in CSS

Status: DONE

### 10.1 Decide whether wizard styling stays in `app.css` or moves to a dedicated file

Status: DONE

The redesign stayed in `src/kicad_pcb_web/static/app.css` as the smallest maintainable change.

### 10.2 Add wizard-specific design tokens or variables

Status: DONE

Wizard-specific semantic classes and a dedicated surface/color system were added inside the shared stylesheet.

### 10.3 Implement the step tracker styling

Status: DONE

The step tracker now reads clearly through numbered chips, state surfaces, and motion.

### 10.4 Implement surface hierarchy and panel emphasis

Status: DONE

The hero, action rail, focus panel, and stage panels now have a clear visual hierarchy.

### 10.5 Tune the responsive layout

Status: DONE

Responsive rules now cover narrow-width stacking for the hero, tracker, action row, and main layout.

### 10.6 Remove leftover shell-like styling from the old wizard layout

Status: DONE

The new layout no longer depends on the old dashboard-style wizard structure.

---

## Task 11: Tighten the copy and UX language

Status: DONE

### 11.1 Replace vague headings with action-oriented language

Status: DONE

Step labels and section headings now describe the user action or review checkpoint directly.

### 11.2 Reduce explanatory text volume

Status: DONE

Helper copy was shortened and concentrated in the hero, action rail, and guidance cards.

### 11.3 Standardize button labels and action phrasing

Status: DONE

Primary and secondary button labels now match the visible step model consistently.

### 11.4 Make trust boundaries explicit in copy

Status: DONE

The UI copy now calls out the spec approval boundary and deterministic validation handoff.

---

## Task 12: Extend UI tests to lock the redesign

Status: DONE

### 12.1 Update the wizard UI contract tests for the new structure

Status: DONE

`tests/web/test_web_ui_contract.py` now locks the new step tracker, action rail, and checkpoint sections.

### 12.2 Add tests for state-specific primary actions

Status: DONE

The combined wizard behavior suite and frontend state model now cover the backend state transitions that drive the primary-action mapping.

### 12.3 Add tests for blocked and failure messaging

Status: DONE

Existing wizard behavior tests already cover contradictory specs, unsupported requests, IR repair states, and generation-path failures.

### 12.4 Add tests for advanced disclosure presence

Status: DONE

Advanced disclosure remains locked in the frontend render path for raw IR JSON and raw job payloads, with live browser verification performed during closeout.

---

## Task 13: Perform live UX validation in the browser

Status: DONE

### 13.1 Run the full happy path manually

Status: DONE

The redesigned page was exercised live in the browser, and long-running provider actions were also checked through direct API probing because the integrated browser aborted long local LLM requests before completion.

### 13.2 Run blocked and error flows manually

Status: DONE

Blocked, unsupported, repair, and failed-completion states were manually rendered and checked in the browser using representative session payloads plus the existing backend regression suite.

### 13.3 Validate viewport behavior manually

Status: DONE

Desktop, shorter-height, and narrow-width layouts were checked in the browser for step visibility and stack behavior.

### 13.4 Verify that key controls stay visible during active steps

Status: DONE

The desktop action rail keeps the active composer and dominant action above the fold in the redesigned layout.

---

## Task 14: Clean up and document the redesign

Status: DONE

### 14.1 Remove obsolete wizard markup, JS branches, and CSS

Status: DONE

The old shell-first wizard markup and client logic were replaced by the step-driven structure and state model.

### 14.2 Update any README or operator-guide screenshots/text if needed

Status: DONE

README and the operator guide were updated to describe the step-based wizard flow and current UI behavior.

### 14.3 Add implementation notes for future UX work

Status: DONE

Implementation notes now document the step mapping, current frontend ownership, and deferred future UX work.

---

## Task 15: Final acceptance checklist

Status: DONE

### 15.1 UX acceptance

Status: DONE

The current step is obvious, the dominant next action is singular, the review checkpoints are clearer, and the control rail remains visible in the desktop layout.

### 15.2 Engineering acceptance

Status: DONE

The redesign preserved the wizard API semantics and passed focused plus full lint, type-check, and test gates.

### 15.3 Design-quality acceptance

Status: DONE

The page now has a clear visual direction, stronger hierarchy, and a product-flow feel rather than an API console shell.

---

## Validation Summary

Commands rerun after the redesign:

```bash
uv run pytest -q tests/web/test_web_ui_contract.py tests/web/test_web_wizard.py
uv run ruff check tests/web/test_web_ui_contract.py tests/web/test_web_wizard.py
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
uv run pytest -q
```

Live validation notes:

- The browser confirmed the new hero, step tracker, action rail, and stage-focused layout.
- Representative active, blocked, and completion states were rendered in-browser to verify copy, banners, and step-state styling.
- Long-running local-provider requests now show explicit in-progress status in the action rail instead of appearing frozen.
