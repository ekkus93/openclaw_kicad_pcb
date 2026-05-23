# WEB_APP_CODE_REVIEW_FIX_SPEC.md

# KiCad PCB Web App Code Review Fix Specification

## Purpose

This specification defines the required stabilization patch for the current `webapp` branch of the KiCad PCB project.

Copilot already implemented the first web app migration. This patch must **not** redesign the web app, rewrite the core KiCad engine, or restart the migration from scratch. This is a targeted code-review fix pass.

The goal is to make the current FastAPI/Jinja web app safer, more deterministic, clearer to users, and better aligned with the original migration TODO.

## Current branch intent

The `webapp` branch should be a Python web app that wraps the existing deterministic KiCad project generation engine.

The required v1 behavior is:

```text
Circuit IR JSON
   ↓
schema/internal validation
   ↓
symbol lookup
   ↓
layout/routing
   ↓
KiCad schematic/project generation
   ↓
warnings/debug artifacts
   ↓
browser/API download
```

The web app must **not** require:

```text
OpenClaw
an LLM
an agent runtime
external AI services
chat/session prompt context
```

LLM-assisted features may be added later, but they must be optional and must not be part of the v1 web app dependency chain.

## Existing implementation summary

The current implementation already has:

```text
src/kicad_pcb/
src/kicad_pcb_web/
legacy/openclaw-skill/
tests/web/
WEB_APP_MIGRATION_SPEC.md
WEB_APP_MIGRATION_TODO.md
```

The current implementation also has working basics:

```text
FastAPI app import
Jinja templates
static frontend assets
doctor endpoint
symbol search endpoint
netlist validation endpoint
job creation endpoint
job workspace directories
artifact listing/download endpoints
web tests
root src/ layout
```

This patch should preserve those.

## Non-goals

Do not do any of the following in this patch:

1. Do not move the project back from `src/` to `kicad-pcb/src/`.
2. Do not reintroduce OpenClaw runtime behavior into the web path.
3. Do not make the web app depend on an LLM.
4. Do not replace FastAPI.
5. Do not add a frontend framework.
6. Do not rewrite the layout/routing engine.
7. Do not make a new job system or database unless absolutely necessary.
8. Do not weaken unrelated regression tests.
9. Do not expose arbitrary filesystem paths.
10. Do not make `data/jobs/<job_id>/job.json` publicly downloadable.

## Required fixes

## 1. Fix domain error handling

### Problem

The current web generation service handles `UserError`, but it does not properly handle broader domain errors from the core package, especially `ToolError`.

For example, when generation uses KiCad validation mode and `kicad-cli` is not installed, `_apply_netlist_to_project()` may raise a `ToolError` such as:

```text
KICAD_CLI_MISSING
```

The web app currently records this as a generic internal error:

```json
{
  "type": "internal_error",
  "code": "INTERNAL_SERVER_ERROR",
  "message": "An unexpected server error occurred.",
  "details": {}
}
```

That is wrong. Missing external tools are user-actionable domain errors, not internal server crashes.

### Required behavior

The web layer must preserve structured domain errors from `kicad_pcb.errors`.

At minimum, handle:

```text
UserError
ToolError
KiCadError
```

Expected payload shape:

```json
{
  "error": {
    "type": "tool_error",
    "code": "KICAD_CLI_MISSING",
    "message": "kicad-cli is required for --mode kicad",
    "details": {
      "hint": "Install KiCad or use validation=internal"
    }
  }
}
```

Exact wording may vary based on the existing exception object, but the response must preserve:

```text
type
code
message
details
```

### Implementation guidance

Update:

```text
src/kicad_pcb_web/errors.py
src/kicad_pcb_web/main.py
src/kicad_pcb_web/services/netlists.py
```

Add helpers such as:

```python
def kicad_error_to_payload(exc: KiCadError) -> dict[str, object]:
    ...
```

The conversion should avoid leaking arbitrary absolute paths unless those paths are already explicitly user-provided and necessary for the error message.

In job generation, catch `KiCadError` before the generic `Exception` handler.

Do not swallow unexpected exceptions silently. If unexpected exceptions are converted into failed jobs, they should still be logged server-side.

## 2. Prefer internal validation by default in the web app

### Problem

