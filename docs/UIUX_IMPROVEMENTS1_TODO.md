# UI/UX Improvements — Batch 1 TODO

Derived from the comprehensive UI/UX review conducted on 2026-06-05 against the current
`webapp` branch after the TanStack Query refactor (phases 1–20).

---

## 1. Connect WizardPage to TanStack Query (P0 — unfinished refactor)

`queries/wizardQueries.ts` was written during the refactor and contains all the necessary
hooks (`useWizardSessionQuery`, `useCreateWizardSessionMutation`, etc.) but `WizardPage.tsx`
never adopted them. Every wizard action still uses raw `useState`/`useEffect`. This is the
largest gap left by the refactor.

### 1.1 Replace session loading `useEffect` with `useWizardSessionQuery`
- [x] Import `useWizardSessionQuery` from `../queries/wizardQueries`
- [x] Remove the `loadedSessionId`, `failedSessionId`, and `loading` state variables
- [x] Remove the `useEffect` that calls `api.getWizardSession(sessionId)` (lines 899–927)
- [x] Derive `session`, `isLoading`, and `error` from the query hook
- [x] Derive `loadedSessionId !== sessionId` loading guard from `isLoading` instead

### 1.2 Replace `handleCreateSession` with `useCreateWizardSessionMutation`
- [x] Import `useCreateWizardSessionMutation`
- [x] Replace the manual `try/catch` in `handleCreateSession` with mutation `mutate`/`mutateAsync`
- [x] Use `mutation.isPending` for the busy state instead of `busyMessage`
- [x] Use `mutation.error` for the error state instead of `errorMessage`
- [x] On `mutation.onSuccess`, navigate to the canonical step (same as current `navigate` call)

### 1.3 Replace `handleSendMessage` with `useAddWizardMessageMutation`
- [x] Import `useAddWizardMessageMutation`
- [x] Instantiate the mutation with the current `session.id`
- [x] Replace `handleSendMessage`'s manual fetch with the mutation call
- [x] Clear the `message` input in `onSuccess` (move `setMessage('')` there)
- [x] Navigate to canonical step in `onSuccess`

### 1.4 Replace `handleApproveSpec` with `useApproveWizardSpecMutation`
- [x] Import `useApproveWizardSpecMutation`
- [x] Replace the manual fetch with the mutation call
- [x] Navigate to canonical step in `onSuccess`

### 1.5 Replace `handleGenerateIr` with `useGenerateWizardIrMutation`
- [x] Import `useGenerateWizardIrMutation`
- [x] Replace the manual fetch with the mutation call
- [x] Handle `ir_needs_repair` response in `onSuccess` (set the error message that explains repair)
- [x] Navigate to canonical step in `onSuccess`

### 1.6 Replace `handleClearIr` with `useClearWizardIrMutation`
- [x] Import `useClearWizardIrMutation`
- [x] Replace the manual fetch with the mutation call
- [x] Navigate to `/wizard/${session.id}/ir` in `onSuccess`

### 1.7 Replace `handleGenerateProject` with `useGenerateWizardProjectMutation`
- [x] Import `useGenerateWizardProjectMutation`
- [x] Replace the manual fetch with the mutation call
- [x] The mutation's `onSuccess` already sets both session and job in the query cache
      (`queryClient.setQueryData` in `wizardQueries.ts` line 71–72) — verified correct
- [x] Remove the separate `setLatestJob` call and `latestJob` state (see Task 2)
- [x] Keep the `window.confirm` guard until Task 6 replaces it

### 1.8 Remove stale manual state after migration
- [x] Remove `loadedSessionId` state
- [x] Remove `failedSessionId` state
- [x] Remove the `errorMessage` state (replaced by `mutation.error`)
- [x] Remove the `busyMessage` state (replaced by `mutation.isPending`)
- [x] Verify `setProjectName`/`setSymbolsDir` still populate correctly from query data

---

## 2. Fix the `latestJob` redundant re-fetch bug (P0 — data race)

After `handleGenerateProject` completes it calls both `setSession(response.session)` and
`setLatestJob(response.job)`. But changing `session` also triggers the `useEffect` on
`session?.latest_job_id` (line 929–949), which re-fetches the same job. If that fetch
errors, its catch block calls `setLatestJob(null)`, wiping out the job data that was just
set correctly. Task 1.7 above removes `latestJob` state entirely; this task tracks the fix
in isolation in case Task 1 is staged.

