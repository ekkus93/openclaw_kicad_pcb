# WEB_APP_CODE_REVIEW_FIX_TODO.md

# KiCad PCB Web App Code Review Fix TODO

This TODO implements the follow-up fixes from the web app code review.

The current branch already contains the initial FastAPI web app migration. Do **not** redo the migration. This is a targeted stabilization and hardening pass.

---

## Task 0: Confirm branch and baseline

Status: DONE

### 0.1 Confirm current branch

Status: DONE

Run:

```bash
git branch --show-current
```

Expected output:

```text
webapp
```

If the current branch is not `webapp`, stop and switch to it:

```bash
git checkout webapp
```

### 0.2 Check current working tree

Status: DONE

Run:

```bash
git status
```

Expected result:

- Clean working tree, or
- Only intentional uncommitted changes.

If there are unrelated local changes, commit or stash them before continuing.

### 0.3 Run baseline web tests

Status: DONE

Run:

```bash
uv sync --extra dev --extra web
uv run pytest tests/web -q
```

If `uv` cannot download the pinned Python in the current environment, document the issue and run with the available Python:

```bash
PYTHONPATH=src pytest tests/web -q
```

Do not start this patch until you know whether the current web tests are passing.

---

## Task 1: Fix structured KiCad/domain error handling

Status: DONE

### 1.1 Inspect existing error classes

Status: DONE

Inspect:

```text
src/kicad_pcb/errors.py
src/kicad_pcb_web/errors.py
src/kicad_pcb_web/main.py
src/kicad_pcb_web/services/netlists.py
```

Identify the actual inheritance and fields for:

```text
KiCadError
UserError
ToolError
```

Do not guess field names. Use the real classes.

### 1.2 Add a generic KiCad error payload helper

Status: DONE

In:

```text
src/kicad_pcb_web/errors.py
```

add a helper that converts `KiCadError` and subclasses to the existing structured payload format.

Expected shape:

```python
def kicad_error_to_payload(exc: KiCadError) -> dict[str, object]:
    return {
        "error": {
            "type": "...",
            "code": "...",
            "message": "...",
            "details": {...},
        }
    }
```

Requirements:

- Preserve `code` if the exception exposes one.
- Preserve `details` if the exception exposes them.
- Preserve a useful message.
- Avoid leaking arbitrary absolute filesystem paths in generic unexpected errors.
- Preserve useful domain details for user-actionable tool errors such as missing `kicad-cli`.

### 1.3 Keep `UserError` mapping compatible

Status: DONE

If `user_error_to_payload()` already exists, keep it working.

It is acceptable for `user_error_to_payload()` to call the broader `kicad_error_to_payload()` helper internally.

### 1.4 Register a `KiCadError` exception handler

Status: DONE

In:

```text
src/kicad_pcb_web/main.py
```

register a handler for `KiCadError`.

Suggested HTTP behavior:

```text
UserError → 400
ToolError → 400 or 503 depending on existing semantics
Other KiCadError → 400 unless clearly server-side
RequestValidationError → 422
Unexpected Exception → 500 generic
```

Prefer consistency with the existing error model.

### 1.5 Handle `KiCadError` in job generation

Status: DONE

In:

```text
src/kicad_pcb_web/services/netlists.py
```

update `generate_project_from_netlist_job()` so it catches `KiCadError` before generic `Exception`.

Required behavior:

- Mark the job as `failed`.
- Store the structured domain error in `job.json`.
- Return or raise consistently with the current route behavior.
- Do not record missing `kicad-cli` as `INTERNAL_SERVER_ERROR`.

### 1.6 Log unexpected exceptions

Status: DONE

If unexpected exceptions are converted to failed jobs, log them server-side using Python logging.

Do not expose tracebacks in API responses.

---

## Task 2: Change web default validation to internal

Status: DONE

### 2.1 Update request schema default

Status: DONE

In:

```text
src/kicad_pcb_web/schemas.py
```

change the default generation validation mode from:

```python
validation: str = "kicad"
```

to:

```python
validation: str = "internal"
```

Only do this for the web request schema. Do not change CLI defaults unless explicitly required.

