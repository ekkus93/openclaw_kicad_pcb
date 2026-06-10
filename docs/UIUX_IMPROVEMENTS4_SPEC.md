# UI/UX Improvements — Batch 4 Spec

## Purpose

Batch 3 is mostly implemented and the frontend/UI changes are in good shape. The remaining work is a small validation and hygiene pass. This batch should close the gap between the Batch 3 checklist and the actual codebase state by fixing one non-hermetic unit test, cleaning up KiCad marker documentation, removing a duplicate controller return property, strengthening one frontend route test, and completing the type/lint checklist verification.

This should be a narrowly scoped patch. Do not perform another broad refactor.

## Background

The Batch 3 review found:

1. Frontend build/lint/tests pass.
2. Python lint/format/mypy pass.
3. Web tests skip KiCad/rsvg-dependent generation tests correctly.
4. The ordinary unit suite can still fail on machines without KiCad system symbol libraries.
5. Some documentation still uses the wrong marker name (`kicad`) instead of the project’s existing `requires_kicad`.
6. `useWizardController.ts` returns `handleClearIr` twice.
7. `WizardInvalidRoute.test.tsx` verifies rendered content but does not prove the route step is normalized before reaching the controller.
8. Batch 3 Task 7 type/lint checkboxes remain unchecked even though the code appears clean.

## Goals

1. Make `tests/unit/` hermetic without requiring KiCad system symbol libraries.
2. Standardize all KiCad test-marker docs on `requires_kicad`.
3. Remove duplicate controller return property.
4. Strengthen invalid wizard route test to prove invalid route steps normalize to `undefined`.
5. Re-run and document type/lint discipline checks.
6. Update the Batch 3 TODO checklist to accurately reflect completion status.
7. Preserve all current UI behavior.

## Non-Goals

- Do not add a new pytest marker named `kicad`.
- Do not replace the existing `requires_kicad` infrastructure.
- Do not introduce preview mocking in this batch.
- Do not change production behavior for preview-generation failures.
- Do not change backend artifact names.
- Do not change wizard routing UX beyond test coverage.
- Do not add broad `any`, TypeScript suppressions, ESLint suppressions, or loosened config.

---

## 1. Fix non-hermetic unit test for KiCad system symbol fallback

### Problem

`tests/unit/test_lib_symbol.py::test_read_lib_symbol_def_flat_falls_through_to_system_library` depends on a host KiCad system symbol library being installed. On a machine without the expected KiCad symbols, this test fails:

```text
assert None is not None
```

That violates the expectation that ordinary `tests/unit/` can run without KiCad system libraries.

### Preferred solution

Make the test hermetic by creating a temporary symbol-library fixture and passing `symbols_dir` explicitly. Unit tests should use controlled local fixtures whenever possible.

For example, the test should create a temporary symbol library file under `tmp_path`, then call the relevant symbol lookup function with `symbols_dir=tmp_path` or the correct equivalent directory.

### Acceptable fallback

If the purpose of this exact test is explicitly to verify real system-library fallback behavior, mark it as KiCad-dependent using the existing project helper:

```python
from tests.conftest import requires_kicad

@requires_kicad()
def test_read_lib_symbol_def_flat_falls_through_to_system_library() -> None:
    ...
```

However, prefer the hermetic fixture approach if the behavior can be tested without the host KiCad installation.

### Requirements

- Do not add a new pytest marker.
- Do not use `@pytest.mark.kicad`.
- Do not skip unrelated tests.
- Do not weaken the assertion to allow `None`.
- Preserve coverage for symbol lookup fallback behavior.
- Ensure `uv run --extra dev --extra web python -m pytest tests/unit/` passes on a machine without KiCad system symbol libraries.

### Acceptance criteria

- `tests/unit/` does not require host KiCad system symbol libraries unless such tests are explicitly marked with `requires_kicad`.
- The previously failing symbol test either becomes hermetic or is clearly marked/skipped through `requires_kicad`.
- Unit validation passes.

---

## 2. Standardize KiCad marker documentation on `requires_kicad`

### Problem

The project already uses `requires_kicad`, but at least one Batch 3 spec/doc still includes:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

That is inconsistent with the existing marker and the Batch 3 replies.

### Required behavior

All active/current validation docs should refer to the existing marker:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

Normal non-KiCad validation may use:

```bash
uv run --extra dev --extra web python -m pytest -m "not requires_kicad"
```

