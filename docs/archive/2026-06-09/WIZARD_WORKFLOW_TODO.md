# Wizard Workflow TODO

This TODO covers the next wizard iteration: converting the current single-page,
state-switched wizard into a true route-per-step workflow with separate pages,
explicit forward progression, and deliberate backward navigation.

The redesign must preserve the current backend semantics:

```text
conversation -> spec draft -> explicit spec approval -> Circuit IR -> validate/fix -> generate project
```

## Current Problems

- The current wizard is staged visually, but it still lives on one page.
- The browser URL does not identify the current step.
- Back navigation is UI-state based instead of route based.
- The server does not yet enforce step progression through page routing.
- Editing an earlier step does not yet have a fully explicit invalidation model
  for downstream spec, IR, and generation artifacts.
- Refresh/deep-link behavior is weaker than a real wizard because the current UI
  reconstructs state client-side.

## Target Outcome

- Each wizard step has its own page and URL.
- The server is authoritative for what step is currently valid.
- Users can move forward only when the current step is complete.
- Users can move backward to earlier steps intentionally.
- When earlier inputs change, downstream derived artifacts are invalidated
  predictably and visibly.
- The wizard still converges on the same deterministic Circuit IR and project
  generation pipeline.

---

## Task 0: Lock the workflow goals and non-goals

Status: DONE

### 0.1 Preserve current wizard backend semantics

Status: DONE

Do not change the core state machine semantics just to make routing easier.

### 0.2 Convert the UI into a true route-per-step wizard

Status: DONE

The new workflow must use separate pages, not only panel toggles.

### 0.3 Keep the backend as the source of truth for progression

Status: DONE

Step access must be validated on the server side.

### 0.4 Preserve explicit human approval and deterministic handoff boundaries

Status: DONE

Spec approval and IR validation must remain visible, enforced checkpoints.

### 0.5 Define backward navigation as a first-class workflow feature

Status: DONE

Going backward must be intentional and supported, not a side effect.

---

## Task 1: Define the canonical step routes

Status: DONE

### 1.1 Define the route family

Status: DONE

Recommended routes:

1. `/wizard`
2. `/wizard/{session_id}/describe`
3. `/wizard/{session_id}/spec`
4. `/wizard/{session_id}/ir`
5. `/wizard/{session_id}/generate`

### 1.2 Define the index-route behavior

Status: DONE

Specify whether `/wizard` is:

- a start page for new sessions only,
- a redirector to the active session step,
- or both.

### 1.3 Define canonical route names in the backend

Status: DONE

Avoid hard-coded ad hoc route strings throughout templates and JS.

### 1.4 Define redirect behavior for missing or unknown sessions

Status: DONE

Invalid session IDs should resolve predictably.

### 1.5 Define deep-link rules for each step page

Status: DONE

Specify which routes are legal to open directly and which should redirect.

---

## Task 2: Define the server-authoritative workflow contract

Status: DONE

### 2.1 Map backend session states to canonical routes

Status: DONE

Map current states like:

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

into the visible route-per-step flow.

### 2.2 Define allowed forward transitions

Status: DONE

Specify the exact conditions that permit advancing from one page to the next.

### 2.3 Define allowed backward transitions

Status: DONE

Specify which earlier steps can always be revisited and which need a warning or
confirmation because they invalidate later work.

### 2.4 Define server-side redirect rules for skipped steps

Status: DONE

Example: opening `/wizard/{session_id}/generate` before valid IR exists should
redirect to the blocking step.

### 2.5 Define completed-session routing behavior

Status: DONE

Decide what route should own the completed state and whether it stays on
`generate` or redirects to a completion page.

---

## Task 3: Define invalidation and derived-state rules

Status: DONE

### 3.1 Define what happens when the conversation changes after a spec exists

Status: DONE

Specify whether spec approval is cleared and whether old spec artifacts are
replaced.

### 3.2 Define what happens when the spec changes after IR exists

Status: DONE

Old Circuit IR should usually be invalidated explicitly.

### 3.3 Define what happens when IR changes after a generation result exists

Status: DONE

Old job links and completion state may need invalidation or clear historical
labeling.

### 3.4 Define user-visible warnings for destructive backward moves

Status: DONE

Users should understand when moving backward will clear later results.

### 3.5 Define artifact retention rules

Status: DONE

Decide what to keep for audit/history versus what to replace as the active
wizard result.

---

## Task 4: Design the route-level information architecture

Status: DONE

### 4.1 Define the layout shared by all step pages

Status: DONE

Shared regions likely include:

- breadcrumb or step tracker,
- session metadata,
- provider indicator,
- step-specific main content,
- back/forward controls.

### 4.2 Define what is global vs step-local

Status: DONE

Move only the truly persistent surfaces into shared layout.

