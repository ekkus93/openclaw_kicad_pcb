# KiCad PCB Web App UI/UX Refactor Spec

## 1. Purpose

This spec defines a frontend-focused UI/UX modernization pass for the KiCad PCB web app.

The current app has a reasonable backend workflow and a useful AI-assisted wizard concept, but the user interface is too dense, too developer-oriented, and too dependent on the wizard path. The current `frontend/src/App.tsx` file is over 2,000 lines and mixes routing, layout, API orchestration, wizard state, job polling, artifact display, error handling, and visual styling in one file.

The goal of this patch series is to turn the web UI into a clearer KiCad project-generation workbench with multiple entry points:

1. AI wizard: describe a circuit and generate a KiCad project.
2. Direct Circuit IR workflow: paste or upload Circuit IR JSON and generate a KiCad project without an LLM.
3. Jobs workflow: review recent/generated projects and artifacts.
4. Setup workflow: check required local tooling and configuration.
5. Symbols workflow: inspect/search available symbol libraries.

This should be implemented as a series of small, testable patches. Do not perform a single large rewrite.

---

## 2. High-Level Product Goals

### 2.1 Make the app understandable to a first-time user

A first-time user should immediately understand:

- What the app does.
- What workflow they should choose.
- Whether AI/LLM features are enabled.
- Whether deterministic generation is still available without an LLM.
- What setup problems exist before they spend time generating a project.
- Where to download the generated KiCad project.

### 2.2 Make the app feel like a KiCad/EDA workbench, not a debug dashboard

The UI should prioritize:

- Current task.
- Primary action.
- Validation status.
- Preview/output.
- Clear next steps.

Developer/debug information should remain available but should be placed behind explicit "Advanced" or "Developer details" disclosures.

### 2.3 Keep the backend workflow intact

Do not break the existing deterministic backend pipeline. The UI refactor should preserve existing API behavior unless a backend bug is explicitly called out in this spec.

### 2.4 Avoid premature Redux adoption

Do not add Redux for this refactor. The app is primarily server-state driven. Use TanStack Query for API/server state and keep local UI state in React component state.

---

## 3. Main Problems To Fix

### 3.1 `App.tsx` is too large

The current `App.tsx` is a monolith. It should be decomposed into:

- Route/page components.
- Layout components.
- Shared UI primitives.
- Wizard-specific components.
- Job/artifact components.
- Setup/doctor components.
- Query hooks.
- API-specific modules.

### 3.2 App is too wizard-centric

The backend already supports non-wizard functionality such as job access, validation, symbol search, and direct netlist/Circuit IR generation. The frontend should expose those features as first-class workflows.

### 3.3 LLM-disabled state is a dead end

If the provider is disabled, the user should still be able to:

- Generate from existing Circuit IR JSON.
- View recent jobs.
- Run setup/doctor checks.
- Search symbols if the backend supports it.

The user should not land on a disabled wizard and have no useful next step.

### 3.4 UI is too visually dense

The current design uses too many nested cards, badges, banners, gradients, shadows, and raw/debug panels. The result feels like an eye chart.

The redesign should use a calmer, simpler engineering-tool visual system.

### 3.5 Preview generation is incorrectly fatal

Project generation should not fail just because schematic preview export fails due to missing `kicad-cli`, `rsvg-convert`, or other optional preview dependencies.

Expected behavior:

1. Generate the KiCad project.
2. Create the downloadable project ZIP.
3. Attempt to generate previews.
4. If preview generation fails, keep the job successful but show a preview warning.

### 3.6 Frontend package lock/build hygiene is broken

`npm ci` must work from a clean checkout. Ensure `package.json` and `package-lock.json` are synchronized.

### 3.7 FastAPI error details are not surfaced well

The frontend should read both structured error payloads and FastAPI-style `detail` responses.

Expected error priority:

1. `payload.error.message`
2. `payload.detail`
3. Generic HTTP status message

---

## 4. State Management Policy

