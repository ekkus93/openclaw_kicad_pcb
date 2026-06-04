# Replies to Claude Code — KiCad Web UI/UX Refactor

This file addresses the questions and implementation issues raised in `responses3.md` regarding `KICAD_WEB_UI_UX_REFACTOR_SPEC.md` and `KICAD_WEB_UI_UX_REFACTOR_TODO.md`.

---

## Question 1 — Preview generation: non-fatal vs required

Use **Option B**, with one refinement.

Preview generation should remain **required** for a successful user-facing generated job.

The earlier user instruction should override the new spec language here:

> If the schematic image cannot be generated, that is a bug. The preview is proof to the user that the schematic was created successfully.

So the spec should be corrected. Do **not** silently mark the job successful if preview generation fails.

However, improve the UX around this failure:

```text
Correct behavior:
- If KiCad project generation succeeds but preview generation fails, mark the job failed.
- Show a clear, actionable failure message.
- Explain whether the problem is missing kicad-cli, missing rsvg-convert, invalid schematic output, or preview export failure.
- The setup/doctor page should catch missing preview dependencies before the user reaches generation.
```

Do **not** present `project.zip` as a successful completed artifact unless preview generation also succeeds.

Implementation note: it is acceptable if the ZIP is created internally before preview generation, but the UI should not treat the job as completed unless the preview exists.

Spec correction:

```text
Replace:
"Preview generation should be non-fatal."

With:
"Preview generation is required for a completed generated job. If preview generation fails, the job fails with an actionable error. The setup/doctor UI must detect missing preview dependencies before generation."
```

---

## Question 2 — Frontend IR auto-repair retry

Use **Option A**, but preserve the user-facing intent.

The frontend should **not** call `generateWizardIr()` a second time automatically after receiving `ir_needs_repair`.

Reason: the backend already owns IR repair policy through `ir_max_repair_rounds`. Having the frontend add another full generation pass makes retry behavior harder to reason about and can burn extra LLM calls/tokens unexpectedly.

The correct fix is:

```text
- Backend owns all automatic repair attempts.
- Frontend calls generate-IR once per user action.
- Backend should perform the configured repair loop internally.
- Frontend should show that automatic repair is happening while the request is in flight.
- Backend response should include enough metadata for the UI to explain what happened.
```

Recommended backend response metadata:

```json
{
  "status": "ir_needs_repair",
  "repair_attempts_used": 2,
  "repair_attempts_max": 2,
  "repair_summary": "Generated IR still references an unresolved symbol.",
  "next_action": "Revise the spec or retry generation."
}
```

So the user still gets the experience they wanted — automatic repair — but it happens in one backend-controlled operation.

Spec clarification:

```text
Remove frontend-level automatic second generate-IR call.
If more repair attempts are desired, increase backend ir_max_repair_rounds or expose a deliberate user-triggered "Try again" action.
```

---

## Question 3 — Visual palette

Phase 17 should **not** be treated as a full rebrand.

Use this interpretation:

```text
Phase 17 is a simplification pass within the existing warm terracotta/amber/cream visual identity.
```

Do:

```text
- Reduce gradients.
- Reduce nested cards.
- Reduce shadows.
- Increase whitespace.
- Improve hierarchy.
- Make primary actions clearer.
- Make cards and panels less dense.
- Use fewer competing accent colors per screen.
```

Do **not** do this in Phase 17:

```text
- Do not replace the whole palette with slate/blue.
- Do not remove the existing warm brand direction.
- Do not perform a full visual rebrand without separate sign-off.
```

Spec correction:

```text
Treat the slate/blue language as design inspiration for clarity and contrast, not as a required palette replacement.
```

If a full palette replacement is desired later, that should be a separate explicit design decision.

---

## Question 4 — Step-level wizard routing

Use **Option A**.

Keep:

```text
/wizard/:sessionId/:step
```

The spec route list was not intended to be exhaustive.

Step-level wizard routing is useful and should be preserved because it supports:

```text
- browser back/forward navigation
- shareable URLs for specific wizard steps
- direct links from breadcrumbs
- easier restoration after refresh
- clearer route-level testing
```

The supported wizard routes should be:

```text
/wizard
/wizard/:sessionId
/wizard/:sessionId/:step
```

Where valid `:step` values are probably:

```text
describe
spec
ir
generate
```

Invalid step values should redirect to the correct current step or show a friendly not-found state.

---

## Additional note — Scope of Phase 0

Phase 0 should be a **discovery and inventory phase**, not a major implementation phase.

It should produce a small written inventory, either in the PR description or a short markdown file.

Recommended deliverable:

```text
docs/UI_UX_REFACTOR_INVENTORY.md
```

Include:

```text
- current frontend route list
- current major App.tsx responsibilities
- current API methods used by frontend
- backend endpoints available but not exposed in UI
- current wizard states and transitions
- current job status/artifact model
- current preview-generation behavior
- current styling/theme constants
- known build/test issues before refactor
```

This prevents Copilot/Claude from refactoring blindly.

Regarding existing docs:

```text
docs/UI_FIXES1_TODO.md
docs/UIUX1_TODO.md
```

Treat those as **previous completed UI work**, not active source-of-truth documents.

Do not automatically carry every old item forward.

Only carry forward items that still apply to the current refactor, especially if they overlap with:

```text
- App.tsx decomposition
- wizard clarity
- artifact/download UX
- setup/doctor visibility
- visual density reduction
- route preservation
- build/test hygiene
```

The new source of truth should be:

```text
KICAD_WEB_UI_UX_REFACTOR_SPEC.md
KICAD_WEB_UI_UX_REFACTOR_TODO.md
```

with the corrections above applied.

---

## Summary of decisions

```text
Q1: Use Option B. Preview is required. Job fails if preview generation fails.
Q2: Use Option A. Remove frontend extra auto-retry; backend owns repair policy.
Q3: Simplify within existing warm palette. No full rebrand in Phase 17.
Q4: Use Option A. Keep /wizard/:sessionId/:step.
Phase 0: Discovery/inventory phase with a small written inventory deliverable.
Old UI docs: Treat as historical/closed unless specific items still overlap.
```