### 4.3 Define how the transcript appears across steps

Status: DONE

Decide whether transcript remains globally visible, summarized, or only fully
visible on the describe step.

### 4.4 Define how advanced detail is distributed across pages

Status: DONE

Avoid duplicating heavy technical detail on every route.

### 4.5 Define refresh behavior per route

Status: DONE

Every step page should remain meaningful after reload.

---

## Task 5: Create the shared route-level wizard shell

Status: DONE

### 5.1 Decide whether to extend the current template stack or add a wizard base template

Status: DONE

Prefer the smallest maintainable structure.

### 5.2 Build a shared step-shell template

Status: DONE

This shell should support:

- current step display,
- complete/blocked styling,
- shared controls,
- step-local content blocks.

### 5.3 Add canonical navigation elements

Status: DONE

Include explicit `Back`, `Continue`, and route-aware action controls where
appropriate.

### 5.4 Add route-safe status messaging regions

Status: DONE

Messages must survive navigation and reloads appropriately.

### 5.5 Ensure keyboard and accessibility semantics remain strong

Status: DONE

Landmarks, headings, and step status should remain accessible.

---

## Task 6: Implement the start and describe step pages

Status: DONE

### 6.1 Decide whether new sessions are created on `/wizard` or `/wizard/new`

Status: DONE

Document the chosen entry behavior.

### 6.2 Implement the describe-step route and template

Status: DONE

This page should own:

- project-name input,
- symbols-dir input if still needed here,
- message composer,
- transcript,
- clarification loop.

### 6.3 Add explicit continue behavior out of the describe step

Status: DONE

Forward movement should happen only when the spec is reviewable.

### 6.4 Add back behavior for the describe step

Status: DONE

Define whether back returns to the wizard landing page or session list.

### 6.5 Ensure unsupported and clarification states are clear on this route

Status: DONE

Users should understand why they cannot move forward yet.

---

## Task 7: Implement the spec review page

Status: DONE

### 7.1 Add a dedicated spec-review route and template

Status: DONE

The page should feel like a checkpoint, not a panel.

### 7.2 Render all key spec sections in human-readable form

Status: DONE

Include:

- purpose,
- project name,
- rails,
- inputs,
- outputs,
- blocks,
- assumptions,
- open questions,
- unsupported constraints.

### 7.3 Add forward action for `Approve Spec`

Status: DONE

Approving should move the user to the IR step or the IR-ready waiting state.

### 7.4 Add backward navigation to the describe step

Status: DONE

This should support correcting the conversation/spec inputs.

### 7.5 Add revision-note handling from the spec step

Status: DONE

Users should be able to revise without losing the workflow context.

---

## Task 8: Implement the Circuit IR page

Status: DONE

### 8.1 Add a dedicated IR-review route and template

Status: DONE

This page should own validation-first review.

### 8.2 Render the IR validation summary prominently

Status: DONE

Lead with:

- valid vs blocked,
- component count,
- net count,
- auto-fix status.

### 8.3 Render fixes, warnings, and repair guidance

Status: DONE

Users should know whether they can proceed safely.

### 8.4 Add raw Circuit IR disclosure on this page only

Status: DONE

Do not duplicate raw JSON across unrelated steps.

### 8.5 Add back navigation to the spec step

Status: DONE

The back action should participate in the invalidation rules.

---

## Task 9: Implement the generation page

Status: DONE

### 9.1 Add a dedicated generation route and template

Status: DONE

This route should own ready, running, success, and failed final states.

### 9.2 Render the generation readiness summary

Status: DONE

Make it obvious why generation is available.

### 9.3 Render the final job result cleanly

Status: DONE

Include:

- job id,
- job status,
- link to job details,
- concise summary,
- advanced raw payload disclosure.

### 9.4 Add back navigation to the IR step

Status: DONE

Going backward from generation should be explicit and may invalidate the old
generation result.

### 9.5 Define completed-session restart behavior

Status: DONE

Users should be able to start a new wizard clearly from here.

---

## Task 10: Update the backend route layer

Status: DONE

### 10.1 Add HTML UI routes for each wizard step

Status: DONE

Keep them colocated with the current UI route module unless there is a clear
reason to split.

### 10.2 Add helpers for canonical step resolution

Status: DONE

Avoid duplicating route/state mapping logic across handlers.

### 10.3 Add redirect helpers for invalid route access

Status: DONE

Centralize the progression guard logic.

### 10.4 Add helpers for backward-navigation invalidation

Status: DONE

The invalidation rules should not be hidden in templates or browser-only code.

### 10.5 Preserve the current API route family unless a specific gap requires expansion

Status: DONE

Avoid unnecessary API churn.

---

## Task 11: Refactor the frontend interaction model

Status: DONE

