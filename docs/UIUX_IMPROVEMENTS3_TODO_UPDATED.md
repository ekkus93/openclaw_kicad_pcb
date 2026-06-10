# UI/UX Improvements — Batch 3 TODO

Derived from the Batch 2 code review. Batch 2 is accepted overall, but this batch fixes the remaining cleanup issues: explicit wizard route-step normalization, `SymbolsPage` accessibility semantics, Generate-step project settings clarity, validation-doc reproducibility, KiCad/web-test hermeticity, and removal of legacy OpenClaw UI copy.

---

## 1. Make wizard route-step normalization explicit (P0 — routing correctness)

`WizardPage.tsx` currently relies on a TypeScript assertion for the route step. Replace that with real runtime normalization.

### 1.1 Add normalization helper

- [x] Open `frontend/src/routes/wizard/wizardStepLogic.ts`
- [x] Add a pure helper named `normalizeWizardStep`
- [x] Accept `raw: string | undefined`
- [x] Return a `WizardStep` only for:
  - [x] `describe`
  - [x] `spec`
  - [x] `ir`
  - [x] `generate`
- [x] Return `undefined` for all other values
- [x] Keep the helper free of React hooks and side effects

Suggested implementation:

```ts
export function normalizeWizardStep(raw: string | undefined): WizardStep | undefined {
  if (raw === 'describe' || raw === 'spec' || raw === 'ir' || raw === 'generate') {
    return raw
  }
  return undefined
}
```

### 1.2 Use the helper in `WizardPage.tsx`

- [x] Open `frontend/src/routes/WizardPage.tsx`
- [x] Replace this pattern or equivalent:

```tsx
const routeStep = step as WizardStep | undefined
```

- [x] Use:

```tsx
const routeStep = normalizeWizardStep(step)
```

- [x] Import `normalizeWizardStep` from `./wizard/wizardStepLogic`
- [x] Verify invalid route steps redirect through existing canonical-step logic
- [x] Do not change valid wizard route behavior

### 1.3 Add helper tests

- [x] Add tests in the existing wizard step logic test file
- [x] Assert `normalizeWizardStep(undefined)` returns `undefined`
- [x] Assert all valid steps return themselves
- [x] Assert unknown values return `undefined`
- [x] Assert mixed-case values such as `SPEC` return `undefined` unless the app intentionally supports case-insensitive routes

### 1.4 Add invalid-route behavior test

- [x] Add or update a route-level wizard test (new file: WizardInvalidRoute.test.tsx)
- [x] Render `/wizard/abc123/not-a-step`
- [x] Mock a representative session
- [x] Verify the app redirects/navigates to the canonical wizard step
- [x] Verify no broken/blank page is rendered

---

## 2. Add missing accessibility semantics to `SymbolsPage` (P1 — accessibility)

Batch 2 covered major async/error UI, but `SymbolsPage` still needs semantic roles.

### 2.1 Add loading/search status semantics

- [x] Open `frontend/src/routes/SymbolsPage.tsx`
- [x] Find the searching/loading banner or inline loading message
- [x] Add `role="status"`
- [x] Add `aria-live="polite"`
- [x] Keep visible loading text intact
- [x] Do not rely on spinner-only feedback

### 2.2 Add error alert semantics

- [x] Find the Symbols search/load error banner
- [x] Add `role="alert"`
- [x] Avoid nested `role="alert"` elements for the same error
- [x] Preserve the current error text and styling

### 2.3 Add/update tests

- [x] Update `SymbolsPage` tests
- [x] Verify searching/loading text is exposed through `getByRole('status')`
- [x] Verify error text is exposed through `getByRole('alert')`
- [x] Ensure existing debounce tests still pass

---

## 3. Add read-only project settings summary to Generate step (P1 — UX clarity)

The Generate step must show the project settings that will be used for generation. These settings are read-only on the Generate step.

### 3.1 Add read-only summary UI

- [x] Open `frontend/src/routes/wizard/WizardGenerateStep.tsx`
- [x] Add a small “Project settings” section near the generation controls
- [x] Display the current project name
- [x] Display the current symbols directory
- [x] Show fallback text `Not set` for missing/empty values
- [x] Use semantic heading/label text
- [x] Keep the fields read-only
- [x] Do not duplicate editable form state in the Generate step
- [x] Do not add a new backend update call
- [x] Added “Edit project details” link back to Describe step (disabled/hidden while busy)