### 4.1 Use TanStack Query for server state

Add TanStack Query for:

- Bootstrap/config state.
- Wizard session fetch/update state.
- Job status and artifacts.
- Job polling.
- Setup/doctor checks.
- Symbol search.
- Circuit IR validation/generation mutations.

Use the package:

```bash
npm install @tanstack/react-query
npm install -D @tanstack/react-query-devtools
```

### 4.2 Keep React Router for now

Do not switch to TanStack Router as part of this refactor. Routing changes and server-state changes should not be combined.

### 4.3 Keep local UI state local

Use normal React state for:

- Textarea contents.
- Form fields.
- Expanded/collapsed panels.
- Selected tabs.
- Modal/dialog open state.
- Local preview toggles.

### 4.4 Do not add Redux

Redux is not required for this patch. Reconsider only if later features require complex client-only state such as undo/redo editing, offline drafts, or large cross-page editable models.

---

## 5. Target Frontend Structure

Refactor toward this structure:

```text
frontend/src/
  App.tsx
  main.tsx
  queryClient.ts
  queryKeys.ts

  api/
    client.ts
    types.ts
    wizardApi.ts
    jobsApi.ts
    setupApi.ts
    symbolsApi.ts
    validationApi.ts

  queries/
    bootstrapQueries.ts
    wizardQueries.ts
    jobQueries.ts
    setupQueries.ts
    symbolQueries.ts
    validationQueries.ts

  routes/
    HomePage.tsx
    WizardPage.tsx
    JobPage.tsx
    JobsPage.tsx
    SetupPage.tsx
    SymbolsPage.tsx
    JsonGeneratePage.tsx

  components/
    layout/
      AppLayout.tsx
      TopNav.tsx
      PageHeader.tsx
      StepNav.tsx

    ui/
      Button.tsx
      Card.tsx
      StatusPill.tsx
      ErrorBanner.tsx
      WarningBanner.tsx
      InfoBanner.tsx
      LoadingState.tsx
      EmptyState.tsx
      Disclosure.tsx
      JsonPanel.tsx
      FormField.tsx
      TextArea.tsx

    wizard/
      WizardStartStep.tsx
      SpecReviewStep.tsx
      CircuitIrStep.tsx
      GenerateProjectStep.tsx
      WizardProgress.tsx
      SpecSummary.tsx
      IrSummary.tsx
      ProviderDisabledPanel.tsx

    jobs/
      JobStatusPanel.tsx
      ArtifactDownloadCard.tsx
      ArtifactList.tsx
      SchematicPreview.tsx
      JobFailurePanel.tsx
      JobWarningsPanel.tsx

    setup/
      DoctorPanel.tsx
      DependencyStatusCard.tsx
      SetupRecommendationPanel.tsx

    symbols/
      SymbolSearchPanel.tsx
      SymbolResultCard.tsx

    json/
      JsonGenerateForm.tsx
      JsonValidationPanel.tsx
```

This exact structure can be adjusted if the existing project conventions differ, but `App.tsx` must become small and should not contain large workflow implementations.

---

## 6. Routing Requirements

### 6.1 Required routes

Implement or preserve these routes:

```text
/                     Home page
/wizard               New AI wizard flow
/wizard/:sessionId    Existing wizard session
/jobs                 Recent jobs
/jobs/:jobId          Job detail page
/setup                Setup/doctor page
/symbols              Symbol search page
/generate-json        Direct Circuit IR JSON generate page
```

### 6.2 Home page behavior

The home page must not redirect immediately to `/wizard`.

The home page should show clear workflow cards:

1. **Use AI Wizard**
   - Describe a circuit and let the app draft the spec and Circuit IR.
   - If provider is disabled, show disabled status and link to setup.

2. **Generate from Circuit IR JSON**
   - Paste or upload existing JSON and generate a KiCad project directly.
   - This should work without an LLM provider.

3. **Open Recent Jobs**
   - View generated projects, previews, warnings, and downloads.

