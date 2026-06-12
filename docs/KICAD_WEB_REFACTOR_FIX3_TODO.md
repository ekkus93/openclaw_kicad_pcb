# KiCad PCB Web App Refactor Fix 3 TODO

Focused hardening follow-up after `KICAD_WEB_REFACTOR_FIX2`. Fix 2 implemented the major refactor, but review found remaining issues around quiet fallbacks, frontend API-client JSON handling, path sanitization, archive-date consistency, and manual smoke-test reporting.

---

## Phase 0 — Scope and baseline

- [ ] Start from the latest `webapp` codebase after `KICAD_WEB_REFACTOR_FIX2`.
- [ ] Do not perform unrelated refactors.
- [ ] Do not change the validation API contract:
  - [ ] user-correctable invalid Circuit IR still returns HTTP 200
  - [ ] invalid validation response still has `valid: false`
  - [ ] invalid validation response still has structured `errors`
- [ ] Do not revert the frontend API package split.
- [ ] Run or record baseline checks if useful:
  - [ ] `uv run --extra dev --extra web pytest tests/web -q -rs`
  - [ ] `cd frontend && npm run test:run`

---

## Phase 1 — Remove quiet fallback in `_safe_symbols_dirs_used()` (P0)

### 1.1 Inspect current implementation

- [ ] Open `src/kicad_pcb_web/services/netlists.py`
- [ ] Locate `_safe_symbols_dirs_used()`
- [ ] Confirm whether it currently catches broad `Exception`
- [ ] Identify the expected exception type for user/config symbol-dir failures, likely `UserError`

### 1.2 Replace silent broad fallback

Choose the narrowest safe implementation.

Preferred:

- [ ] Catch `UserError` only
- [ ] Return `[]` only for expected user/config symbol-dir failures
- [ ] Let unexpected exceptions fail loudly

Acceptable if broad fallback is truly needed:

- [ ] Keep broad catch only if there is a documented reason
- [ ] Add `logger.exception(...)` before returning `[]`
- [ ] Ensure unexpected failures are visible in logs

Required either way:

- [ ] Do not leave `except Exception: return []` with no logging
- [ ] Preserve normal valid validation behavior
- [ ] Preserve invalid validation response shape

### 1.3 Add/update backend tests

Add or update tests for the chosen behavior:

- [ ] Expected symbol-dir user/config failure does not crash validation if that is intended
- [ ] Unexpected `SymbolIndex` failure is not silently swallowed, or is logged if fallback is retained
- [ ] `symbols_dirs_used` remains populated for valid symbol-dir cases where available

---

## Phase 2 — Harden frontend API client invalid success JSON handling (P0)

### 2.1 Inspect `requestJson()`

- [ ] Open `frontend/src/api/client.ts`
- [ ] Locate `requestJson<T>()`
- [ ] Find current JSON parse behavior
- [ ] Confirm whether successful invalid JSON can currently return `null as T`

### 2.2 Change successful invalid JSON behavior

- [ ] For `response.ok` with valid JSON, return parsed payload
- [ ] For `response.ok` with invalid JSON, throw `ApiError`
- [ ] Error message should clearly say expected JSON was invalid or malformed
- [ ] Preserve `204 No Content` handling only if explicitly supported
- [ ] Do not silently return `null as T` for normal 200 invalid JSON

### 2.3 Preserve error response behavior

- [ ] Non-OK structured JSON errors still produce useful `ApiError`
- [ ] FastAPI validation errors still extract useful message text
- [ ] Non-OK invalid/non-JSON responses still throw `ApiError`
- [ ] Preserve `status` on `ApiError`
- [ ] Preserve parsed error payload on `ApiError` where available

### 2.4 Preserve request header behavior

- [ ] `Content-Type: application/json` is only set when request body exists
- [ ] Caller-supplied headers are preserved
- [ ] Caller-supplied `Content-Type` is not overwritten unnecessarily

### 2.5 Add focused frontend API-client tests

Create or update a test file such as:

```text
frontend/src/test/apiClient.test.ts
```

Tests should cover:

- [ ] Successful JSON response returns parsed payload
- [ ] Successful invalid JSON response throws `ApiError`
- [ ] `204 No Content` behavior, if supported
- [ ] Structured backend error payload becomes `ApiError`
- [ ] FastAPI validation error payload message extraction still works
- [ ] Invalid/non-JSON error response still throws useful `ApiError`
- [ ] `Content-Type` is not set on GET/no-body requests
- [ ] `Content-Type: application/json` is set on JSON body requests
- [ ] Caller-supplied headers are preserved

---

## Phase 3 — Strengthen validation error sanitization (P0)

### 3.1 Inspect current sanitization

- [ ] Open `src/kicad_pcb_web/services/netlists.py`
- [ ] Locate `_sanitize_path_text()` or equivalent
- [ ] Locate `kicad_error_to_payload()` usage
- [ ] Locate the helper that converts `UserError` to `ValidationIssue`
- [ ] Confirm whether top-level messages are sanitized
- [ ] Confirm whether embedded paths inside longer strings are sanitized

### 3.2 Add recursive public-payload sanitizer

Implement or update sanitization so it handles:

- [ ] plain string details
- [ ] embedded absolute paths inside longer strings
- [ ] list values
- [ ] dict values
- [ ] nested list/dict values if present
- [ ] top-level validation issue message
- [ ] validation issue details