### 2.2 Verify browser UI behavior

Status: DONE

Inspect:

```text
src/kicad_pcb_web/static/app.js
src/kicad_pcb_web/templates/index.html
```

Ensure the browser generate flow either:

- sends `validation: "internal"`, or
- omits validation and relies on the schema default.

Avoid inconsistent UI/API defaults.

### 2.3 Optional: add visible validation mode control

Status: SKIPPED

If adding a validation mode selector is simple and low risk, add one with options:

```text
internal
kicad
lint
```

If the actual valid modes differ, use the existing core validation mode names.

Do not add this if it expands the patch too much.

---

## Task 3: Stop exposing private `job.json` as an artifact

Status: DONE

### 3.1 Find where `job.json` is copied or written into artifacts

Status: DONE

Inspect:

```text
src/kicad_pcb_web/services/jobs.py
src/kicad_pcb_web/services/artifacts.py
src/kicad_pcb_web/services/netlists.py
```

Find any code that writes:

```text
<job_dir>/artifacts/job.json
```

or includes `job.json` in public artifact lists.

### 3.2 Remove public artifact copy

Status: DONE

Keep private metadata here:

```text
data/jobs/<job_id>/job.json
```

Remove any code that writes or copies:

```text
data/jobs/<job_id>/artifacts/job.json
```

### 3.3 Ensure artifact listing excludes private metadata

Status: DONE

Update artifact listing logic so `job.json` is not returned as an artifact.

The public artifact list should usually include files like:

```text
project.zip
warnings.json
debug.json
job_summary.json, only if intentionally sanitized
```

### 3.4 Optional: create sanitized `job_summary.json`

Status: SKIPPED

Only if needed, create:

```text
artifacts/job_summary.json
```

Rules:

- No absolute filesystem paths.
- No private server-only fields.
- No raw request secrets.
- Include only safe public fields.

Acceptable fields:

```text
job_id
status
project_name
created_at
updated_at
artifacts
warnings_count
```

### 3.5 Verify direct download behavior

Status: DONE

After the fix:

```text
GET /api/jobs/<job_id>/artifacts/job.json
```

must return 404 unless there is an intentional sanitized public artifact with that exact name. The preferred behavior is 404.

---

## Task 4: Add package-data config for bundled symbols

Status: DONE

### 4.1 Confirm bundled symbol path

Status: DONE

Inspect:

```text
src/kicad_pcb/resources/symbols/
```

Confirm the actual files and extensions, especially:

```text
*.kicad_sym
```

### 4.2 Update `pyproject.toml`

Status: DONE

Add package-data configuration.

Recommended:

```toml
[tool.setuptools.package-data]
kicad_pcb = ["resources/symbols/*.kicad_sym"]
```

If there is already package-data configuration, merge this into it without deleting existing entries.

### 4.3 Verify importlib resources can see bundled symbols

Status: DONE

Add a unit test or simple runtime check using `importlib.resources`.

Example shape:

```python
from importlib import resources

def test_bundled_symbol_resources_visible() -> None:
    symbol_dir = resources.files("kicad_pcb").joinpath("resources", "symbols")
    names = {p.name for p in symbol_dir.iterdir()}
    assert any(name.endswith(".kicad_sym") for name in names)
```

Adjust for the actual package resource structure.

---

## Task 5: Fix empty symbol search response code

Status: DONE

### 5.1 Update route validation

Status: DONE

In:

```text
src/kicad_pcb_web/routes/api_symbols.py
```

do not use FastAPI `min_length=1` for query `q`.

Change from a pattern like:

```python
q: str = Query(..., min_length=1)
```

to a pattern like:

```python
q: str = Query("")
```

### 5.2 Validate in service layer

Status: DONE

In:

```text
src/kicad_pcb_web/services/symbols.py
```

ensure empty or whitespace-only query raises a domain/user error that maps to HTTP 400.

Example:

```python
if not query.strip():
    raise UserError(...)
```

Use the actual `UserError` constructor signature from the core package.

### 5.3 Add or update test

Status: DONE

Add a web test:

