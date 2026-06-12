# UI/UX Improvements — Batch 2 Spec

## Purpose

This spec defines the second UI/UX and frontend-quality improvement batch for the KiCad PCB web app after completion of `docs/UIUX_IMPROVEMENTS1_TODO(1).md`.

Batch 1 successfully migrated the wizard and job screens toward TanStack Query, fixed several functional UI bugs, extracted some shared components, and improved the generation flow. Batch 2 should consolidate that work by reducing the remaining oversized wizard module, adding regression coverage for the recently changed behavior, improving accessibility of async state feedback, tightening production build hygiene, and clarifying the relationship between job polling in the frontend and the currently synchronous backend job execution model.

The goal is not to redesign the entire app. The goal is to make the current app easier to maintain, safer to modify, and less brittle for future Claude Code/Copilot work.

---

## Current State Summary

The latest review found that the Batch 1 TODO items were implemented correctly overall:

- `WizardPage.tsx` now uses wizard/session/job TanStack Query hooks instead of raw local fetch effects.
- `AppShell` now uses `useBootstrapQuery`.
- `JobPage` now uses `useJobQuery` and polls queued/running jobs.
- The redundant `latestJob` race was removed.
- The Wizard nav active-state bug was fixed.
- `SymbolsPage` debounce behavior was fixed with a `useRef` timer.
- `window.confirm()` was replaced by inline regenerate confirmation UI.
- Failed jobs now show a more appropriate retry label/style.
- LLM-disabled guards were added to spec revision and IR generation actions.
- Shared utilities/components were extracted.
- Frontend build and lint pass.

Remaining concerns:

1. `frontend/src/routes/WizardPage.tsx` is still too large and combines too many responsibilities.
2. React Query Devtools are rendered unconditionally and should not be included in production UI/runtime.
3. The repository/export contains generated Python cache artifacts such as `__pycache__` and `.pyc` files.
4. The most important recently changed frontend behaviors do not have automated UI tests.
5. Async/error/status UI is visually clear but lacks consistent accessibility semantics such as `role="alert"`, `role="status"`, and `aria-live`.
6. Frontend job polling is correct, but backend project generation currently appears synchronous. This should be documented and, if feasible in this batch, lightly improved so the UI behavior and backend model do not mislead future maintainers.

---

## Scope

Batch 2 includes the following workstreams:

1. Split `WizardPage.tsx` into smaller wizard-specific modules.
2. Gate React Query Devtools to development mode only.
3. Add automated frontend regression tests for Batch 1 behavior.
4. Add accessibility semantics to important async, error, warning, and status feedback.
5. Clean generated Python cache artifacts and reinforce repo/export hygiene.
6. Clarify the frontend polling versus backend synchronous job execution behavior.
7. Preserve current user-facing behavior unless explicitly changed by this spec.

---

## Non-Goals

Do not perform these in this batch unless explicitly requested later:

- Do not redesign the entire visual system.
- Do not replace TanStack Query.
- Do not migrate routing frameworks.
- Do not rewrite the backend job system into a full async worker architecture unless a minimal safe change is already present and low risk.
- Do not change API contracts unless required to preserve existing behavior.
- Do not alter the KiCad generation algorithm.
- Do not change project naming, branding, or major navigation structure.

---

## Design Principles

### 1. Preserve behavior while changing structure

The wizard split should be a refactor, not a product redesign. Existing routes, buttons, labels, mutation behavior, canonical-step navigation, and cache behavior should continue to work.

### 2. Make wizard responsibilities explicit

Separate these concerns:

- Route parameter parsing and page-level loading/error handling.
- Query/mutation orchestration.
- Wizard step-state derivation.
- Individual step rendering.
- Shared wizard-only UI components.
- Pure helper functions.

### 3. Test the behaviors that previously broke

Batch 1 fixed several subtle bugs. Batch 2 should add regression tests for those exact behaviors so they do not regress during future refactors.

### 4. Prefer small modules with narrow contracts

A component or hook should have a clear purpose. Avoid moving 1,500 lines from one file into another single file.

### 5. Production builds should not expose development tooling

