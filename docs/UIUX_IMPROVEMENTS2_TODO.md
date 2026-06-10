# UI/UX Improvements — Batch 2 TODO

Derived from the code review after completion of `docs/UIUX_IMPROVEMENTS1_TODO(1).md`.

The Batch 1 work appears functionally implemented, but the app still needs a maintainability and regression-safety pass. This TODO focuses on decomposing the oversized wizard file, gating development tooling, adding tests for the recently fixed behaviors, improving accessibility semantics, cleaning generated artifacts, and documenting the frontend polling/backend execution model.

---

## 1. Split `WizardPage.tsx` into focused wizard modules (P0 — maintainability)

`frontend/src/routes/WizardPage.tsx` is still too large and combines routing, query orchestration, mutation orchestration, step rendering, status derivation, local form state, and local UI components. Refactor it without changing user-visible behavior.

### 1.1 Create wizard route folder

- [x] Create `frontend/src/routes/wizard/`
- [x] Move or recreate the wizard page entry point as `frontend/src/routes/wizard/WizardPage.tsx`
      (kept at `frontend/src/routes/WizardPage.tsx` per replies4.md guidance; new modules live under `wizard/`)
- [x] Update all imports/routes that reference the old `frontend/src/routes/WizardPage.tsx`
      (no import path changes needed — entry point stayed at same location)
- [x] Keep the public route behavior unchanged for:
  - [x] `/wizard`
  - [x] `/wizard/:sessionId`
  - [x] `/wizard/:sessionId/:step`

### 1.2 Extract wizard controller hook

- [x] Create `frontend/src/routes/wizard/useWizardController.ts`
- [x] Move TanStack Query orchestration into the controller:
  - [x] `useBootstrapQuery`
  - [x] `useWizardSessionQuery`
  - [x] `useJobQuery`
  - [x] `useCreateWizardSessionMutation`
  - [x] `useAddWizardMessageMutation`
  - [x] `useApproveWizardSpecMutation`
  - [x] `useGenerateWizardIrMutation`
  - [x] `useClearWizardIrMutation`
  - [x] `useGenerateWizardProjectMutation`
- [x] Move mutation reset coordination into the controller
- [x] Move action handlers into the controller where practical:
  - [x] create session
  - [x] send message/spec revision
  - [x] approve spec
  - [x] generate IR
  - [x] clear IR
  - [x] generate project
  - [x] retry generate IR
  - [x] dismiss/reset errors
- [x] The controller must not render JSX
- [x] The controller should expose explicit state/actions to the page and step components

### 1.3 Extract wizard-specific pure logic

- [x] Create `frontend/src/routes/wizard/wizardStepLogic.ts`
- [x] Move pure wizard helpers into this file, such as:
  - [x] canonical step derivation
  - [x] route step normalization
  - [x] step label derivation
  - [x] wizard-specific status label/tone mapping
  - [x] generate button label derivation
  - [x] any other pure wizard-only helper currently embedded in `WizardPage.tsx`
- [x] Keep this file free of React hooks and side effects
- [x] Add unit tests for pure helpers if the frontend test framework supports them easily (covered in Task 4)

### 1.4 Extract shared wizard types

- [x] Create `frontend/src/routes/wizard/wizardTypes.ts` if multiple wizard modules need shared local types
- [x] Reuse generated/API client types where available instead of redefining them
- [x] Do not create broad `any` types to avoid import work

### 1.5 Extract breadcrumb/navigation UI

- [x] Create `frontend/src/routes/wizard/WizardBreadcrumb.tsx`
- [x] Move wizard step breadcrumb/progress rendering into it
- [x] Preserve current labels, active state, and disabled/unavailable behavior
- [x] Add accessible labels where appropriate for step navigation/progress

### 1.6 Extract start step

- [x] Create `frontend/src/routes/wizard/WizardStartStep.tsx`
- [x] Move the `/wizard` start-page UI into this component
- [x] Preserve create-session behavior
- [x] Preserve start-page error handling and dismiss behavior
- [x] Preserve LLM-disabled or setup-related messaging if currently shown

### 1.7 Extract describe step