```python
def test_empty_symbol_search_returns_400(client):
    response = client.get("/api/symbols/search?q=")
    assert response.status_code == 400
    payload = response.json()
    assert payload["error"]["type"] in {"user_error", "validation_error"}
```

Use the existing test client fixture style.

---

## Task 6: Improve job detail UI

Status: DONE

### 6.1 Update `job_detail.html`

Status: DONE

In:

```text
src/kicad_pcb_web/templates/job_detail.html
```

create explicit sections for:

```text
Job Status
Timestamps
Project Name
Error Details
Result Summary
Warnings
Diagnostics / Debug
Artifacts
Raw JSON
```

### 6.2 Render warnings outside raw JSON

Status: DONE

If warnings are present in the job result, render them as a list or table.

Do not make users inspect raw JSON to find warnings.

### 6.3 Render diagnostics/debug outside raw JSON

Status: DONE

If `debug.json` exists or diagnostics are present in the result, render:

```text
debug artifact link
diagnostic summary if available
```

Do not embed enormous debug JSON directly by default. A link is acceptable.

### 6.4 Keep raw JSON available for debugging

Status: DONE

Keep raw JSON in a collapsible `<details>` block or clearly labeled debug section.

---

## Task 7: Improve Generate button artifact links

Status: DONE

### 7.1 Update frontend generate result renderer

Status: DONE

In:

```text
src/kicad_pcb_web/static/app.js
```

after successful `POST /api/jobs/from-netlist`, render:

```text
job detail page link
artifact download links
```

### 7.2 Use API artifact URLs only

Status: DONE

Artifact links must use:

```text
/api/jobs/<job_id>/artifacts/<artifact_name>
```

Do not use local filesystem paths.

### 7.3 Handle no-artifact cases cleanly

Status: DONE

If there are no artifacts yet, display:

```text
No artifacts available yet.
```

Do not crash the frontend.

---

## Task 8: Rewrite README webapp framing

Status: DONE

### 8.1 Update README introduction

Status: DONE

In:

```text
README.md
```

rewrite the top section so it describes the current branch as a Python web app.

Required language or equivalent:

```text
This branch provides a Python FastAPI web app for deterministic KiCad project generation from Circuit IR JSON.
The web app does not require OpenClaw or an LLM.
```

### 8.2 Preserve web run instructions

Status: DONE

Ensure README includes:

```bash
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

### 8.3 Document data directory

Status: DONE

Ensure README documents:

```text
KICAD_PCB_WEB_DATA_DIR
./data default
data/jobs/<job_id>/
```

### 8.4 Document local-only default

Status: DONE

Ensure README states:

```text
The app binds to 127.0.0.1 by default.
Do not expose it publicly without authentication and additional sandboxing.
```

### 8.5 Move OpenClaw/LLM content into legacy framing

Status: DONE

Any OpenClaw or LLM discussion should be clearly marked as:

```text
legacy
archived
optional future integration
not required by the web app
```

Do not delete useful historical notes unless they are actively misleading.

---

## Task 9: Revert unrelated regression-threshold changes

Status: DONE

### 9.1 Inspect regression guardrail diff

Status: DONE

Inspect:

```text
tests/unit/test_phase7_regression_guardrails.py
```

Look for unrelated threshold changes such as:

```text
0.05 → 0.07
0.72 → 0.78
0.16 → 0.22
```

### 9.2 Revert threshold weakening

Status: SKIPPED

If those threshold changes are present and not justified by a deliberate core engine change, revert them to the prior values.

This patch is about the web app. It should not weaken core layout/readability regression tests.

### 9.3 If not reverting, document the reason

Status: DONE

If a threshold must remain changed, add a concise code comment explaining:

```text
what behavior changed
why the new threshold is correct
which fixture or engine change requires it
```

Do not leave unexplained threshold weakening.

---

## Task 10: Clean up stale post-migration directories

Status: DONE

### 10.1 Inspect leftover `kicad-pcb/`

Status: DONE

Run:

```bash
find kicad-pcb -maxdepth 4 -type f | sort
```

If `kicad-pcb/` does not exist, record that this task is already complete.

### 10.2 Remove stale duplicate tests

Status: DONE

If there are stale duplicate tests under:

```text
kicad-pcb/tests/
```

and the active tests live under:

```text
tests/
```

remove the stale duplicate directory:

```bash
git rm -r kicad-pcb/tests
```

Only remove files after confirming they are duplicates or obsolete.

### 10.3 Preserve legacy OpenClaw archive

Status: DONE

Do not remove:

```text
legacy/openclaw-skill/
```

That directory intentionally preserves the old OpenClaw skill files.

### 10.4 Remove empty `kicad-pcb/` if applicable

Status: DONE

If `kicad-pcb/` becomes empty after cleanup:

```bash
rmdir kicad-pcb
```

If not empty, leave it and document why.

---

## Task 11: Add web regression tests for this patch

Status: DONE

### 11.1 Add `test_web_error_handling.py`

Status: DONE

Create:

```text
tests/web/test_web_error_handling.py
```

Test at least:

1. Explicit KiCad validation with missing `kicad-cli` returns a structured domain/tool error.
2. The error code is not `INTERNAL_SERVER_ERROR`.
3. The job is marked `failed`.

Use monkeypatching where needed to simulate a `ToolError` without relying on the local system actually missing `kicad-cli`.

### 11.2 Add `test_web_artifact_privacy.py`

Status: DONE

Create or update:

```text
tests/web/test_web_artifact_privacy.py
```

Test:

1. Successful job artifact list does not include `job.json`.
2. `GET /api/jobs/<job_id>/artifacts/job.json` returns 404.
3. `project.zip` remains downloadable.
4. Existing path traversal rejection still works.

### 11.3 Update symbol search tests

Status: DONE

Update:

```text
tests/web/test_web_symbols.py
```

Add:

```text
GET /api/symbols/search?q= returns 400
```

### 11.4 Add package resource test

Status: DONE

Create or update:

```text
tests/unit/test_package_resources.py
```

Test that bundled `.kicad_sym` resources are discoverable.

### 11.5 Optional UI contract test

Status: DONE

If feasible, add a simple template-level or route-level test that:

```text
GET /jobs/<job_id>
```

contains the headings:

```text
Warnings
Diagnostics
Artifacts
```

Do not overcomplicate browser testing.

---

## Task 12: Re-run validation commands

Status: DONE

### 12.1 Run import checks

Status: DONE

Run:

```bash
uv run python -c "from kicad_pcb_web.main import app; print(app.title)"
uv run python -c "from kicad_pcb.cli import main; print('cli import ok')"
```

Expected:

```text
KiCad PCB Web App
cli import ok
```

### 12.2 Run web tests

Status: DONE

Run:

```bash
uv run pytest tests/web -q
```

Expected:

```text
all tests pass
```

### 12.3 Run unit tests

Status: DONE

Run:

```bash
uv run pytest tests/unit -q
```

If integration/system dependencies are unavailable, document which tests are skipped or fail due to environment.

### 12.4 Run lint/type checks

Status: DONE

Run:

```bash
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web || true
```

If mypy has existing known failures, do not hide new errors caused by this patch.

### 12.5 Verify no core-to-web imports

Status: DONE

Run:

```bash
grep -R "kicad_pcb_web" -n src/kicad_pcb tests || true
```

The core package must not import the web package.

Allowed direction:

```text
kicad_pcb_web imports kicad_pcb
```

Forbidden direction:

```text
kicad_pcb imports kicad_pcb_web
```

---

## Task 13: Manual QA

Status: DONE

### 13.1 Start server

Status: DONE

Run:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

Open:

```text
http://127.0.0.1:8000/
```

### 13.2 Browser checks

Status: DONE

Verify:

1. Page loads.
2. Doctor panel loads.
3. Symbol search works.
4. Empty symbol search shows a structured error.
5. JSON file upload fills the Circuit IR textarea.
6. Validate button works.
7. Generate button creates a job using internal validation by default.
8. Generate result shows job detail link.
9. Generate result shows artifact links.
10. Job detail page shows Warnings section.
11. Job detail page shows Diagnostics/Debug section.
12. Job detail page shows artifact download links.
13. `project.zip` downloads.
14. `job.json` is not shown as a downloadable artifact.
15. Direct `/api/jobs/<job_id>/artifacts/job.json` returns 404.

### 13.3 API checks

Status: DONE

Run:

```bash
curl -s http://127.0.0.1:8000/api/doctor | jq .
curl -i 'http://127.0.0.1:8000/api/symbols/search?q='
curl -s 'http://127.0.0.1:8000/api/symbols/search?q=resistor' | jq .
```

Verify:

```text
doctor returns JSON
empty symbol search returns HTTP 400
resistor search returns results or a valid empty result set
```

---

## Task 14: Update migration documents status

Status: DONE

### 14.1 Do not mark old TODO as fully correct without fixes

Status: DONE

Do not edit `WEB_APP_MIGRATION_TODO.md` to claim everything was correct unless the fixes in this TODO are complete.

### 14.2 Add this review-fix TODO to the repo root

Status: DONE

Ensure this file exists at repo root:

```text
WEB_APP_CODE_REVIEW_FIX_TODO.md
```

### 14.3 Add this review-fix spec to the repo root

Status: DONE

Ensure this file exists at repo root:

```text
WEB_APP_CODE_REVIEW_FIX_SPEC.md
```

---

## Task 15: Commit the patch

Status: DONE

### 15.1 Review diff before commit

Status: DONE

Run:

```bash
git diff --stat
git diff
```

Verify the diff is limited to:

```text
web error handling
web schemas/routes/services
artifact privacy
package data
README
frontend UI
templates
tests
stale directory cleanup
optional regression threshold revert
```

The diff must not contain unrelated core engine rewrites.

### 15.2 Commit

Status: DONE

Recommended single commit:

```bash
git add -A
git commit -m "Fix web app error handling and artifact safety"
```

If the README/stale cleanup changes are large, use two commits:

```bash
git add src/kicad_pcb_web tests pyproject.toml
git commit -m "Fix web app error handling and artifact safety"