### 3.2 Preserve existing generation behavior

- [x] Existing Generate Project behavior still works
- [x] Existing Generate Again confirmation still works
- [x] Existing Retry Generation behavior for failed jobs still works
- [x] Existing latest-job summary display still works
- [x] Existing query-cache behavior from project generation mutation is unchanged

### 3.3 Add/update tests

- [x] Verify project name appears on the Generate step
- [x] Verify symbols directory appears on the Generate step
- [x] Verify missing values show fallback text
- [x] Verify edit link points to the Describe step
- [x] Verify edit link is absent (replaced by disabled text) when busyMessage is set
- [x] Verify existing regenerate confirmation tests still pass
- [x] Verify failed-job retry tests still pass

---

## 4. Fix validation documentation for clean environments (P1 — reproducibility)

The current documented mypy command may miss web dependencies. Make the validation commands reproducible from a clean checkout.

### 4.1 Inspect project dependency declaration

- [ ] Open `pyproject.toml`
- [ ] Determine whether the project uses extras, dependency groups, or both
- [ ] Identify the correct way to install/run dev dependencies
- [ ] Identify the correct way to include web dependencies such as FastAPI

### 4.2 Update validation docs

- [ ] Search docs for stale commands:
  - [ ] `uv run mypy src/kicad_pcb src/kicad_pcb_web`
  - [ ] `uv run pytest tests/unit/`
  - [ ] other commands missing required extras/groups
- [ ] Update docs to use the repo-supported full commands

Preferred if supported:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Do not document unsupported `uv` flags
- [ ] Keep frontend validation commands unchanged unless needed

### 4.3 Update completion checklist docs

- [ ] If `docs/UIUX_IMPROVEMENTS2_TODO(1).md` or copied Batch 2 docs exist in the repo, update their validation command examples or add an erratum
- [ ] Add Batch 3 validation commands to the new Batch 3 TODO/docs if those docs are committed
- [ ] Make clear when KiCad CLI is required versus optional

---

## 5. Mark KiCad integration tests explicitly (P0 — test reliability)

Use Option C. Tests that require real `kicad-cli` or real KiCad system symbol libraries must be explicitly marked and skipped when KiCad is unavailable. Do not implement preview mocking or make preview generation non-fatal in this batch.

### 5.1 Identify affected tests

- [ ] Run or inspect the web/backend test subset
- [ ] Identify tests that fail because `kicad-cli` is missing
- [ ] Identify tests that fail because KiCad system symbol libraries are missing
- [ ] Identify tests that are truly KiCad integration tests
- [ ] Identify tests that should remain ordinary non-KiCad tests

### 5.2 Add pytest marker

- [ ] Add `@pytest.mark.kicad` to tests that require real KiCad CLI behavior
- [ ] Add `@pytest.mark.kicad` to tests that require host KiCad symbol libraries
- [ ] Do not mark tests as KiCad merely to hide unrelated failures
- [ ] Keep ordinary web/job tests unmarked when they do not require KiCad

### 5.3 Register marker

- [ ] Open `pyproject.toml`
- [ ] Register the `kicad` marker in pytest configuration
- [ ] Add a clear marker description, e.g. `requires kicad-cli and system KiCad libraries`

### 5.4 Add skip helper/fixture

- [ ] Add a pytest fixture or helper that checks for `kicad-cli` with `shutil.which("kicad-cli")`
- [ ] Automatically skip KiCad-marked tests when `kicad-cli` is unavailable
- [ ] Make the skip reason clear
- [ ] Ensure non-KiCad tests do not depend on this skip

### 5.5 Document KiCad integration test command

- [ ] Update validation docs to explain that KiCad integration tests require KiCad CLI
- [ ] Document this command:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

- [ ] Document how to run the normal non-KiCad validation suite

### 5.6 Add/update tests for marker behavior

- [ ] Verify KiCad-marked tests skip cleanly when `kicad-cli` is unavailable
- [ ] Verify non-KiCad unit/web tests still run without KiCad
- [ ] Verify no real project-generation regression is hidden by weakened assertions

---

## 6. Remove legacy OpenClaw UI copy (P2 — product polish)

