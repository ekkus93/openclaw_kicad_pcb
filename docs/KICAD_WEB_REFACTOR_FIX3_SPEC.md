# KiCad PCB Web App Refactor Fix 3 Spec

## Purpose

`KICAD_WEB_REFACTOR_FIX2` implemented the major refactor work correctly: LLM client lifecycle cleanup, direct netlist validation contract, frontend validation UI updates, frontend API module split, and historical document archiving.

The Fix 2 review found several remaining hardening issues that should be addressed before calling the refactor fully production-ready:

1. A quiet broad fallback in `_safe_symbols_dirs_used()`.
2. Frontend API client behavior that can silently return `null as T` for successful invalid JSON responses.
3. Validation-error sanitization that is too narrow for embedded absolute paths.
4. Archive-date mismatch between the TODO and the actual archive directories.
5. Manual smoke-test status not recorded explicitly.

This Fix 3 batch is a focused hardening pass. Do not perform unrelated refactors.

---

## Goals

1. Remove or justify quiet/silent fallback behavior.
2. Make frontend API JSON parsing failures explicit and test-covered.
3. Redact embedded filesystem paths from public validation-error payloads.
4. Resolve the archive-date mismatch in docs and/or directory structure.
5. Record manual smoke-test status honestly.
6. Preserve the successful Fix 2 API contract and UI behavior.

---

## Non-Goals

- Do not redesign the validation API.
- Do not revert the frontend API module split.
- Do not change the `valid: false` HTTP 200 validation contract for user-correctable Circuit IR errors.
- Do not implement background job workers.
- Do not change backend artifact names.
- Do not silently suppress unexpected exceptions.
- Do not archive or move unrelated active docs beyond the archive-date correction.
- Do not broaden this into another general UI refactor.

---

## 1. Remove quiet fallback in `_safe_symbols_dirs_used()`

### Problem

`src/kicad_pcb_web/services/netlists.py` currently contains a fallback like:

```python
def _safe_symbols_dirs_used(symbols_dir: Path | None) -> list[str]:
    try:
        return [str(p) for p in SymbolIndex(symbols_dir=symbols_dir).directories]
    except Exception:
        return []
```

This catches all exceptions and silently returns an empty list. That can hide programming errors, broken `SymbolIndex` behavior, environment regressions, or unexpected runtime failures.

### Required behavior

Expected user/config errors may be handled gracefully. Unexpected exceptions must not disappear silently.

### Preferred implementation

Catch only expected, user-correctable errors such as `UserError`, if that is the actual exception type raised by symbol-directory validation:

```python
except UserError:
    return []
```

If broader catching is necessary for compatibility, log unexpected exceptions with stack trace before returning a fallback:

```python
except Exception:
    logger.exception("Failed to determine symbols directories used")
    return []
```

Do not use a broad `except Exception` without logging.

### Acceptance criteria

- `_safe_symbols_dirs_used()` no longer contains a silent broad `except Exception: return []`.
- Expected user/config failures remain safe for validation responses.
- Unexpected exceptions are either allowed to fail loudly or logged before fallback.
- Tests cover the chosen behavior.

---

## 2. Harden frontend API client successful invalid-JSON handling

### Problem

`frontend/src/api/client.ts` currently parses JSON in a way that can turn a successful but invalid/non-JSON response into `null as T`.

Pattern to avoid:

```ts
const payload = await response.json().catch(() => null)
return payload as T
```

For successful API calls, invalid JSON should be a clear API/protocol error, not a quiet `null`.

### Required behavior

For `response.ok`:

- Valid JSON returns normally.
- Invalid JSON throws an `ApiError` with a clear message.
- Empty/no-content successful responses should only be allowed if the API client explicitly supports 204/no-content behavior.

For non-OK responses:

- Preserve existing structured error parsing behavior.
- Preserve FastAPI validation error message extraction behavior.
- If error JSON parsing fails, still throw `ApiError` with useful status/message.

### Suggested implementation

Use explicit success/error parsing branches:

```ts
let payload: unknown = null

if (response.ok) {
  if (response.status === 204) {
    return null as T
  }

  try {
    payload = await response.json()
  } catch {
    throw new ApiError('Expected JSON response but received invalid JSON.', response.status, null)
  }

  return payload as T
}

try {
  payload = await response.json()
} catch {
  payload = null
}

throw buildApiErrorFromPayload(response.status, payload)
```

Adjust to fit the current `client.ts` structure.

### Acceptance criteria

- Successful invalid JSON responses throw `ApiError`.
- Structured backend error parsing still works.
- FastAPI validation error extraction still works.
- Caller-supplied headers remain preserved.
- `Content-Type: application/json` is still only set when a request body is present.
- Focused API client tests cover these behaviors.

---

## 3. Strengthen validation-error path sanitization

### Problem