git add README.md kicad-pcb tests/unit/test_phase7_regression_guardrails.py
git commit -m "Clarify web app docs and cleanup migration leftovers"
```

---

## Final acceptance checklist

Status: DONE

The patch is complete only when all of these are true:

- [x] Current branch is `webapp`.
- [x] `WEB_APP_CODE_REVIEW_FIX_SPEC.md` exists at repo root.
- [x] `WEB_APP_CODE_REVIEW_FIX_TODO.md` exists at repo root.
- [x] `kicad_pcb_web.main:app` imports successfully.
- [x] CLI import still works.
- [x] `GET /api/doctor` returns JSON.
- [x] `GET /api/symbols/search?q=resistor` works.
- [x] `GET /api/symbols/search?q=` returns HTTP 400.
- [x] `POST /api/netlists/validate` works.
- [x] `POST /api/jobs/from-netlist` works without requiring `kicad-cli` by default.
- [x] Explicit KiCad validation with missing `kicad-cli` returns a structured tool/domain error, not `INTERNAL_SERVER_ERROR`.
- [x] Failed jobs preserve structured error information.
- [x] `job.json` remains private under `data/jobs/<job_id>/job.json`.
- [x] `job.json` is not listed as a downloadable artifact.
- [x] `/api/jobs/<job_id>/artifacts/job.json` returns 404.
- [x] `project.zip` remains downloadable.
- [x] Artifact path traversal is still rejected.
- [x] Bundled `.kicad_sym` files are included as package data.
- [x] Job detail page clearly shows warnings.
- [x] Job detail page clearly shows diagnostics/debug information.
- [x] Generate button output renders direct artifact links.
- [x] README states the web app is LLM-free.
- [x] README clearly marks OpenClaw/LLM support as legacy or optional.
- [x] Unrelated regression-threshold changes are reverted or explicitly justified.
- [x] Stale `kicad-pcb/tests/` leftovers are removed or moved.
- [x] Web tests pass.
- [x] Unit tests pass, or environment-only failures are documented.
- [x] Core package does not import `kicad_pcb_web`.