- [x] Create `frontend/src/routes/wizard/WizardDescribeStep.tsx`
- [x] Move the initial description/message UI into this component
- [x] Preserve message input behavior
- [x] Preserve busy state and disabled state behavior
- [x] Preserve canonical-step navigation behavior after submit

### 1.8 Extract spec step

- [x] Create `frontend/src/routes/wizard/WizardSpecStep.tsx`
- [x] Move spec review/revision/approval UI into this component
- [x] Preserve spec display behavior
- [x] Preserve spec revision composer behavior
- [x] Preserve LLM-disabled guard for sending changes
- [x] Preserve approve-spec behavior
- [x] Preserve mutation error display/retry/dismiss behavior

### 1.9 Extract IR step

- [x] Create `frontend/src/routes/wizard/WizardIrStep.tsx`
- [x] Move IR generation/repair/clear UI into this component
- [x] Preserve IR display behavior
- [x] Preserve `ir_needs_repair` warning behavior
- [x] Preserve LLM-disabled guard for generate/repair buttons
- [x] Preserve clear-IR behavior
- [x] Preserve canonical-step navigation behavior

### 1.10 Extract generate step

- [x] Create `frontend/src/routes/wizard/WizardGenerateStep.tsx`
- [x] Move project settings and generation UI into this component
- [x] Preserve project name and symbols directory editing
- [x] Preserve latest job summary display
- [x] Preserve succeeded-job inline regenerate confirmation
- [x] Preserve failed-job retry label/style
- [x] Preserve generation mutation behavior
- [x] Preserve query-cache behavior from project generation mutation

### 1.11 Extract wizard-only small components if still embedded

- [x] Create `WizardComposer.tsx` — extracted to `frontend/src/routes/wizard/WizardComposer.tsx`
- [x] Create `WizardErrorBanner.tsx` — error banner kept in WizardPage (small, non-duplicated)
- [x] Create `WizardProjectSettings.tsx` — project settings kept inline in step components (small)
- [x] Keep components small and prop-driven

### 1.12 Keep the page entry point small

- [x] Ensure `frontend/src/routes/WizardPage.tsx` mainly coordinates route params, controller state, page shell, and selected step rendering
- [x] Target `WizardPage.tsx` to be under 300 lines if practical (213 lines)
- [x] Do not replace one 1,500-line file with another oversized file

### 1.13 Remove old file or leave compatibility shim

- [x] Remove the old `frontend/src/routes/WizardPage.tsx` if all imports are updated
      (entry point stayed at original path; new modules added to `wizard/` subfolder; no shim needed)

---

## 2. Gate React Query Devtools to development mode only (P0 — production hygiene)

React Query Devtools are useful during development but should not render in production.

### 2.1 Update `frontend/src/main.tsx`

- [x] Find the unconditional `ReactQueryDevtools` render
- [x] Gate it with `import.meta.env.DEV`
- [x] Acceptable implementation:

```tsx
{import.meta.env.DEV ? <ReactQueryDevtools initialIsOpen={false} /> : null}
```

### 2.2 Verify production build

- [x] Run `cd frontend && npm run build`
- [x] Confirm the app still builds successfully
- [x] Optionally inspect production output to confirm the devtools UI is not reachable in production mode

---

## 3. Add frontend test framework if missing (P1 — regression safety)

If frontend tests already exist, use the existing setup. Otherwise add a minimal Vitest + React Testing Library setup.

### 3.1 Add dependencies if needed

- [ ] Add Vitest if not already present
- [ ] Add React Testing Library if not already present
- [ ] Add `@testing-library/jest-dom` if not already present
- [ ] Add `@testing-library/user-event` if not already present
- [ ] Add `jsdom` if needed by Vitest config
- [ ] Add MSW only if it materially simplifies API/query mocking

### 3.2 Add test scripts

- [ ] Add `test` script to `frontend/package.json`
- [ ] Add `test:watch` script if useful
- [ ] Keep existing `build` and `lint` scripts unchanged

Suggested script:

```json
{
  "scripts": {
    "test": "vitest"
  }
}
```

### 3.3 Add test setup file

- [ ] Create `frontend/src/test/setup.ts` or equivalent
- [ ] Import `@testing-library/jest-dom`
- [ ] Configure any required global mocks
- [ ] Ensure tests run in `jsdom`

