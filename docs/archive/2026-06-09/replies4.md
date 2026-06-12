# Replies to Claude Code — UI/UX Improvements Batch 2

These answers address the pre-implementation questions raised after reviewing `docs/UIUX_IMPROVEMENTS2_SPEC.md` and `docs/UIUX_IMPROVEMENTS2_TODO.md`.

## Summary of decisions

Proceed with the proposed defaults:

1. Use targeted `vi.mock(...)` hook/module mocks instead of MSW for this batch.
2. Put Vitest configuration in the existing `vite.config.ts`.
3. Test the polling indicator by mocking `useJobQuery` with `isFetching: true` and `isLoading: false`.
4. Treat `WizardStartStep` as the no-session `/wizard` landing page and `WizardDescribeStep` as the `/wizard/:sessionId/describe` step.
5. Extract `WizardComposer` into its own wizard-folder component.

No additional blocking decisions are required.

---

## Q1 — MSW vs. hook mocking for tests

Yes, skipping MSW is acceptable for this batch.

Use targeted `vi.mock(...)` mocks for query hooks, mutation hooks, and API-facing modules where appropriate. The purpose of these tests is to lock down **component behavior** and **UI state transitions**, not to integration-test HTTP request/response behavior.

For this batch, hook/module mocks are the better choice because the tests need to verify things like:

- A failed latest job renders `Retry Generation`.
- A succeeded latest job renders `Generate Again` and shows the inline confirmation banner.
- `bootstrap.llm_enabled === false` disables spec-revision and IR-generation controls.
- `useJobQuery(...).isFetching === true` renders the subtle polling indicator.
- The wizard start page and step pages render the correct affordances for specific query/mutation states.

Those are all local rendering contracts. MSW would add harness complexity without materially improving coverage for these specific requirements.

### Implementation guidance

Use hook mocks when the behavior under test depends primarily on query return shape:

```ts
vi.mock('../queries/jobQueries', () => ({
  useJobQuery: vi.fn(),
}))
```

Then set the return value per test:

```ts
const useJobQueryMock = vi.mocked(useJobQuery)

useJobQueryMock.mockReturnValue({
  data: failedJob,
  isLoading: false,
  isFetching: false,
  error: null,
})
```

Use API mocks only where the component under test still directly calls API functions. After the Batch 1 refactor, most wizard and job behavior should be testable through query/mutation hook mocks.

### Important caveat

Do **not** over-mock pure rendering helpers or extracted step components. Test at the highest useful component boundary. Prefer rendering the route/page or extracted step component with realistic props over mocking every child.

MSW can be added later if we introduce broader integration tests that need to validate endpoint paths, payload shape, retry behavior, or query-cache behavior across real HTTP-like boundaries. It is not required for this batch.

---

## Q2 — Vitest config placement

Use option 1: add the `test: { ... }` block to the existing `frontend/vite.config.ts`.

That is the cleanest setup for this project because the current Vite config is minimal and the test runner should share the same React plugin/JSX transform behavior as the app build.

Recommended shape:

```ts
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.ts',
  },
})
```

If TypeScript complains about `test` not existing on the Vite config type, use the Vitest config helper import instead:

```ts
import { defineConfig } from 'vitest/config'
```

Do not create a separate `vitest.config.ts` unless the test configuration starts to diverge meaningfully from the Vite build configuration. For this batch, separation would be unnecessary ceremony.

---

## Q3 — Polling indicator test scope

Yes, that scope is acceptable.

For Task 4.6, mock `useJobQuery` to return:

```ts
{
  data: runningJob,
  isLoading: false,
  isFetching: true,
  error: null,
}
```

Then assert that `Checking for updates…` is rendered.

This correctly tests the app-owned behavior: **when the query reports a background fetch while a job is active, the UI shows a subtle polling indicator instead of a full-page loading state**.

Do not try to unit-test TanStack Query's internal polling interval. The 3-second refetch interval belongs to the query hook/library contract and is already expressed in `useJobQuery`. A component test should not need to advance timers until React Query refetches unless we are writing an integration test around the hook itself.

If you want one additional lightweight test around the hook configuration, it is acceptable to test `useJobQuery` separately with a mocked API and fake timers, but that is optional for this batch. The required acceptance test is the component contract around `isFetching`.

---

## Q4 — WizardDescribeStep vs. WizardStartStep naming

Your reading is correct.

Use the following split:

- `WizardStartStep` = the no-session `/wizard` landing page.
  - Contains project-name input.
  - Contains symbols-dir input.
  - Contains the `Start Wizard` action.
  - Handles create-session pending/error UI.
  - Renders when `sessionId` is absent.

- `WizardDescribeStep` = the existing `describe` route step.
  - Renders at `/wizard/:sessionId/describe`.
  - Contains the initial circuit-description composer.
  - Uses the shared `WizardComposer` component.