### 11.1 Reduce the current single-page wizard JS to route-local behavior

Status: DONE

The current `wizard.js` likely needs decomposition or partial replacement.

### 11.2 Decide what state remains client-side vs server-rendered

Status: DONE

Prefer server-rendered route truth over client-side reconstruction.

### 11.3 Add route-safe action handling

Status: DONE

Posting actions should return the user to the correct route.

### 11.4 Preserve consistent busy, success, and error feedback across routes

Status: DONE

The loading/error model should remain coherent after navigation.

### 11.5 Remove obsolete single-page assumptions

Status: DONE

Delete logic that only exists because all stages shared one DOM.

---

## Task 12: Update visual design for multi-page wizard flow

Status: DONE

### 12.1 Adapt the current wizard shell styling for per-route pages

Status: DONE

Reuse what works, but do not force single-page assumptions.

### 12.2 Design clear forward/back controls

Status: DONE

The navigation must read as workflow controls, not generic buttons.

### 12.3 Design route-aware step tracker states

Status: DONE

The step tracker must still show active, complete, and blocked states on every
page.

### 12.4 Keep primary controls visible on desktop

Status: DONE

Preserve the no-scroll-hunting requirement.

### 12.5 Tune mobile and narrow-height behavior

Status: DONE

Check how separate pages reduce or change the prior layout tradeoffs.

---

## Task 13: Define and implement invalidation UX

Status: DONE

### 13.1 Add user-facing warnings before destructive backward changes when needed

Status: DONE

Warnings should be concise and specific.

### 13.2 Show what downstream artifacts will be cleared

Status: DONE

Examples:

- approved spec,
- Circuit IR,
- generation result.

### 13.3 Add visible confirmation of invalidation after the move

Status: DONE

Users should not wonder whether old derived state is still active.

### 13.4 Ensure invalidation behavior is deterministic and testable

Status: DONE

No hidden side effects.

---

## Task 14: Extend tests for the workflow rewrite

Status: DONE

### 14.1 Update UI contract tests for route-per-step pages

Status: DONE

Lock the new step-route structure.

### 14.2 Add tests for route guarding and redirection

Status: DONE

Cover attempts to skip ahead.

### 14.3 Add tests for backward navigation and invalidation

Status: DONE

Changing earlier steps should invalidate later derived artifacts predictably.

### 14.4 Add tests for completed-session routing

Status: DONE

Ensure completed flows land on the right page.

### 14.5 Add tests for refresh and deep-link behavior

Status: DONE

Reloading a valid step page should preserve meaning.

### 14.6 Retain current backend wizard regressions

Status: DONE

Do not lose the existing spec-to-IR and repair-loop coverage.

---

## Task 15: Perform live browser validation

Status: DONE

### 15.1 Run the full happy path through separate routes

Status: DONE

Validate the user can progress naturally step to step.

### 15.2 Run backward-navigation scenarios

Status: DONE

Validate that going back behaves correctly and visibly.

### 15.3 Run invalidation scenarios

Status: DONE

Check that editing earlier steps clears later state as designed.

### 15.4 Run blocked and failure scenarios

Status: DONE

Cover:

- clarification loop,
- unsupported request,
- IR repair loop,
- generation failure.

### 15.5 Validate refresh/deep-link behavior manually

Status: DONE

Each step route should survive reload and illegal deep links should redirect.

---

## Task 16: Update docs and operator guidance

Status: DONE

### 16.1 Update the README wizard section

Status: DONE

Document the step routes and workflow behavior.

### 16.2 Update the operator guide

Status: DONE

Explain forward/back behavior and invalidation rules.

### 16.3 Add implementation notes for future route-level wizard work

Status: DONE

Capture step mapping, routing rules, and invalidation decisions.

### 16.4 Remove or revise stale single-page wizard documentation

Status: DONE

Keep docs aligned with the actual interaction model.

---

## Task 17: Final acceptance checklist

Status: DONE

### 17.1 Workflow acceptance

Status: DONE

Confirm all of the following:

- Each step has its own page.
- Users can move forward only when the current step is valid.
- Users can move backward intentionally.
- Skipped-step URLs redirect correctly.
- Earlier edits invalidate later derived state predictably.

### 17.2 UX acceptance

Status: DONE

Confirm all of the following:

- The current step is obvious on every route.
- The next action is clear.
- Back/forward controls are understandable.
- Key controls remain visible without scroll hunting on desktop.

### 17.3 Engineering acceptance

Status: DONE

Confirm all of the following:

- Existing wizard API semantics are preserved unless deliberately expanded.
- Route guards are server-authoritative.
- Tests cover progression, invalidation, and deep-link behavior.
- `uv run ruff check .`, `uv run mypy src/kicad_pcb src/kicad_pcb_web`, and
  `uv run pytest -q` are green.