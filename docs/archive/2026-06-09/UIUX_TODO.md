# UIUX1_TODO.md

The canonical redesign backlog and completion record now lives at
`docs/UIUX1_TODO.md`.

Use that file for current status, validation notes, and acceptance closeout.

### 3.4 Design a step tracker with real hierarchy

Status: TODO

The step tracker must distinguish:

- not started,
- active,
- completed,
- blocked.

### 3.5 Define motion and transitions deliberately

Status: TODO

Add only a few meaningful transitions, such as:

- step transitions,
- success/failure state reveal,
- advanced detail expansion,
- completion confirmation.

---

## Task 4: Redesign the conversation step

Status: TODO

### 4.1 Replace the generic control slab with a real authoring surface

Status: TODO

The conversation step should center on:

- message composer,
- transcript,
- next-action button,
- concise guidance.

### 4.2 Improve transcript readability and hierarchy

Status: TODO

Differentiate assistant vs user messages clearly through:

- alignment,
- surface treatment,
- spacing,
- labels,
- timestamps only if useful.

### 4.3 Provide concise guidance for the first message

Status: TODO

Guide the user toward including:

- purpose,
- inputs/outputs,
- rails,
- component constraints,
- packaging preferences.

Keep this guidance brief.

### 4.4 Define the clarification-loop UI

Status: TODO

When the wizard is waiting for more information, the UI should clearly say so.

The next needed input should be visually obvious.

### 4.5 Surface unsupported requests clearly and respectfully

Status: TODO

If the wizard marks a request as unsupported, show:

- why,
- what is missing or contradictory,
- whether the user can refine the request.

---

## Task 5: Redesign the spec review step

Status: TODO

### 5.1 Turn spec review into a real approval checkpoint

Status: TODO

The spec review step should feel like a deliberate gate before IR generation.

### 5.2 Group spec fields into human-readable sections

Status: TODO

Recommended groups:

- purpose,
- project name,
- supply rails,
- inputs,
- outputs,
- functional blocks,
- assumptions,
- open questions,
- unsupported reasons.

### 5.3 Replace weak summaries with richer cards/lists

Status: TODO

Do not rely on sparse counters alone.

Show readable summaries of what the circuit is supposed to do.

### 5.4 Place approval and revision affordances near the review content

Status: TODO

The user should be able to either:

- approve the spec, or
- continue clarifying it,

without scanning the page for the correct controls.

### 5.5 Make unresolved questions impossible to miss

Status: TODO

Open questions should be visually tied to why approval is blocked.

---

## Task 6: Redesign the Circuit IR review step

Status: TODO

### 6.1 Lead with validation status, not raw JSON

Status: TODO

The first thing shown should be whether the IR is usable.

### 6.2 Summarize the generated IR in human terms

Status: TODO

Show:

- component count,
- net count,
- major blocks or connections when possible,
- whether auto-fix ran.

### 6.3 Surface deterministic fixes and warnings clearly

Status: TODO

Users should immediately understand:

- what changed,
- what is still concerning,
- whether the result is safe to generate from.

### 6.4 Move raw JSON into an advanced disclosure

Status: TODO

Keep the raw IR available but not dominant.

### 6.5 Clarify repair-loop outcomes

Status: TODO

If IR repair fails or remains blocked, the UI should explain:

- what failed,
- whether more user clarification is needed,
- whether the wizard exhausted its attempts.

---

## Task 7: Redesign the generation step

Status: TODO

### 7.1 Make project generation feel like a final handoff

Status: TODO

The generation step should visually communicate:

- ready state,
- running state,
- success state,
- failure state.

### 7.2 Improve job-result presentation

Status: TODO

Show:

- job id,
- job status,
- link to the job detail page,
- concise success/failure message.

### 7.3 Offer post-generation next steps

Status: TODO

Examples:

- open job details,
- inspect artifacts,
- start a new wizard session.

### 7.4 Make generation errors readable

Status: TODO

Summarize the error first and keep raw payloads behind disclosure.

---

## Task 8: Rewrite the interaction logic in the frontend

Status: TODO

### 8.1 Refactor the frontend around state-driven rendering

Status: TODO

Do not keep the current UI logic as a loose collection of independent panel
renderers.

Organize rendering around the visible wizard states and steps.

### 8.2 Derive visible step state from backend session data

Status: TODO

Create a clear mapping from backend status values to:

- current visible step,
- completion flags,
- blocked flags,
- primary action label.

### 8.3 Centralize primary-action computation

Status: TODO

The frontend should compute one obvious current action rather than spreading
button enablement logic across the page.

### 8.4 Improve error rendering consistency

Status: TODO

All wizard actions should surface errors in a consistent location and format.

### 8.5 Support empty, loading, success, and failure sub-states cleanly

Status: TODO

Avoid abrupt placeholder shifts or inconsistent panel resets between actions.

---

## Task 9: Rewrite the page structure and markup

Status: TODO

### 9.1 Rebuild `src/kicad_pcb_web/templates/wizard.html`

