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

- [x] Add Vitest if not already present
- [x] Add React Testing Library if not already present
- [x] Add `@testing-library/jest-dom` if not already present
- [x] Add `@testing-library/user-event` if not already present
- [x] Add `jsdom` if needed by Vitest config
- [x] Add MSW only if it materially simplifies API/query mocking — skipped per replies4.md; using vi.mock instead

### 3.2 Add test scripts

- [x] Add `test` script to `frontend/package.json`
- [x] Add `test:watch` script if useful — added `test:run` (non-watch mode) instead
- [x] Keep existing `build` and `lint` scripts unchanged

### 3.3 Add test setup file

- [x] Create `frontend/src/test/setup.ts` or equivalent
- [x] Import `@testing-library/jest-dom`
- [x] Configure any required global mocks
- [x] Ensure tests run in `jsdom`

### 3.4 Add test utilities

- [x] Create a small test render helper that wraps components in:
  - [x] `QueryClientProvider`
  - [x] React Router memory router/provider
  - [x] any app-level providers required by the frontend
- [x] Ensure each test uses a fresh `QueryClient`
- [x] Disable query retries in tests unless a test explicitly needs retry behavior

---

## 4. Add regression tests for Batch 1 behavior (P1 — required coverage)

These tests should cover the exact behaviors that were fixed or changed in Batch 1.

### 4.1 Wizard nav active state test

- [x] Render the app/layout at `/wizard`
- [x] Mock or set localStorage so the last session points to a different path such as `/wizard/abc123`
- [x] Verify the Wizard nav item is active on `/wizard`
- [x] Verify the Wizard nav link still points to the last session path when appropriate

### 4.2 Inline regenerate confirmation test

- [x] Render the generate step with a latest job whose status is `succeeded`
- [x] Click `Generate Again`
- [x] Verify an inline confirmation banner appears
- [x] Verify `window.confirm` is not called
- [x] Click `Cancel`
- [x] Verify the confirmation banner disappears
- [x] Click `Generate Again` again
- [x] Click `Confirm — Generate Again`
- [x] Verify generation action is invoked

### 4.3 Failed-job retry label/style test

- [x] Render the generate step with a latest job whose status is `failed`
- [x] Verify the main action label is `Retry Generation`
- [x] Verify clicking it does not first show the succeeded-regeneration confirmation
- [x] Verify it invokes generation action directly or follows the current intended retry flow
- [x] Verify it does not use the destructive/danger style intended for overwriting a succeeded job

### 4.4 LLM-disabled spec revision test

- [x] Render the spec step with `llm_enabled` false
- [x] Verify the spec revision composer submit button is disabled
- [x] Verify the help text explains the LLM provider is unavailable/offline/disabled
- [x] Verify approval behavior remains available if approval does not require LLM, matching current app behavior

### 4.5 LLM-disabled IR generation test

- [x] Render the IR step with `llm_enabled` false
- [x] Verify Generate/Regenerate/Repair Circuit IR button is disabled
- [x] Verify help text explains the LLM provider is unavailable/offline/disabled

### 4.6 Job polling indicator test

- [x] Render `JobPage` or the job status component with a queued/running job
- [x] Simulate background query refetching
- [x] Verify the subtle `Checking for updates…` indicator appears near the status field
- [x] Verify initial loading still uses the full-page loading state
- [x] Verify succeeded/failed jobs do not show the polling indicator

### 4.7 Symbols debounce test

- [x] Use fake timers
- [x] Render `SymbolsPage` or the extracted search control
- [x] Type multiple characters rapidly
- [x] Verify intermediate keystrokes do not immediately trigger multiple query updates/searches
- [x] Advance timers by the debounce interval
- [x] Verify only the final query is applied
- [x] Unmount before the timer fires
- [x] Verify no state-update-after-unmount warnings or pending timer behavior occurs

### 4.8 Wizard step smoke tests

- [x] Render the start step with representative bootstrap data
- [x] Render the describe step with representative session data
- [x] Render the spec step with representative spec data
- [x] Render the IR step with representative IR data and repair warning data
- [x] Render the generate step with representative latest job data
- [x] Include at least one missing/partial-data case for each step where optional API data may be absent

---

## 5. Add accessibility semantics to async/error/status UI (P1 — accessibility)

Add semantic roles and live regions where state changes are important.

### 5.1 Error banners

- [x] Add `role="alert"` to wizard mutation error banners — WizardPage.tsx
- [x] Add `role="alert"` to start-page create-session error banner — WizardStartStep.tsx
- [x] Add `role="alert"` to page-level fatal load error banners — App.tsx AppShell, WizardPage.tsx
- [x] Add `role="alert"` to job-page fatal load error banners — JobPage error renders NotFoundScreen (full page, not a banner); generation-failed banner in WizardGenerateStep.tsx covered
- [x] Do not add multiple nested `role="alert"` regions for the same error

