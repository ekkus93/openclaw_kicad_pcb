# UI/UX Fixes — Batch 1 TODO

Derived from the comprehensive UI/UX review conducted on 2026-06-02 against the current
`webapp` branch after the per-step wizard refactor.

---

## 1. Raw JSON Overload

The biggest usability problem across the app is that raw JSON payloads are primary page
content. Users are circuit designers, not API debuggers.

### 1.1 Circuit IR step — collapse the Raw Circuit IR JSON block
- [x] Wrap the `<JsonPanel title="Raw Circuit IR JSON" ...>` in a `<details>` / disclosure
      component so it is collapsed by default
- [x] Show a one-line summary when collapsed: e.g. "Raw Circuit IR JSON — 8 components,
      5 nets (click to expand)"
- [x] Keep it expanded automatically only when `ir_validation.valid === false` so the user
      can inspect the bad payload without an extra click

### 1.2 Job detail page — collapse Request Payload and Result Payload
- [x] Move `Request Payload` and `Result Payload` JSON panels into collapsed disclosures
      labelled "Developer details" at the bottom of the page
- [x] Both should be collapsed by default; the user should have to opt in to see them
- [x] Keep `Error Payload` expanded by default when present (it contains the failure reason)

### 1.3 Replace empty `[]` Warnings blocks with plain-text "No warnings"
- [x] In `JobSummaryPanel` (wizard generate step): if warnings array is empty, render
      `<p>No warnings.</p>` instead of a dark JSON panel showing `[]`
- [x] Apply the same fix to the Warnings panel on the job detail page
- [x] Apply the same fix to the Validation Warnings panel on the IR step

### 1.4 Job detail — promote the useful summary, demote raw payloads
- [x] Reorder job page sections: Artifacts → Summary → Warnings → Error (if present) →
      Diagnostics/Debug (collapsed) → Developer details (collapsed)
- [x] The hero area stat cards (Artifacts, Components, Nets) are the right information;
      make sure they are always above the fold

---

## 2. Status Labels and User-Facing Copy

Internal state-machine enum values are shown directly to users.

### 2.1 Replace raw status enum values with human-readable labels
- [x] Add a `statusLabel(status)` helper that maps `WizardStatus` values to plain English:
  - `drafting_spec` → "Drafting spec…"
  - `awaiting_user_clarification` → "Your input needed"
  - `spec_ready_for_review` → "Spec ready for review"
  - `spec_approved` → "Spec approved"
  - `drafting_ir` → "Generating Circuit IR…"
  - `ir_needs_repair` → "IR needs repair"
  - `ir_ready_for_generation` → "IR ready"
  - `generation_started` → "Generating project…"
  - `completed` → "Completed"
  - `failed` → "Failed"
- [x] Replace all `session.status.replaceAll('_', ' ')` calls with `statusLabel(session.status)`
- [x] Apply the same approach to job status pills (currently `job.status` passed directly)

### 2.2 Fix the IR step button and heading when valid IR already exists
- [x] When `session.ir_validation?.valid === true`, change the panel heading from
      "Generate Circuit IR" to "Circuit IR — Valid"
- [x] Change the button label from "Generate Circuit IR" to "Regenerate Circuit IR" when
      valid IR already exists
- [x] Show the validation summary (Valid, Components, Nets) as the primary content when
      IR is valid; move the regenerate button to a secondary/destructive position

### 2.3 Surface the failure reason on the Generate step
- [x] When `visibleLatestJob?.status === 'failed'`, display the job's error message
      directly on the Generate step page (not just inside the job detail page)
- [x] Add a short note such as "Generation failed — review the error below or open the
      job for details" next to the failed job status pill

---

## 3. Navigation and Wayfinding

### 3.1 Fix the "← New Session" label on the Describe step footer
- [x] Rename "← New Session" to "Start New Session" and remove the back-arrow
- [x] Move it out of the back/forward nav row so it is not mistaken for a "go back" action
      (it remains in the footer row but is labelled clearly as a new-session action)

### 3.2 Add a back-link from job detail pages to the originating wizard session
- [x] Pass `?from=session_id` when navigating from wizard to job via `JobSummaryPanel`
- [x] In `JobPage`, read the `from` search param and render a "← Back to session" link
      when present

### 3.3 Header nav — make "Wizard" link smarter
- [x] Write the last visited session ID to `localStorage` whenever a session loads
- [x] The "Wizard" header link reads from `localStorage` and navigates to the last session
      if one exists, otherwise falls back to `/wizard`

### 3.4 Mobile breadcrumb — show step labels, not just numbers
- [x] Remove the `hidden sm:inline` / `sm:hidden` toggle that hid labels on small screens
- [x] Show abbreviated labels on all sizes: "Describe", "Spec", "IR", "Generate"

### 3.5 Add visual separation between breadcrumb and page content
- [x] Added `mb-2` to the breadcrumb nav element

---

## 4. Form Fields and Inputs

### 4.1 Add placeholder and help text for "Symbols Directory"
- [x] Add a `placeholder` to the Symbols Directory input: "Leave blank to use built-in symbols"
- [x] Add a `<p>` help line below the field explaining what it is and that it is optional
- [x] Field is present on both the wizard start page and within the collapsed metadata
      section on the Describe step

### 4.2 Collapse Project Name and Symbols Directory on the Describe step for existing sessions
- [x] On the Describe step (existing session), the metadata fields section is collapsed by
      default behind an "Edit session metadata" toggle button
- [x] On the wizard start page, keep them expanded (they are being set for the first time)

