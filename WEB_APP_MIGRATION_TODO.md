# WEB_APP_MIGRATION_TODO.md

# KiCad PCB Web App Migration TODO

This TODO is for converting the current OpenClaw skill-oriented KiCad PCB repository into a Python web app on a new git branch named `webapp`.

The implementation must preserve the existing `kicad_pcb` engine and add a new `kicad_pcb_web` FastAPI/Jinja layer.

---

## Task 0: Create and verify the git branch

Status: DONE

### 0.1 Check the current git state

Status: DONE

Run:

```bash
git status
```

Expected result:

- Working tree is clean, or any local changes are intentionally committed/stashed before proceeding.

If the working tree is dirty, stop and do one of the following before continuing:

```bash
git add -A
git commit -m "Save current work before webapp migration"
```

or:

```bash
git stash push -u -m "pre-webapp-migration"
```

### 0.2 Start from the main development branch

Status: DONE

Run one of these depending on the repository's actual default branch:

```bash
git checkout master
```

or:

```bash
git checkout main
```

If a remote is configured and the local branch tracks it, run:

```bash
git pull --ff-only
```

If there is no remote or no upstream, do not force anything. Continue from the local branch.

### 0.3 Create the new branch

Status: DONE

Run:

```bash
git checkout -b webapp
```

If the branch already exists locally, run:

```bash
git checkout webapp
```

### 0.4 Confirm branch

Status: DONE

Run:

```bash
git branch --show-current
```

Expected output:

```text
webapp
```

Do not proceed unless the current branch is `webapp`.

---

## Task 1: Add the migration documents to the repo

Status: DONE

### 1.1 Copy these files into the repository root

Status: DONE

Add:

```text
WEB_APP_MIGRATION_SPEC.md
WEB_APP_MIGRATION_TODO.md
```

They should sit beside:

```text
pyproject.toml
README.md
AGENTS.md
memory.md
```

### 1.2 Commit the planning docs

Status: DONE

Run:

```bash
git add WEB_APP_MIGRATION_SPEC.md WEB_APP_MIGRATION_TODO.md
git commit -m "Add web app migration spec and TODO"
```

---

## Task 2: Decide the directory migration mode

Status: DONE

Use the low-risk mode first.

### 2.1 Keep the existing core package location for the first implementation

Status: DONE

Do not move this yet:

```text
kicad-pcb/src/kicad_pcb/
```

Add the web package beside it:

```text
kicad-pcb/src/kicad_pcb_web/
```

This gives:

```text
kicad-pcb/src/
├── kicad_pcb/
└── kicad_pcb_web/
```

### 2.2 Do not delete OpenClaw files yet

Status: DONE

Keep these during the first working web app implementation:

```text
kicad-pcb/SKILL.md
kicad-pcb/skill.json
kicad-pcb/_meta.json
kicad-pcb/scripts/kicad_pcb.py
```

Reason: deleting or moving them early makes the diff noisy and risks breaking existing CLI/skill assumptions before the web path is proven.

### 2.3 Add a later cleanup task for optional root `src/` layout

Status: DONE

Do not perform this task until the FastAPI app and tests work.

Later optional move:

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

If this optional move is done, also update imports/tests/scripts as needed and run the full test suite before committing.

---

## Task 3: Update `pyproject.toml` for web dependencies

Status: DONE

### 3.1 Add a `web` optional dependency group

Status: DONE

In `pyproject.toml`, keep the existing core dependency:

```toml
dependencies = [
    "pydantic>=2.0",
]
```

Add or update optional dependencies so both `dev` and `web` exist:

```toml
[project.optional-dependencies]
dev = [
    "kiutils>=1.4",
    "pytest>=8.0",
    "pytest-cov>=5.0",
    "ruff>=0.4",
    "mypy>=1.10",
]
web = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "jinja2>=3.1",
    "python-multipart>=0.0.9",
]
```

If FastAPI test support requires it in this environment, add `httpx` to `dev`:

```toml
"httpx>=0.27",
```

### 3.2 Rename project metadata away from OpenClaw-only wording

Status: DONE

Change:

```toml
name = "kicad-pcb-skill"
description = "KiCad PCB automation skill for OpenClaw"
```