### 5.2 Loading and busy states

- [x] Add `role="status"` to full-page loading banners where appropriate — App.tsx, JobPage.tsx, WizardPage.tsx
- [x] Add `aria-live="polite"` to non-blocking loading/busy indicators — WizardStartStep.tsx busy banner, WizardPage.tsx
- [x] Ensure visible loading text remains present; do not rely on spinner-only feedback

### 5.3 Polling indicator

- [x] Add `role="status"` to the `Checking for updates…` indicator — JobPage.tsx
- [x] Add `aria-live="polite"` — JobPage.tsx
- [x] Avoid assertive announcements during polling

### 5.4 Success/status messages

- [x] Add `role="status"` and `aria-live="polite"` to success or completion messages that appear dynamically — no standalone success banners exist; status is shown via StatusPill which is static labelling
- [x] Do not add live regions to static decorative labels

### 5.5 Confirmation/warning UI

- [x] Ensure the regenerate confirmation banner has a clear heading or accessible label — `<strong>` is the visual label; `aria-live="polite"` added so screen readers announce it
- [x] Ensure `Confirm — Generate Again` and `Cancel` buttons have clear accessible names — button text is the accessible name
- [x] Use `role="alert"` only if the confirmation is urgent/blocking; otherwise prefer a labelled region — used `aria-live="polite"` (non-assertive)

### 5.6 Preserve keyboard usability

- [x] Confirm all new/extracted controls remain keyboard reachable — all controls use native button/input/textarea elements
- [x] Confirm no clickable `div`/`span` controls are introduced — none added
- [x] Confirm focus outlines are not removed — Tailwind defaults preserved

---

## 6. Clean generated artifacts from repo/export (P1 — repository hygiene)

The reviewed zip contained generated Python cache artifacts. Remove them and prevent recurrence.

### 6.1 Remove generated Python cache files

- [x] Run cleanup commands — no `__pycache__` or `.pyc`/`.pyo` files were present
- [x] Verify no `__pycache__` directories remain
- [x] Verify no `.pyc` or `.pyo` files remain

### 6.2 Verify `.gitignore`

- [x] Confirm `.gitignore` contains patterns for:
  - [x] `__pycache__/` — present in root `.gitignore`
  - [x] `*.py[cod]` — present as `*.py[codz]` in root `.gitignore`
  - [x] `.pytest_cache/` — present in root `.gitignore`
  - [x] `.mypy_cache/` — present in root `.gitignore`
  - [x] `.ruff_cache/` — present in root `.gitignore`
  - [x] frontend build output — `dist` in `frontend/.gitignore`; SPA output intentionally committed
  - [x] dependency folders such as `node_modules/` — in `frontend/.gitignore`
- [x] Add missing patterns if needed — none needed

### 6.3 Confirm cleanup does not remove fixtures

- [x] Check `git status` — clean, no accidental removals
- [x] Ensure no source files, test fixtures, golden files, or snapshots were accidentally removed
- [x] No tracked cache files found — nothing to remove from git history

---

## 7. Document frontend polling vs backend job execution model (P2 — maintainer clarity)

Frontend job polling supports queued/running jobs, but current backend generation may complete synchronously. Clarify this so future maintainers understand the behavior.

### 7.1 Add a short code comment near polling logic

- [x] In `frontend/src/queries/jobQueries.ts`, added comment explaining synchronous backend, polling interval, and future async model

### 7.2 Add optional developer doc

- [x] Created `docs/JOB_EXECUTION_MODEL.md` with job statuses, polling behaviour, current sync model, and future async model notes

### 7.3 Do not implement a half-finished worker

- [x] No worker system added

---

## 8. Strengthen type and lint discipline during refactor (P2 — code quality)

The wizard refactor should not hide type problems.

### 8.1 Avoid broad `any`

- [x] No broad `any` types introduced — all extracted components use specific types from `types.ts`
- [x] One narrow `as Error | null` cast in `useWizardController.ts` for TanStack Query v5 unknown error type, documented in code

### 8.2 Avoid new suppressions

- [x] No `// eslint-disable`, `// @ts-ignore`, or `// @ts-expect-error` added
- [x] No changes to `tsconfig` strictness

### 8.3 Keep query cache behavior intact

- [x] All query keys from existing modules preserved
- [x] Project generation cache update behavior preserved in `useWizardController.ts`
- [x] Polling stop behaviour for terminal statuses preserved (`useJobQuery`)
- [x] Bootstrap invalidation on AppShell retry preserved

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
