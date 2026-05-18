# WEB_APP_MIGRATION_SPEC.md

# KiCad PCB Web App Migration Specification

## 1. Goal

Convert this repository from an OpenClaw skill-oriented project into a Python web app while preserving the existing KiCad generation engine.

The web app must allow a user to:

1. Open a browser.
2. Paste or upload a Circuit IR JSON netlist.
3. Validate the netlist.
4. Search KiCad symbols.
5. Generate a KiCad project from the netlist.
6. View generation warnings and diagnostics.
7. Download generated KiCad artifacts.

The conversion must not rewrite the core schematic/layout/router/sexpr logic. The existing `kicad_pcb` package is the engine. The new web layer should call that engine through explicit service functions.

## 2. Current Repository Layout

The current repository has this important structure:

```text
.
├── pyproject.toml
├── uv.lock
├── README.md
├── AGENTS.md
├── memory.md
├── docs/
├── scripts/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
└── kicad-pcb/
    ├── SKILL.md
    ├── skill.json
    ├── _meta.json
    ├── scripts/
    │   └── kicad_pcb.py
    └── src/
        └── kicad_pcb/
            ├── cli.py
            ├── config.py
            ├── results.py
            ├── commands/
            ├── graphviz_layout/
            ├── ir/
            ├── lint/
            ├── sch_doc/
            ├── sexpr/
            ├── symbol_index.py
            ├── symbol_cache.py
            ├── layout.py
            ├── router.py
            └── ...
```

The OpenClaw-specific files are primarily:

```text
kicad-pcb/SKILL.md
kicad-pcb/skill.json
kicad-pcb/_meta.json
kicad-pcb/scripts/kicad_pcb.py
```

The reusable Python core is primarily:

```text
kicad-pcb/src/kicad_pcb/
```

The tests are already rooted at:

```text
tests/
```

The current `pyproject.toml` uses:

```toml
[tool.pytest.ini_options]
pythonpath = ["kicad-pcb/src"]

[tool.setuptools.packages.find]
where = ["kicad-pcb/src"]

[tool.coverage.run]
source = ["kicad-pcb/src"]
```

That means package discovery currently depends on the nested `kicad-pcb/src` layout.

## 3. Branch Requirement

All migration work must happen on a new git branch named:

```text
webapp
```

The first implementation step must be:

```bash
git status
git checkout master || git checkout main
git pull --ff-only || true
git checkout -b webapp
```

If the repository already has local uncommitted changes, stop and either commit/stash them before creating the branch.

If the `webapp` branch already exists locally, use:

```bash
git checkout webapp
```

Do not do web-app migration work directly on `master` or `main`.

## 4. Recommended Migration Strategy

Use a two-stage migration.

### Stage A: Low-risk web app layer

Initially keep the core package where it is:

```text
kicad-pcb/src/kicad_pcb/
```

Add the web app beside it:

```text
kicad-pcb/src/kicad_pcb_web/
```

This avoids a large package move before the web app works.

The immediate result should be:

```text
kicad-pcb/src/
├── kicad_pcb/
│   └── existing core package
└── kicad_pcb_web/
    ├── __init__.py
    ├── main.py
    ├── settings.py
    ├── schemas.py
    ├── errors.py
    ├── deps.py
    ├── services/
    │   ├── __init__.py
    │   ├── jobs.py
    │   ├── netlists.py
    │   ├── symbols.py
    │   ├── doctor.py
    │   ├── artifacts.py
    │   └── preview.py
    ├── routes/
    │   ├── __init__.py
    │   ├── ui.py
    │   ├── api_jobs.py
    │   ├── api_netlists.py
    │   ├── api_symbols.py
    │   └── api_doctor.py
    ├── templates/
    │   ├── base.html
    │   ├── index.html
    │   └── job_detail.html
    └── static/
        ├── app.css
        └── app.js
```

### Stage B: Optional cleanup after tests pass

After the web app works and tests pass, optionally move both packages to a normal root `src/` layout:

```text
src/
├── kicad_pcb/
└── kicad_pcb_web/
```

If Stage B is performed, use `git mv` so history is preserved:

```bash
mkdir -p src
git mv kicad-pcb/src/kicad_pcb src/kicad_pcb
git mv kicad-pcb/src/kicad_pcb_web src/kicad_pcb_web
```