Development-only tooling should be removed from production runtime and bundle behavior whenever practical.

### 6. Accessibility improvements should be semantic, not cosmetic

Add ARIA roles and live-region behavior where state changes are important. Do not add noisy or redundant announcements.

---

## Target Wizard Module Layout

Recommended target layout:

```text
frontend/src/routes/wizard/
  WizardPage.tsx
  useWizardController.ts
  wizardTypes.ts
  wizardStepLogic.ts
  WizardBreadcrumb.tsx
  WizardStartStep.tsx
  WizardDescribeStep.tsx
  WizardSpecStep.tsx
  WizardIrStep.tsx
  WizardGenerateStep.tsx
  WizardComposer.tsx
  WizardErrorBanner.tsx
  WizardProjectSettings.tsx
```

This layout may be adjusted if the existing code suggests a cleaner split, but the final result should avoid keeping most wizard logic in a single very large file.

### `WizardPage.tsx`

Responsibilities:

- Read route params.
- Call `useBootstrapQuery` if still needed at page level.
- Call `useWizardController`.
- Render page shell, breadcrumb, loading/error states, and the active step component.
- Keep routing/navigation at the page boundary where possible.

Expected size target: ideally under 300 lines.

### `useWizardController.ts`

Responsibilities:

- Own wizard query/mutation orchestration.
- Derive session, latest job, busy states, current mutation errors, and submit disabled conditions.
- Provide action handlers such as create session, send message, approve spec, generate IR, clear IR, generate project, and retry/dismiss behavior.
- Keep all mutation reset coordination in one place.

Do not bury UI markup in this hook.

### Step components

Each step component should receive explicit props from the controller/page and render one wizard step only:

- `WizardStartStep.tsx`
- `WizardDescribeStep.tsx`
- `WizardSpecStep.tsx`
- `WizardIrStep.tsx`
- `WizardGenerateStep.tsx`

Step components may use shared app components, but should not directly call API methods or instantiate TanStack Query mutations unless there is a strong reason. Prefer controller-provided actions.

### `wizardStepLogic.ts`

Pure helpers only. Candidate helpers:

- Canonical wizard step calculation.
- Step label/tone derivation.
- Route step normalization.
- Derived button label helpers.
- Small status mapping functions that are wizard-specific.

No React hooks in this file.

### `wizardTypes.ts`

Wizard-specific types that are shared by multiple wizard modules. Do not duplicate API types if already exported from the API client.

---

## React Query Devtools Requirement

React Query Devtools must not render in production.

Acceptable implementation:

```tsx
{import.meta.env.DEV ? <ReactQueryDevtools initialIsOpen={false} /> : null}
```

If using a dynamic import is straightforward, that is also acceptable, but not required for this batch.

Production build should still pass after this change.

---

## Frontend Testing Requirement

Add frontend behavior tests for the Batch 1 changes and Batch 2 refactor.

Preferred stack:

- Vitest
- React Testing Library
- `@testing-library/jest-dom`
- `@testing-library/user-event`
- MSW or targeted module mocks for API/query behavior

If the project already has a frontend test framework, use the existing framework instead of adding a duplicate.

Required test coverage:

1. Wizard nav active state:
   - When the current path is `/wizard`, the Wizard nav item is active even if `lastSession` points to `/wizard/:id`.

2. Inline regenerate confirmation:
   - A succeeded previous job causes `Generate Again` to open inline confirmation.
   - `window.confirm` is not called.
   - Cancel hides the confirmation.
   - Confirm triggers generation.

3. Failed job retry label/style:
   - A failed previous job shows `Retry Generation`.
   - It does not show the destructive confirmation first.

4. LLM-disabled guards:
   - Spec revision composer disables submission when `llm_enabled` is false.
   - IR generation/repair button is disabled when `llm_enabled` is false.
   - Help text explains that the LLM provider is unavailable.

5. Job polling UI:
   - For a running/queued job, background refetching shows the subtle `Checking for updates…` indicator.
   - Initial load still shows a normal loading state.
   - Terminal statuses do not show the polling indicator.

6. Symbols debounce:
   - Rapid typing does not trigger multiple immediate search updates.
   - The final query is applied after the debounce interval.
   - Pending timers are cleared on unmount.

