# UI/UX Improvements — Batch 4 TODO

Derived from the Batch 3 review. Batch 3 is mostly complete, but there are several cleanup issues before the batch should be considered fully closed: one non-hermetic unit test, one stale KiCad marker command in docs, a duplicate controller return property, a weak invalid-route test, and unchecked type/lint discipline checklist items.

---

## 1. Fix non-hermetic unit test for KiCad system symbol fallback (P0 — test reliability)

`tests/unit/test_lib_symbol.py::test_read_lib_symbol_def_flat_falls_through_to_system_library` can fail on machines without KiCad system symbol libraries.

### 1.1 Inspect the failing test

- [x] Open `tests/unit/test_lib_symbol.py`
- [x] Locate `test_read_lib_symbol_def_flat_falls_through_to_system_library`
- [x] Confirm whether it is intended to test:
  - [ ] generic fallback behavior that can be tested with a local fixture, or
  - [x] real host KiCad system-library fallback behavior

### 1.2 Preferred fix: make the test hermetic

Not applicable — this test explicitly verifies system-library fallback (symbols_dir=None path). Hermetic fixture would not exercise the real fallthrough path.

### 1.3 Acceptable fallback: mark as KiCad-dependent

- [x] Import or use the existing `requires_kicad` helper from `tests/conftest.py`
- [x] Decorate the test with `@requires_kicad` (no parentheses — matches existing API)
- [x] Do not add a new marker
- [x] Do not use `@pytest.mark.kicad`
- [x] Ensure the skip reason is clear when KiCad is unavailable

Added `kicad_system_symbols_available()` to `tests/conftest.py` and updated `requires_kicad` to also skip when system symbol libraries are absent (not just when kicad-cli is absent).

### 1.4 Validate unit hermeticity

- [x] Unit tests pass (3 passed — on this machine kicad-cli and system symbols are present, so the marked test runs)
- [x] No unrelated tests are skipped just to hide failures

---

## 2. Standardize KiCad marker docs on `requires_kicad` (P1 — documentation correctness)

The project uses `requires_kicad`, not `kicad`.

### 2.1 Search for stale marker commands

- [x] Search docs for `-m kicad`
- [x] Search docs for `pytest -m kicad`
- [x] Search current Batch 3 docs, especially `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md`
- [x] Search `CLAUDE.md`

### 2.2 Replace stale commands

- [x] Replace active/current `pytest -m kicad` examples with `-m requires_kicad`

Fixed `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md` line 333. `CLAUDE.md` had no stale marker. Historical completion notes and other docs referencing `python -m kicad_pcb.cli` (CLI module invocations) were left unchanged.

- [x] Do not create or document a new `kicad` marker
- [x] Do not rewrite historical completion notes unless they are used as active instructions

### 2.3 Confirm docs are consistent

- [x] No active/current docs instruct developers to run `pytest -m kicad`
- [x] Docs identify `requires_kicad` as the canonical marker
- [x] Docs explain when KiCad CLI / rsvg tools are required or skipped

---

## 3. Remove duplicate `handleClearIr` return property (P2 — code hygiene)

`useWizardController.ts` currently returns `handleClearIr` twice.

### 3.1 Clean controller return object

- [x] Open `frontend/src/routes/wizard/useWizardController.ts`
- [x] Find duplicate `handleClearIr` in the returned object
- [x] No code change needed — already resolved before this batch

Verified: `grep -n "handleClearIr" useWizardController.ts` shows one definition (line 218) and one return entry (line 280). No duplicate present.

### 3.2 Validate frontend

- [x] `cd frontend && npm run build` passes
- [x] `cd frontend && npm run lint` passes
- [x] Existing wizard tests pass

---

## 4. Strengthen invalid wizard route test (P1 — regression coverage)

`WizardInvalidRoute.test.tsx` should prove that invalid route steps are normalized before reaching the controller.

### 4.1 Inspect current test

- [x] Open `frontend/src/test/WizardInvalidRoute.test.tsx`
- [x] Confirm how `useWizardController` is mocked — was a plain factory, not a spy
- [x] Identified: original test would pass even if raw `not-a-step` were passed to controller

### 4.2 Add assertion for normalized controller argument

- [x] Changed mock to `vi.fn()` and set return value in `beforeEach`
- [x] Added test: render `/wizard/abc123/not-a-step`, assert `toHaveBeenCalledWith('abc123', undefined)`
- [x] Kept existing content-render assertions

Controller takes positional args `(sessionId, routeStep)`, so assertion uses positional form (not `objectContaining`).

### 4.3 Add/keep valid route control test

- [x] Added test: render `/wizard/abc123/spec`, assert `toHaveBeenCalledWith('abc123', 'spec')`
- [x] Valid route behavior remains unchanged

### 4.4 Validate test behavior

