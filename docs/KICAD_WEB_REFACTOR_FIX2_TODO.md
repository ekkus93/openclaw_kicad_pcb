# KiCad PCB Web App Refactor Fix 2 TODO

## Phase 0 — Baseline

- [ ] Start from the latest refactored codebase.
- [ ] Run baseline checks and record current failures, if any:
  - [ ] `uv run pytest tests/web -q`
  - [ ] `uv run ruff check .`
  - [ ] `cd frontend && npm run lint`
  - [ ] `cd frontend && npm run test:run`
  - [ ] `cd frontend && npm run build`
- [ ] Do not start unrelated refactors in this patch.

## Phase 1 — LLM HTTP Client Lifecycle

- [ ] Update `src/kicad_pcb_web/deps.py`.
  - [ ] Import `Generator` from `collections.abc`.
  - [ ] Convert `get_llm_client()` to a generator dependency.
  - [ ] Build the client before `yield`.
  - [ ] Yield `None` when LLM is disabled.
  - [ ] In `finally`, call `client.close()` only if the yielded client has a callable `close` attribute.
  - [ ] Do not require `close()` on every `LlmClient` fake/test implementation.

- [ ] Add backend tests for the dependency lifecycle.
  - [ ] Closable fake client is closed after successful request/dependency use.
  - [ ] Closable fake client is closed after an exception path.
  - [ ] Non-closable fake client does not crash finalization.
  - [ ] Disabled LLM yields `None` and performs no close call.

- [ ] Run targeted backend tests.
  - [ ] `uv run pytest tests/web -q`

## Phase 2 — Direct Netlist Validation Contract

- [ ] Update `src/kicad_pcb_web/schemas.py`.
  - [ ] Add a `ValidationIssue` Pydantic model.
  - [ ] Add `errors: list[ValidationIssue]` to `ValidateNetlistResponse`.
  - [ ] Change `component_count` to `int | None = None`.
  - [ ] Change `net_count` to `int | None = None`.
  - [ ] Keep `warnings` and `symbols_dirs_used` defaulting to empty lists.

- [ ] Update `src/kicad_pcb_web/services/netlists.py`.
  - [ ] Add a helper to convert `UserError` to `ValidationIssue` using `kicad_error_to_payload()`.
  - [ ] Update `validate_netlist_dict()` so valid input returns `valid=True` with counts/warnings/symbol dirs.
  - [ ] Catch `UserError` from the validation path and return `valid=False` with a non-empty `errors` array.
  - [ ] Do not catch broad `Exception` in `validate_netlist_dict()`.
  - [ ] Ensure invalid validation responses do not leak absolute temp paths or private filesystem prefixes.
  - [ ] Preserve `symbols_dirs_used` where safely available.

- [ ] Keep `src/kicad_pcb_web/routes/api_netlists.py` returning `ValidateNetlistResponse` normally.
  - [ ] Do not raise `HTTPException` for normal invalid Circuit IR validation.
  - [ ] Keep malformed request-body behavior unchanged.

- [ ] Add or update backend validation tests.
  - [ ] Valid Circuit IR returns HTTP 200 and `valid: true`.
  - [ ] Invalid user-correctable Circuit IR returns HTTP 200 and `valid: false`.
  - [ ] Invalid response includes `errors.length >= 1`.
  - [ ] Invalid response has `component_count === null` and `net_count === null`, unless a count is truly known.
  - [ ] Invalid response contains sanitized public error details only.

## Phase 3 — Frontend Validation UI Updates

- [ ] Update `frontend/src/types.ts`.
  - [ ] Add a `ValidationIssue` interface.
  - [ ] Add `errors: ValidationIssue[]` to `ValidateNetlistResponse`.
  - [ ] Change `component_count` to `number | null`.
  - [ ] Change `net_count` to `number | null`.

- [ ] Update `frontend/src/routes/JsonGeneratePage.tsx`.
  - [ ] Treat `valid: false` validate responses as normal validation results.
  - [ ] Render validation errors in the validation result panel.
  - [ ] Show `—` or `Unknown` when counts are null.
  - [ ] Ensure `Generate KiCad Project` remains unavailable when `valid` is false.
  - [ ] Add a `handleSymbolsDirChange()` handler.
  - [ ] Clear `validationResult` and `errorMessage` when Symbols Directory changes.
  - [ ] Keep existing JSON text change behavior that clears stale validation.

- [ ] Add frontend tests.
  - [ ] Mock `api.validateNetlist()` returning `valid: false`.
  - [ ] Assert the page shows “Validation Failed”.
  - [ ] Assert validation error messages render.
  - [ ] Assert no generate action is available for invalid validation.
  - [ ] Assert changing Symbols Directory clears a prior successful validation result.

## Phase 4 — Split Frontend API Module by Domain