### 2.1 Gate the job-fetch `useEffect` to avoid redundant re-fetches
- [x] (preferred): replaced the `latestJob` state + `useEffect` with `useJobQuery`
      from `queries/jobQueries.ts` keyed on `session?.latest_job_id`

### 2.2 Remove `setLatestJob(null)` from action handlers
- [x] Removed all `setLatestJob(null)` calls — job state now derives from session's
      `latest_job_id` via `useJobQuery` automatically

---

## 3. Migrate `AppShell` bootstrap fetch to `useBootstrapQuery` (P1)

`AppShell` fetches bootstrap with its own `useState`/`useEffect` and passes the result as a
prop to `WizardPage`. `HomePage` already uses `useBootstrapQuery()` from
`queries/bootstrapQueries.ts`. This dual approach is inconsistent and means bootstrap data
isn't shared from the query cache.

### 3.1 Adopt `useBootstrapQuery` in `AppShell`
- [x] Import `useBootstrapQuery` from `./queries/bootstrapQueries`
- [x] Remove the `bootstrap` state, `errorMessage` state, `retryKey` state, and the
      `useEffect` that calls `api.getBootstrap()`
- [x] Derive `bootstrap`, `isLoading`, and `error` from the query hook
- [x] Keep the "Retry" behaviour by calling `queryClient.invalidateQueries` on the
      bootstrap query key when the user clicks Retry

### 3.2 Stop passing `bootstrap` as a prop to `WizardPage`
- [x] `WizardPage` now calls `useBootstrapQuery()` internally
- [x] Remove the `bootstrap: UiBootstrapResponse` prop from `WizardPage`'s interface
- [x] Remove the corresponding prop from the `<WizardPage>` call in `App.tsx`

---

## 4. Fix the "Wizard" nav active state on the start page (P1 — navigation bug)

When the user is at `/wizard` (the start page) but has a previous session stored in
`localStorage`, the nav link's `to` is `/wizard/${lastSession}`. React Router `NavLink`
checks prefix matching — `/wizard/abc123` does not prefix-match `/wizard`, so the "Wizard"
pill appears inactive even though the user is on the wizard page.

### 4.1 Fix NavLink active detection for the Wizard entry
- [ ] In `App.tsx` `Layout`, pass `end={false}` to the "Wizard" `NavLink` so it matches any
      `/wizard/*` sub-path, not just the specific stored session path:
      ```tsx
      <NavLink end={false} ...>
      ```
      This is safe because no other nav item starts with `/wizard`.
- [ ] Alternatively, use the `isActive` callback form and check
      `location.pathname.startsWith('/wizard')` for this specific item

### 4.2 Keep the "last session" redirect working
- [ ] Verify that after the active-state fix, navigating via the "Wizard" nav still takes
      the user to `lastSession` if one exists (the `to` prop still does this; only the
      `isActive` detection changes)

---

## 5. Fix the `SymbolsPage` debounce leak (P1 — functional bug)

`handleQueryChange` creates a `setTimeout` and returns a cleanup function, but since it is
called as a plain event handler the return value is discarded. Every keystroke creates a
timer that is never cleaned up.

### 5.1 Rewrite `handleQueryChange` to use a `useRef` timer
- [ ] Add `const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)`
- [ ] In `handleQueryChange`, call `clearTimeout(debounceRef.current ?? undefined)` before
      setting a new timeout, and store the new timer ID in `debounceRef.current`
- [ ] Remove the `return () => clearTimeout(timer)` line (it is never called anyway)
- [ ] Add a `useEffect` cleanup: `return () => clearTimeout(debounceRef.current ?? undefined)`
      so pending timers are cleared on unmount

---

## 6. Replace `window.confirm()` with an inline confirmation UI (P2)

`handleGenerateProject` uses a native browser `window.confirm()` dialog to gate re-generation
when a successful job already exists. This is visually inconsistent with the rest of the UI.

