# KiCad PCB Web App UI/UX Refactor TODO

This TODO implements `KICAD_WEB_UI_UX_REFACTOR_SPEC.md`.

Work in small patches. After each phase, run the relevant tests/build commands and fix regressions before continuing.

---

## Phase 0: Baseline Verification

- [ ] Unzip/open the latest repo and inspect the current frontend/backend structure.
- [ ] Confirm current frontend package manager workflow.
- [ ] Confirm whether `frontend/package.json` has scripts for:
  - [ ] `build`
  - [ ] `test`
  - [ ] `lint`
  - [ ] `typecheck`
- [ ] Run current frontend install/build from a clean state:
  - [ ] `cd frontend`
  - [ ] `npm ci`
  - [ ] `npm run build`
- [ ] Record any current failures before making changes.
- [ ] Run relevant backend tests and record current failures before making changes.
- [ ] Identify the current API endpoints used by the frontend.
- [ ] Identify existing backend endpoints for:
  - [ ] bootstrap/config
  - [ ] jobs list
  - [ ] job detail
  - [ ] doctor/setup
  - [ ] symbol search
  - [ ] Circuit IR/netlist validation
  - [ ] direct generation from Circuit IR/netlist
  - [ ] wizard session actions

Acceptance criteria:

- [ ] Current baseline failures are known.
- [ ] No code changes have been made yet except optional notes/docs.

---

## Phase 1: Frontend Package/Build Hygiene

- [ ] Fix `frontend/package.json` and `frontend/package-lock.json` synchronization.
- [ ] Ensure `npm ci` works from a clean checkout.
- [ ] Ensure frontend dependencies include all required type packages.
- [ ] Remove stale lockfile entries if needed by regenerating the lockfile with the project npm version.
- [ ] Commit/package the updated lockfile.
- [ ] Run:
  - [ ] `cd frontend && npm ci`
  - [ ] `cd frontend && npm run build`
- [ ] If a frontend test script exists, run it and fix failures.

Acceptance criteria:

- [ ] `npm ci` succeeds.
- [ ] `npm run build` succeeds.
- [ ] The committed lockfile is synchronized with `package.json`.

---

## Phase 2: Add TanStack Query Infrastructure

- [ ] Install TanStack Query:
  - [ ] `npm install @tanstack/react-query`
  - [ ] `npm install -D @tanstack/react-query-devtools`
- [ ] Create `frontend/src/queryClient.ts`.
- [ ] Configure conservative defaults:
  - [ ] `staleTime: 10_000`
  - [ ] `gcTime: 5 * 60_000`
  - [ ] `retry: 1`
  - [ ] `refetchOnWindowFocus: false`
- [ ] Create `frontend/src/queryKeys.ts`.
- [ ] Add stable query keys for:
  - [ ] bootstrap
  - [ ] jobs list
  - [ ] job detail
  - [ ] doctor/setup
  - [ ] symbols search
  - [ ] wizard session
  - [ ] validation/generation if useful
- [ ] Wrap the app in `QueryClientProvider` in `main.tsx`.
- [ ] Add React Query Devtools with `initialIsOpen={false}`.
- [ ] Ensure Devtools are either development-only or acceptable in the current local web app context.
- [ ] Do not change visible UI behavior in this phase.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] TanStack Query is installed.
- [ ] App is wrapped in `QueryClientProvider`.
- [ ] Existing UI still renders.
- [ ] Frontend build passes.
- [ ] Redux has not been added.
- [ ] React Router remains in use.

---

## Phase 3: Improve API Error Handling

- [ ] Locate the low-level request helper, currently likely in `frontend/src/api.ts`.
- [ ] Update error parsing to prefer:
  - [ ] `payload.error.message`
  - [ ] `payload.detail`
  - [ ] generic HTTP status message