7. Wizard refactor smoke test:
   - At least one test renders each wizard step with representative data.
   - Step components should not crash when optional data is absent.

Testing does not need to be exhaustive. The goal is to lock down the known bug fixes and high-risk refactor points.

---

## Accessibility Requirement

Add semantic accessibility roles to async and error feedback UI.

Use these guidelines:

- Error banners: `role="alert"`.
- Success messages that appear after an action: `role="status"` and `aria-live="polite"`.
- Background polling indicator: `role="status"` and `aria-live="polite"`.
- Loading states that replace page content: `role="status"` when appropriate.
- Warning/confirmation banners: use `role="alert"` only when immediate attention is required; otherwise use a labelled region or clear heading.
- Buttons that reveal/hide confirmation UI should have clear accessible names.
- Do not use `aria-live="assertive"` except for actual blocking errors.

Avoid adding excessive live regions that would spam screen-reader users during polling.

---

## Repository and Export Hygiene Requirement

Generated files must not be committed or included in deliverable zip exports.

Remove from the working tree/export:

- `__pycache__/`
- `*.pyc`
- `*.pyo`
- test cache artifacts that are not intentionally tracked

Confirm `.gitignore` covers these patterns. If not, update `.gitignore`.

Suggested cleanup command:

```bash
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.py[co]' -delete
```

Do not delete source files, fixtures, snapshots, or intentionally tracked golden files.

---

## Job Polling and Backend Job Model Requirement

The frontend now polls queued/running jobs. That behavior is correct and should remain.

However, if backend project generation is currently synchronous, document that explicitly in code comments or developer docs so future maintainers understand why polling may rarely be visible during normal generation.

Minimum acceptable change:

- Add a short developer-facing comment near the frontend job polling query or backend generation endpoint explaining that polling supports queued/running statuses, but current generation may complete synchronously depending on backend execution mode.

Preferred but optional change if low risk:

- Add a small docs note such as `docs/JOB_EXECUTION_MODEL.md` explaining current synchronous execution, intended future async worker model, job statuses, and how frontend polling fits.

Do not introduce a half-finished async worker system in this batch.

---

## Validation Requirements

Run these commands before considering the batch complete:

```bash
cd frontend && npm run build
cd frontend && npm run lint
cd frontend && npm test -- --run
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
uv run pytest tests/unit/
```

If `npm test` uses a different project script after setup, document the exact command in `frontend/package.json` and in the TODO completion notes.

Manual QA:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

Then manually exercise:

1. Start wizard.
2. Load existing wizard session.
3. Submit spec revision.
4. Generate/repair IR.
5. Generate project.
6. Retry failed generation state if feasible.
7. Confirm succeeded regeneration state uses inline confirmation.
8. Visit standalone job page for running and terminal jobs.
9. Type into Symbols search rapidly and confirm debounce behavior.
10. Build production frontend and verify React Query Devtools are absent.

---

## Acceptance Criteria

The batch is complete when all of the following are true:

- `WizardPage.tsx` is decomposed into smaller modules with clear responsibilities.
- Existing wizard behavior is preserved.
- React Query Devtools are gated to development mode only.
- Frontend build and lint pass.
- Frontend tests exist and pass for the required behaviors.
- Backend validation commands pass in the intended project environment, or any environment-specific failure is documented precisely.
- Async/error/status feedback has appropriate accessibility semantics.
- Generated cache artifacts are removed from the repo/export.
- Job polling behavior is documented relative to the backend execution model.
- No new TypeScript suppressions, lint suppressions, or broad `any` usage are introduced without a narrow justification.

---

## Implementation Notes for Claude Code

- Prefer small, reviewable commits/patches.
- Refactor first with minimal behavior change, then add tests.
- Run build/lint after the wizard split before continuing.
- If a test requires heavy mocking, first extract a smaller pure helper or component boundary rather than mocking the whole application.
- Avoid adding new global state.
- Avoid duplicating API types in test fixtures when importing existing types is practical.
- Keep the user-facing copy stable unless this spec explicitly requests a copy change.