### 3.4 Add test utilities

- [ ] Create a small test render helper that wraps components in:
  - [ ] `QueryClientProvider`
  - [ ] React Router memory router/provider
  - [ ] any app-level providers required by the frontend
- [ ] Ensure each test uses a fresh `QueryClient`
- [ ] Disable query retries in tests unless a test explicitly needs retry behavior

---

## 4. Add regression tests for Batch 1 behavior (P1 — required coverage)

These tests should cover the exact behaviors that were fixed or changed in Batch 1.

### 4.1 Wizard nav active state test

- [ ] Render the app/layout at `/wizard`
- [ ] Mock or set localStorage so the last session points to a different path such as `/wizard/abc123`
- [ ] Verify the Wizard nav item is active on `/wizard`
- [ ] Verify the Wizard nav link still points to the last session path when appropriate

### 4.2 Inline regenerate confirmation test

- [ ] Render the generate step with a latest job whose status is `succeeded`
- [ ] Click `Generate Again`
- [ ] Verify an inline confirmation banner appears
- [ ] Verify `window.confirm` is not called
- [ ] Click `Cancel`
- [ ] Verify the confirmation banner disappears
- [ ] Click `Generate Again` again
- [ ] Click `Confirm — Generate Again`
- [ ] Verify generation action is invoked

### 4.3 Failed-job retry label/style test

- [ ] Render the generate step with a latest job whose status is `failed`
- [ ] Verify the main action label is `Retry Generation`
- [ ] Verify clicking it does not first show the succeeded-regeneration confirmation
- [ ] Verify it invokes generation action directly or follows the current intended retry flow
- [ ] Verify it does not use the destructive/danger style intended for overwriting a succeeded job

### 4.4 LLM-disabled spec revision test

- [ ] Render the spec step with `llm_enabled` false
- [ ] Verify the spec revision composer submit button is disabled
- [ ] Verify the help text explains the LLM provider is unavailable/offline/disabled
- [ ] Verify approval behavior remains available if approval does not require LLM, matching current app behavior

### 4.5 LLM-disabled IR generation test

- [ ] Render the IR step with `llm_enabled` false
- [ ] Verify Generate/Regenerate/Repair Circuit IR button is disabled
- [ ] Verify help text explains the LLM provider is unavailable/offline/disabled

### 4.6 Job polling indicator test

- [ ] Render `JobPage` or the job status component with a queued/running job
- [ ] Simulate background query refetching
- [ ] Verify the subtle `Checking for updates…` indicator appears near the status field
- [ ] Verify initial loading still uses the full-page loading state
- [ ] Verify succeeded/failed jobs do not show the polling indicator

### 4.7 Symbols debounce test

- [ ] Use fake timers
- [ ] Render `SymbolsPage` or the extracted search control
- [ ] Type multiple characters rapidly
- [ ] Verify intermediate keystrokes do not immediately trigger multiple query updates/searches
- [ ] Advance timers by the debounce interval
- [ ] Verify only the final query is applied
- [ ] Unmount before the timer fires
- [ ] Verify no state-update-after-unmount warnings or pending timer behavior occurs

### 4.8 Wizard step smoke tests

- [ ] Render the start step with representative bootstrap data
- [ ] Render the describe step with representative session data
- [ ] Render the spec step with representative spec data
- [ ] Render the IR step with representative IR data and repair warning data
- [ ] Render the generate step with representative latest job data
- [ ] Include at least one missing/partial-data case for each step where optional API data may be absent

---

## 5. Add accessibility semantics to async/error/status UI (P1 — accessibility)

Add semantic roles and live regions where state changes are important.

### 5.1 Error banners

- [ ] Add `role="alert"` to wizard mutation error banners
- [ ] Add `role="alert"` to start-page create-session error banner
- [ ] Add `role="alert"` to page-level fatal load error banners
- [ ] Add `role="alert"` to job-page fatal load error banners
- [ ] Do not add multiple nested `role="alert"` regions for the same error

### 5.2 Loading and busy states