The current web request schema defaults to:

```python
validation: str = "kicad"
```

This makes the default API behavior depend on the external `kicad-cli` executable. That is too brittle for a local v1 web app.

The browser UI already appears to submit `validation="internal"`, which means the API default and UI default are inconsistent.

### Required behavior

For the web app, default generation should use:

```python
validation: str = "internal"
```

Users may still request KiCad validation explicitly if the API/UI supports that option.

### Required docs

README and UI text should make clear:

```text
internal validation is the default
KiCad CLI validation is optional and requires KiCad/kicad-cli
```

## 3. Stop exposing private job metadata as an artifact

### Problem

The current implementation writes or copies:

```text
data/jobs/<job_id>/artifacts/job.json
```

This makes server metadata directly downloadable through:

```text
/api/jobs/<job_id>/artifacts/job.json
```

That is unsafe because `job.json` contains absolute local paths and internal state.

### Required behavior

Keep private canonical job metadata here:

```text
data/jobs/<job_id>/job.json
```

Do **not** copy it into:

```text
data/jobs/<job_id>/artifacts/
```

The artifact list must not include `job.json`.

The artifact download route must not return private metadata.

### Optional sanitized summary

If a downloadable job summary is useful, create a separate sanitized artifact:

```text
data/jobs/<job_id>/artifacts/job_summary.json
```

That file must contain only public/relative data, for example:

```json
{
  "job_id": "20260518_abcd1234",
  "status": "succeeded",
  "project_name": "Example",
  "artifacts": ["project.zip", "warnings.json", "debug.json"]
}
```

No absolute filesystem paths.

## 4. Package bundled KiCad symbol resources

### Problem

The current branch includes bundled symbols under:

```text
src/kicad_pcb/resources/symbols/
```

but `pyproject.toml` does not clearly include these non-Python `.kicad_sym` files as package data.

A built wheel or sdist may omit them.

### Required behavior

Update `pyproject.toml` so bundled symbol files are included.

Recommended:

```toml
[tool.setuptools.package-data]
kicad_pcb = ["resources/symbols/*.kicad_sym"]
```

Verify with a packaging-oriented test or at least a runtime test that `importlib.resources` can find the bundled symbols from the installed package layout.

## 5. Fix empty symbol search status code

### Problem

The TODO required:

```text
Empty query returns HTTP 400.
```

The current route likely uses:

```python
q: str = Query(..., min_length=1)
```

FastAPI returns `422 Unprocessable Entity` before the service can raise the domain error.

### Required behavior

This request:

```text
GET /api/symbols/search?q=
```

must return HTTP 400 with the structured web error payload.

Do not rely on FastAPI's request validation for this case.

### Implementation guidance

Change the route to accept an empty string and let the service validate it:

```python
q: str = Query("")
```

Then have the service raise `UserError` or another domain error that maps to HTTP 400.

## 6. Improve job detail UI sections

### Problem

The current job detail template mostly dumps raw JSON for result data. The TODO required separate sections for warnings and diagnostics.

### Required behavior

`job_detail.html` must include clear sections:

```text
Job Status
Timestamps
Project Name
Error Details, if failed
Result Summary, if succeeded
Warnings
Diagnostics / Debug
Artifacts
Raw JSON, optional collapsible/debug section
```

Warnings should not be hidden only inside raw JSON.

Diagnostics should be visible when `debug.json` or debug result metadata is present.

Artifact download links must be obvious.

## 7. Improve Generate button result UI

### Problem

After clicking Generate, the frontend links to the job page but does not directly render artifact links even when artifacts are available.

### Required behavior

After a successful `POST /api/jobs/from-netlist`, `static/app.js` must render:

```text
Job detail page link
Download project.zip link, if present
Download warnings.json link, if present
Download debug.json link, if present
Any other returned artifacts
```

The artifact URLs must use the public API route:

```text
/api/jobs/<job_id>/artifacts/<artifact_name>
```

The frontend must not construct filesystem paths.

## 8. Rewrite README framing for the web app branch

### Problem

The README still frames the project primarily as an OpenClaw/LLM skill.

That is misleading for the `webapp` branch.

### Required behavior

The README introduction must state:

```text
This branch provides a Python FastAPI web app for deterministic KiCad project generation from Circuit IR JSON.
The web app does not require OpenClaw or an LLM.
OpenClaw skill files are archived under legacy/openclaw-skill for reference.
```

The README must keep or add:

```text
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
KICAD_PCB_WEB_DATA_DIR
data/jobs/<job_id>/
local-only 127.0.0.1 default
do not expose publicly without authentication/sandboxing
```

Move LLM/OpenClaw discussion into a legacy or optional section.

## 9. Revert unrelated regression threshold changes unless justified

### Problem

The migration appears to have modified unrelated regression guardrail thresholds in:

```text
tests/unit/test_phase7_regression_guardrails.py
```

Examples observed during review:

```text
0.05 → 0.07
0.72 → 0.78
0.16 → 0.22
```

This web migration should not weaken schematic layout/readability regression tests unless there is a reviewed, documented engine reason.

### Required behavior

Either:

1. Revert these threshold changes.

or:

2. Add a clear comment and commit rationale explaining why the thresholds must change because of a deliberate engine behavior change.

Since this patch is not intended to change the core layout engine, the expected action is to revert.

## 10. Clean up stale directories after root `src/` migration

### Problem

The current branch moved active packages to:

```text
src/kicad_pcb/
src/kicad_pcb_web/
```

but may still contain stale leftovers under:

```text
kicad-pcb/tests/
```

That is confusing because active tests are under:

```text
tests/
```

### Required behavior

Remove stale leftover test directories that are no longer used, unless they contain unique fixtures or documentation that must be moved.

Do not remove:

```text
legacy/openclaw-skill/
```

That archive should remain.

## 11. Add missing tests

The patch must add tests for all behavior changes.

Required tests:

```text
tests/web/test_web_error_handling.py
tests/web/test_web_artifact_privacy.py
tests/web/test_web_symbols.py update or new cases
tests/web/test_web_ui_contract.py optional but preferred
tests/unit/test_package_resources.py or equivalent
```

At minimum, test:

1. Missing `kicad-cli`/`ToolError` produces a structured domain error, not `INTERNAL_SERVER_ERROR`.
2. Default web generation uses internal validation or otherwise does not require `kicad-cli`.
3. `job.json` is not listed as an artifact.
4. `/api/jobs/<job_id>/artifacts/job.json` returns 404 unless a sanitized public summary is intentionally created.
5. Empty symbol query returns HTTP 400.
6. Bundled symbol resources are package-visible.
7. Existing artifact path traversal tests still pass.
8. Existing CLI import still works.
9. Core package does not import `kicad_pcb_web`.

## Acceptance criteria

The patch is acceptable when all of the following are true:

```text
kicad_pcb_web.main:app imports successfully
GET /api/doctor returns JSON
GET /api/symbols/search?q=resistor works
GET /api/symbols/search?q= returns 400
POST /api/netlists/validate works
POST /api/jobs/from-netlist works without requiring kicad-cli by default
missing kicad-cli is reported as a structured tool/domain error when KiCad validation is explicitly requested
job.json is private and not downloadable as an artifact
project.zip remains downloadable
artifact path traversal is still rejected
bundled KiCad symbols are included as package data
job detail page clearly shows warnings and diagnostics
Generate button renders artifact links
README clearly states the web app is LLM-free
unrelated regression thresholds are reverted or explicitly justified
stale kicad-pcb/tests leftovers are removed or moved
web tests pass
existing unit tests pass or any remaining environment-only failures are documented
```

## Commands to run

Run these before marking the patch complete:

```bash
uv sync --extra dev --extra web
uv run python -c "from kicad_pcb_web.main import app; print(app.title)"
uv run pytest tests/web -q
uv run pytest tests/unit -q
uv run ruff check .
uv run mypy src/kicad_pcb src/kicad_pcb_web || true
```

If `uv` cannot run because the environment cannot download Python, document that explicitly and run the equivalent commands using the available Python environment.

## Commit guidance

Use a focused commit message such as:

```bash
git add -A
git commit -m "Fix web app error handling and artifact safety"
```

If the README/test cleanup is large, split into two commits:

```bash
git commit -m "Fix web app error handling and artifact safety"
git commit -m "Clarify web app documentation and cleanup migration leftovers"
```
