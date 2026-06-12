# UI/UX Refactor Inventory

Baseline inventory captured at the start of the KICAD_WEB_UI_UX_REFACTOR work.
Branch: webapp. Date: 2026-06-04.

---

## Current frontend structure

```
frontend/src/
  App.tsx         2,172 lines — monolith (all routes, all components, all logic)
  api.ts          130 lines — single API module
  main.tsx        ~10 lines — mounts App
  types.ts        ~150 lines — all TypeScript types
  index.css       ~130 lines — global CSS + Tailwind tokens
  assets/
```

No `components/`, `routes/`, `queries/`, or `api/` subdirectories exist yet.

---

## App.tsx responsibilities (current)

- All React Router route definitions
- `Layout` (header/nav/footer)
- `AppShell` (bootstrap loading, QueryClientProvider equivalent)
- `HomePage` (redirects to /wizard — no real home page)
- `WizardPage` — entire wizard (2 steps of state, 4 step views, all handlers)
- `JobPage` — job detail view
- `JobSummaryPanel` — used inside wizard generate step
- All shared UI components (StatusPill, DisclosurePanel, WarningsPanel, etc.)
- All utility functions (statusLabel, formatDate, joinClasses, etc.)
- All wizard state logic (canonicalWizardStep, wizardStepUnlocked, etc.)
- localStorage session tracking
- Direct `api.*` calls inside component handlers

---

## Current routes

| Route | Component | Notes |
|---|---|---|
| `/` | `HomePage` | Immediately redirects to `/wizard` |
| `/wizard` | `WizardPage` | Start new session |
| `/wizard/:sessionId` | `WizardPage` | Redirect to canonical step |
| `/wizard/:sessionId/:step` | `WizardPage` | Step: describe/spec/ir/generate |
| `/jobs/:jobId` | `JobPage` | Job detail |
| `*` | `<Navigate to="/">` | Catch-all redirect |

Missing routes (to be added): `/jobs`, `/setup`, `/symbols`, `/generate-json`

---

## Frontend API methods (api.ts)

| Method | Endpoint | Used in UI |
|---|---|---|
| `getBootstrap()` | `GET /api/ui/bootstrap` | AppShell |
| `getDoctor()` | `GET /api/doctor` | NOT exposed in UI |
| `getJobs()` | `GET /api/jobs` | NOT exposed in UI |
| `getJob(id)` | `GET /api/jobs/:id` | JobPage, JobSummaryPanel |
| `searchSymbols(q)` | `GET /api/symbols/search` | NOT exposed in UI |
| `validateNetlist(…)` | `POST /api/netlists/validate` | NOT exposed in UI |
| `createJobFromNetlist(…)` | `POST /api/jobs/from-netlist` | NOT exposed in UI |
| `createWizardSession(…)` | `POST /api/wizard/sessions` | WizardPage start step |
| `getWizardSession(id)` | `GET /api/wizard/sessions/:id` | WizardPage |
| `addWizardMessage(…)` | `POST /api/wizard/sessions/:id/messages` | WizardPage describe step |
| `approveWizardSpec(id)` | `POST /api/wizard/sessions/:id/approve-spec` | WizardPage spec step |
| `clearWizardIr(id)` | `POST /api/wizard/sessions/:id/clear-ir` | WizardPage IR step |
| `generateWizardIr(id)` | `POST /api/wizard/sessions/:id/generate-ir` | WizardPage IR step |
| `generateWizardProject(id)` | `POST /api/wizard/sessions/:id/generate-project` | WizardPage generate step |

`getDoctor`, `getJobs`, `searchSymbols`, `validateNetlist`, `createJobFromNetlist` exist in the
API client but have no UI entry point.

---

## Backend endpoints (full list)

From `GET /openapi.json`:

```
GET  /api/doctor
GET  /api/symbols/search
POST /api/netlists/validate
POST /api/jobs/from-netlist
GET  /api/jobs
GET  /api/jobs/:job_id
GET  /api/jobs/:job_id/artifacts
GET  /api/jobs/:job_id/artifacts/:artifact_name
GET  /api/ui/bootstrap
POST /api/wizard/sessions
GET  /api/wizard/sessions/:session_id
POST /api/wizard/sessions/:session_id/messages
POST /api/wizard/sessions/:session_id/approve-spec
POST /api/wizard/sessions/:session_id/generate-ir
POST /api/wizard/sessions/:session_id/generate-project
POST /api/wizard/sessions/:session_id/clear-ir
GET  /
GET  /wizard
GET  /wizard/:session_id
GET  /wizard/:session_id/:step
GET  /jobs/:job_id
```