The router step enum/path list should remain the four canonical session steps:

```ts
'describe' | 'spec' | 'ir' | 'generate'
```

`start` should **not** become a canonical session step. It is a route state for `/wizard` before a session exists.

This distinction matters because canonical wizard navigation should continue to be driven by the server/session state once a session exists. The start page should only create or redirect into a real session.

---

## Q5 — WizardComposer extraction

Yes, extract `WizardComposer`.

Because it is used in the describe step, spec-revision path, and IR-repair path, keeping it local inside `WizardPage.tsx` would undermine the decomposition. The extracted step components should not import local implementation details from a sibling route file.

Recommended location:

```text
frontend/src/routes/wizard/WizardComposer.tsx
```

Acceptable alternatives:

```text
frontend/src/routes/wizard/components/WizardComposer.tsx
frontend/src/components/WizardComposer.tsx
```

Use the first option unless there is already a local `components/` convention under `routes/wizard/`. The composer is wizard-specific, so it does not need to live in global `src/components` yet.

### Extraction guidance

Keep the component mostly mechanical:

- Preserve the current props and behavior.
- Do not redesign the UI while extracting.
- Export a clear props type if that helps step components stay readable.
- Keep focus/submit/disabled behavior identical.
- Keep help text and pending text behavior identical.

Example target shape:

```ts
export interface WizardComposerProps {
  value: string
  onChange: (value: string) => void
  onSubmit: () => void | Promise<void>
  disabled?: boolean
  submitDisabled?: boolean
  submitLabel: string
  placeholder?: string
  helpText?: string
  pendingText?: string
}

export function WizardComposer(props: WizardComposerProps) {
  // existing implementation moved here
}
```

The exact prop names do not need to match this example if the current component already has a good interface. Prefer preserving the existing API to avoid broad churn.

---

## Additional implementation notes

### Keep the first decomposition pass behavior-preserving

For Task 1, prioritize a mechanical split over redesign. The goal is to reduce `WizardPage.tsx` size and isolate responsibilities without changing user-visible behavior.

A good final structure would be:

```text
frontend/src/routes/WizardPage.tsx
frontend/src/routes/wizard/useWizardController.ts
frontend/src/routes/wizard/wizardStepLogic.ts
frontend/src/routes/wizard/WizardStartStep.tsx
frontend/src/routes/wizard/WizardDescribeStep.tsx
frontend/src/routes/wizard/WizardSpecStep.tsx
frontend/src/routes/wizard/WizardIrStep.tsx
frontend/src/routes/wizard/WizardGenerateStep.tsx
frontend/src/routes/wizard/WizardComposer.tsx
frontend/src/routes/wizard/WizardBreadcrumb.tsx
```

The exact filenames can vary slightly, but the responsibilities should remain separated:

- `WizardPage.tsx`: route params, high-level composition, loading/error boundaries.
- `useWizardController.ts`: query/mutation wiring and action handlers.
- `wizardStepLogic.ts`: pure helpers for canonical step, labels, status/tone mapping, and step visibility.
- Step components: render-only or mostly render-only components with explicit props.

### Avoid accidentally reintroducing local data-fetch effects

During the split, do not move back to raw `useEffect` + `api.*` calls for session/job/bootstrap state. The Batch 1 improvement should stay intact:

- session from `useWizardSessionQuery`
- latest job from `useJobQuery`
- bootstrap from `useBootstrapQuery`
- wizard actions through wizard mutation hooks

### React Query Devtools

Gate devtools behind development mode:

```tsx
{import.meta.env.DEV ? <ReactQueryDevtools initialIsOpen={false} /> : null}
```

Do not remove devtools entirely. They are useful during development, but they should not appear in production builds.

### Accessibility updates

Use semantic roles carefully:

- Error banners: `role="alert"`
- Passive loading/status updates: `role="status"` and `aria-live="polite"`
- Destructive confirmation banner: make sure the heading/text is programmatically associated if practical

Do not add noisy `aria-live` regions around large containers that update frequently.

### Test philosophy

Keep tests small and behavior-focused. Prefer assertions that match user-visible outcomes:

- button text
- disabled/enabled state
- warning/error/status text
- route-visible component state
- presence/absence of confirmation banners

Avoid brittle assertions around class-name implementation unless the test specifically targets a style/tone requirement.

For the failed/succeeded generation button style requirement, it is acceptable to assert class/tone only if there is no better semantic affordance. If possible, use accessible names and visible text first.

---

## Final instruction to proceed

Proceed with implementation using the decisions above. The defaults Claude Code proposed are approved:

- no MSW for this batch;
- test config inside `vite.config.ts`;
- polling indicator tested by mocked `isFetching` state;
- `WizardStartStep` is `/wizard` without a session;
- `WizardDescribeStep` is the canonical `describe` step;
- extract `WizardComposer` into the wizard module folder.