At minimum, redact embedded paths beginning with:

- [ ] `/tmp/`
- [ ] `/var/folders/`
- [ ] `/private/var/`
- [ ] `/home/<user>/`
- [ ] `/Users/<user>/`

Use stable replacement text such as:

```text
<redacted-path>
```

### 3.3 Avoid over-redaction

- [ ] Do not redact relative KiCad library IDs
- [ ] Do not redact component references such as `R1`, `U2`
- [ ] Do not redact net names merely because they contain slashes unless they look like private absolute paths
- [ ] Preserve useful user-correctable error information

### 3.4 Add backend sanitization tests

Add tests for invalid validation responses where error message/details contain embedded paths:

- [ ] `/tmp/kicad-pcb-web-prepare-abc/circuit_ir.json`
- [ ] `/home/alice/private/project/file.kicad_sch`
- [ ] `/Users/alice/private/project/file.kicad_sch`
- [ ] `/var/folders/abc/private/file`
- [ ] `/private/var/folders/abc/private/file`

Assertions:

- [ ] public response does not contain the raw absolute path
- [ ] public response contains `<redacted-path>` or chosen placeholder
- [ ] non-path error context remains readable
- [ ] response still has HTTP 200 and `valid: false` for user-correctable validation errors

---

## Phase 4 — Resolve archive-date mismatch (P1)

### 4.1 Inspect archive directories

- [ ] Check actual archive directories:
  - [ ] `docs/archive/`
  - [ ] `code_review/archive/`
- [ ] Confirm whether current archive date is `2026-06-09`
- [ ] Confirm whether any active docs still say `2026-06-11`

### 4.2 Prefer docs update over directory churn

If archive directories already exist as `2026-06-09` and links are correct:

- [ ] Update active docs/TODO/completion notes to say `2026-06-09`
- [ ] Do not rename archive directories just to match the old TODO
- [ ] Confirm active links still work

If maintainers prefer `2026-06-11` instead:

- [ ] Rename both archive directories consistently
- [ ] Update all links
- [ ] Preserve archive contents
- [ ] Avoid delete/add churn if using git

### 4.3 Verify archive consistency

- [ ] Active docs no longer disagree about the archive date
- [ ] `docs/` top level remains focused on active references
- [ ] `code_review/` remains archive-oriented with README guidance

---

## Phase 5 — Record manual smoke-test status explicitly (P1)

### 5.1 Direct JSON smoke status

Record one of:

- [ ] `Performed: PASS`
- [ ] `Performed: FAIL` with details
- [ ] `Not performed in this environment`

If performed, record:

- [ ] app URL/environment
- [ ] valid Circuit IR validation result
- [ ] symbols-dir change clears validation result
- [ ] invalid Circuit IR shows “Validation Failed”
- [ ] invalid validation does not show top-level API exception banner
- [ ] Generate unavailable when invalid
- [ ] valid JSON can generate a KiCad project

### 5.2 Wizard with LLM smoke status

Record one of:

- [ ] `Performed: PASS`
- [ ] `Performed: FAIL` with details
- [ ] `Not performed in this environment`

If performed, record:

- [ ] LLM provider used
- [ ] wizard session created
- [ ] follow-up message sent
- [ ] IR generated
- [ ] no socket/resource leak warnings in logs

### 5.3 Do not imply unperformed manual tests passed

- [ ] If not run, explicitly say not run
- [ ] Do not mark manual smoke items complete without evidence
- [ ] Keep automated and manual validation separate

---

## Phase 6 — Final validation (P0)

### 6.1 Backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web pytest tests/web -q -rs
```

- [ ] Ruff passes
- [ ] Format check passes
- [ ] Mypy passes
- [ ] Web tests pass or skip only expected environment-dependent tests

### 6.2 Frontend validation

```bash
cd frontend
npm run lint
npm run test:run
npm run build
```

- [ ] Lint passes
- [ ] Tests pass
- [ ] Build passes
- [ ] New API-client tests are included in the test run

### 6.3 Optional full validation

If practical:

```bash
uv run --extra dev --extra web pytest -q -rs
```

- [ ] Full pytest passes, or
- [ ] timeout/incomplete status is recorded honestly with last observed progress

### 6.4 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Only intentional files changed

---

## Done Definition

- [ ] No silent broad fallback remains in `_safe_symbols_dirs_used()`.
- [ ] Successful invalid JSON responses throw `ApiError`.
- [ ] API-client behavior is directly tested.
- [ ] Embedded private filesystem paths are redacted from validation errors.
- [ ] Sanitization behavior is tested.
- [ ] Archive-date docs match actual archive directories.
- [ ] Manual smoke-test status is explicit.
- [ ] Backend and frontend validation pass.
- [ ] No unrelated refactors are included.

---

## Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] `_safe_symbols_dirs_used()` change and rationale
- [ ] Whether unexpected exceptions log or fail loudly
- [ ] API-client behavior changes
- [ ] API-client tests added
- [ ] Sanitization changes
- [ ] Sanitization tests added
- [ ] Archive-date decision
- [ ] Manual smoke-test status
- [ ] Exact validation commands and results
- [ ] Skipped tests and reasons
- [ ] Artifact hygiene result