or a narrower path such as `tests/unit/`, provided it is now hermetic.

### Requirements

- Search current docs for `-m kicad`.
- Replace current/active docs with `-m requires_kicad`.
- Do not rewrite historical completion notes unless they are part of current instructions.
- Update `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md` if it exists in the repo.
- Keep `CLAUDE.md` consistent with the project’s actual marker.
- Do not add a second marker.

### Acceptance criteria

- No current validation instructions tell developers to run `pytest -m kicad`.
- Docs clearly identify `requires_kicad` as the canonical marker.
- Normal and KiCad-dependent test commands are documented separately.

---

## 3. Remove duplicate `handleClearIr` return property

### Problem

`frontend/src/routes/wizard/useWizardController.ts` returns `handleClearIr` twice in the controller object.

This is harmless at runtime but sloppy and should be cleaned up.

### Requirements

- Open `frontend/src/routes/wizard/useWizardController.ts`.
- Find duplicate `handleClearIr` in the returned object.
- Remove one duplicate.
- Ensure the controller’s public return shape remains otherwise unchanged.
- Ensure TypeScript build and frontend tests still pass.

### Acceptance criteria

- `handleClearIr` appears only once in the returned controller object.
- No behavior changes.
- Frontend build/lint/tests pass.

---

## 4. Strengthen invalid wizard route test

### Problem

`WizardInvalidRoute.test.tsx` verifies that canonical content renders, but if the controller is fully mocked and always returns `currentStep: 'describe'`, the test may not prove that `WizardPage.tsx` normalized the invalid route step before calling the controller.

### Required behavior

The invalid-route test should assert that the page passes `undefined` to `useWizardController` for an invalid route step such as:

```text
/wizard/abc123/not-a-step
```

### Requirements

- Update `frontend/src/test/WizardInvalidRoute.test.tsx` or equivalent.
- Mock `useWizardController` with a spy.
- Render `/wizard/abc123/not-a-step`.
- Assert the mocked controller was called with the normalized route step argument as `undefined`.
- Continue asserting that canonical content renders and the page is not blank.
- Add/keep a valid route test to ensure valid steps are passed through unchanged, if practical.

### Acceptance criteria

- The invalid-route test fails if `WizardPage.tsx` passes raw `not-a-step` to the controller.
- The test passes only when normalization happens before controller invocation.
- Existing route behavior remains unchanged.

---

## 5. Complete and verify type/lint discipline checklist

### Problem

Batch 3 Task 7 checkboxes remain unchecked even though the code appears to satisfy them.

### Required behavior

Re-verify the codebase and mark the checklist complete only if the checks pass.

### Requirements

Check for new or inappropriate usage of:

- broad `any`
- `// @ts-ignore`
- `// @ts-expect-error`
- `eslint-disable`
- weakened `tsconfig` strictness
- weakened Python lint/mypy settings

Use targeted search commands such as:

```bash
grep -R "eslint-disable\|@ts-ignore\|@ts-expect-error" frontend/src tests src -n
grep -R "\bany\b" frontend/src -n
```

Adjust the exact commands as needed for the repo.

### Acceptance criteria

- No new broad `any` usage is introduced.
- No new TypeScript suppressions are introduced.
- No unexplained lint suppressions are introduced.
- No strictness settings are weakened.
- Batch 3 Task 7 checkboxes are updated only after verification.

---

## 6. Validation

Run the full validation set after implementation.

### Frontend

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

### Python/backend

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

### Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

### KiCad-dependent tests

If KiCad CLI and related tools are installed:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

If not installed, confirm KiCad-marked tests skip cleanly.

### Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- Frontend build, lint, and tests pass.
- Python ruff, format check, mypy, and unit tests pass.
- Web tests pass or skip only explicitly marked KiCad/rsvg-dependent tests.
- KiCad-dependent tests either pass or skip cleanly.
- No generated cache artifacts are tracked.
- `git status --short` contains only intentional changes.

---

## Completion notes required

Claude Code should report:

- files changed,
- exact unit test fix chosen: hermetic fixture or `requires_kicad` marking,
- docs updated for `requires_kicad`,
- duplicate return property removed,
- invalid-route test strengthening details,
- type/lint search commands run,
- exact validation commands and results,
- whether KiCad CLI and `rsvg-convert` were available,
- any tests skipped and why.