- [ ] If `payload.detail` is an array, object, or validation payload, stringify or format it readably.
- [ ] Preserve existing custom error behavior if present.
- [ ] Add or update tests if frontend tests cover API errors.
- [ ] Manually verify that FastAPI `HTTPException(detail='...')` style errors show useful messages.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] FastAPI `detail` errors are displayed to users.
- [ ] Existing structured error payloads still work.
- [ ] Frontend build passes.

---

## Phase 4: Convert Bootstrap and Job Fetching to TanStack Query

- [ ] Create `frontend/src/queries/bootstrapQueries.ts`.
- [ ] Implement `useBootstrapQuery()`.
- [ ] Replace manual bootstrap loading state in `App.tsx` or current page components with `useBootstrapQuery()`.
- [ ] Create `frontend/src/queries/jobQueries.ts`.
- [ ] Implement `useJobsQuery()` if a jobs-list endpoint exists.
- [ ] Implement `useJobQuery(jobId)`.
- [ ] Move job polling into `useJobQuery(jobId)` using `refetchInterval`.
- [ ] Poll only when job status is active, such as queued/running.
- [ ] Stop polling for terminal statuses.
- [ ] Remove manual `setInterval`/timer job polling from UI components where replaced.
- [ ] Preserve visible behavior.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Bootstrap server state uses TanStack Query.
- [ ] Job detail server state uses TanStack Query.
- [ ] Job polling uses TanStack Query polling.
- [ ] Manual component-level polling is removed for converted jobs.
- [ ] Frontend build passes.

---

## Phase 5: Convert Wizard Actions to TanStack Query Mutations

- [ ] Create `frontend/src/queries/wizardQueries.ts`.
- [ ] Implement mutation hooks for:
  - [ ] start wizard session
  - [ ] submit/generate spec
  - [ ] approve spec
  - [ ] revise spec if supported
  - [ ] generate Circuit IR
  - [ ] generate KiCad project
- [ ] On mutation success, update or invalidate relevant wizard-session query data.
- [ ] Preserve current navigation behavior after session creation.
- [ ] Preserve current navigation behavior after project generation.
- [ ] Remove duplicate frontend IR repair retry if the backend already handles repair attempts.
- [ ] Ensure the frontend calls generate-IR once per user action unless the user explicitly retries.
- [ ] Surface backend `ir_needs_repair` or equivalent statuses clearly instead of silently calling again.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Wizard actions use mutation hooks.
- [ ] Backend remains the source of truth for IR repair policy.
- [ ] No hidden double LLM calls from the frontend.
- [ ] Frontend build passes.

---

## Phase 6: Extract Shared UI Components

Create reusable components before moving entire pages.

- [ ] Create `frontend/src/components/ui/Button.tsx`.
- [ ] Create `frontend/src/components/ui/Card.tsx`.
- [ ] Create `frontend/src/components/ui/StatusPill.tsx`.
- [ ] Create `frontend/src/components/ui/ErrorBanner.tsx`.
- [ ] Create `frontend/src/components/ui/WarningBanner.tsx` if needed.
- [ ] Create `frontend/src/components/ui/InfoBanner.tsx` if needed.
- [ ] Create `frontend/src/components/ui/LoadingState.tsx`.
- [ ] Create `frontend/src/components/ui/EmptyState.tsx`.
- [ ] Create `frontend/src/components/ui/Disclosure.tsx`.
- [ ] Create `frontend/src/components/ui/JsonPanel.tsx`.
- [ ] Create `frontend/src/components/ui/FormField.tsx` if useful.
- [ ] Create `frontend/src/components/ui/TextArea.tsx` if useful.
- [ ] Replace duplicated inline UI fragments in `App.tsx` with these components incrementally.
- [ ] Avoid changing layout/styling drastically in this phase.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Common UI fragments are extracted.
- [ ] `App.tsx` line count is reduced.
- [ ] Visible behavior is preserved.
- [ ] Frontend build passes.

---

## Phase 7: Extract Layout Components