Then update `pyproject.toml`:

```toml
[tool.pytest.ini_options]
pythonpath = ["src"]

[tool.setuptools.packages.find]
where = ["src"]

[tool.coverage.run]
source = ["src"]
```

The OpenClaw skill metadata can then be archived instead of deleted:

```bash
mkdir -p legacy/openclaw-skill
git mv kicad-pcb/SKILL.md legacy/openclaw-skill/SKILL.md
git mv kicad-pcb/skill.json legacy/openclaw-skill/skill.json
git mv kicad-pcb/_meta.json legacy/openclaw-skill/_meta.json
git mv kicad-pcb/scripts legacy/openclaw-skill/scripts
```

Do not delete OpenClaw files until the web app passes tests and the CLI compatibility decision has been made.

## 5. Python Package and Dependency Requirements

Keep Python 3.11+.

Add web dependencies to `pyproject.toml` under a new optional dependency group:

```toml
[project.optional-dependencies]
web = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
```

Keep the existing `dev` group.

If the project metadata is renamed, use a neutral name that no longer describes it as only an OpenClaw skill:

```toml
[project]
name = "kicad-pcb-webapp"
description = "Python web app for generating KiCad projects from Circuit IR"
```

Do not rename the Python package `kicad_pcb`; keep it stable so existing imports and tests continue working.

## 6. Runtime Model

The app must be local-first and single-user by default.

For v1, this is an internal/local app rather than a public-facing service.
Keep the existing path-safety and job-isolation rules, but do not assume
internet-facing deployment requirements such as authentication are part of the
initial migration scope.

Default bind address:

```text
127.0.0.1
```

Default port:

```text
8000
```

Default data directory:

```text
./data
```

Default jobs directory:

```text
./data/jobs
```

The app must never use global CLI state for web jobs.

Avoid web use of:

```python
get_current_project()
set_current_project()
get_current_session()
set_current_session()
```

Instead, every web request must use an explicit job workspace and explicit project path.

## 7. Job Workspace Layout

Each generation request must create an isolated job directory:

```text
data/
└── jobs/
    └── <job_id>/
        ├── job.json
        ├── input/
        │   └── circuit_ir.json
        ├── project/
        │   └── <project_name>/
        │       ├── <project_name>.kicad_pro
        │       ├── <project_name>.kicad_sch
        │       ├── <project_name>.kicad_pcb
        │       └── OpenClaw_Managed.kicad_sch
        ├── artifacts/
        │   ├── project.zip
        │   ├── job.json
        │   ├── warnings.json
        │   ├── debug.json
        │   └── preview.svg
```

`job_id` must be generated by the server, not accepted from the client.

Use a safe ID format such as:

```text
YYYYMMDD_HHMMSS_<8 hex chars>
```

or a UUID string.

Project names must be sanitized before being used as directory names. Only allow letters, digits, underscore, dash, and dot after normalization. Spaces should become underscores.

## 8. Job State

For v1, job metadata may be stored as JSON files inside each job directory.

The file must be:

```text
job.json
```

It must include:

```json
{
  "id": "...",
  "status": "queued|running|succeeded|failed|cancelled",
  "project_name": "...",
  "created_at": "...",
  "updated_at": "...",
  "work_dir": "...",
  "input_path": "...",
  "project_dir": "...",
  "artifacts_dir": "...",
  "request": {},
  "result": null,
  "error": null
}
```

Do not add SQLAlchemy in v1 unless needed. A file-backed job store is enough for a local single-user app.

The design may later move to SQLite, but do not start there unless the rest of the app is already working.

The root `job.json` is the canonical writable job-state file. A copy should also
be written to `artifacts/job.json` so downloads can remain scoped to the
artifacts directory.

## 9. Service Layer Requirement

Do not call `argparse` or the CLI parser from FastAPI routes.

Do not build fake `argparse.Namespace` objects in route handlers except as a temporary adapter during the first bridge commit.

Create a service layer under:

```text
kicad-pcb/src/kicad_pcb_web/services/
```

The service layer must be the boundary between the web app and the existing `kicad_pcb` engine.

Required services:

```text
services/jobs.py       # job IDs, workspace creation, job.json read/write
services/netlists.py   # validate/generate Circuit IR jobs
services/symbols.py    # symbol search wrappers
services/doctor.py     # dependency/config checks
services/artifacts.py  # artifact allowlisting, zipping, downloads
services/preview.py    # schematic/PCB preview generation wrappers
```

Eventually, move reusable command logic from `kicad_pcb.commands.*` into engine-level services that both CLI and web can call.

The desired long-term direction is:

```text
CLI parser       -> kicad_pcb service function -> engine
FastAPI endpoint -> kicad_pcb service function -> engine
```

Not:

```text
FastAPI endpoint -> fake argparse.Namespace -> CLI command -> engine
```

## 10. Web App Schemas

Add Pydantic v2 request/response models in:

```text
kicad-pcb/src/kicad_pcb_web/schemas.py
```

Required models:

```python
class ValidateNetlistRequest(BaseModel):
    netlist_json: dict[str, Any]
    symbols_dir: str | None = None

class ValidateNetlistResponse(BaseModel):
    valid: bool
    component_count: int
    net_count: int
    warnings: list[dict[str, Any]] = []
    symbols_dirs_used: list[str] = []

class CreateJobFromNetlistRequest(BaseModel):
    project_name: str
    netlist_json: dict[str, Any]
    symbols_dir: str | None = None
    validation: str = "kicad"
    layout: str | None = None
    routing: str | None = None
    heuristic_profile: str | None = None
    label_mode: str | None = None
    auto_fix: bool = True
    strict: bool = False

class JobSummary(BaseModel):
    id: str
    status: str
    project_name: str
    created_at: str
    updated_at: str

class JobDetail(JobSummary):
    request: dict[str, Any]
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    artifacts: list[str] = []

class SymbolSearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]

class DoctorResponse(BaseModel):
    ok: bool
    checks: list[dict[str, Any]]
```

Route handlers must return these typed schemas or simple `FileResponse` objects for downloads.

## 11. FastAPI Routes

Add the app entrypoint:

```text
kicad-pcb/src/kicad_pcb_web/main.py
```

It must expose:

```python
app = FastAPI(...)
```

Required UI routes:

```text
GET /
GET /jobs/{job_id}
```

Required API routes:

```text
GET  /api/doctor
GET  /api/symbols/search?q=<query>
POST /api/netlists/validate
POST /api/jobs/from-netlist
GET  /api/jobs
GET  /api/jobs/{job_id}
GET  /api/jobs/{job_id}/artifacts
GET  /api/jobs/{job_id}/artifacts/{artifact_name}
```

The first v1 implementation must run generation synchronously inside
`POST /api/jobs/from-netlist`. Even in synchronous mode, create a job and
persist `job.json` before doing work.

The endpoint must return the final job detail for the completed synchronous job.

## 12. UI Requirements

Use Jinja templates and simple static assets. Do not add React/Vue/Svelte in v1.

Required templates:

```text
templates/base.html
templates/index.html
templates/job_detail.html
```

Required static files:

```text
static/app.css
static/app.js
```

The home page must include:

1. Project name input.
2. Symbols directory input, optional.
3. Circuit IR JSON textarea.
4. Upload JSON file control.
5. Validate button.
6. Generate button.
7. Symbol search field.
8. Doctor/status panel.
9. Result/warnings panel.

The job detail page must include:

1. Job status.
2. Project path relative to the job workspace.
3. Component/net counts if available.
4. Warnings.
5. Diagnostics.
6. Artifact download links.
7. Preview image/link if available.

## 13. Artifact Rules

Downloads must be safe.

Never accept arbitrary filesystem paths from the browser.

Artifact download must use:

```text
job_id + artifact_name
```

Only serve files from:

```text
data/jobs/<job_id>/artifacts/
```

Only allow artifact names from an allowlist generated by scanning that job's artifacts directory.

Reject names containing:

```text
/
\
..
```

Required artifacts:

```text
project.zip
warnings.json
job.json
```

Optional artifacts:

```text
debug.json
preview.svg
preview.png
<project_name>.kicad_sch
OpenClaw_Managed.kicad_sch
```

The app should package the full generated KiCad project directory into `project.zip` after successful generation.

## 14. Error Handling

Convert existing `kicad_pcb.errors.UserError` exceptions into HTTP 400 responses with structured JSON:

```json
{
  "error": {
    "type": "user_error",
    "code": "...",
    "message": "...",
    "details": {}
  }
}
```

Unexpected exceptions should become HTTP 500 responses and should also be written into the job error field when they occur during generation.

Do not leak arbitrary local filesystem paths in browser-visible errors unless the path is inside the job workspace or explicitly configured by the user as `symbols_dir`.

## 15. Engine Refactoring Rules

Keep the existing `kicad_pcb` package as the authoritative engine.

Do not fork or duplicate:

```text
circuit_ir.py
ir/validate.py
symbol_index.py
symbol_cache.py
layout.py
router.py
sch_doc/
sexpr/
commands/_sch_apply.py
```

Instead, wrap or gradually extract from command modules.

High-priority extraction targets:

1. Netlist validation currently in `commands/netlist.py`.
2. New project creation currently in `commands/_project.py`.
3. New-from-netlist orchestration currently in `commands/netlist.py`.
4. Symbol search currently in `commands/search.py`.
5. Doctor checks currently in `commands/doctor.py`.
6. Preview/export logic currently in `commands/preview.py` and `commands/export.py`.

When extracting, preserve CLI behavior by making existing CLI command functions call the new service functions.

## 16. Global State Removal for Web Path

The CLI may continue to use these for backward compatibility:

```python
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
get_current_project()
set_current_project()
get_current_session()
set_current_session()
```

The web app must not use them.

Known issue: `_create_project()` currently calls `set_current_project(project)`. For the web path, either:

1. Add a `set_current: bool = True` parameter to `_create_project()` and pass `False` from the web service, or
2. Extract a new lower-level `create_project_files()` function that never touches global state.

Preferred fix:

```python
def create_project_files(*, name: str, out_dir: Path, description: str) -> ProjectRef:
    ...


def _create_project(*, name: str, out_dir: Path | None, description: str) -> ProjectRef:
    project = create_project_files(...)
    set_current_project(project)
    return project
```

Then web code calls `create_project_files()`.

## 17. Security Requirements

The app is local-first, but it still must be safe.

Required rules:

1. Bind to `127.0.0.1` by default.
2. Do not expose arbitrary file download.
3. Do not accept arbitrary output directories from web requests.
4. Do not run shell commands with unsanitized user input.
5. Use subprocess argument lists, not `shell=True`.
6. Validate JSON before generation.
7. Sanitize project names.
8. Keep each job isolated in its own directory.
9. Do not reuse global current-project/current-session state in web requests.
10. Never hide warnings or diagnostics.

## 18. Test Requirements

Add tests under:

```text
tests/web/
```

Required test files:

```text
tests/web/test_web_doctor.py
tests/web/test_web_symbols.py
tests/web/test_web_validate_netlist.py
tests/web/test_web_jobs.py
tests/web/test_web_artifacts.py
tests/web/test_web_path_safety.py
```

Tests should use FastAPI `TestClient`.

Add `httpx` to the dev dependencies if needed by the installed FastAPI/TestClient version.

The web tests must verify:

1. App imports successfully.
2. `GET /api/doctor` returns JSON.
3. Symbol search endpoint works against fixture symbols.
4. Netlist validation endpoint returns success for a known fixture.
5. Invalid JSON/netlist returns structured 400 error.
6. Job creation writes a job workspace.
7. Artifact listing only exposes files inside the job artifacts directory.
8. Path traversal attempts are rejected.

Existing unit and integration tests must continue to pass unless explicitly updated for the package path move.

## 19. Run Commands

Development install:

```bash
uv sync --extra dev --extra web
```

Run tests:

```bash
uv run pytest
```

Run only web tests:

```bash
uv run pytest tests/web
```

Run web app:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

If Stage B moved packages to root `src/`, the same uvicorn command must still work.

## 20. Definition of Done

The migration is complete when:

1. Branch `webapp` exists.
2. The repo contains a working `kicad_pcb_web` package.
3. `uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload` starts the app.
4. `GET /api/doctor` works.
5. Symbol search works from the browser and API.
6. Netlist validation works from the browser and API.
7. Project generation works from the browser and API.
8. Generated artifacts are downloadable from safe job-scoped URLs.
9. No web route uses global CLI current project/session state.
10. Existing core tests pass.
11. New web tests pass.
12. The README explains how to install and run the web app.