- [ ] Replace `frontend/src/api.ts` with an API package directory.
  - [ ] Create `frontend/src/api/client.ts`.
  - [ ] Create `frontend/src/api/bootstrap.ts`.
  - [ ] Create `frontend/src/api/doctor.ts`.
  - [ ] Create `frontend/src/api/jobs.ts`.
  - [ ] Create `frontend/src/api/netlists.ts`.
  - [ ] Create `frontend/src/api/symbols.ts`.
  - [ ] Create `frontend/src/api/wizard.ts`.
  - [ ] Create `frontend/src/api/index.ts`.
  - [ ] Remove `frontend/src/api.ts` after imports resolve through `frontend/src/api/index.ts`.

- [ ] Move shared API client code.
  - [ ] Move `ApiError` into `api/client.ts`.
  - [ ] Move `requestJson<T>()` into `api/client.ts`.
  - [ ] Move JSON stringification helper into `api/client.ts` as `jsonBody()` or equivalent.

- [ ] Fix request headers.
  - [ ] Only set `Content-Type: application/json` when `init.body` is present.
  - [ ] Preserve caller-supplied headers.
  - [ ] Keep structured error parsing behavior.
  - [ ] Keep FastAPI validation error message extraction behavior.

- [ ] Move endpoint functions into domain files.
  - [ ] `bootstrap.ts`: `getBootstrap()`.
  - [ ] `doctor.ts`: `getDoctor()`.
  - [ ] `jobs.ts`: `getJobs()`, `getJob()`, and optionally `createJobFromNetlist()`.
  - [ ] `netlists.ts`: `validateNetlist()` and optionally `createJobFromNetlist()`.
  - [ ] `symbols.ts`: `searchSymbols()`.
  - [ ] `wizard.ts`: all wizard session/message/spec/IR/project functions.

- [ ] Preserve public API shape in `api/index.ts`.
  - [ ] Export `ApiError`.
  - [ ] Export `api` with the same method names used today.
  - [ ] Ensure existing imports from `../api` and `./api` still compile.

- [ ] Run frontend checks.
  - [ ] `cd frontend && npm run lint`
  - [ ] `cd frontend && npm run test:run`
  - [ ] `cd frontend && npm run build`

## Phase 5 — Archive Historical Markdown/Review Files

- [ ] Create archive directories.
  - [ ] `docs/archive/2026-06-11/`
  - [ ] `code_review/archive/2026-06-11/`

- [ ] Archive `code_review/` historical files.
  - [ ] Move existing top-level `code_review/*` files into `code_review/archive/2026-06-11/` with `git mv`.
  - [ ] Leave a small `code_review/README.md` or `code_review/archive/README.md` explaining that historical review artifacts live under dated archive directories.

- [ ] Archive old docs in `docs/` conservatively.
  - [ ] Move old phase-specific specs/TODOs that are no longer active.
  - [ ] Move old `replies*.md`, `responses*.md`, and `repsonses*.md` files.
  - [ ] Move old one-off patch/instruction docs that are superseded.
  - [ ] Keep current operator/design references at top level.
  - [ ] Keep this new spec/TODO pair at top level.

- [ ] Check and update links.
  - [ ] Grep active docs and README for moved filenames.
  - [ ] Update links to archived paths where references are still useful.
  - [ ] Remove obsolete links where the old docs are no longer relevant.

- [ ] Verify archive behavior.
  - [ ] `git status` should show mostly renames/moves, not delete/add churn.
  - [ ] Top-level `docs/` should be cleaner and focused on active references.

## Phase 6 — Final Validation

- [ ] Run backend validation.
  - [ ] `uv run ruff check .`
  - [ ] `uv run pytest tests/web -q`
  - [ ] If normally expected for this repo: `uv run pytest -q`

- [ ] Run frontend validation.
  - [ ] `cd frontend && npm run lint`
  - [ ] `cd frontend && npm run test:run`
  - [ ] `cd frontend && npm run build`

- [ ] Manual smoke test the direct JSON flow.
  - [ ] Load the app.
  - [ ] Paste valid Circuit IR JSON.
  - [ ] Validate successfully.
  - [ ] Change Symbols Directory and confirm validation result clears.
  - [ ] Validate invalid Circuit IR JSON and confirm the page shows “Validation Failed” with errors, without a top-level API exception banner.
  - [ ] Confirm Generate is unavailable after invalid validation.
  - [ ] Validate valid JSON again and generate a KiCad project.

- [ ] Manual smoke test the wizard flow with LLM enabled.
  - [ ] Create a wizard session.
  - [ ] Send one follow-up message.
  - [ ] Generate IR.
  - [ ] Confirm no socket/resource leak warnings appear in logs.

## Done Definition

- [ ] Accepted design decisions are implemented exactly as specified.
- [ ] No unrelated architecture changes are included.
- [ ] New/updated tests cover the changed behavior.
- [ ] Frontend and backend checks pass.
- [ ] Historical docs are archived, not deleted.
- [ ] The code remains easy for the next review pass to inspect.
