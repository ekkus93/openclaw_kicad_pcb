# KICAD_WEB_REFACTOR_FIX3 Completion Notes

## Files changed

**Backend:**
- `src/kicad_pcb_web/errors.py` — added `_PRIVATE_PATH_RE`, updated
  `_sanitize_path_text()`, sanitize `message` in `kicad_error_to_payload()`
- `src/kicad_pcb_web/services/netlists.py` — `_safe_symbols_dirs_used()` now
  catches `UserError` only
- `tests/web/test_web_error_sanitization.py` — new; 18 tests for
  `kicad_error_to_payload` sanitization + integration route check
- `tests/web/test_web_validate_netlist.py` — added 2 tests for
  `_safe_symbols_dirs_used` behavior

**Frontend:**
- `frontend/src/api/client.ts` — `requestJson<T>()` split into explicit
  ok/error branches; successful invalid JSON now throws `ApiError`
- `frontend/src/test/apiClient.test.ts` — new; 13 tests for client behavior
- `src/kicad_pcb_web/static/spa/` — rebuilt SPA artifacts

**Docs:**
- `docs/KICAD_WEB_REFACTOR_FIX2_SPEC.md` — corrected archive date
  `2026-06-11` → `2026-06-09`
- `docs/KICAD_WEB_REFACTOR_FIX2_TODO.md` — corrected archive date
  `2026-06-11` → `2026-06-09`
- `docs/KICAD_WEB_REFACTOR_FIX3_COMPLETION.md` — this file

---

## `_safe_symbols_dirs_used()` change

Changed `except Exception: return []` to `except UserError: return []`.
`SymbolIndex.__init__` raises `UserError(SYMBOL_DIR_MISSING)` when no symbol
libraries are found — that is the expected user/config failure. Any other
exception (e.g., `OSError`, `RuntimeError`) now propagates and will be
caught by the route-level unexpected-error handler, which returns HTTP 500.

**Unexpected exceptions**: fail loudly — propagate to the route handler
where they are logged and returned as an opaque HTTP 500 error.

---

## API client change

`requestJson<T>()` now has two separate branches:

- `response.ok` + valid JSON → return parsed payload (unchanged behavior)
- `response.ok` + invalid JSON → throw `ApiError('Expected JSON response
  but received invalid JSON.', status, null)` — previously returned
  `null as T` silently
- `!response.ok` + valid JSON → `_buildApiError()` extracts structured
  message from `error.message` or FastAPI `detail` (unchanged behavior)
- `!response.ok` + invalid JSON → `ApiError` with status-based message
  (unchanged behavior)

No 204 No Content branch added — no current endpoint returns 204.

---

## API client tests added

`frontend/src/test/apiClient.test.ts` — 13 tests covering:

- Successful JSON response returns parsed payload
- Successful invalid JSON throws `ApiError`
- `ApiError` for invalid success JSON has a clear message
- No silent `null` return for invalid success JSON
- Structured backend error payload produces `ApiError` with extracted message
- FastAPI `detail` string extracted as message
- FastAPI `detail` array joined as message
- Non-JSON error response throws `ApiError` with status
- Parsed error payload preserved on `ApiError.details`
- No `Content-Type` on GET without body
- `Content-Type: application/json` set on body requests
- Caller-supplied headers preserved
- Caller-supplied `Content-Type` not overwritten

---

## Sanitization changes

`_sanitize_path_text()` in `errors.py` now:

1. Returns `<redacted-path>` for any standalone absolute path value
   (previously returned the filename component only).
2. Uses `_PRIVATE_PATH_RE` to replace embedded private path substrings
   anywhere in a longer string: `/tmp/`, `/var/folders/`, `/private/var/`,
   `/home/<user>/`, `/Users/<user>/`.

`kicad_error_to_payload()` now applies `_sanitize_path_text()` to the
top-level `message` (`str(exc)`). Previously only `details` were sanitized.

All callers of `kicad_error_to_payload()` — including the HTTP exception
handlers `handle_user_error` and `handle_kicad_error` — inherit the fix.

---

## Sanitization tests added

`tests/web/test_web_error_sanitization.py` — 18 tests:

- `kicad_error_to_payload` redacts `/tmp/` in message
- `kicad_error_to_payload` redacts `/home/<user>/` in message
- `kicad_error_to_payload` redacts `/Users/<user>/` in message
- `kicad_error_to_payload` redacts `/var/folders/` in message
- `kicad_error_to_payload` redacts `/private/var/` in message
- Normal messages without paths are preserved unchanged
- String detail values are redacted
- List detail values are redacted
- Dict detail values are redacted
- Component refs (R1, U2) are not redacted
- KiCad library IDs (Device:R) are not redacted
- Integration: validation route errors do not contain `/tmp/` or
  `kicad-pcb-web-prepare` strings
- Parametrized: all 5 embedded path prefixes are redacted from message

---

## Archive-date decision

Archive directories exist as `2026-06-09/` (the date the work was
committed). The Fix2 SPEC and TODO docs incorrectly named `2026-06-11/`.
Updated both docs to state `2026-06-09`. No directory renaming was done.

---

## Automated validation results

```
uv run --extra dev --extra web ruff check .         → All checks passed
uv run --extra dev --extra web ruff format --check . → 446 files already formatted
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
                                                    → no issues found in 211 source files
uv run --extra dev --extra web python -m pytest tests/web/
                                                    → 74 passed, 1 skipped
uv run --extra dev --extra web python -m pytest tests/unit/
                                                    → passed (exit 0)
cd frontend && npm run lint                         → passed
cd frontend && npm run test:run                     → 105 passed (11 test files)
cd frontend && npm run build                        → succeeded
```

**Skipped test:** `tests/web/test_web_llm_clients.py` — 1 test skipped:
`Set RUN_LIVE_PROVIDER_TESTS=1 to run live provider probes.` This skip is
expected and environment-dependent.

**Tool availability:** `kicad-cli` available at `/usr/bin/kicad-cli`.
`rsvg-convert` available at `/usr/bin/rsvg-convert`.

**Artifact hygiene:** `__pycache__` directories exist locally but are
untracked (covered by `.gitignore`). No generated cache files are committed.
`git status` shows no unintended staged or unstaged changes.

---

## Playwright E2E tests added

`frontend/e2e/json-generate.spec.ts` — 7 automated smoke tests for the direct
JSON flow (see commit `feat(e2e): add Playwright smoke tests for direct JSON flow`):

- Valid IR validates successfully
- Generate button present and enabled after valid validation
- Invalid IR shows Validation Failed with error messages
- Generate button absent after invalid validation
- Changing Symbols Directory clears prior validation result
- Validation result does not leak private paths in error response
- Generate KiCad project navigates to the job page

`frontend/e2e/wizard.spec.ts` — 1 test gated behind `RUN_E2E_WIZARD_TESTS=1`:
LLM session creation, assistant reply, IR generation, no browser console errors.

Run with: `cd frontend && npm run test:e2e`

**Side fix:** `src/kicad_pcb_web/routes/ui.py` was missing server-side routes
for `/generate-json`, `/jobs`, `/setup`, and `/symbols`. Deep-link navigation
to these paths returned HTTP 404 instead of serving the SPA `index.html`. Fixed
as part of the E2E implementation when tests exposed this gap.

---

## Manual smoke-test status

**Direct JSON flow:** Covered by automated Playwright E2E tests (7 passing).

**Wizard flow with LLM:** Manual smoke tests not performed in this environment.
Set `RUN_E2E_WIZARD_TESTS=1` to run the automated wizard E2E test when an LLM
provider is configured.