- [ ] Create `frontend/src/components/layout/AppLayout.tsx`.
- [ ] Create `frontend/src/components/layout/TopNav.tsx`.
- [ ] Create `frontend/src/components/layout/PageHeader.tsx`.
- [ ] Create `frontend/src/components/layout/StepNav.tsx` if useful for wizard progress.
- [ ] Move header/nav rendering out of `App.tsx`.
- [ ] Fix the “last session” header behavior so it is reactive:
  - [ ] Do not rely only on direct localStorage reads during render.
  - [ ] Use React state/context or query/cache-backed state for the current last session id.
- [ ] Preserve existing routes.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Layout/header code is out of `App.tsx`.
- [ ] Header last-session link updates after a new session is created.
- [ ] Frontend build passes.

---

## Phase 8: Extract Wizard Page and Step Components

- [ ] Create `frontend/src/routes/WizardPage.tsx`.
- [ ] Move wizard route/page logic out of `App.tsx`.
- [ ] Create `frontend/src/components/wizard/WizardProgress.tsx`.
- [ ] Create `frontend/src/components/wizard/WizardStartStep.tsx`.
- [ ] Create `frontend/src/components/wizard/SpecReviewStep.tsx`.
- [ ] Create `frontend/src/components/wizard/CircuitIrStep.tsx`.
- [ ] Create `frontend/src/components/wizard/GenerateProjectStep.tsx`.
- [ ] Create `frontend/src/components/wizard/SpecSummary.tsx`.
- [ ] Create `frontend/src/components/wizard/IrSummary.tsx`.
- [ ] Create `frontend/src/components/wizard/ProviderDisabledPanel.tsx`.
- [ ] Keep local form state local to the relevant step component.
- [ ] Keep server state in query/mutation hooks.
- [ ] Ensure wizard can still resume by session id.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Wizard implementation is no longer embedded in `App.tsx`.
- [ ] Wizard workflow still functions.
- [ ] Wizard route with session id still works.
- [ ] Frontend build passes.

---

## Phase 9: Add a Real Home Page

- [ ] Stop redirecting `/` directly to `/wizard`.
- [ ] Create `frontend/src/routes/HomePage.tsx`.
- [ ] Add workflow cards for:
  - [ ] Use AI Wizard
  - [ ] Generate from Circuit IR JSON
  - [ ] Open Recent Jobs
  - [ ] Check Setup
  - [ ] Search Symbols
- [ ] Show provider status from bootstrap query.
- [ ] If provider is disabled, show AI Wizard as unavailable or limited, but keep other workflows active.
- [ ] Add clear descriptions for each workflow.
- [ ] Add primary/secondary action buttons.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] `/` loads a real home page.
- [ ] User sees useful options even when provider is disabled.
- [ ] Frontend build passes.

---

## Phase 10: Add Setup/Doctor Page

- [ ] Create `frontend/src/routes/SetupPage.tsx`.
- [ ] Create `frontend/src/queries/setupQueries.ts`.
- [ ] Implement `useDoctorQuery()` or equivalent using the existing backend endpoint.
- [ ] Create `frontend/src/components/setup/DoctorPanel.tsx`.
- [ ] Create `frontend/src/components/setup/DependencyStatusCard.tsx`.
- [ ] Create `frontend/src/components/setup/SetupRecommendationPanel.tsx` if useful.
- [ ] Show status for:
  - [ ] KiCad CLI
  - [ ] optional preview tooling
  - [ ] symbol library configuration
  - [ ] data/artifact directory
  - [ ] LLM provider status
  - [ ] other backend-reported setup issues
- [ ] Distinguish required dependencies from optional preview dependencies.
- [ ] Provide concise fix guidance where known.
- [ ] Add `/setup` link to top nav and home page.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] `/setup` loads and reports environment status.
- [ ] Missing optional preview tooling is not presented as a full app blocker.
- [ ] Frontend build passes.

---

## Phase 11: Add Jobs List and Job Detail Pages