### 6.1 Add inline confirmation state to the Generate step
- [ ] Add a `confirmRegenerate` boolean state, defaulting to `false`
- [ ] When the "Generate Again" button is clicked and a succeeded job exists, set
      `confirmRegenerate = true` instead of calling `window.confirm()`
- [ ] Render a warning banner when `confirmRegenerate` is true:
      "This will replace the current generation result." with two buttons:
      "Confirm — Generate Again" (danger) and "Cancel" (secondary)
- [ ] "Confirm" calls `handleGenerateProject` directly and resets `confirmRegenerate = false`
- [ ] "Cancel" resets `confirmRegenerate = false`
- [ ] Remove the `window.confirm()` call from `handleGenerateProject`

---

## 7. Fix "Generate Again" label and style on failed previous jobs (P2)

When a job previously failed, the "Generate Again" button appears in the primary blue style
with the label "Generate Again". "Generate Again" implies a completed state being redone;
a failed state warrants "Retry Generation" with a neutral style.

### 7.1 Differentiate label and style by previous job outcome
- [ ] Change the button label logic:
  ```tsx
  {!visibleLatestJob
    ? 'Generate Project'
    : visibleLatestJob.status === 'failed'
      ? 'Retry Generation'
      : 'Generate Again'}
  ```
- [ ] Change the button class logic:
  ```tsx
  {visibleLatestJob?.status === 'succeeded' ? buttonDangerClass : buttonPrimaryClass}
  ```
  A failed job retry should use `buttonPrimaryClass` (not danger), since regenerating after
  failure is the expected recovery action, not a destructive one.

---

## 8. Guard spec revision and IR generation against disabled LLM (P1)

The spec step's "Send Changes" button and the IR step's "Generate Circuit IR" button do not
check `bootstrap.llm_enabled`. If the provider goes offline after a session is created, these
buttons fire and fail with a raw API error rather than a clearly disabled state.

