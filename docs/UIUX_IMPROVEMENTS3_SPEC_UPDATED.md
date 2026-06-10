# UI/UX Improvements — Batch 3 Spec

## Purpose

This batch is a focused follow-up to the Batch 2 review. Batch 2 successfully decomposed the wizard, gated React Query Devtools, added frontend regression tests, improved accessibility semantics, cleaned artifacts, and documented the polling model. The remaining issues are smaller but important for routing correctness, accessibility consistency, validation reliability, test hermeticity, and user-facing product polish.

This batch should be implemented as a small, low-risk patch. Do not perform another broad refactor. Preserve all user-visible behavior except where this spec explicitly calls for improved text, accessibility semantics, or invalid-route handling.

## Primary Goals

1. Make wizard route-step normalization explicit and tested.
2. Add missing accessibility semantics to `SymbolsPage` loading/error UI.
3. Clarify the Generate-step project settings behavior.
4. Make validation documentation reproducible in clean environments.
5. Make web/backend tests hermetic around KiCad CLI and schematic preview generation.
6. Remove or soften legacy `OpenClaw_Managed.kicad_sch` wording from normal user-facing UI.

## Non-Goals

- Do not rewrite the Batch 2 wizard decomposition.
- Do not introduce a new worker/job queue system.
- Do not change the frontend routing scheme.
- Do not rename generated artifacts unless existing backend compatibility is preserved.
- Do not introduce broad `any`, `@ts-ignore`, disabled lint rules, or test-only production branches.
- Do not remove existing tests unless they are replaced with equal or better coverage.

---

## 1. Explicit wizard route-step normalization

### Problem

`frontend/src/routes/WizardPage.tsx` currently treats the route step parameter as:

```tsx
const routeStep = step as WizardStep | undefined
```

This is only a TypeScript assertion. It does not validate the actual runtime value. Invalid routes such as `/wizard/:sessionId/not-a-step` may appear to work only because downstream comparisons accidentally redirect, not because route normalization is deliberate.

### Required behavior

Valid wizard step route values are:

```text
describe
spec
ir
generate
```

Anything else must normalize to `undefined`.

The page should then use the existing canonical-step redirect logic to send the user to the correct canonical step for the current session state.

### Implementation requirements

Add a pure helper in `frontend/src/routes/wizard/wizardStepLogic.ts`:

```ts
export function normalizeWizardStep(raw: string | undefined): WizardStep | undefined {
  if (raw === 'describe' || raw === 'spec' || raw === 'ir' || raw === 'generate') {
    return raw
  }
  return undefined
}
```

Using a `Set<WizardStep>` is also acceptable if it remains strongly typed and readable.

Update `WizardPage.tsx` to use the helper instead of a type assertion.

Add tests for:

- `normalizeWizardStep(undefined)` returns `undefined`.
- Each valid step returns itself.
- Unknown strings return `undefined`.
- A route such as `/wizard/abc123/not-a-step` redirects to the canonical step instead of rendering a broken or empty page.

### Acceptance criteria

- No unsafe route-step cast remains in `WizardPage.tsx`.
- Invalid wizard step routes are handled intentionally.
- Pure helper tests pass.
- Existing wizard route behavior remains unchanged for valid paths.

---

## 2. Add missing accessibility semantics to `SymbolsPage`

### Problem

Batch 2 added roles/live regions to the major async/error flows, but `SymbolsPage` still has async loading and error banners without semantic roles. This makes state changes less reliable for screen-reader users.

### Required behavior

The search/loading state should be announced politely. The search/error state should be announced as an alert.

### Implementation requirements

In `frontend/src/routes/SymbolsPage.tsx`:

- Add `role="status"` and `aria-live="polite"` to non-blocking loading/searching status UI.
- Add `role="alert"` to the error banner.
- Preserve existing visual styling and text.
- Do not add nested alerts for the same error.
- Do not make static labels live regions.

### Tests

Add or update a SymbolsPage test that verifies:

- The searching/loading message has `role="status"`.
- The error message has `role="alert"`.
- Existing debounce tests still pass.

### Acceptance criteria

- `SymbolsPage` loading and error states are accessible.
- No visual regression.
- Existing and new frontend tests pass.

---

## 3. Add read-only project settings summary on Generate step

### Decision

Use the read-only summary approach. The Generate step must show the project settings that will be used for generation, but it must not introduce editable fields or new backend update calls.

### Problem

The Batch 2 TODO placed “project name and symbols directory editing” under the Generate-step extraction. The implementation preserved project metadata editing earlier in the wizard, but the Generate step should still make the final generation inputs visible so the user can confirm what will be generated.

### Required behavior

The Generate step must show a small read-only project settings summary:

- Project name
- Symbols directory

If the app supports returning to Describe safely, include a clear link/button such as “Edit project details” that navigates back to the Describe step. Do not add editable fields on Generate unless the backend already supports updating those fields at that stage.

### Implementation requirements

In `frontend/src/routes/wizard/WizardGenerateStep.tsx`:

- Add a read-only settings block near the generation controls.
- Display the project name.
- Display the symbols directory.
- Display fallback text for missing/empty values, such as “Not set”.
- Use accessible labels/headings.
- Avoid duplicating editable state.
- Do not add a new backend update call.
- Preserve existing Generate Again / Retry Generation behavior.

### Tests

Add or update Generate-step tests for:

- Project name is displayed when present.
- Symbols directory is displayed when present.
- Missing values render sensible fallback text.
- Optional edit navigation, if implemented, links to the Describe step.
- Existing regenerate confirmation and failed-job retry behavior still works.

### Acceptance criteria

- The Generate step clearly shows the generation settings in read-only form.
- No unsupported editing behavior is added.
- Existing Generate-step regeneration/retry behavior remains unchanged.

---

## 4. Fix validation documentation for clean environments

### Problem

The current validation checklist says:

```bash
uv run mypy src/kicad_pcb src/kicad_pcb_web
```

In a clean environment this can fail if FastAPI/web dependencies are not installed. The reproducible command should include the relevant extras.

### Required behavior

Validation documentation should use commands that work from a clean checkout with the project’s declared dependency groups/extras.

### Implementation requirements

Update relevant docs, likely including:

- `docs/UIUX_IMPROVEMENTS2_TODO(1).md` if present in repo as a copied doc
- current Batch 3 docs if added
- README or developer validation docs if they contain the stale command

Preferred commands:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

If the project uses dependency groups instead of extras, use the repo’s actual supported syntax. Do not invent dependency flags that are not declared in `pyproject.toml`.

### Tests

No code test is required, but the final validation commands must be run and recorded.

### Acceptance criteria

- Docs no longer recommend a mypy command that misses web dependencies.
- The final completion notes show the exact validation commands used.
- Frontend validation commands remain unchanged.

---

## 5. Mark KiCad integration tests explicitly

### Decision

Use Option C. Do not implement the preview-mocking approach in this batch, and do not change product behavior by making preview generation non-fatal unless a separate product decision is made later.

### Problem

Some web/backend tests can fail on machines that do not have `kicad-cli` or system KiCad symbol libraries installed. Tests that truly validate KiCad CLI integration should be separated from ordinary unit/web tests so developers can run the normal validation suite without a full KiCad installation.

### Required behavior

Tests that require real KiCad CLI behavior must be explicitly marked as KiCad integration tests.

Tests that do not require KiCad CLI must remain runnable without KiCad CLI installed.

### Implementation requirements

- Add a clear pytest marker, e.g. `@pytest.mark.kicad`, to tests that require real `kicad-cli` or real KiCad system symbol libraries.
- Register the marker in `pyproject.toml`.
- Add a fixture/helper that checks for `kicad-cli` using `shutil.which("kicad-cli")`.
- Skip KiCad-marked tests cleanly when `kicad-cli` is unavailable.
- Keep non-KiCad tests runnable without KiCad.
- Do not make tests pass by weakening assertions.
- Do not suppress real project-generation failures in non-skipped tests.
- Do not rename or change backend artifacts as part of this task.

### Tests

Add or update tests to cover:

- KiCad-marked tests are skipped cleanly when `kicad-cli` is unavailable.
- Non-KiCad unit/web tests can run without `kicad-cli`.
- KiCad-marked tests still run normally when `kicad-cli` is available.

### Acceptance criteria

- `uv run --extra dev --extra web python -m pytest tests/unit/` passes without KiCad CLI, excluding explicitly marked KiCad integration tests when necessary.
- Tests that require KiCad are clearly marked and skipped when KiCad is unavailable.
- The docs explain how to run KiCad integration tests.
- No real project-generation regression is hidden by weakened assertions.

---

## 6. Remove legacy OpenClaw UI copy

### Problem

The frontend still displays `OpenClaw_Managed.kicad_sch` in normal user-facing UI, including `JobPage.tsx` and `WizardGenerateStep.tsx`. That name may be historically accurate internally, but it is poor product copy for a generic KiCad PCB web app, especially if OpenClaw is now legacy/archived branding.

### Required behavior

Normal user-facing UI must not show legacy OpenClaw copy.

Acceptable user-facing labels:

```text
Generated managed schematic
managed schematic sheet
KiCad managed schematic
```

The backend artifact filename may remain `OpenClaw_Managed.kicad_sch` only if required for backend compatibility or existing generated project structure, but normal UI should not display it.

### Implementation requirements

- Update `frontend/src/routes/JobPage.tsx`.
- Update `frontend/src/routes/wizard/WizardGenerateStep.tsx`.
- Search the frontend for other normal UI appearances of `OpenClaw_Managed.kicad_sch` and remove them from normal UI.
- Do not rename backend artifacts unless explicitly safe.
- If the filename is shown in a developer diagnostics panel, it may remain there.

### Tests

Update affected tests/snapshots/assertions to expect the new neutral copy.

### Acceptance criteria

- Normal user-facing UI does not show `OpenClaw_Managed.kicad_sch`.
- Backend artifact compatibility is preserved.
- Frontend tests pass.

---

## 7. Final validation

Run the full validation set after implementation.

### Frontend

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

### Python/backend

Use the repo-supported dependency flags. Preferred if supported:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

If KiCad-specific tests exist, also run:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

or document that they were skipped because KiCad CLI was unavailable.

### Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- Frontend build, lint, and tests pass.
- Python lint, format check, mypy, and non-KiCad tests pass.
- KiCad-dependent tests are either passing or clearly skipped with a marker.
- No generated cache artifacts are present.
- No broad `any`, `@ts-ignore`, unexplained lint suppressions, or weakened test assertions are introduced.

---

## Expected completion notes

Claude Code should include final notes with:

- files changed,
- tests added/updated,
- exact validation commands run,
- whether KiCad CLI was available,
- whether any KiCad-marked tests were skipped,
- any deliberate product-copy decisions,
- any remaining manual QA steps.