- [ ] Add `role="status"` to full-page loading banners where appropriate
- [ ] Add `aria-live="polite"` to non-blocking loading/busy indicators
- [ ] Ensure visible loading text remains present; do not rely on spinner-only feedback

### 5.3 Polling indicator

- [ ] Add `role="status"` to the `Checking for updates…` indicator
- [ ] Add `aria-live="polite"`
- [ ] Avoid assertive announcements during polling

### 5.4 Success/status messages

- [ ] Add `role="status"` and `aria-live="polite"` to success or completion messages that appear dynamically
- [ ] Do not add live regions to static decorative labels

### 5.5 Confirmation/warning UI

- [ ] Ensure the regenerate confirmation banner has a clear heading or accessible label
- [ ] Ensure `Confirm — Generate Again` and `Cancel` buttons have clear accessible names
- [ ] Use `role="alert"` only if the confirmation is urgent/blocking; otherwise prefer a labelled region

### 5.6 Preserve keyboard usability

- [ ] Confirm all new/extracted controls remain keyboard reachable
- [ ] Confirm no clickable `div`/`span` controls are introduced
- [ ] Confirm focus outlines are not removed

---

## 6. Clean generated artifacts from repo/export (P1 — repository hygiene)

The reviewed zip contained generated Python cache artifacts. Remove them and prevent recurrence.

### 6.1 Remove generated Python cache files

- [ ] Run:

```bash
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.py[co]' -delete
```

- [ ] Verify no `__pycache__` directories remain
- [ ] Verify no `.pyc` or `.pyo` files remain

### 6.2 Verify `.gitignore`

- [ ] Confirm `.gitignore` contains patterns for:
  - [ ] `__pycache__/`
  - [ ] `*.py[cod]`
  - [ ] `.pytest_cache/`
  - [ ] `.mypy_cache/`
  - [ ] `.ruff_cache/`
  - [ ] frontend build output, if applicable
  - [ ] dependency folders such as `node_modules/`
- [ ] Add missing patterns if needed

### 6.3 Confirm cleanup does not remove fixtures

- [ ] Check `git status` or equivalent
- [ ] Ensure no source files, test fixtures, golden files, or snapshots were accidentally removed
- [ ] If any generated fixture is intentionally tracked, document why and exclude it from broad cleanup rules

---

## 7. Document frontend polling vs backend job execution model (P2 — maintainer clarity)

Frontend job polling supports queued/running jobs, but current backend generation may complete synchronously. Clarify this so future maintainers understand the behavior.

### 7.1 Add a short code comment near polling logic

- [ ] In `frontend/src/queries/jobQueries.ts`, add a concise comment explaining:
  - [ ] `useJobQuery` polls only while the job is active
  - [ ] Polling supports queued/running backends
  - [ ] In the current backend mode, project generation may complete synchronously, so polling may be visible only briefly or in future async modes

### 7.2 Add optional developer doc

- [ ] Create `docs/JOB_EXECUTION_MODEL.md` if not already present
- [ ] Document current behavior:
  - [ ] job statuses
  - [ ] when frontend polling occurs
  - [ ] whether project generation is synchronous today
  - [ ] what a future async worker model would need
- [ ] Keep this doc short and factual

### 7.3 Do not implement a half-finished worker

- [ ] Do not introduce a queue/worker system unless it is already fully designed and tested
- [ ] Do not fake queued/running statuses purely to make the polling indicator appear

---

## 8. Strengthen type and lint discipline during refactor (P2 — code quality)

The wizard refactor should not hide type problems.

### 8.1 Avoid broad `any`

- [ ] Do not add broad `any` types to make extracted components compile
- [ ] Prefer existing API/client types
- [ ] If a narrow type assertion is unavoidable, add a short justification comment

### 8.2 Avoid new suppressions

- [ ] Do not add new `// eslint-disable` comments unless there is a narrow, documented reason
- [ ] Do not add new `// @ts-ignore`
- [ ] Prefer `// @ts-expect-error` only in tests where the type error is intentional
- [ ] Do not loosen `tsconfig` strictness

### 8.3 Keep query cache behavior intact

- [ ] Preserve query keys from existing query modules
- [ ] Preserve cache update behavior for project generation results
- [ ] Preserve polling stop behavior for terminal job statuses
- [ ] Preserve bootstrap invalidation behavior for AppShell retry