### 4.3 Hide `Ctrl/Cmd+Enter to send` when input is disabled
- [x] In `WizardComposer`, when `disabled === true`, show "Waiting for response…" and hide
      the keyboard shortcut hint

### 4.4 Explain why "Start Session" / "Approve Spec" is disabled
- [x] When LLM is disabled, render an inline note below the Start Session form explaining
      how to configure a provider in `kicad_pcb_web.toml`
- [x] When `Approve Spec` is disabled, render a specific inline explanation for every
      blocking reason (open questions, unsupported reasons, not yet approved, not yet generated)

---

## 5. Timestamps

### 5.1 Format all dates as human-readable strings
- [x] Added `formatDate(isoString)` helper using `Intl.DateTimeFormat`
- [x] Applied to `updated_at` and `created_at` in `JobSummaryPanel` and `JobPage`
- [x] Applied to `updated_at` display in `JobPage` hero card

---

## 6. Job Detail Page — General UX

### 6.1 Fix "Components: —" and "Nets: —" hero metrics on failed jobs
- [x] When `job.status === 'failed'`, hide the Components and Nets metric cards entirely;
      only the Artifacts card is shown in the hero stat grid
- [x] Summary `<dl>` also omits Components/Nets rows on failed jobs

### 6.2 Fix artifact download buttons on failed jobs
- [x] In `JobSummaryPanel`: guard artifact links with `job.artifacts.length > 0` check;
      show "No artifacts — generation did not complete" when empty
- [x] Same fix applied on the job detail Artifacts section

### 6.3 Improve the Diagnostics/Debug panel
- [x] Renamed from "Diagnostics / Debug" to "Build Summary"
- [x] Moved inside a `DisclosurePanel` (collapsed by default) in both `JobSummaryPanel`
      and `JobPage`

---

## 7. Error States and Recovery

### 7.1 Improve the 404 / not-found error screen
- [x] Added `NotFoundScreen` component with a heading, message, and "← Back to Wizard" CTA
- [x] Applied to job not-found and session not-found states
- [x] Also used for the missing-job-id edge case

### 7.2 Add a retry button on bootstrap failure
- [x] When `/api/ui/bootstrap` fails, a "Retry" button is shown alongside the error message
- [x] Clicking Retry increments a `retryKey` state, re-triggering the bootstrap `useEffect`

### 7.3 Add feedback during active spec/IR/generation operations
- [x] Busy banners already show per-action messages (e.g. "Talking to openai to draft the
      first spec…", "Generating the KiCad project…")
- [x] Ellipsis characters updated to use proper Unicode `…` throughout
- Note: a persistent "done" state banner after long-running operations is deferred to a
  future polish pass

---

## 8. Server-Side Path Leakage

### 8.1 Sanitise "Symbols Dirs Used" display value
- [x] Added `displaySymbolsDir(raw)` helper that maps internal resource paths to
      "Built-in symbols" and strips absolute paths to their final directory component
- [x] Applied to the IR validation `symbols_dirs_used` display in the IR step
- [x] Renamed `<dt>` label from "Symbols Dirs Used" to "Symbols"

---

## 9. Minor / Polish

### 9.1 Breadcrumb: add `aria-current="step"` to the current step item
- [x] The current step pill now carries `aria-current="step"`
- [x] Locked/done steps remain non-links (correct for screen readers)

### 9.2 Add `<title>` changes per wizard step
- [x] `WizardPage` has a `useEffect` that sets `document.title` to
      `"<Step> — <ProjectName> — KiCad PCB Web App"` (or without project name if not yet set)
- [x] `JobPage` sets `document.title` to `"<ProjectName> — Job — KiCad PCB Web App"`
- [x] Wizard start page sets `document.title` to `"New Session — KiCad PCB Web App"`

### 9.3 Wizard start page — unify the two accent panels into one
- [x] Merged the intro panel and the form into a single `panelAccentClass` section;
      there is now one visual container on the start page instead of two identical-looking ones

### 9.4 "Open latest job" redundancy on Generate step
- [x] Removed the "Open latest job" link from the Generate step header status row;
      the "Open Job Detail" button inside `JobSummaryPanel` is sufficient

### 9.5 `Generate Again` — add a confirmation or at least a visual warning
- [x] Added `window.confirm()` gate before generating when a succeeded job already exists
- [x] Added an inline warning note: "A project has already been generated. Generating again
      will replace the current job result."
- [x] "Generate Again" button uses `buttonDangerClass` (red border/text) when a succeeded
      job exists to signal destructive intent

### 9.6 Session ID in breadcrumb/page title area
- [x] Project name now appears in the eyebrow line of each step header:
      "Step N of 4 — {project name}" when a project name is available
- [x] This replaces the old raw session ID that was shown in the previous hero panel

---

## Priority Order (suggested)

| Priority | Items |
|---|---|
| P0 — Bugs/breakage | 1.3 ✓, 4.3 ✓, 7.1 ✓, 7.2 ✓, 6.2 ✓, 8.1 ✓ |
| P1 — High user impact | 1.1 ✓, 1.2 ✓, 2.1 ✓, 2.2 ✓, 2.3 ✓, 3.1 ✓, 5.1 ✓ |
| P2 — Meaningful improvement | 1.4 ✓, 3.2 ✓, 3.4 ✓, 4.1 ✓, 4.2 ✓, 4.4 ✓, 6.1 ✓, 6.3 ✓ |
| P3 — Polish | 3.3 ✓, 3.5 ✓, 7.3 ✓, 9.1 ✓, 9.2 ✓, 9.3 ✓, 9.4 ✓, 9.5 ✓, 9.6 ✓ |

All tasks complete.