Current sanitization appears to redact detail values that are themselves absolute paths, but it may not redact embedded paths inside longer strings. It may also miss the top-level message returned by `kicad_error_to_payload()`.

Examples that must not leak public response data:

```text
Invalid JSON in /tmp/kicad-pcb-web-prepare-abc/circuit_ir.json
Could not read /home/alice/private/project/file.kicad_sch
Failure under /var/folders/xyz/private-dir
```

### Required behavior

Public validation responses must not leak absolute temp paths, home-directory paths, or private filesystem prefixes in:

- top-level validation issue message,
- validation issue details,
- nested string values in details,
- lists/dicts of details if applicable.

### Required redaction

At minimum, redact embedded Unix absolute paths beginning with common private prefixes:

```text
/tmp/...
/var/folders/...
/private/var/...
/home/<user>/...
/Users/<user>/...
```

Replace with stable public text such as:

```text
<redacted-path>
```

or a more specific neutral placeholder.

Do not over-redact ordinary component names, net names, or relative KiCad library identifiers.

### Implementation guidance

Add a recursive sanitizer for public error payloads:

- strings: replace embedded private path substrings,
- lists: sanitize each item,
- dicts: sanitize each value,
- other primitives: preserve unchanged.

Apply sanitization to both message and details when converting a `UserError` into a `ValidationIssue`.

### Acceptance criteria

- Invalid validation responses do not leak embedded absolute temp/home/private paths.
- Message and details are both sanitized.
- Existing validation response shape is preserved.
- Tests cover embedded path redaction.

---

## 4. Resolve archive-date mismatch

### Problem

`KICAD_WEB_REFACTOR_FIX2_TODO.md` requested archive directories:

```text
docs/archive/2026-06-11/
code_review/archive/2026-06-11/
```

The reviewed repo used:

```text
docs/archive/2026-06-09/
code_review/archive/2026-06-09/
```

This mismatch is confusing for future reviews.

### Decision

Use the already-created archive date if links and references are correct. Do **not** churn archive directories just to match an older TODO date unless there is a real reason.

### Required behavior

Update active docs/TODO/completion notes to state the actual archive date used:

```text
2026-06-09
```

If the repo maintainers prefer the requested date instead, rename both archive directories consistently and update links. But the preferred approach is documentation consistency with the existing archive.

### Acceptance criteria

- Active docs no longer claim the archive date is `2026-06-11` if the actual archive date is `2026-06-09`.
- Existing links to archived docs work or are updated.
- No active docs are accidentally archived.
- No delete/add churn if a simple docs update is sufficient.

---

## 5. Record manual smoke-test status explicitly

### Problem

Automated checks passed, but the manual direct JSON flow and wizard-with-LLM flow were not evidenced in the reviewed snapshot. Manual work should not be silently marked complete unless performed.

### Required behavior

Update completion notes or the active TODO to explicitly state one of:

```text
Manual smoke tests performed: PASS
```

with details, or:

```text
Manual smoke tests not performed in this environment.
```

### Required smoke tests to record

Direct JSON flow:

1. Load the app.
2. Paste valid Circuit IR JSON.
3. Validate successfully.
4. Change Symbols Directory and confirm validation result clears.
5. Validate invalid Circuit IR JSON.
6. Confirm page shows “Validation Failed” with errors and no top-level API exception banner.
7. Confirm Generate is unavailable after invalid validation.
8. Validate valid JSON again and generate a KiCad project.

Wizard flow with LLM enabled:

1. Create a wizard session.
2. Send one follow-up message.
3. Generate IR.
4. Confirm no socket/resource leak warnings appear in logs.

### Acceptance criteria

- Manual smoke-test status is explicit.
- If not performed, it is marked not performed.
- If performed, exact environment and result are recorded.
- No manual item is implied complete without evidence.

---

## 6. Validation requirements

Run the applicable validation after implementation.

Backend:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web pytest tests/web -q -rs
```

Frontend:

```bash
cd frontend
npm run lint
npm run test:run
npm run build
```

Targeted frontend API-client tests should be included in `npm run test:run`.

If a full pytest run is attempted but times out, report that honestly and include the last observed status.

### Acceptance criteria

- Ruff passes.
- Ruff format check passes.
- Mypy passes.
- Web tests pass or skip only expected environment-dependent tests.
- Frontend lint/tests/build pass.
- New tests cover the changed behavior.
- No generated cache artifacts are tracked.

---

## Completion notes required

Claude Code should report:

- Files changed.
- How `_safe_symbols_dirs_used()` was changed.
- Whether unexpected exceptions now log or fail loudly.
- API client behavior for successful invalid JSON.
- API client tests added.
- Sanitization tests added.
- Archive-date decision and files updated.
- Manual smoke-test status.
- Exact validation commands and results.
- Any skipped tests and why.
- Artifact hygiene result.