- [ ] Create `frontend/src/routes/JobsPage.tsx`.
- [ ] Create or improve `frontend/src/routes/JobPage.tsx`.
- [ ] Create `frontend/src/components/jobs/JobStatusPanel.tsx`.
- [ ] Create `frontend/src/components/jobs/ArtifactList.tsx`.
- [ ] Create `frontend/src/components/jobs/ArtifactDownloadCard.tsx`.
- [ ] Create `frontend/src/components/jobs/SchematicPreview.tsx`.
- [ ] Create `frontend/src/components/jobs/JobFailurePanel.tsx`.
- [ ] Create `frontend/src/components/jobs/JobWarningsPanel.tsx`.
- [ ] Jobs list should show:
  - [ ] status
  - [ ] created/updated timestamp if available
  - [ ] source type if available
  - [ ] artifact availability
  - [ ] warnings/errors indicator
  - [ ] link to detail page
- [ ] Job detail should show:
  - [ ] status
  - [ ] preview if available
  - [ ] preview-unavailable warning if applicable
  - [ ] human-readable artifact cards
  - [ ] warnings/errors
  - [ ] advanced debug details behind disclosure
- [ ] Add `/jobs` link to top nav and home page.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] `/jobs` shows recent jobs if backend supports listing.
- [ ] `/jobs/:jobId` shows job detail.
- [ ] Job polling works on active job detail pages.
- [ ] Artifacts are shown with human-readable labels.
- [ ] Frontend build passes.

---

## Phase 12: Add Direct Circuit IR JSON Generate Page

- [ ] Create `frontend/src/routes/JsonGeneratePage.tsx`.
- [ ] Create `frontend/src/queries/validationQueries.ts` if useful.
- [ ] Create `frontend/src/components/json/JsonGenerateForm.tsx`.
- [ ] Create `frontend/src/components/json/JsonValidationPanel.tsx`.
- [ ] Provide a textarea for Circuit IR JSON.
- [ ] Use monospace styling for the JSON textarea.
- [ ] Add Validate action.
- [ ] Add Generate Project action.
- [ ] Disable Generate action when JSON is invalid.
- [ ] Display validation results grouped by:
  - [ ] errors
  - [ ] warnings
  - [ ] summary
- [ ] If upload support is simple, allow selecting a `.json` file and loading it into the textarea.
- [ ] On successful generation, navigate to the job detail page.
- [ ] This workflow must work without an LLM provider.
- [ ] Add `/generate-json` link to top nav and home page.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] User can paste Circuit IR JSON and validate it.
- [ ] User can generate a KiCad project from valid JSON.
- [ ] LLM provider is not required.
- [ ] Resulting job page is reachable.
- [ ] Frontend build passes.

---

## Phase 13: Add Symbols Page

- [ ] Confirm backend symbol search endpoint behavior.
- [ ] Create `frontend/src/routes/SymbolsPage.tsx`.
- [ ] Create `frontend/src/queries/symbolQueries.ts`.
- [ ] Create `frontend/src/components/symbols/SymbolSearchPanel.tsx`.
- [ ] Create `frontend/src/components/symbols/SymbolResultCard.tsx`.
- [ ] Add search input.
- [ ] Show matching symbols with library/name metadata.
- [ ] Show loading state.
- [ ] Show empty state.
- [ ] Show setup guidance if symbol search is unavailable.
- [ ] Add `/symbols` link to top nav and home page.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] `/symbols` loads.
- [ ] User can search symbols if backend supports it.
- [ ] Empty/error states are understandable.
- [ ] Frontend build passes.

---

## Phase 14: Improve Wizard UX and Copy

- [ ] Add a concrete example prompt to the start step:
  - [ ] 555 timer LED blinker
  - [ ] 5V supply
  - [ ] one LED output
  - [ ] through-hole/NE555/about 1 Hz constraints
- [ ] Optionally add guided fields:
  - [ ] purpose
  - [ ] power supply
  - [ ] inputs
  - [ ] outputs
  - [ ] required parts
  - [ ] constraints
- [ ] Keep a freeform description option.
- [ ] Improve provider-disabled panel with links to:
  - [ ] Setup
  - [ ] Generate from Circuit IR JSON
  - [ ] Jobs