4. **Check Setup**
   - Verify KiCad CLI, optional preview tooling, symbol paths, and app configuration.

5. **Search Symbols**
   - Inspect available symbols/libraries.

---

## 7. TanStack Query Requirements

### 7.1 Query client

Create `frontend/src/queryClient.ts`:

```ts
import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 10_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
})
```

### 7.2 Query provider

Wrap the app in `QueryClientProvider` in `main.tsx`.

Add React Query Devtools in development only, or add it with `initialIsOpen={false}` if that matches the project style.

### 7.3 Query keys

Create `frontend/src/queryKeys.ts` with stable, typed query keys:

```ts
export const queryKeys = {
  bootstrap: ['bootstrap'] as const,
  jobs: ['jobs'] as const,
  job: (jobId: string) => ['jobs', jobId] as const,
  doctor: ['doctor'] as const,
  symbols: (query: string) => ['symbols', query] as const,
  wizardSession: (sessionId: string) => ['wizard', sessionId] as const,
}
```

Adjust names as needed for the actual API types.

### 7.4 Job polling

Job polling should be implemented with TanStack Query `refetchInterval`, not manual `setInterval` code inside page components.

Poll only while the job status is active, such as:

```text
queued
running
```

Stop polling when the job reaches a terminal state, such as:

```text
succeeded
failed
cancelled
completed
```

Use the actual status enum from the backend.

### 7.5 Mutations

Use mutations for actions:

- Start wizard session.
- Submit/refresh spec.
- Approve spec.
- Generate Circuit IR.
- Generate project.
- Validate Circuit IR JSON.
- Generate project from Circuit IR JSON.

On successful mutations, update or invalidate relevant queries.

---

## 8. API Client Requirements

### 8.1 Preserve type safety

Keep API response/request types centralized. Do not spread ad-hoc `any` types through components.

### 8.2 Improve error parsing

Update request error handling to support:

```ts
const message =
  payload?.error?.message ??
  payload?.detail ??
  `Request failed with status ${response.status}`
```

If `payload.detail` can be an array/object, convert it to a readable string.

### 8.3 Split API modules

Move large API definitions toward:

- `api/client.ts` for low-level request handling.
- `api/wizardApi.ts` for wizard endpoints.
- `api/jobsApi.ts` for jobs endpoints.
- `api/setupApi.ts` for doctor/bootstrap endpoints.
- `api/symbolsApi.ts` for symbol endpoints.
- `api/validationApi.ts` for Circuit IR validation/generation endpoints.

If the current app is small enough to keep a single `api.ts`, this split can be delayed, but the components should not contain raw `fetch` calls.

---

## 9. Wizard UX Requirements

### 9.1 Wizard should remain available

Preserve the AI wizard workflow.

### 9.2 Provider-disabled state

If the provider is disabled:

- Show a clear disabled panel.
- Explain that AI generation is unavailable.
- Provide links/actions to:
  - Setup page.
  - Direct Circuit IR JSON generation page.
  - Recent jobs.

Do not leave the user with only a disabled button.

### 9.3 Start step copy

The start step should provide a clear example prompt:

```text
Example:
Make a 555 timer LED blinker.
Supply: 5V.
Output: one LED.
Constraints: through-hole parts, use NE555, about 1 Hz blink rate.
```

Optionally provide guided fields:

- Purpose.
- Power supply.
- Inputs.
- Outputs.
- Required parts.
- Constraints.

If guided fields are added, keep a freeform description path too.

### 9.4 Spec review step

The spec review should show these fields clearly, if available:

- Purpose/goal.
- Power rails.
- Inputs.
- Outputs.
- Functional blocks.
- Required components.
- Packaging preferences.
- Assumptions.
- Open questions.
- Unsupported items/reasons.
- Approval blockers.

Approval should be visually tied to the review checklist.

If approval is blocked, show the exact blocker list immediately above or near the approval action.

### 9.5 Circuit IR step

The Circuit IR step should use user-friendly language first:

- Generated circuit plan.
- Component count.
- Net count.
- Validation status.
- Warnings/errors.
- Auto-repair status.

Raw Circuit IR JSON should be hidden behind an Advanced/Developer disclosure.

### 9.6 Remove duplicate frontend IR repair retry

If the backend already performs IR repair attempts, the frontend should not immediately call the same generate-IR endpoint a second time after receiving an `ir_needs_repair` status.

The backend should own repair policy. The frontend should display the returned status and recommended next action.

### 9.7 Generate project step

The final wizard step should clearly show:

- Primary action: Generate KiCad Project.
- Validation summary.
- Warnings summary.
- Output destination once generated.
- Link to resulting job detail page.

---

## 10. Direct Circuit IR JSON Workflow

Add `/generate-json`.

The page should allow the user to:

1. Paste Circuit IR JSON into a text area.
2. Optionally upload a `.json` file if file-upload support is simple to implement.
3. Validate the JSON.
4. See validation errors/warnings in a readable panel.
5. Generate a KiCad project from valid JSON.
6. Navigate to the resulting job detail page.

This workflow must not require an LLM provider.

### 10.1 Validation UX

Validation results should be grouped:

- Errors: must fix before generation.
- Warnings: generation may continue, but review suggested.
- Summary: component count, net count, unsupported fields if available.

### 10.2 JSON editor UX

Do not build a full code editor unless already available. A textarea is acceptable for the first pass.

Minimum UX requirements:

- Monospace text area.
- Reasonable height.
- Clear validation button.
- Clear generate button.
- Disabled generate button when JSON is invalid.
- Example JSON link or inline sample if an example exists in the repo.

---

## 11. Jobs UX Requirements

### 11.1 Jobs list page

Add `/jobs` with recent jobs.

Each job card/row should show:

- Status.
- Created/updated time if available.
- Source type if available: wizard or JSON.
- Project/artifact availability.
- Warning/error indicator.
- Link to detail page.

### 11.2 Job detail page

Add `/jobs/:jobId` or improve the existing job detail view.

Show:

- Job status.
- Schematic preview if available.
- Preview unavailable warning if preview generation failed.
- Human-readable artifact download cards.
- Warnings/errors.
- Advanced debug details.

### 11.3 Artifact download cards

Do not show raw filenames as the primary UI label.

Use labels like:

- Download KiCad Project
- Download Schematic
- Download Warnings Report
- Download Debug Data

Raw filenames can appear as secondary metadata.

---

## 12. Setup/Doctor UX Requirements

Add `/setup`.

The setup page should call the existing doctor/bootstrap endpoints if available.

Show checks for:

- KiCad CLI availability.
- Optional preview tooling availability.
- Symbol library configuration.
- Data/artifact directory availability.
- LLM provider status.
- Any environment/config warnings.

Each check should show:

- Status: pass/warn/fail/unknown.
- What it means.
- How to fix it if known.

The setup page should make clear that preview tooling is optional if project ZIP generation can still succeed without it.

---

## 13. Symbols UX Requirements

Add `/symbols` if the backend supports symbol search.

The symbol search page should allow:

- Search by symbol name/keyword.
- Show matching library/name pairs.
- Show symbol metadata if available.
- Show empty state if no results.
- Show setup guidance if symbols are unavailable.

Do not make symbol search required for the main wizard flow.

---

## 14. Visual Design Requirements

### 14.1 General style

Use a cleaner engineering-tool visual language.

Recommended direction:

- Light neutral background.
- White or very light panels.
- Slate/gray text.
- Deep blue primary actions.
- Amber/copper warning accents.
- Green success accents.
- Red error accents.
- Minimal gradients.
- Minimal shadows.
- More whitespace between major sections.

### 14.2 Avoid eye-chart density

Reduce:

- Nested cards.
- Excessive uppercase labels.
- Excessive badges/pills.
- Multiple competing banners.
- Raw JSON visible by default.
- Long paragraphs in narrow cards.