---

## Wizard session states and transitions

```
drafting_spec
awaiting_user_clarification
spec_ready_for_review
spec_approved
drafting_ir
ir_needs_repair
ir_ready_for_generation
generation_started
completed
failed
```

Step routing derived from status:
- `describe`: drafting_spec, awaiting_user_clarification, failed (no spec/IR)
- `spec`: spec_ready_for_review, failed (has spec, no IR)
- `ir`: spec_approved, drafting_ir, ir_needs_repair
- `generate`: ir_ready_for_generation, generation_started, completed, failed (has IR)

---

## Job status model

```
queued → running → succeeded | failed | cancelled
```

Artifact files (when present):
- `project.zip` — KiCad project archive
- `warnings.json` — generation advisory warnings
- `debug.json` — schematic diagnostics
- `schematic_preview.png` — schematic PNG preview (required for success)

---

## Preview generation behavior (current)

Preview generation is **required** for job success. If `kicad-cli sch export svg`
or `rsvg-convert` fails, the job fails with a `RuntimeError`. The `project.zip`
may be created internally but the job is not marked successful without a preview.

This is intentional per explicit user instruction.

---

## State management (current)

- No TanStack Query.
- All server state managed with `useState` + manual `useEffect` + `api.*` calls.
- Bootstrap fetched in `AppShell` via `useEffect`, passed down as props.
- Wizard session fetched in `WizardPage` via `useEffect`.
- Job fetched in `JobPage` via `useEffect`.
- No job polling exists (jobs in this app are synchronous — they complete within the
  HTTP response).
- Session ID persisted to `localStorage` for "last session" nav link.

---

## Frontend IR auto-repair (current — to be removed per replies3.md)

After `generateWizardIr()` returns `ir_needs_repair`, `WizardPage` currently
calls `generateWizardIr()` a second time automatically with a "attempting
automatic repair" busy message. This will be removed in Phase 5: the backend
already runs `ir_max_repair_rounds` internally, and the frontend should call
once per user action.

---

## Styling / theme constants (current)

All Tailwind utility constants defined at module level in `App.tsx` (~90 lines of
`const pageStackClass = '...'` style). No separate design-token file exists yet.

Palette: warm terracotta/amber/cream. Dark code blocks for JSON. This palette is
kept in Phase 17 (simplification pass, not rebrand).

---

## Build / test baseline

| Check | Status |
|---|---|
| `cd frontend && npm ci` | ✓ passes |
| `cd frontend && npm run build` | ✓ passes |
| `cd frontend && npm run lint` | not checked (no ESLint errors in build) |
| `uv run python -m pytest tests/unit/` | ✓ 2525 passed |
| `uv run ruff check .` | ✓ all checks passed |
| `uv run mypy src/kicad_pcb src/kicad_pcb_web` | ✓ no issues |

Note: `uv run pytest` currently resolves to the mambaforge pytest binary which uses
Python 3.10 and fails on `StrEnum`. Use `uv run python -m pytest` instead until
the PATH issue is resolved.

---

## Known issues before refactor

1. **No real home page** — `/` redirects immediately to `/wizard`. Users with no
   LLM provider hit a dead end.
2. **No `/jobs` list page** — `GET /api/jobs` is implemented but unexposed.
3. **No `/setup` page** — `GET /api/doctor` is implemented but unexposed.
4. **No `/symbols` page** — `GET /api/symbols/search` is implemented but unexposed.
5. **No `/generate-json` page** — `POST /api/jobs/from-netlist` and
   `POST /api/netlists/validate` are implemented but unexposed.
6. **Frontend IR auto-retry** — to be removed (see replies3.md Q2).
7. **`App.tsx` is 2,172 lines** — entire app in one file.
8. **`uv run pytest` uses wrong Python** — use `uv run python -m pytest` instead.