Status: TODO

Replace the current panel arrangement with explicit step-oriented structure.

### 9.2 Add semantic hooks for step states and emphasis

Status: TODO

Markup should support styling for:

- active step,
- completed step,
- blocked step,
- active panel,
- advanced disclosure,
- status banner.

### 9.3 Add accessible labels and landmarks

Status: TODO

Ensure strong semantics for:

- headings,
- buttons,
- transcript region,
- status messages,
- expandable advanced content.

### 9.4 Ensure keyboard-friendly flow

Status: TODO

Critical actions must be reachable in a sensible tab order.

---

## Task 10: Implement the visual system in CSS

Status: TODO

### 10.1 Decide whether wizard styling stays in `app.css` or moves to a dedicated file

Status: TODO

Use the smallest maintainable change, but avoid making shared styles messy.

### 10.2 Add wizard-specific design tokens or variables

Status: TODO

Define reusable values for:

- colors,
- spacing,
- radii,
- shadows,
- typography,
- animation timing.

### 10.3 Implement the step tracker styling

Status: TODO

Ensure the tracker reads clearly at a glance.

### 10.4 Implement surface hierarchy and panel emphasis

Status: TODO

Primary content should visually dominate secondary and advanced content.

### 10.5 Tune the responsive layout

Status: TODO

Verify desktop, tablet, and narrow mobile behavior separately.

### 10.6 Remove leftover shell-like styling from the old wizard layout

Status: TODO

Do not leave obsolete wizard classes or dead style fragments behind.

---

## Task 11: Tighten the copy and UX language

Status: TODO

### 11.1 Replace vague headings with action-oriented language

Status: TODO

Every step label and major section title should tell the user what to do or what
they are reviewing.

### 11.2 Reduce explanatory text volume

Status: TODO

Keep helper copy short and high-signal.

### 11.3 Standardize button labels and action phrasing

Status: TODO

Use labels that match the visible step model.

### 11.4 Make trust boundaries explicit in copy

Status: TODO

Tell the user where human approval and deterministic validation happen.

---

## Task 12: Extend UI tests to lock the redesign

Status: TODO

### 12.1 Update the wizard UI contract tests for the new structure

Status: TODO

Lock in:

- step tracker,
- primary action surface,
- spec review checkpoint,
- IR review checkpoint,
- generation result section.

### 12.2 Add tests for state-specific primary actions

Status: TODO

Cover:

- no session,
- awaiting clarification,
- spec ready for review,
- spec approved,
- IR valid,
- generation complete.

### 12.3 Add tests for blocked and failure messaging

Status: TODO

Cover:

- unsupported request,
- validation failure,
- generation failure.

### 12.4 Add tests for advanced disclosure presence

Status: TODO

Ensure raw JSON remains accessible without dominating the default page.

---

## Task 13: Perform live UX validation in the browser

Status: TODO

### 13.1 Run the full happy path manually

Status: TODO

Validate:

1. create session,
2. clarify request,
3. review and approve spec,
4. generate IR,
5. inspect fixes/warnings,
6. generate project,
7. open job detail.

### 13.2 Run blocked and error flows manually

Status: TODO

Validate:

- disabled provider,
- unsupported request,
- incomplete spec,
- failed IR generation,
- failed project generation.

### 13.3 Validate viewport behavior manually

Status: TODO

Check:

- desktop,
- laptop-height viewport,
- narrow mobile width.

### 13.4 Verify that key controls stay visible during active steps

Status: TODO

Ensure the user does not have to scroll hunt to proceed.

---

## Task 14: Clean up and document the redesign

Status: TODO

### 14.1 Remove obsolete wizard markup, JS branches, and CSS

Status: TODO

Delete dead code from the old shell-based implementation.

### 14.2 Update any README or operator-guide screenshots/text if needed

Status: TODO

Keep docs aligned with the redesigned interaction model.

### 14.3 Add implementation notes for future UX work

Status: TODO

Document:

- what was intentionally deferred,
- what state mappings exist,
- where the frontend step model is encoded.

---

## Task 15: Final acceptance checklist

Status: TODO

### 15.1 UX acceptance

Status: TODO

Confirm all of the following:

- A first-time user can identify the current step immediately.
- There is exactly one visually dominant next action.
- Spec approval feels like a distinct checkpoint.
- IR review feels validation-first rather than JSON-first.
- Generation feels like a final handoff rather than another generic button.
- Core controls remain visible without unnecessary scrolling.

### 15.2 Engineering acceptance

Status: TODO

Confirm all of the following:

- Existing wizard API semantics remain intact.
- UI contract tests are updated and green.
- Wizard behavior tests remain green.
- Lint and type checks remain green for touched files.

### 15.3 Design-quality acceptance

Status: TODO

Confirm all of the following:

- The page has a clear visual direction.
- Hierarchy is obvious without reading every label.
- Advanced detail is available but not dominant.
- The page no longer feels like a thin shell over API calls.