To:

```toml
name = "kicad-pcb-webapp"
description = "Python web app for generating KiCad projects from Circuit IR"
```

Do not rename the Python import package `kicad_pcb`.

### 3.3 Verify dependency installation

Status: DONE

Run:

```bash
uv sync --extra dev --extra web
```

Then run:

```bash
uv run python -c "import fastapi, jinja2; print('web deps ok')"
```

---

## Task 4: Create the `kicad_pcb_web` package skeleton

Status: DONE

### 4.1 Create directories

Status: DONE

Run:

```bash
mkdir -p kicad-pcb/src/kicad_pcb_web/services
mkdir -p kicad-pcb/src/kicad_pcb_web/routes
mkdir -p kicad-pcb/src/kicad_pcb_web/templates
mkdir -p kicad-pcb/src/kicad_pcb_web/static
```

### 4.2 Create Python package files

Status: DONE

Create:

```text
kicad-pcb/src/kicad_pcb_web/__init__.py
kicad-pcb/src/kicad_pcb_web/main.py
kicad-pcb/src/kicad_pcb_web/settings.py
kicad-pcb/src/kicad_pcb_web/schemas.py
kicad-pcb/src/kicad_pcb_web/errors.py
kicad-pcb/src/kicad_pcb_web/deps.py
kicad-pcb/src/kicad_pcb_web/services/__init__.py
kicad-pcb/src/kicad_pcb_web/services/jobs.py
kicad-pcb/src/kicad_pcb_web/services/netlists.py
kicad-pcb/src/kicad_pcb_web/services/symbols.py
kicad-pcb/src/kicad_pcb_web/services/doctor.py
kicad-pcb/src/kicad_pcb_web/services/artifacts.py
kicad-pcb/src/kicad_pcb_web/services/preview.py
kicad-pcb/src/kicad_pcb_web/routes/__init__.py
kicad-pcb/src/kicad_pcb_web/routes/ui.py
kicad-pcb/src/kicad_pcb_web/routes/api_jobs.py
kicad-pcb/src/kicad_pcb_web/routes/api_netlists.py
kicad-pcb/src/kicad_pcb_web/routes/api_symbols.py
kicad-pcb/src/kicad_pcb_web/routes/api_doctor.py
```

### 4.3 Create template/static files

Status: DONE

Create:

```text
kicad-pcb/src/kicad_pcb_web/templates/base.html
kicad-pcb/src/kicad_pcb_web/templates/index.html
kicad-pcb/src/kicad_pcb_web/templates/job_detail.html
kicad-pcb/src/kicad_pcb_web/static/app.css
kicad-pcb/src/kicad_pcb_web/static/app.js
```

---

## Task 5: Implement web app settings

Status: DONE

### 5.1 Implement `settings.py`

Status: DONE

Create a simple settings dataclass. Do not add `pydantic-settings` in v1 unless needed.

Required settings:

```python
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WebSettings:
    data_dir: Path
    jobs_dir: Path
    default_host: str = "127.0.0.1"
    default_port: int = 8000


def load_settings() -> WebSettings:
    data_dir = Path(os.environ.get("KICAD_PCB_WEB_DATA_DIR", "data")).resolve()
    jobs_dir = data_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    return WebSettings(data_dir=data_dir, jobs_dir=jobs_dir)
```

### 5.2 Add `.gitignore` entries

Status: DONE

Ensure generated web data is ignored:

```gitignore
/data/
```

If the repo already ignores data/build folders, verify this case is covered.

---

## Task 6: Implement Pydantic web schemas

Status: DONE

### 6.1 Implement `schemas.py`

Status: DONE

Add request and response models for:

```text
ValidateNetlistRequest
ValidateNetlistResponse
CreateJobFromNetlistRequest
JobSummary
JobDetail
SymbolSearchResponse
DoctorResponse
ArtifactListResponse
ErrorResponse
```

### 6.2 Project name validation

Status: DONE

In `CreateJobFromNetlistRequest`, require:

- Non-empty project name.
- Maximum length 120 characters.
- No path separators.

The service layer must still sanitize the name before writing directories.

### 6.3 Avoid raw Path objects in API responses

Status: DONE