Prefer:

- One clear page heading.
- One primary action per step.
- Short explanatory copy.
- Collapsed advanced sections.
- Clear grouping by task.

### 14.3 Design token constants

If Tailwind class constants are used, centralize them in a small design token module rather than defining many constants in `App.tsx`.

Possible file:

```text
frontend/src/styles/designTokens.ts
```

Do not over-engineer a design system. The immediate goal is consistency and readability.

---

## 15. Backend UX Bug Fix Requirements

### 15.1 Preview generation must be non-fatal

If schematic preview generation fails:

- Do not fail the entire project generation job if the KiCad project itself was generated.
- Still create the project ZIP.
- Record a warning describing why preview is unavailable.
- Surface that warning in the job result UI.

### 15.2 Preserve fatal errors for actual generation failures

Actual generation failures should remain fatal, including:

- Invalid Circuit IR.
- Unresolvable required symbols/components if generation cannot proceed.
- File write failures.
- Backend exceptions during project creation.

### 15.3 Tests for preview fallback

Add or update tests proving:

- Missing `kicad-cli` does not prevent `project.zip` from being created.
- Preview warning is recorded.
- Job status remains successful/completed if only preview generation failed.

---

## 16. Testing Requirements

### 16.1 Frontend build/package tests

These must pass from a clean checkout:

```bash
cd frontend
npm ci
npm run build
npm test
```

If there is no frontend test script, do not invent one unless adding tests. At minimum, build must pass.

### 16.2 Backend tests

Run the backend test suite relevant to:

- Wizard endpoints.
- Netlist/Circuit IR validation.
- Job generation.
- Artifact generation.
- Preview fallback.

Use the project's existing test commands.

### 16.3 Manual smoke tests

Verify these browser workflows:

1. Home page loads and shows multiple workflows.
2. Provider-disabled wizard shows useful alternatives.
3. Direct JSON generation works without provider enabled.
4. Wizard flow still works when provider is enabled/configured.
5. Job detail page displays artifacts and warnings.
6. Missing preview tooling does not prevent ZIP download.
7. Setup page clearly reports missing optional/required tools.

---

## 17. Acceptance Criteria

The refactor is complete when:

- `App.tsx` is small and primarily contains route wiring.
- TanStack Query is installed and used for server state.
- Job polling uses TanStack Query polling, not manual component intervals.
- React Router remains in place.
- Redux is not added.
- Home page no longer redirects immediately to the wizard.
- LLM-disabled state is no longer a dead end.
- Direct Circuit IR JSON generation is available from the UI.
- Setup/doctor page is available from the UI.
- Jobs list/detail views are available from the UI.
- Wizard steps are split into readable components.
- Raw JSON/debug details are hidden behind Advanced/Developer disclosures.
- Artifact downloads use human-readable labels.
- Preview generation failure does not fail the entire job.
- `npm ci` works with the committed lockfile.
- Frontend build passes.
- Relevant backend tests pass.

---

## 18. Non-Goals

Do not implement these in this patch series:

- Redux migration.
- TanStack Router migration.
- Full schematic editor.
- Drag-and-drop circuit design canvas.
- Full Monaco/code-editor integration unless already trivial.
- Authentication/multi-user accounts.
- Cloud job storage.
- Real-time collaboration.
- Major backend API redesign unrelated to the UX bugs listed here.

---

## 19. Implementation Strategy

Implement as multiple small patches, each with tests/build checks.

Recommended patch order:

1. Package/build hygiene.
2. TanStack Query infrastructure.
3. Query hooks for bootstrap/jobs/job polling.
4. Split layout/shared UI components.
5. Split wizard components.
6. Add real home page.
7. Add setup page.
8. Add jobs pages.
9. Add direct JSON generation page.
10. Improve wizard copy/spec/IR review UX.
11. Improve artifact/job detail UX.
12. Make preview generation non-fatal.
13. Final visual polish and cleanup.

Do not combine all phases into one PR/patch.