### 8.1 Disable spec revision "Send Changes" when LLM is not available
- [ ] In the spec step's `WizardComposer`, add `!bootstrap.llm_enabled` to the
      `submitDisabled` condition (mirror the describe step's pattern)
- [ ] Add a help text line below the composer when `!bootstrap.llm_enabled`:
      "LLM provider is not available — revision requires a configured provider."

### 8.2 Disable IR generation button when LLM is not available
- [ ] Add `!bootstrap.llm_enabled` to the `disabled` condition on the "Generate Circuit IR"
      and "Repair Circuit IR" buttons
- [ ] Add a `<p>` help text when `!bootstrap.llm_enabled`:
      "LLM provider is not available — IR generation requires a configured provider."

---

## 9. Add polling / refresh for running and queued jobs (P2)

`JobPage` fetches the job once on mount and does not poll. If a job is `queued` or `running`,
the user must manually reload the page to see completion.

### 9.1 Auto-refresh job data on `JobPage` while job is in-progress
- [ ] In `JobPage`, replace the raw `useState`/`useEffect` fetch with `useJobQuery` from
      `queries/jobQueries.ts`
- [ ] Add a `refetchInterval` to `useJobQuery` that returns `5000` (5 seconds) when
      `job.status === 'queued' || job.status === 'running'`, and `false` otherwise:
      ```ts
      refetchInterval: (data) =>
        data?.status === 'queued' || data?.status === 'running' ? 5000 : false,
      ```
- [ ] When the job transitions to `succeeded` or `failed`, refetching stops automatically

### 9.2 Show a live "checking for updates" indicator while polling
- [ ] When the job is in-progress and the query is refetching (`isFetching`), show a small
      spinner or "Checking for updates…" note near the Status field in the hero card
- [ ] Do not show a full-page loading banner for background refetches — it should be subtle

---

## 10. Extract shared components and utilities (P3 — duplication)

The following are copy-pasted across `WizardPage.tsx`, `JobPage.tsx`, and
`JsonGeneratePage.tsx`. Each duplicate is a maintenance hazard — a bug fix in one file
does not propagate to the others.

### 10.1 Create `src/utils.ts` (or `src/utils/`) for shared pure helpers
- [ ] Move `joinClasses(...)` to a shared `utils.ts` and import it in all pages
- [ ] Move `formatDate(...)` to the same file
- [ ] Move `getErrorMessage(...)` to the same file
- [ ] Move `statusBannerToneClass(...)` to the same file (identical in 3+ files)
- [ ] Move `statusTone(...)` to the same file (or a `statusHelpers.ts`)
- [ ] Move `statusLabel(...)` to the same file
- [ ] Move `readLastSession()` and `writeLastSession()` to `utils/session.ts` (see 10.4)

### 10.2 Create `src/components/StatusPill.tsx`
- [ ] Extract the `StatusPill` component from `WizardPage.tsx`, `JobPage.tsx`, and
      `JobsPage.tsx` into a shared component file
- [ ] Accept `tone` and `children` as props (same interface already used everywhere)
- [ ] Update all import sites

### 10.3 Create `src/components/WarningCard.tsx` and `src/components/DisclosurePanel.tsx`
- [ ] Extract `WarningCard` from `WizardPage.tsx`, `JobPage.tsx`, and `JsonGeneratePage.tsx`
- [ ] Extract `DisclosurePanel` from `WizardPage.tsx` and `JobPage.tsx`
- [ ] Extract `BuildSummaryPanel` from `WizardPage.tsx` and `JobPage.tsx`
- [ ] Extract `WarningsPanel` from `WizardPage.tsx` and `JobPage.tsx`
- [ ] Update all import sites

### 10.4 Move `readLastSession` / `writeLastSession` out of `WizardPage.tsx`
- [x] Created `src/utils/session.ts` with `readLastSession()`, `writeLastSession()`, and the
      `LS_LAST_SESSION` constant
- [x] Updated `WizardPage.tsx` and `App.tsx` imports to use the new location
- [x] Removed the re-export at the bottom of `WizardPage.tsx` (`export { readLastSession }`)

---

## 11. Minor UX polish (P3)

### 11.1 Suppress the "Warnings" section when empty on `JobPage`
- [ ] In `JobPage`, conditionally render `WarningsPanel` only when `warnings.length > 0`:
      ```tsx
      {warnings.length > 0 ? <WarningsPanel warnings={warnings} /> : null}
      ```
- [ ] The "No warnings." reassurance is already shown in `JobSummaryPanel` on the wizard
      Generate step — that context benefits from it; the standalone job page does not

### 11.2 Fix `JobPage` to stop using raw `useState`/`useEffect`
- [ ] Confirm whether `useJobQuery` exists in `queries/jobQueries.ts` (it is used in
      `JobsPage.tsx` so the hooks file exists — verify the individual job query)
- [ ] Replace the manual fetch in `JobPage` with the appropriate TanStack Query hook
- [ ] This is a prerequisite for Task 9 (polling)

### 11.3 "Retry" button on wizard action failures
- [ ] Currently only the bootstrap load has a "Retry" button; wizard mutation failures show
      an error banner with no recovery affordance
- [ ] After migrating to TanStack Query mutations (Task 1), add a "Try again" button to
      the error banner that calls `mutation.reset()` followed by re-submitting
- [ ] Scoping: add "Try again" to the IR generation failure case first (most common failure
      point), then extend to other actions if needed

### 11.4 `WarningCard` detail filter is inconsistently applied
- [ ] `WizardPage.tsx` line 350 filters `hint` from `detailEntries` but `JobPage.tsx`'s copy
      of `WarningCard` has the same filter (both are fine) — once extracted to a shared
      component (Task 10.3) this inconsistency is resolved automatically; document the
      intended filter rule in a comment in the shared component

---

## Validation checklist

After completing each task, verify with:

```bash
cd frontend && npm run build     # must produce zero TypeScript errors
cd frontend && npm run lint      # must pass
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web
uv run pytest tests/unit/
```

For Task 1 (TanStack Query migration) and Task 9 (polling), additionally run the app and
exercise the wizard happy path manually:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Functional bugs | 1 (TanStack Query migration), 2 (latestJob race) |
| P1 — Broken behaviour | 3 (AppShell bootstrap), 4 (Wizard nav active state), 5 (SymbolsPage debounce), 8 (llm_enabled guards) |
| P2 — Meaningful UX improvement | 6 (inline confirm), 7 (retry label/style), 9 (job polling) |
| P3 — Code quality / polish | 10 (deduplication), 11 (minor UX) |
