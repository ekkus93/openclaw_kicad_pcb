# UI/UX Improvements — Batch 4 TODO

Derived from the Batch 3 review. Batch 3 is mostly complete, but there are several cleanup issues before the batch should be considered fully closed: one non-hermetic unit test, one stale KiCad marker command in docs, a duplicate controller return property, a weak invalid-route test, and unchecked type/lint discipline checklist items.

---

## 1. Fix non-hermetic unit test for KiCad system symbol fallback (P0 — test reliability)

`tests/unit/test_lib_symbol.py::test_read_lib_symbol_def_flat_falls_through_to_system_library` can fail on machines without KiCad system symbol libraries.

### 1.1 Inspect the failing test

- [ ] Open `tests/unit/test_lib_symbol.py`
- [ ] Locate `test_read_lib_symbol_def_flat_falls_through_to_system_library`
- [ ] Confirm whether it is intended to test:
  - [ ] generic fallback behavior that can be tested with a local fixture, or
  - [ ] real host KiCad system-library fallback behavior

### 1.2 Preferred fix: make the test hermetic

Use this approach unless the test specifically must validate the real host KiCad installation.

- [ ] Create a temporary symbol library fixture using `tmp_path`
- [ ] Write a minimal KiCad symbol library containing the symbol needed by the test, such as `power:+5V`
- [ ] Pass the temporary symbols directory explicitly to the function under test
- [ ] Keep the assertion strong: the symbol definition must be found
- [ ] Do not rely on `/usr/share/kicad/symbols`
- [ ] Do not require `kicad-cli`
- [ ] Do not skip the test

### 1.3 Acceptable fallback: mark as KiCad-dependent

Only use this if the test’s purpose is explicitly to verify real system-library fallback.

- [ ] Import or use the existing `requires_kicad()` helper from `tests/conftest.py`
- [ ] Decorate the test with `@requires_kicad()`
- [ ] Do not add a new marker
- [ ] Do not use `@pytest.mark.kicad`
- [ ] Ensure the skip reason is clear when KiCad is unavailable

### 1.4 Validate unit hermeticity

Run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

- [ ] Unit tests pass without requiring host KiCad system symbol libraries
- [ ] No unrelated tests are skipped just to hide failures

---

## 2. Standardize KiCad marker docs on `requires_kicad` (P1 — documentation correctness)

The project uses `requires_kicad`, not `kicad`.

### 2.1 Search for stale marker commands

- [ ] Search docs for `-m kicad`
- [ ] Search docs for `pytest -m kicad`
- [ ] Search current Batch 3 docs, especially `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md`
- [ ] Search `CLAUDE.md`

### 2.2 Replace stale commands

- [ ] Replace active/current `pytest -m kicad` examples with:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

- [ ] If documenting normal non-KiCad tests, use either:

```bash
uv run --extra dev --extra web python -m pytest -m "not requires_kicad"
```

or a narrower hermetic path such as:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Do not create or document a new `kicad` marker
- [ ] Do not rewrite historical completion notes unless they are used as active instructions

### 2.3 Confirm docs are consistent

- [ ] No active/current docs instruct developers to run `pytest -m kicad`
- [ ] Docs identify `requires_kicad` as the canonical marker
- [ ] Docs explain when KiCad CLI / rsvg tools are required or skipped

---

## 3. Remove duplicate `handleClearIr` return property (P2 — code hygiene)

`useWizardController.ts` currently returns `handleClearIr` twice.

### 3.1 Clean controller return object

- [ ] Open `frontend/src/routes/wizard/useWizardController.ts`
- [ ] Find duplicate `handleClearIr` in the returned object
- [ ] Remove one duplicate
- [ ] Do not change the name or behavior of the remaining property
- [ ] Do not otherwise restructure the controller

### 3.2 Validate frontend

- [ ] `cd frontend && npm run build` passes
- [ ] `cd frontend && npm run lint` passes
- [ ] Existing wizard tests pass

---

## 4. Strengthen invalid wizard route test (P1 — regression coverage)