- [ ] Update spec review display to include, when available:
  - [ ] purpose/goal
  - [ ] power rails
  - [ ] inputs
  - [ ] outputs
  - [ ] functional blocks
  - [ ] required components
  - [ ] packaging preferences
  - [ ] assumptions
  - [ ] open questions
  - [ ] unsupported items/reasons
  - [ ] approval blockers
- [ ] Place approval blockers near the approval button.
- [ ] Rename/soften user-facing “Circuit IR” language where possible:
  - [ ] Generated circuit plan
  - [ ] Advanced: Circuit IR JSON
- [ ] Hide raw JSON behind Advanced/Developer disclosure by default.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Wizard is more understandable to non-developer users.
- [ ] Spec review shows all important review fields.
- [ ] Raw JSON is no longer the primary visual surface.
- [ ] Frontend build passes.

---

## Phase 15: Improve Job Result and Artifact UX

- [ ] Replace raw artifact filename labels with human-readable labels:
  - [ ] Download KiCad Project
  - [ ] Download Schematic
  - [ ] Download Warnings Report
  - [ ] Download Debug Data
- [ ] Show raw filename as secondary metadata only.
- [ ] Add short artifact descriptions.
- [ ] Make the project ZIP the primary download action.
- [ ] Show schematic preview prominently when available.
- [ ] Show preview unavailable warning when preview failed but project exists.
- [ ] Move debug JSON/details behind Advanced/Developer disclosure.
- [ ] Add “Next steps” section:
  - [ ] Open project in KiCad
  - [ ] Run ERC
  - [ ] Review symbols/footprints
- [ ] Run frontend build.

Acceptance criteria:

- [ ] Download UI feels like a product UI, not a raw file browser.
- [ ] Project ZIP is clearly the main output.
- [ ] Preview failures are understandable.
- [ ] Frontend build passes.

---

## Phase 16: Backend Fix - Make Preview Generation Non-Fatal

- [ ] Locate project generation flow, likely in `src/kicad_pcb_web/services/netlists.py` or equivalent.
- [ ] Identify where schematic preview generation is called.
- [ ] Ensure project generation and ZIP creation happen even if preview export fails.
- [ ] Wrap preview generation in a narrow try/except.
- [ ] Record a warning when preview generation fails.
- [ ] Preserve fatal behavior for actual project generation failures.
- [ ] Ensure warning is included in job status/artifacts response if possible.
- [ ] Add or update tests for missing preview tooling.
- [ ] Verify that missing `kicad-cli` or `rsvg-convert` does not prevent project ZIP creation when the project itself is valid.
- [ ] Run backend tests.

Acceptance criteria:

- [ ] Preview export failure no longer fails the entire job.
- [ ] Project ZIP is still created.
- [ ] Job result contains a preview-unavailable warning.
- [ ] Backend tests pass.

---

## Phase 17: Visual Design Simplification

- [ ] Reduce nested cards in main workflows.
- [ ] Remove or reduce gradients that compete for attention.
- [ ] Use fewer shadows.
- [ ] Use fewer uppercase labels.
- [ ] Use badges/pills only for meaningful status values.
- [ ] Collapse “no warnings” or “empty debug” panels by default.
- [ ] Increase spacing between major sections.
- [ ] Make page hierarchy clearer:
  - [ ] page title
  - [ ] short description
  - [ ] primary work area
  - [ ] secondary details
  - [ ] advanced details
- [ ] Ensure each wizard step has one obvious primary action.
- [ ] Create `frontend/src/styles/designTokens.ts` if styling constants are still repeated.
- [ ] Prefer a calm engineering palette:
  - [ ] neutral light background
  - [ ] white/light panels
  - [ ] slate text
  - [ ] deep blue primary action
  - [ ] amber warning
  - [ ] green success
  - [ ] red error
- [ ] Run frontend build.

Acceptance criteria:

- [ ] UI is visibly less dense.
- [ ] Advanced/debug information is still available but not visually dominant.
- [ ] Primary actions are easy to identify.
- [ ] Frontend build passes.