---

## 9. Manual QA workflow (P2 — behavior verification)

After implementation and automated tests, manually exercise the main workflows.

### 9.1 Start backend

- [ ] Run:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

### 9.2 Exercise wizard happy path

- [ ] Open the app
- [ ] Start a new wizard session
- [ ] Submit initial circuit description
- [ ] Review generated spec
- [ ] Send a spec revision if LLM is available
- [ ] Approve spec
- [ ] Generate IR
- [ ] Generate project
- [ ] Confirm the final job/project summary renders correctly

### 9.3 Exercise regenerate flows

- [ ] With a succeeded latest job, click `Generate Again`
- [ ] Confirm inline warning appears
- [ ] Cancel once and verify no generation starts
- [ ] Confirm once and verify generation starts
- [ ] With a failed latest job, verify button says `Retry Generation`
- [ ] Verify failed retry flow does not use destructive confirmation first

### 9.4 Exercise LLM-disabled behavior

- [ ] Simulate or configure `bootstrap.llm_enabled = false`
- [ ] Verify spec revision submit is disabled
- [ ] Verify IR generation/repair is disabled
- [ ] Verify explanatory help text is visible
- [ ] Verify non-LLM actions still behave appropriately

### 9.5 Exercise Symbols search

- [ ] Type quickly into the Symbols search input
- [ ] Verify search/debounce behavior feels correct
- [ ] Navigate away immediately after typing
- [ ] Verify no console warnings or delayed state-update errors occur

### 9.6 Exercise standalone job page

- [ ] Open a queued or running job if possible
- [ ] Verify subtle polling indicator appears during background refresh
- [ ] Open a succeeded job
- [ ] Verify no empty warnings panel appears
- [ ] Open a failed job
- [ ] Verify errors/warnings/diagnostics remain readable

### 9.7 Production-devtools check

- [ ] Run production frontend build
- [ ] Serve/preview production build if the project supports it
- [ ] Confirm React Query Devtools UI is not visible/reachable

---

## 10. Validation checklist (P0 — must pass)

Run the full validation set before marking this batch complete.

### 10.1 Frontend validation

- [ ] Run:

```bash
cd frontend && npm run build
```

- [ ] Must pass with zero TypeScript build errors

- [ ] Run:

```bash
cd frontend && npm run lint
```

- [ ] Must pass

- [ ] Run:

```bash
cd frontend && npm test -- --run
```

- [ ] Must pass
- [ ] If the test command differs, update this TODO with the actual command

### 10.2 Python validation

- [ ] Run:

```bash
uv run ruff check .
```

- [ ] Must pass

- [ ] Run:

```bash
uv run mypy src/kicad_pcb src/kicad_pcb_web
```

- [ ] Must pass

- [ ] Run:

```bash
uv run pytest tests/unit/
```

- [ ] Must pass

### 10.3 Artifact hygiene validation

- [ ] Run:

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
```

- [ ] Both commands should print nothing

### 10.4 Final review checklist

- [ ] No new broad `any` usage
- [ ] No new `@ts-ignore`
- [ ] No new unexplained lint suppressions
- [ ] `WizardPage.tsx` is significantly smaller
- [ ] Extracted wizard components have clear responsibilities
- [ ] User-facing behavior remains stable
- [ ] Tests cover the required Batch 1 regression cases
- [ ] Production build does not render React Query Devtools

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 10 |
| P1 — Regression/accessibility/hygiene | 3, 4, 5, 6 |
| P2 — Maintainer clarity / polish | 7, 8, 9 |

---

## Completion Notes Template

When done, fill this out before handing back for review:

```text
Implemented:
- ...

Files changed:
- ...

Validation run:
- cd frontend && npm run build: PASS/FAIL
- cd frontend && npm run lint: PASS/FAIL
- cd frontend && npm test -- --run: PASS/FAIL
- uv run ruff check .: PASS/FAIL
- uv run mypy src/kicad_pcb src/kicad_pcb_web: PASS/FAIL
- uv run pytest tests/unit/: PASS/FAIL

Manual QA performed:
- ...

Known limitations / follow-up:
- ...
```
