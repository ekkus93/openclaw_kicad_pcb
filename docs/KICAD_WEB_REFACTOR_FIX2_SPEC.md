# KiCad PCB Web App Refactor Fix 2 Spec

## Purpose

Implement the next cleanup pass after the large web-app refactor. This pass addresses four accepted design decisions:

1. Manage LLM HTTP clients with a FastAPI generator dependency that yields the client and closes it after the request.
2. Change direct Circuit IR validation to return HTTP 200 with `valid: false` for user-correctable validation failures.
3. Split the frontend API module by domain while preserving the existing public import ergonomics.
4. Archive historical review/TODO markdown files instead of keeping them mixed with active documentation.

This is a stabilization/refinement patch. Do not redesign the wizard, generation pipeline, routing model, styling system, or backend job model in this pass.

## Current State

Relevant current files:

- `src/kicad_pcb_web/deps.py`
  - `get_llm_client()` currently returns a newly-built client directly.
  - HTTP-backed LLM clients expose `close()` through `BaseHttpLlmClient`, but the dependency does not close the client.

- `src/kicad_pcb_web/routes/api_netlists.py`
  - `POST /api/netlists/validate` delegates to `validate_netlist_dict()`.
  - Invalid Circuit IR currently bubbles through the global exception handlers as an HTTP error.

- `src/kicad_pcb_web/services/netlists.py`
  - `validate_netlist_dict()` currently returns `valid=True` on success and raises on invalid input.

- `src/kicad_pcb_web/schemas.py`
  - `ValidateNetlistResponse` has `valid`, `component_count`, `net_count`, `warnings`, and `symbols_dirs_used`, but no structured `errors` field.

- `frontend/src/api.ts`
  - All frontend API calls and `ApiError` live in one file.
  - `requestJson()` currently sends `Content-Type: application/json` on all requests, including GETs.

- `frontend/src/routes/JsonGeneratePage.tsx`
  - The page has UI for `validationResult.valid === false`, but the backend does not currently return that shape for invalid user input.
  - Adjacent correctness issue: changing the symbols directory after validation should invalidate the previous validation result.

- `docs/` and `code_review/`
  - Many historical review, TODO, response, and phase-specific markdown files are still mixed with active docs.

## Non-Goals

Do not implement these in this pass unless they are required to keep tests green:

- Do not replace the synchronous job model with a background worker.
- Do not rewrite the wizard workflow.
- Do not change LLM prompts or prompt versions.
- Do not remove old docs permanently; archive them with `git mv`.
- Do not change project generation behavior except where tests or compile errors require a small compatibility fix.
- Do not add a new state-management library.

## Requirement 1: LLM Client Generator Dependency

### Desired Behavior

`get_llm_client()` must become a FastAPI generator dependency:

- It builds the configured LLM client for the request.
- It yields `None` when LLM is disabled.
- It yields the concrete client when LLM is enabled.
- It closes the client after the route completes, even if the route raises.
- It must tolerate fake/test clients that satisfy `LlmClient` but do not expose `close()`.

### Implementation Guidance

In `src/kicad_pcb_web/deps.py`, change the dependency shape from direct return to generator/yield. For example:

```python
from collections.abc import Generator


def get_llm_client(
    settings: WebSettings = Depends(get_settings),
) -> Generator[LlmClient | None, None, None]:
    client = build_llm_client(settings)
    try:
        yield client
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
```

Notes:

- Do not require `close()` on the `LlmClient` protocol unless you want every fake client to implement it.
- Do not create a singleton LLM client in this pass; the accepted decision is per-request generator lifecycle.
- Keep dependency usage in `api_wizard.py` the same: `Depends(get_llm_client)`.
- If `build_llm_client()` raises `UserError` because configuration is invalid, keep the existing error behavior.

### Tests

Add or update backend tests so they prove:

- A closable fake LLM client is closed after a successful wizard route request.
- A closable fake LLM client is closed after a route-level failure.
- A non-closable fake LLM client does not crash the dependency finalizer.
- LLM disabled still yields `None` and does not attempt to close anything.

A focused unit test for the dependency generator is acceptable if full FastAPI route tests are cumbersome, but at least one integration-style test through `TestClient` is preferred.

## Requirement 2: Direct Validation Uses HTTP 200 for User-Correctable Invalid Circuit IR

### Desired Behavior

`POST /api/netlists/validate` must distinguish between normal invalid user input and actual server failure.

For syntactically valid JSON that is a user-correctable invalid Circuit IR:

- Return HTTP 200.
- Return a `ValidateNetlistResponse` with `valid: false`.
- Include structured validation errors that the frontend can render.
- Do not create a project or job.
- Do not show this as a top-level API failure banner in the frontend.

For valid Circuit IR:

- Return HTTP 200.
- Return `valid: true`.
- Include `component_count`, `net_count`, `warnings`, and `symbols_dirs_used`.

For malformed request bodies or server failures:

- Keep existing FastAPI/global error behavior.
- Request-model validation failures may still return 422.
- Unexpected exceptions must still return sanitized 500 responses.

### Response Model

Update `ValidateNetlistResponse` in `src/kicad_pcb_web/schemas.py` to support invalid results explicitly.

Recommended shape:

```python
class ValidationIssue(BaseModel):
    type: str = "validation_error"
    code: str | None = None
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidateNetlistResponse(BaseModel):
    valid: bool
    component_count: int | None = None
    net_count: int | None = None
    warnings: list[dict[str, Any]] = Field(default_factory=list)
    symbols_dirs_used: list[str] = Field(default_factory=list)
    errors: list[ValidationIssue] = Field(default_factory=list)
```

Use `None` for counts when validation fails. Do not use misleading `0` counts unless the service actually knows the counts.

### Backend Service Contract

Change `validate_netlist_dict()` so it catches user-correctable KiCad validation failures and converts them to `valid: false`.

Recommended behavior:

- Catch `UserError` from the validation stack and return `valid: false`.
- Use existing public error conversion helpers where possible, especially `kicad_error_to_payload()`, so messages/details remain sanitized.
- Include one error item at minimum.
- Preserve `symbols_dirs_used` when it can be computed safely.
- Do not catch broad `Exception` here; unexpected exceptions should still flow to the global 500 handler.

Example implementation direction:

```python
def _validation_issue_from_user_error(exc: UserError) -> ValidationIssue:
    payload = cast(dict[str, Any], kicad_error_to_payload(exc)["error"])
    return ValidationIssue(
        type=str(payload.get("type") or "validation_error"),
        code=cast(str | None, payload.get("code")),
        message=str(payload.get("message") or "Validation failed."),
        details=cast(dict[str, Any], payload.get("details") or {}),
    )
```

Then:

```python
try:
    prepared = prepare_netlist_dict(...)
except UserError as exc:
    return ValidateNetlistResponse(
        valid=False,
        errors=[_validation_issue_from_user_error(exc)],
        symbols_dirs_used=_safe_symbols_dirs_used(symbols_dir),
    )
```

If `_safe_symbols_dirs_used()` is added, it should not raise just because validation failed.

### Frontend Behavior

Update `frontend/src/types.ts`:

- `component_count` and `net_count` should become `number | null`.
- Add `errors` to `ValidateNetlistResponse`.

Update `JsonGeneratePage.tsx`:

- Invalid validation results should render in the validation result panel, not as an exception banner.
- Show each validation issue with message, code, and relevant details.
- Disable `Generate KiCad Project` when `valid` is false.
- Display count fields as `—` or `Unknown` when null.
- Clear `validationResult` when the symbols directory changes, because validation is tied to both JSON and symbol-library context.

Recommended adjacent fix:

```tsx
function handleSymbolsDirChange(value: string) {
  setSymbolsDir(value)
  setValidationResult(null)
  setErrorMessage(null)
}
```

Use this handler for the Symbols Directory input.

### Tests

Backend tests should cover:

- Valid IR returns HTTP 200 and `valid: true`.
- Invalid but user-correctable IR returns HTTP 200 and `valid: false`.
- Invalid response includes a non-empty `errors` array.
- Invalid response does not include unsanitized absolute temp paths.

Frontend tests should cover:

- `valid: false` renders “Validation Failed”.
- Validation errors are visible to the user.
- Generate button is absent or disabled for invalid results.
- Changing Symbols Directory after a successful validation clears the previous validation result.

## Requirement 3: Split Frontend API Module by Domain

### Desired Behavior

Replace the monolithic `frontend/src/api.ts` with a domain-split API package while preserving the existing consumer-facing API object.

Existing imports like these should continue to work after the move:

```ts
import { api } from '../api'
import { ApiError } from './api'
```

This is easiest if `frontend/src/api.ts` is removed and replaced with a directory containing `frontend/src/api/index.ts`.

### Recommended File Layout

Create:

```text
frontend/src/api/
  client.ts
  index.ts
  bootstrap.ts
  doctor.ts
  jobs.ts
  netlists.ts
  symbols.ts
  wizard.ts
```

Responsibilities:

- `client.ts`
  - `ApiError`
  - `requestJson<T>()`
  - `jsonBody()` or equivalent helper