The app still shows `OpenClaw_Managed.kicad_sch` in normal user-facing UI. Remove this legacy OpenClaw UI copy from normal UI and replace it with neutral product copy. It may remain only where a literal backend artifact filename or developer diagnostic value is required.

### 6.1 Search frontend UI copy

- [ ] Search `frontend/src` for `OpenClaw_Managed.kicad_sch`
- [ ] Search `frontend/src` for other normal user-facing `OpenClaw` strings and remove them from normal UI
- [ ] Classify each occurrence:
  - [ ] normal user-facing UI
  - [ ] literal backend artifact filename
  - [ ] developer diagnostics/debug output
  - [ ] tests/fixtures

### 6.2 Update `JobPage.tsx`

- [ ] Replace normal user-facing `OpenClaw_Managed.kicad_sch` text with neutral copy
- [ ] Suggested copy: `Generated managed schematic`
- [ ] Preserve real artifact filenames in download links or diagnostics if needed

### 6.3 Update `WizardGenerateStep.tsx`

- [ ] Replace normal user-facing `OpenClaw_Managed.kicad_sch` text with neutral copy
- [ ] Suggested copy: `Generated managed schematic`
- [ ] Preserve backend artifact compatibility

### 6.4 Update tests

- [ ] Update test assertions that expected `OpenClaw_Managed.kicad_sch` in normal UI
- [ ] Add assertions for the new neutral copy
- [ ] Do not change backend filename tests unless backend filenames are intentionally changed

---

## 7. Preserve type/lint discipline (P0 — code quality guardrail)

### 7.1 Avoid broad `any`

- [ ] Do not introduce broad `any`
- [ ] Prefer generated/API types and local specific types
- [ ] If an unavoidable cast is required, keep it narrow and comment why

### 7.2 Avoid suppressions

- [ ] Do not add `// @ts-ignore`
- [ ] Do not add `// @ts-expect-error` unless the test specifically requires it and the reason is documented
- [ ] Do not add unexplained `eslint-disable`
- [ ] Do not weaken `tsconfig` strictness
- [ ] Do not weaken Python lint/mypy settings

### 7.3 Preserve existing behavior

- [ ] Valid wizard routes still work
- [ ] Wizard canonical redirects still work
- [ ] Generate Again confirmation still works
- [ ] Retry Generation for failed jobs still works
- [ ] LLM-disabled guards still work
- [ ] Job polling indicator still works
- [ ] Symbols debounce still works

---

## 8. Final validation checklist (P0 — must pass)

Run all applicable validation before marking this batch complete.

### 8.1 Frontend validation

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Build passes
- [ ] Lint passes
- [ ] Tests pass

### 8.2 Python/backend validation

Use the dependency flags supported by the repo. Preferred if supported:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Ruff passes
- [ ] Ruff format check passes
- [ ] Mypy passes
- [ ] Non-KiCad unit/web tests pass

### 8.3 KiCad integration validation

If KiCad CLI is installed:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

- [ ] KiCad-marked tests pass when KiCad CLI is available

If KiCad CLI is not installed:

- [ ] KiCad-marked tests skip cleanly
- [ ] Non-KiCad tests still pass

### 8.4 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No `__pycache__` directories remain
- [ ] No `.pyc` or `.pyo` files remain
- [ ] No unintended files changed
- [ ] `.gitignore` still covers generated artifacts

### 8.5 Final manual smoke checks

- [ ] `/wizard` still renders the start page
- [ ] `/wizard/:sessionId` redirects to the canonical step
- [ ] `/wizard/:sessionId/not-a-step` redirects to the canonical step
- [ ] Symbols search still debounces
- [ ] Job page still shows polling status for queued/running jobs
- [ ] Normal UI no longer shows `OpenClaw_Managed.kicad_sch`

---

## 9. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Tests added/updated
- [ ] Exact validation commands run
- [ ] Whether KiCad CLI was installed
- [ ] Whether KiCad-marked tests passed or skipped
- [ ] Whether preview-generation failure is now mocked, skipped, or non-fatal
- [ ] Product-copy decision for `OpenClaw_Managed.kicad_sch`
- [ ] Any remaining manual QA steps

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 5, 7, 8 |
| P1 — Accessibility/reproducibility/UX clarity | 2, 3, 4 |
| P2 — Product polish | 6, 9 |
