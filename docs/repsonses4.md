# Batch 2 — Pre-Implementation Questions and Issues

Raised after reading `docs/UIUX_IMPROVEMENTS2_SPEC.md` and `docs/UIUX_IMPROVEMENTS2_TODO.md`
and inspecting the current codebase state.

---

## Q1 — MSW vs. hook mocking for tests (Tasks 3 and 4)

The spec says "MSW or targeted module mocks for API/query behavior."

MSW requires a service worker setup, a `public/mockServiceWorker.js` file, and adds
meaningful complexity to the test harness. The required tests are all about component
rendering behavior given specific data shapes (a job with `status: 'failed'`, a session
with `ir_needs_repair`, etc.) — not about verifying HTTP request shapes or response
handling.

For this kind of test, `vi.mock` on the query hooks is simpler and more targeted:

```ts
vi.mock('../queries/jobQueries', () => ({
  useJobQuery: vi.fn().mockReturnValue({ data: job, isLoading: false, isFetching: false, error: null }),
}))
```

My plan is to skip MSW and mock the query hooks directly. Is that acceptable, or do you
want MSW for any specific test?

---

## Q2 — Vitest config placement (Task 3)

Two options:

1. Add a `test: { ... }` block to the existing `vite.config.ts` — Vitest then shares the
   same Vite plugin config (including the React JSX transform plugin). Standard approach
   for Vite projects.
2. Create a separate `vitest.config.ts` that extends the Vite config.

The existing `vite.config.ts` is minimal (just `@vitejs/plugin-react`), so option 1 is
cleaner. I'll go with option 1 unless you prefer separation.

---

## Q3 — "Simulate background query refetching" for polling indicator test (Task 4.6)

TanStack Query v5 does not expose a simple `triggerRefetch()` test utility. The practical
approach is to mock `useJobQuery` to return `{ isFetching: true, isLoading: false, data:
job, error: null }` and assert that the `Checking for updates…` indicator renders.

This tests that the component correctly responds to `isFetching: true`, which is the
correct behavior contract. It does not test the TanStack Query refetch interval itself
(that's a library concern, not our code). Is this scope acceptable for task 4.6?

---

## Q4 — WizardDescribeStep vs. WizardStartStep naming (Task 1.6 and 1.7)

The spec names five step components: Start, Describe, Spec, IR, Generate. The current
router only has four step paths: `describe | spec | ir | generate`. The "start" page (no
`sessionId` in the URL, i.e. `/wizard`) is a separate route condition.

My reading:

- `WizardStartStep` = the no-session landing page (project name/symbols dir input + "Start
  Wizard" button), rendered when `sessionId` is absent.
- `WizardDescribeStep` = the `describe` route step (initial circuit description composer).

Confirming this is correct before splitting.

---

## Q5 — WizardComposer extraction (Task 1.11)

`WizardComposer` (currently at line 379 of `WizardPage.tsx`, ~75 lines) is used in three
places: the describe step, the spec revision path, and the IR repair path. Task 1.11 says
"Create `WizardComposer.tsx` if the composer is still local and large enough to justify
extraction."

It is still local, and being shared across three step components makes extraction clearly
worthwhile — otherwise each step that uses it would need to either inline it or import from
a sibling step file (bad). My plan is to extract it as `WizardComposer.tsx` in the wizard
folder. Confirm?

---

## No blocking issues

None of the above are blockers. If you confirm Q1 (skip MSW) and Q5 (extract
WizardComposer), I can proceed. For Q2, Q3, and Q4 I'll proceed on the defaults described
unless you say otherwise.