---

## Phase 18: App.tsx Final Cleanup

- [ ] Reduce `frontend/src/App.tsx` to route wiring and minimal app-level setup.
- [ ] Move all large UI sections out of `App.tsx`.
- [ ] Move all large workflow logic out of `App.tsx`.
- [ ] Move repeated style constants out of `App.tsx`.
- [ ] Remove unused imports/types/functions from `App.tsx`.
- [ ] Confirm `App.tsx` is easy to read from top to bottom.
- [ ] Run frontend build.

Acceptance criteria:

- [ ] `App.tsx` is no longer a 2,000+ line monolith.
- [ ] `App.tsx` primarily wires providers/layout/routes.
- [ ] Frontend build passes.

---

## Phase 19: Tests and Manual Smoke Testing

### Frontend commands

- [ ] Run `cd frontend && npm ci`.
- [ ] Run `cd frontend && npm run build`.
- [ ] Run frontend tests if available.
- [ ] Run frontend lint/typecheck if available.

### Backend commands

- [ ] Run the backend test suite relevant to web/API/job generation.
- [ ] Run specific tests for preview fallback.

### Manual browser smoke tests

- [ ] `/` loads the home page.
- [ ] Home page shows all major workflows.
- [ ] `/wizard` loads.
- [ ] Provider-disabled wizard shows useful alternatives.
- [ ] Wizard session can start when provider is enabled/configured.
- [ ] Spec review displays required components, assumptions, blockers, and open questions.
- [ ] Circuit plan/IR step works.
- [ ] Project generation starts and navigates to/shows job result.
- [ ] `/generate-json` allows JSON validation.
- [ ] `/generate-json` can generate a project without an LLM provider.
- [ ] `/jobs` lists recent jobs if supported.
- [ ] `/jobs/:jobId` displays status, preview/artifacts, warnings, and downloads.
- [ ] Missing preview tooling does not prevent ZIP download.
- [ ] `/setup` reports setup/dependency status.
- [ ] `/symbols` search works if backend supports it.
- [ ] Raw JSON/debug panels are hidden behind Advanced/Developer disclosures by default.
- [ ] The UI is readable at common desktop widths.
- [ ] The UI is usable at narrow/mobile widths.

Acceptance criteria:

- [ ] All required builds/tests pass.
- [ ] Manual smoke tests pass.
- [ ] Known remaining issues are documented.

---

## Phase 20: Final Review Checklist

- [ ] No Redux dependency was added.
- [ ] React Router was not replaced.
- [ ] TanStack Query is used for server state.
- [ ] Local UI state remains local.
- [ ] Query keys are centralized.
- [ ] API calls are not scattered directly through UI components.
- [ ] Job polling is query-driven.
- [ ] Wizard components are split by step.
- [ ] Home page exists and is useful.
- [ ] Setup page exists and is useful.
- [ ] Direct JSON generation page exists and does not require LLM provider.
- [ ] Jobs pages exist and are useful.
- [ ] Symbols page exists if backend supports it.
- [ ] Preview generation failure is non-fatal.
- [ ] FastAPI `detail` errors are surfaced.
- [ ] `npm ci` works.
- [ ] Frontend build passes.
- [ ] Backend tests pass or known unrelated failures are documented.
- [ ] UI is significantly less dense than before.
- [ ] `App.tsx` is substantially smaller and easier to understand.

---

## Notes for Copilot

- Do not implement all phases in one giant patch.
- Prefer small, reviewable commits/patches.
- Preserve existing backend behavior unless this TODO explicitly calls for a backend fix.
- Do not invent new backend endpoints if suitable endpoints already exist.
- Do not remove advanced/debug data; move it behind disclosures.
- Do not hide warnings/errors just to make the UI look cleaner.
- Do not make preview tooling mandatory.
- Do not add Redux.
- Do not switch to TanStack Router in this patch series.
- Keep the app local-first and developer-friendly, but make the default UI understandable to a normal user.