- [x] New tests would fail if normalization were removed (raw string would fail equality with 'spec'/undefined checks)
- [x] Frontend tests pass (87 passed)

---

## 5. Complete type/lint discipline checklist (P1 — checklist accuracy)

Batch 3 Task 7 checkboxes remained unchecked. Re-verify and mark them complete only if true.

### 5.1 Search for TypeScript suppressions

- [x] Ran: `grep -R "eslint-disable\|@ts-ignore\|@ts-expect-error" frontend/src tests src -n` — zero results
- [x] No new `@ts-ignore`
- [x] No new `@ts-expect-error`
- [x] No unexplained `eslint-disable`

### 5.2 Search for broad `any`

- [x] Ran: `grep -R "\bany\b" frontend/src -n` — only English prose in string literals, no TypeScript type usage
- [x] No new broad `any`
- [x] No existing `any` type usage found
- [x] Generated/API types and local specific types used throughout

### 5.3 Check config strictness

- [x] `frontend/tsconfig*.json` strictness was not weakened
- [x] Python lint/mypy settings were not weakened
- [x] No lint rules were relaxed to pass this batch

### 5.4 Update Batch 3 TODO checklist

- [x] Marked Task 7.1 complete — broad `any` check passes
- [x] Marked Task 7.2 complete — suppression/config checks pass

---

## 6. Run final validation (P0 — must pass)

### 6.1 Frontend validation

- [x] `npm run build` — ✓ built in 197ms, zero TypeScript errors
- [x] `npm run lint` — clean
- [x] `npm test -- --run` — 87 passed (9 test files)

### 6.2 Python/backend validation

- [x] `ruff check .` — All checks passed
- [x] `ruff format --check .` — 216 files already formatted
- [x] `mypy src/kicad_pcb src/kicad_pcb_web` — no issues found in 109 source files
- [x] `python -m pytest tests/unit/` — passed (all tests pass on this machine which has kicad-cli and system symbols)

### 6.3 Web tests

- [x] Web tests pass — 6 tests skipped (4 rsvg-convert, 1 live-provider, 1 rsvg-convert in wizard)
- [x] KiCad/rsvg-dependent tests skip cleanly — rsvg-convert is absent, skip reasons shown
- [x] No unmarked non-hermetic tests fail

### 6.4 KiCad-dependent tests

kicad-cli is available and system KiCad symbol libraries are present on this machine. KiCad-marked tests run and pass (included in unit test run above).

### 6.5 Artifact hygiene

- [x] No generated cache artifacts tracked
- [x] No unintended files changed
- [x] `git status --short` shows only 6 intentional changes: conftest.py, test_lib_symbol.py, WizardInvalidRoute.test.tsx, UIUX_IMPROVEMENTS3_SPEC_UPDATED.md, UIUX_IMPROVEMENTS3_TODO_UPDATED.md, UIUX_IMPROVEMENTS4_TODO.md

---

## 7. Completion notes required

Claude Code should report:

- [x] Files changed: `tests/conftest.py`, `tests/unit/test_lib_symbol.py`, `frontend/src/test/WizardInvalidRoute.test.tsx`, `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md`, `docs/UIUX_IMPROVEMENTS3_TODO_UPDATED.md`, `docs/UIUX_IMPROVEMENTS4_TODO.md`
- [x] Unit test marked `@requires_kicad` (no parens) — not made hermetic
- [x] Reason: test is explicitly named/written to verify system-library fallback (symbols_dir=None); a hermetic fixture would not exercise that path. Also added `kicad_system_symbols_available()` to conftest and updated `requires_kicad` to skip when system symbol libraries are absent, not just when kicad-cli is absent.
- [x] Docs updated: `docs/UIUX_IMPROVEMENTS3_SPEC_UPDATED.md` line 333 fixed from `-m kicad` to `-m requires_kicad`
- [x] Duplicate `handleClearIr`: already absent before this batch — verified by grep, no code change made
- [x] Invalid-route test strengthened: changed mock to `vi.fn()`, added `beforeEach` setup, added two spy assertions (`toHaveBeenCalledWith('abc123', undefined)` and `toHaveBeenCalledWith('abc123', 'spec')`)
- [x] Type/lint search commands run: grep for `eslint-disable|@ts-ignore|@ts-expect-error` (zero results); grep for `\bany\b` (only English prose, no type usage)
- [x] Validation: all gates passed — ruff, mypy, 87 frontend tests, unit tests
- [x] kicad-cli available: yes (kicad-cli >= 9.0.0)
- [x] rsvg-convert available: no — 6 web tests skip with clear reason
- [x] Tests skipped: 4 rsvg-dependent web tests + 1 live-provider probe + 1 rsvg-dependent wizard web test

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 6 |
| P1 — Test/docs/checklist correctness | 2, 4, 5 |
| P2 — Small code hygiene | 3, 7 |