Convert paths to strings, preferably paths relative to the job directory where possible.

---

## Task 7: Implement structured web error handling

Status: DONE

### 7.1 Implement `errors.py`

Status: DONE

Map existing `kicad_pcb.errors.UserError` to HTTP 400.

Implement a helper like:

```python
def user_error_to_payload(exc: UserError) -> dict[str, object]:
    return {
        "error": {
            "type": "user_error",
            "code": getattr(exc, "code", None),
            "message": str(exc),
            "details": getattr(exc, "details", {}) or {},
        }
    }
```

### 7.2 Register exception handlers in `main.py`

Status: DONE

FastAPI app must register handlers for:

```text
UserError
RequestValidationError
Unhandled Exception
```

Unexpected exceptions should produce a generic JSON response and should not expose arbitrary local paths.

---

## Task 8: Implement the FastAPI app entrypoint

Status: DONE

### 8.1 Implement `main.py`

Status: DONE

Required behavior:

- Create `FastAPI(title="KiCad PCB Web App")`.
- Mount static files at `/static`.
- Include UI routes.
- Include API routes.
- Register exception handlers.

Expected structure:

```python
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .routes import api_doctor, api_jobs, api_netlists, api_symbols, ui

app = FastAPI(title="KiCad PCB Web App")

app.mount("/static", StaticFiles(directory=...), name="static")
app.include_router(ui.router)
app.include_router(api_doctor.router, prefix="/api")
app.include_router(api_symbols.router, prefix="/api")
app.include_router(api_netlists.router, prefix="/api")
app.include_router(api_jobs.router, prefix="/api")
```

### 8.2 Verify import

Status: DONE

Run:

```bash
uv run python -c "from kicad_pcb_web.main import app; print(app.title)"
```

Expected output includes:

```text
KiCad PCB Web App
```

---

## Task 9: Implement job workspace service

Status: IN PROGRESS

### 9.1 Implement `services/jobs.py`

Status: IN PROGRESS

Required functions:

```python
def sanitize_project_name(name: str) -> str: ...
def new_job_id() -> str: ...
def create_job_workspace(settings: WebSettings, project_name: str, request: dict[str, Any]) -> JobRecord: ...
def read_job(settings: WebSettings, job_id: str) -> JobRecord: ...
def write_job(record: JobRecord) -> None: ...
def list_jobs(settings: WebSettings) -> list[JobRecord]: ...
def update_job_status(...): ...
```

### 9.2 Required job directory layout

Status: PENDING

For every job:

```text
data/jobs/<job_id>/input/
data/jobs/<job_id>/project/
data/jobs/<job_id>/artifacts/
data/jobs/<job_id>/job.json
```

### 9.3 Required job statuses

Status: PENDING

Use only:

```text
queued
running
succeeded
failed
cancelled
```

### 9.4 Path safety

Status: PENDING

Reject any `job_id` containing:

```text
/
\
..
```

Also reject empty job IDs.

---

## Task 10: Extract project creation away from global CLI state

### 10.1 Inspect current project creation

Current project creation is in:

```text
kicad-pcb/src/kicad_pcb/commands/_project.py
```

The existing `_create_project()` writes KiCad project files and calls:

```python
set_current_project(project)
```

That call is not acceptable in the web path.

### 10.2 Add a lower-level no-global-state function

In `commands/_project.py`, add:

```python
def create_project_files(*, name: str, out_dir: Path, description: str) -> ProjectRef:
    """Create a KiCad project directory and seed files without changing global CLI state."""
    ...
```

Move the file-writing body from `_create_project()` into `create_project_files()`.

### 10.3 Keep CLI behavior unchanged

Rewrite `_create_project()` to call the new helper and then set current project:

```python
def _create_project(*, name: str, out_dir: Path | None, description: str) -> ProjectRef:
    if out_dir is None:
        cfg = load_config()
        base = Path(cfg.get("projects_dir", PROJECTS_DIR))
    else:
        base = out_dir

    project = create_project_files(name=name, out_dir=base, description=description)
    set_current_project(project)
    return project
```

### 10.4 Add tests

Add or update unit tests proving:

1. `create_project_files()` creates `.kicad_pro`, `.kicad_sch`, and `.kicad_pcb`.
2. `create_project_files()` does not write `CURRENT_PROJECT_FILE`.
3. `_create_project()` still writes current project state for CLI compatibility.

---

## Task 11: Implement netlist validation service

### 11.1 Implement `services/netlists.py` validation

Create a function:

```python
def validate_netlist_dict(*, netlist_json: dict[str, Any], symbols_dir: Path | None) -> ValidateNetlistResponse:
    ...
```

Implementation requirements:

1. Write `netlist_json` to a temporary or job-local `circuit_ir.json` file.
2. Use existing validation logic from `kicad_pcb.commands.netlist` or extract the core of `cmd_validate_netlist()`.
3. Return component count, net count, warnings, and symbol directories used.
4. Do not create a KiCad project.
5. Do not touch current project/current session state.

### 11.2 Prefer direct engine calls over fake argparse

Preferred path:

```python
symbol_index = SymbolIndex(symbols_dir=symbols_dir)
ir = full_validate(netlist_path, symbol_index)
raise_for_blocking_advisories(ir, symbol_index)
warnings = advisory_warnings(ir, symbol_index)
```

Avoid:

```python
args = argparse.Namespace(...)
cmd_validate_netlist(args)
```

### 11.3 Add endpoint

In `routes/api_netlists.py`, add:

```text
POST /netlists/validate
```

Because `main.py` mounts this router with `/api`, the final route must be:

```text
POST /api/netlists/validate
```

---

## Task 12: Implement project generation service

### 12.1 Implement generation request handling

In `services/netlists.py`, add:

```python
def generate_project_from_netlist_job(
    *,
    settings: WebSettings,
    request: CreateJobFromNetlistRequest,
) -> JobDetail:
    ...
```

### 12.2 Required behavior

The service must:

1. Create a job workspace.
2. Write `input/circuit_ir.json`.
3. Mark job `running`.
4. Validate the netlist before creating project files.
5. Create a project under `project/<safe_project_name>/`.
6. Apply the netlist to the project.
7. Write warnings to `artifacts/warnings.json`.
8. Write debug dump to `artifacts/debug.json` if available.
9. Zip the generated project directory into `artifacts/project.zip`.
10. Mark job `succeeded` with result details.
11. On `UserError`, mark job `failed` with structured error.
12. On unexpected exception, mark job `failed` with structured generic error and re-raise or return a failed job detail.

### 12.3 Do not use global session behavior

Do not call:

```python
get_current_session()
get_current_project()
set_current_session()
set_current_project()
```

### 12.4 Reuse `_apply_netlist_to_project()` carefully

Existing apply logic is in:

```text
kicad-pcb/src/kicad_pcb/commands/_sch_apply.py
```

It exposes:

```python
_apply_netlist_to_project(project, _ApplyNetlistRequest(...))
```

The web service may call this lower-level function directly after creating a `ProjectRef` with `create_project_files()`.

Required request fields:

```python
_ApplyNetlistRequest(
    netlist_path=job_input_path,
    symbols_dir=symbols_dir,
    mode_name=request.validation,
    force=True,
    dry_run=False,
    backup=False,
    strict=request.strict,
    layout_name=request.layout,
    routing_name=request.routing,
    label_mode_name=request.label_mode,
    heuristic_profile_name=request.heuristic_profile,
    debug_dump_path=artifacts_dir / "debug.json",
)
```

If `_ApplyNetlistRequest` does not currently accept `backup`, match the actual dataclass signature in `_sch_apply.py`.

### 12.5 Create zip artifact

In `services/artifacts.py`, implement:

```python
def create_project_zip(project_dir: Path, artifacts_dir: Path) -> Path:
    ...
```

It must write:

```text
artifacts/project.zip
```

It must preserve paths relative to `project_dir.parent` or `project_dir`, but must not include absolute paths.

---

## Task 13: Implement jobs API routes

### 13.1 Add `POST /api/jobs/from-netlist`

In `routes/api_jobs.py`, add:

```text
POST /jobs/from-netlist
```

Final mounted route:

```text
POST /api/jobs/from-netlist
```

It must accept `CreateJobFromNetlistRequest` and return `JobDetail`.

### 13.2 Add job listing route