- `bootstrap.ts`
  - `getBootstrap()`

- `doctor.ts`
  - `getDoctor()`

- `jobs.ts`
  - `getJobs()`
  - `getJob()`
  - `createJobFromNetlist()` if you decide job creation belongs under jobs rather than netlists

- `netlists.ts`
  - `validateNetlist()`
  - optionally `createJobFromNetlist()` if you prefer grouping by entrypoint/user flow

- `symbols.ts`
  - `searchSymbols()`

- `wizard.ts`
  - wizard session/message/spec/IR/project functions

- `index.ts`
  - re-export `ApiError`
  - export `api` object with the same method names used today

### Request Headers

Fix `requestJson()` so it only sends `Content-Type: application/json` when there is a request body. GET requests should not receive JSON content-type by default.

Recommended approach:

```ts
const headers = new Headers(init?.headers)
if (init?.body !== undefined && !headers.has('Content-Type')) {
  headers.set('Content-Type', 'application/json')
}
```

Also ensure `requestJson()` handles empty success responses safely, even though current endpoints mostly return JSON.

### Tests

Existing frontend tests should keep passing without broad rewrites. Add or update tests only where needed for changed validation behavior.

Acceptance checks:

- `npm run lint`
- `npm run test:run`
- `npm run build`

## Requirement 4: Archive Historical Review/TODO Markdown Files

### Desired Behavior

Move old review/TODO/response markdown files out of the active docs surface without deleting them.

Use `git mv`, not delete/recreate, so history remains clear.

Recommended archive directories:

```text
docs/archive/2026-06-11/
code_review/archive/2026-06-11/
```

Add a short archive README if useful:

```text
docs/archive/README.md
code_review/archive/README.md
```

### Archive Candidates

Archive these categories:

- old review files
- old TODO/spec files tied to already-completed phases
- replies/responses files
- typo variants such as `repsonses*.md`
- old code-review support artifacts under `code_review/`

The whole current top-level `code_review/` content appears historical. It is acceptable to move all current `code_review/*` files into `code_review/archive/2026-06-11/`, leaving only an archive README at `code_review/README.md` or `code_review/archive/README.md`.

For `docs/`, be more conservative. Keep active/current operator or design references at top level, such as:

- `JOB_EXECUTION_MODEL.md`
- `LLM_WIZARD_DESIGN.md`
- `LLM_WIZARD_OPERATOR_GUIDE.md`
- `MODEL_KICAD_CORPUS.md` if still used as a current reference
- `ORIENTATION_CONVENTIONS.md`
- `PLACEHOLDER_SYMBOL_SPEC.md`
- this new spec/TODO pair

Archive old phase-specific files such as:

- `PYTHON_TEST_HOME_ISOLATION*`
- `UIUX*`
- `UI_FIXES*`
- `WEB_APP_MIGRATION*`
- old `WEB_APP_CODE_REVIEW_FIX*` after this new pair exists
- old `replies*`, `responses*`, and `repsonses*`
- old one-off patch instruction docs that are no longer current

Before committing, run a grep for links to moved docs and update any active top-level docs that link to archived files.

### Tests / Validation

Archiving docs does not require code tests, but it should satisfy:

- `git status` shows moves, not mass delete/add churn where avoidable.
- Active docs directory is materially cleaner.
- README links are not broken for active workflows.
- The new spec/TODO remain easy to find.

## Acceptance Criteria

The patch is complete when all of the following are true:

1. `get_llm_client()` is a generator dependency and closes closable clients after request completion.
2. Direct validation returns HTTP 200 with `valid: false` and structured errors for user-correctable invalid Circuit IR.
3. The frontend renders validation failures from the normal validation response instead of treating them as API exceptions.
4. The frontend clears stale validation state when JSON or symbols directory changes.
5. `frontend/src/api.ts` has been replaced by a domain-split API package, while existing `api` and `ApiError` imports still work.
6. `requestJson()` no longer sends `Content-Type: application/json` on GET requests without a body.
7. Historical markdown/review files are archived via `git mv`, not deleted.
8. All relevant tests pass:

```bash
uv run ruff check .
uv run pytest tests/web -q
cd frontend
npm run lint
npm run test:run
npm run build
```

If the full backend test suite is normally required in this repo, also run:

```bash
uv run pytest -q
```

## Suggested Commit Breakdown

Use small commits so failures are easy to bisect:

1. `web: close llm clients from dependency finalizer`
2. `web: return structured invalid netlist validation results`
3. `frontend: split api module by domain`
4. `docs: archive historical review and todo files`