`WizardInvalidRoute.test.tsx` should prove that invalid route steps are normalized before reaching the controller.

### 4.1 Inspect current test

- [ ] Open `frontend/src/test/WizardInvalidRoute.test.tsx`
- [ ] Confirm how `useWizardController` is mocked
- [ ] Identify whether the test would still pass if raw `not-a-step` were passed to the controller

### 4.2 Add assertion for normalized controller argument

- [ ] Spy on the mocked `useWizardController`
- [ ] Render `/wizard/abc123/not-a-step`
- [ ] Assert the controller receives `undefined` for the route step argument
- [ ] Continue asserting canonical content renders
- [ ] Continue asserting no blank/broken page is rendered

Expected concept:

```ts
expect(useWizardControllerMock).toHaveBeenCalledWith(
  expect.objectContaining({
    routeStep: undefined,
  }),
)
```

Adjust the exact assertion to match the real controller argument shape.

### 4.3 Add/keep valid route control test

- [ ] Render a valid route such as `/wizard/abc123/spec`
- [ ] Assert the controller receives `spec`
- [ ] Verify valid route behavior remains unchanged

### 4.4 Validate test behavior

- [ ] Temporarily confirm the test would fail if normalization were removed or bypassed
- [ ] Restore correct implementation
- [ ] Frontend tests pass

---

## 5. Complete type/lint discipline checklist (P1 — checklist accuracy)

Batch 3 Task 7 checkboxes remained unchecked. Re-verify and mark them complete only if true.

### 5.1 Search for TypeScript suppressions

Run an equivalent search:

```bash
grep -R "eslint-disable\|@ts-ignore\|@ts-expect-error" frontend/src tests src -n
```

- [ ] No new `@ts-ignore`
- [ ] No new `@ts-expect-error`
- [ ] No unexplained `eslint-disable`

### 5.2 Search for broad `any`

Run an equivalent search:

```bash
grep -R "\bany\b" frontend/src -n
```

- [ ] No new broad `any`
- [ ] Any existing `any` usage is either pre-existing or narrowly justified
- [ ] Prefer generated/API types and local specific types

### 5.3 Check config strictness

- [ ] Confirm `frontend/tsconfig*.json` strictness was not weakened
- [ ] Confirm Python lint/mypy settings were not weakened
- [ ] Confirm no lint rules were relaxed to pass this batch

### 5.4 Update Batch 3 TODO checklist

- [ ] Mark Task 7.1 complete if broad `any` check passes
- [ ] Mark Task 7.2 complete if suppression/config checks pass
- [ ] Do not mark unchecked items complete without verification

---

## 6. Run final validation (P0 — must pass)

### 6.1 Frontend validation

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Build passes
- [ ] Lint passes
- [ ] Tests pass

### 6.2 Python/backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Ruff passes
- [ ] Ruff format check passes
- [ ] Mypy passes
- [ ] Unit tests pass

### 6.3 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Web tests pass
- [ ] KiCad/rsvg-dependent tests skip cleanly when required tools are unavailable
- [ ] No unmarked non-hermetic tests fail

### 6.4 KiCad-dependent tests

If KiCad CLI and related tools are available:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [ ] KiCad-dependent tests pass when tools are available

If tools are unavailable:

- [ ] KiCad-dependent tests skip cleanly
- [ ] Skip reasons are clear

### 6.5 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] No unintended files changed
- [ ] `git status --short` contains only intentional changes

---

## 7. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Whether the unit test was made hermetic or marked `requires_kicad`
- [ ] Why that approach was chosen
- [ ] Docs updated for `requires_kicad`
- [ ] Duplicate `handleClearIr` return property removed
- [ ] Invalid-route test strengthened and how
- [ ] Type/lint search commands run
- [ ] Exact validation commands and results
- [ ] Whether KiCad CLI was available
- [ ] Whether `rsvg-convert` was available
- [ ] Tests skipped and skip reasons

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 6 |
| P1 — Test/docs/checklist correctness | 2, 4, 5 |
| P2 — Small code hygiene | 3, 7 |