Add:

```text
GET /jobs
```

Final route:

```text
GET /api/jobs
```

Return newest jobs first.

### 13.3 Add job detail route

Add:

```text
GET /jobs/{job_id}
```

Final route:

```text
GET /api/jobs/{job_id}
```

Reject unsafe job IDs.

---

## Task 14: Implement artifacts service and routes

### 14.1 Implement `services/artifacts.py`

Required functions:

```python
def list_artifacts(job_dir: Path) -> list[str]: ...
def resolve_artifact_path(job_dir: Path, artifact_name: str) -> Path: ...
def create_project_zip(project_dir: Path, artifacts_dir: Path) -> Path: ...
```

### 14.2 Artifact path safety

`resolve_artifact_path()` must reject names containing:

```text
/
\
..
```

It must only return paths inside:

```text
<job_dir>/artifacts/
```

Use `Path.resolve()` and verify the resolved path is under the resolved artifacts directory.

### 14.3 Add artifact routes

In `routes/api_jobs.py`, add:

```text
GET /jobs/{job_id}/artifacts
GET /jobs/{job_id}/artifacts/{artifact_name}
```

Final routes:

```text
GET /api/jobs/{job_id}/artifacts
GET /api/jobs/{job_id}/artifacts/{artifact_name}
```

The download route must return `FileResponse`.

---

## Task 15: Implement symbol search service and route

### 15.1 Inspect existing symbol search

Existing code is in:

```text
kicad-pcb/src/kicad_pcb/commands/search.py
kicad-pcb/src/kicad_pcb/symbol_index.py
kicad-pcb/src/kicad_pcb/symbol_cache.py
```

### 15.2 Implement `services/symbols.py`

Add:

```python
def search_symbols(*, query: str, symbols_dir: Path | None, limit: int = 20) -> SymbolSearchResponse:
    ...
```

Use `SymbolIndex` directly if possible.

Return stable JSON with at least:

```text
library
name
qualified_name
aliases or keywords if available
```

### 15.3 Add route

In `routes/api_symbols.py`, add:

```text
GET /symbols/search?q=<query>&limit=<n>&symbols_dir=<path>
```

Final route:

```text
GET /api/symbols/search
```

Validation:

- Empty query returns HTTP 400.
- Limit must be between 1 and 100.

---

## Task 16: Implement doctor service and route

### 16.1 Inspect existing doctor command

Existing code is in:

```text
kicad-pcb/src/kicad_pcb/commands/doctor.py
```

### 16.2 Implement `services/doctor.py`

Add:

```python
def run_doctor() -> DoctorResponse:
    ...
```

Checks should include:

1. Python version.
2. Whether app data/jobs directory is writable.
3. Whether KiCad symbols directory can be discovered.
4. Whether `kicad-cli` is available.
5. Whether Graphviz `dot` is available.
6. Whether optional preview tooling is available.

Do not fail the whole doctor call just because optional tools are missing. Return `ok=false` only if core required checks fail.

### 16.3 Add route

In `routes/api_doctor.py`, add:

```text
GET /doctor
```

Final route:

```text
GET /api/doctor
```

---

## Task 17: Implement basic UI routes

### 17.1 Implement `routes/ui.py`

Required routes:

```text
GET /
GET /jobs/{job_id}
```

Use Jinja2 templates.

### 17.2 Implement `base.html`

Required content:

- Page `<title>`.
- Link to `/static/app.css`.
- Header/nav.
- Main content block.
- Script tag for `/static/app.js`.

### 17.3 Implement `index.html`

Required controls:

1. Project name input.
2. Optional symbols directory input.
3. Netlist JSON textarea.
4. File upload input for `.json`.
5. Validate button.
6. Generate button.
7. Symbol search input.
8. Doctor/status area.
9. Results/warnings area.

### 17.4 Implement `job_detail.html`

Required sections:

1. Job status.
2. Job timestamps.
3. Project name.
4. Errors if failed.
5. Result summary if succeeded.
6. Warnings.
7. Diagnostics.
8. Artifact download links.

---

## Task 18: Implement frontend JavaScript

### 18.1 Implement JSON file upload behavior

In `static/app.js`:

- When a user selects a JSON file, read it with `FileReader`.
- Put contents into the Circuit IR textarea.
- Do not upload immediately.

### 18.2 Implement Validate button

On click:

1. Parse textarea as JSON.
2. POST to `/api/netlists/validate`.
3. Show success or structured errors.
4. Show warnings.

### 18.3 Implement Generate button

On click:

1. Parse textarea as JSON.
2. POST to `/api/jobs/from-netlist`.
3. Show job result.
4. Link to `/jobs/{job_id}`.
5. Show artifact links if succeeded.

### 18.4 Implement Symbol Search button

On click:

1. Read query.
2. Call `/api/symbols/search?q=...`.
3. Render results in a simple table/list.

### 18.5 Implement Doctor load

On page load:

1. Call `/api/doctor`.
2. Render checks.

Do not add a frontend framework in v1.

---

## Task 19: Add web tests

### 19.1 Create test directory

Create:

```text
tests/web/
```

### 19.2 Add test files

Create:

```text
tests/web/test_web_app_import.py
tests/web/test_web_doctor.py
tests/web/test_web_symbols.py
tests/web/test_web_validate_netlist.py
tests/web/test_web_jobs.py
tests/web/test_web_artifacts.py
tests/web/test_web_path_safety.py
```

### 19.3 Required tests

Implement tests for:

1. `from kicad_pcb_web.main import app` succeeds.
2. `GET /api/doctor` returns 200 and JSON.
3. `GET /api/symbols/search?q=resistor` returns JSON.
4. `POST /api/netlists/validate` accepts a fixture netlist.
5. Invalid netlist returns structured 400.
6. `POST /api/jobs/from-netlist` creates a job directory.
7. Successful job exposes `project.zip`.
8. `GET /api/jobs/{job_id}/artifacts/../job.json` is rejected.
9. `GET /api/jobs/{job_id}/artifacts/foo/bar` is rejected.
10. Unknown job ID returns 404.

### 19.4 Use temporary data directories in tests

Tests must not write to the real repo `data/` directory unless using a pytest tmp path.

Use environment variable override:

```python
monkeypatch.setenv("KICAD_PCB_WEB_DATA_DIR", str(tmp_path / "data"))
```

Reload settings or construct test app dependencies so the temp path is used.

---

## Task 20: Preserve existing CLI behavior

### 20.1 Run existing tests

Run:

```bash
uv run pytest tests/unit
```

Then:

```bash
uv run pytest tests/integration
```

If integration tests require KiCad CLI and it is not installed, document skipped/failing tests explicitly.

### 20.2 Verify CLI import still works

Run:

```bash
uv run python -c "from kicad_pcb.cli import main; print('cli import ok')"
```

### 20.3 Verify no accidental web dependency in core

The core package `kicad_pcb` must not import FastAPI, Starlette, Jinja2, or web-only modules.

Allowed direction:

```text
kicad_pcb_web imports kicad_pcb
```

Forbidden direction:

```text
kicad_pcb imports kicad_pcb_web
```

---

## Task 21: Update README

### 21.1 Add web app section

Update `README.md` with:

```text
## Web App
```

Include:

```bash
uv sync --extra dev --extra web
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

### 21.2 Explain data directory

Document:

```text
KICAD_PCB_WEB_DATA_DIR
```

Default:

```text
./data
```

### 21.3 Explain local-only default

State clearly that the app binds to `127.0.0.1` by default and should not be exposed publicly without authentication and additional sandboxing.

### 21.4 Explain artifacts

Document that generated projects are stored under:

```text
data/jobs/<job_id>/
```

and downloadable through the web UI.

---

## Task 22: Optional later cleanup to root `src/` layout

Do this only after Tasks 0 through 21 pass.

### 22.1 Move packages

Run:

```bash
mkdir -p src
git mv kicad-pcb/src/kicad_pcb src/kicad_pcb
git mv kicad-pcb/src/kicad_pcb_web src/kicad_pcb_web
```

### 22.2 Update `pyproject.toml`

Change:

```toml
pythonpath = ["kicad-pcb/src"]
where = ["kicad-pcb/src"]
source = ["kicad-pcb/src"]
```

To:

```toml
pythonpath = ["src"]
where = ["src"]
source = ["src"]
```

### 22.3 Archive OpenClaw files

Run:

```bash
mkdir -p legacy/openclaw-skill
git mv kicad-pcb/SKILL.md legacy/openclaw-skill/SKILL.md
git mv kicad-pcb/skill.json legacy/openclaw-skill/skill.json
git mv kicad-pcb/_meta.json legacy/openclaw-skill/_meta.json
git mv kicad-pcb/scripts legacy/openclaw-skill/scripts
```

### 22.4 Remove empty directory if applicable

If `kicad-pcb/` is empty after moves, remove it:

```bash
rmdir kicad-pcb
```

Only do this if it is truly empty.

### 22.5 Run full tests after move

Run:

```bash
uv run pytest
uv run ruff check .
uv run mypy kicad-pcb/src/kicad_pcb kicad-pcb/src/kicad_pcb_web || true
```

If packages were moved to `src/`, use:

```bash
uv run mypy src/kicad_pcb src/kicad_pcb_web || true
```

Do not commit the layout move until tests pass or every remaining failure is clearly documented.

---

## Task 23: Manual QA checklist

### 23.1 Start the server

Run:

```bash
uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload
```

### 23.2 Browser checks

Open:

```text
http://127.0.0.1:8000/
```

Verify:

1. Page loads.
2. Doctor panel loads.
3. Symbol search works.
4. JSON file upload fills textarea.
5. Validate button works.
6. Generate button creates a job.
7. Job page opens.
8. Warnings are visible.
9. `project.zip` downloads.
10. Downloaded project zip contains KiCad files.

### 23.3 API checks

Run:

```bash
curl -s http://127.0.0.1:8000/api/doctor | jq .
```

Run a symbol search:

```bash
curl -s 'http://127.0.0.1:8000/api/symbols/search?q=resistor' | jq .
```

Run validation using a known fixture netlist after adapting the path:

```bash
python - <<'PY'
import json
from pathlib import Path
p = Path('tests/fixtures/regressions/headphone_amp_ir.json')
print(json.dumps({'netlist_json': json.loads(p.read_text())}))
PY
```

Use that JSON body with `curl` or the browser UI.

---

## Task 24: Commit milestones

Use small commits.

Recommended commit sequence:

```bash
git add WEB_APP_MIGRATION_SPEC.md WEB_APP_MIGRATION_TODO.md
git commit -m "Add web app migration plan"

git add pyproject.toml uv.lock .gitignore
git commit -m "Add web app dependencies"

git add kicad-pcb/src/kicad_pcb_web
git commit -m "Add FastAPI web app skeleton"

git add kicad-pcb/src/kicad_pcb/commands/_project.py tests/unit
git commit -m "Extract project creation without global CLI state"

git add kicad-pcb/src/kicad_pcb_web/services kicad-pcb/src/kicad_pcb_web/routes tests/web
git commit -m "Add web API services and tests"

git add kicad-pcb/src/kicad_pcb_web/templates kicad-pcb/src/kicad_pcb_web/static README.md
git commit -m "Add browser UI for KiCad project generation"
```

Do not make one giant migration commit if avoidable.

---

## Task 25: Final acceptance criteria

The branch is acceptable when all of these are true:

- Current branch is `webapp`.
- `WEB_APP_MIGRATION_SPEC.md` exists at repo root.
- `WEB_APP_MIGRATION_TODO.md` exists at repo root.
- `kicad_pcb_web.main:app` imports successfully.
- `uv run uvicorn kicad_pcb_web.main:app --host 127.0.0.1 --port 8000 --reload` starts the app.
- `GET /api/doctor` works.
- `GET /api/symbols/search?q=resistor` works.
- `POST /api/netlists/validate` works.
- `POST /api/jobs/from-netlist` creates an isolated job workspace.
- Generated project files are written under `data/jobs/<job_id>/project/`.
- Downloadable artifacts are written under `data/jobs/<job_id>/artifacts/`.
- Artifact downloads reject path traversal.
- Web code does not rely on current project/current session global CLI state.
- Existing core tests still pass or every remaining failure is clearly documented with root cause.
- New web tests pass.
- README contains web app run instructions.
