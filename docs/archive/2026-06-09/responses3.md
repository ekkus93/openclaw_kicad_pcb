# responses3.md — Claude Code questions on KICAD_WEB_UI_UX_REFACTOR_SPEC.md / KICAD_WEB_UI_UX_REFACTOR_TODO.md

---

## Question 1 — Preview generation: non-fatal vs. required (Spec §3.5, Phase 16)

The spec says preview generation should be non-fatal:

> "If preview generation fails, keep the job successful but show a preview warning."

However, the current codebase was deliberately changed to make preview generation a **hard failure** based on an explicit user instruction during development:

> "If the image for the schematic cannot be generated, that's a bug. Generating the image is required. It's proof to the user that the schematic was created successfully."

The code currently raises `RuntimeError` with a specific error message if `kicad-cli` or `rsvg-convert` is missing or fails, and the job is marked failed as a result.

**Which behavior should be implemented?**

- Option A (spec): Non-fatal. Project ZIP is always created. Preview failure is recorded as a warning but the job succeeds.
- Option B (current code): Fatal. If preview cannot be generated, the job fails. No ZIP without a preview.

---

## Question 2 — Frontend IR auto-repair retry (Spec §9.6, Phase 5)

The spec says:

> "If the backend already performs IR repair attempts, the frontend should not immediately call the same generate-IR endpoint a second time after receiving an `ir_needs_repair` status. The backend should own repair policy."

The current codebase has a frontend auto-retry: when `generateWizardIr` returns `ir_needs_repair`, the frontend immediately calls it again automatically, showing a "attempting automatic repair…" banner. This was added in direct response to a user request:

> "Why doesn't the program try to automatically repair it like with the other error? Why do you need to ask me to fix the error?"

The backend already runs up to `ir_max_repair_rounds` internally (default 2 rounds). The frontend auto-retry effectively adds one additional generation pass on top of the backend's own repair loop.

**Should the frontend auto-retry be removed?**

- Option A (spec): Remove it. The frontend calls generate-IR once per user action. The backend handles all repair rounds internally. The frontend shows the returned status and lets the user decide to retry manually.
- Option B (current code): Keep it. The frontend fires one automatic extra attempt when it receives `ir_needs_repair`, giving the user a seamless repair experience before showing the manual button.

---

## Question 3 — Visual palette (Spec §14.1, Phase 17)

The spec describes a different visual direction:

> "Neutral light background. White or very light panels. Slate/gray text. Deep blue primary actions. Amber/copper warning accents. Minimal gradients. Minimal shadows."

The current app uses a warm terracotta/amber palette (cream backgrounds, brand-deep brown/rust, accent teal) that has been actively developed and refined through multiple UI/UX passes over the past several weeks.

Phase 17 is explicitly scoped as visual design simplification, not a full rebrand. But the color direction described in the spec is a significant departure from the current system.

**Clarification needed:**

- Is Phase 17 intended as a full palette replacement (remove warm colors, adopt slate/blue), or is it a simplification pass within the existing palette (fewer gradients, less density, more whitespace)?
- If it is a full palette replacement, should that be treated as a separate, explicit decision with its own sign-off before Phase 17 begins?

---

## Question 4 — Step-level wizard routing (Spec §6.1)

The spec lists these wizard routes:

```
/wizard               New AI wizard flow
/wizard/:sessionId    Existing wizard session
```

The current implementation also has:

```
/wizard/:sessionId/:step
```

This per-step route is central to the current wizard UX — each step (describe, spec, ir, generate) is its own navigable URL, the breadcrumb links to each step by URL, and the browser back button works step-by-step.

**Should the step-level routing be kept, removed, or deferred to Phase 8?**

- Option A: Keep `/wizard/:sessionId/:step`. The spec's route list was not meant to be exhaustive and step-level routing should be preserved.
- Option B: Remove step-level routing. The spec intends `/wizard/:sessionId` to handle all step rendering internally without sub-routes.
- Option C: Defer the decision to Phase 8 when wizard components are extracted.

---

## Additional note — Scope of Phase 0

Phase 0 asks to "unzip/open the latest repo and inspect the current frontend/backend structure." This implies starting from a clean checkout. The current `App.tsx` is approximately 2,200 lines. Before implementation begins it would be useful to confirm:

- Whether Phase 0 is intended as a discovery/documentation phase only, or whether it produces deliverables (e.g. a written inventory of current API endpoints).
- Whether the existing `docs/UI_FIXES1_TODO.md` and `docs/UIUX1_TODO.md` (previous UI work) should be considered complete and closed, or whether any items from those lists carry forward into this refactor.
