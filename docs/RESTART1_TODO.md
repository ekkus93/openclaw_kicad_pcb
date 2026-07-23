# RESTART1 Implementation TODO

**Repository:** `ekkus93/openclaw_kicad_pcb`  
**Target baseline branch:** `webapp`  
**Document purpose:** Ordered implementation checklist for restarting active development after the web-app migration.  
**Scope:** The six highest-value restart priorities identified during the July 23, 2026 repository review.

---

## 0. How to use this TODO

This file is the authoritative ordered checklist for RESTART1. Implement the phases in order unless a task explicitly says it may run in parallel.

A checkbox may be marked complete only when all of the following are true:

- The implementation is present in the repository.
- Relevant automated tests exist and pass.
- Required failure-injection or negative-path tests pass.
- Documentation and commands are updated.
- No dangerous fallback, swallowed exception, stale generated state, or quiet success remains.
- The implementation commit and verification commands are recorded in the completion evidence section at the end of this file.

Code snippets in this TODO are implementation guidance. Adapt them to the repository, preserve existing public behavior unless this TODO explicitly changes it, and test the integrated result. Do not paste snippets blindly without reconciling imports, error types, type checking, and current call sites.

### 0.1 Locked architecture and behavior

The following decisions are already made and must not be revisited during RESTART1:

- The deterministic `Circuit IR JSON -> validated KiCad project` pipeline remains authoritative.
- The LLM wizard is only a producer of circuit specifications and Circuit IR. It must not write or mutate KiCad files directly.
- Direct JSON generation and wizard generation must converge on the same validation and generation services.
- The core generation path must continue to work with the LLM provider disabled.
- `POST /api/netlists/validate` must continue to use the established Option A behavior: user-correctable validation failures return HTTP 200 with `valid: false` and structured errors.
- Schematic preview generation is optional. Preview failure may be non-fatal only when the generated KiCad project is otherwise valid, and the degradation must be returned as an explicit warning.
- There must be no silent provider fallback, no silent validation downgrade, no silent replacement of malformed persisted state, no missing-file-as-success behavior, and no broad exception handler that hides a traceback from server logs.
- `wizard.json` and `job.json` are private server state and must never become downloadable artifacts.
- The model corpus remains a deterministic fixture/evaluation system, not an ML-training pipeline.
- Curated model-corpus fixtures belong under `tests/fixtures/model_corpus/`.
- Regenerable model-evaluation output continues to use `code_review/generated/model_eval/` and must remain untracked.
- Raw corpus `source.kicad_sch` files must remain unchanged. Any normalization must continue to produce a separate deterministic `source_normalized.kicad_sch`.
- Do not rewrite Git history as part of RESTART1. Historical repository-size cleanup requires a separate explicit decision.

### 0.2 Expected implementation order

| Phase | Workstream | Depends on |
|---|---|---|
| P0 | Baseline and safety setup | None |
| P1 | Repair CI and establish real gates | P0 |
| P2 | Durable and concurrency-safe persistence | P0; basic P1 Python gate available |
| P3 | Correct wizard failure semantics and observability | P2 |
| P4 | Make packaging and installed frontend delivery reliable | P1 |
| P5 | Add service-level and browser-level workflow tests | P2, P3, P4 |
| P6 | Correct layout-evaluation score semantics | P1 |
| P7 | Separate curated fixtures from generated output | P1; coordinate with P6 |
| P8 | Final integrated verification and closeout | P2-P7 |

P4, P6, and P7 may proceed in parallel after P1 is stable. P5 must not be treated as complete until the persistence, error-semantics, and packaging changes it verifies have landed.

---

# P0. Baseline, inventory, and implementation safety

## P0.1 Capture the starting point

- [ ] Record the implementation branch and starting commit before changing code.
  - [ ] Run `git status --short --branch`.
  - [ ] Run `git rev-parse HEAD`.
  - [ ] Confirm the starting branch is based on `webapp`, not the unrelated `master` history.
  - [ ] Record the values in the RESTART1 completion evidence section.
- [ ] Confirm the working tree does not contain unrelated user changes before implementing.
  - [ ] Do not use `git add -A` if unrelated files are present.
  - [ ] Stage only RESTART1 files.

## P0.2 Run and record the current gates before editing

- [ ] Install the current Python and web dependencies using the lockfile.
  - [ ] Run `uv sync --frozen --extra dev --extra web`.
- [ ] Run the current Python checks and record all failures without suppressing them.
  - [ ] `uv run ruff check .`
  - [ ] `uv run ruff format --check .`
  - [ ] `uv run mypy src/kicad_pcb src/kicad_pcb_web`
  - [ ] `uv run pytest tests/unit/ -q`
- [ ] Run the current frontend checks and record all failures.
  - [ ] `cd frontend && npm ci`
  - [ ] `npm run lint`
  - [ ] `npm run test:run`
  - [ ] `npm run build`
- [ ] Do not mark a missing command, missing dependency, or stale path as a pass.
- [ ] Add any genuine pre-existing blocker to the implementation notes before modifying it.

## P0.3 Inventory the exact affected files

- [ ] Confirm the current content and call sites for at least the following files:
  - [ ] `.github/workflows/ci.yml`
  - [ ] `scripts/validate.sh`
  - [ ] `pyproject.toml`
  - [ ] `uv.lock`
  - [ ] `frontend/package.json`
  - [ ] `frontend/package-lock.json`
  - [ ] `frontend/vite.config.ts`
  - [ ] `frontend/playwright.config.ts`
  - [ ] `frontend/src/App.tsx`
  - [ ] `src/kicad_pcb_web/main.py`
  - [ ] `src/kicad_pcb_web/deps.py`
  - [ ] `src/kicad_pcb_web/errors.py`
  - [ ] `src/kicad_pcb_web/routes/api_jobs.py`
  - [ ] `src/kicad_pcb_web/routes/api_netlists.py`
  - [ ] `src/kicad_pcb_web/routes/api_ui.py`
  - [ ] `src/kicad_pcb_web/routes/api_wizard.py`
  - [ ] `src/kicad_pcb_web/routes/ui.py`
  - [ ] `src/kicad_pcb_web/services/jobs.py`
  - [ ] `src/kicad_pcb_web/services/netlists.py`
  - [ ] `src/kicad_pcb_web/services/wizard.py`
  - [ ] `src/kicad_pcb_web/services/_wizard_session_io.py`
  - [ ] `src/kicad_pcb/evaluation/similarity.py`
  - [ ] `src/kicad_pcb/evaluation/_reports_fixture.py`
  - [ ] `src/kicad_pcb/evaluation/_reports_helpers.py`
  - [ ] `.gitignore`
- [ ] Search for every direct write of `job.json`, `wizard.json`, `spec.json`, and `circuit_ir.json`.
- [ ] Search for every `except Exception`, `except BaseException`, empty `except`, `pass`, `return []`, and `return None` in the web package and classify whether each is intentional.
- [ ] Search for all code that assigns a successful status after a warning or fallback.

### P0 acceptance criteria

- [ ] The pre-change state is recorded.
- [ ] Current failures are known rather than hidden by later changes.
- [ ] No unrelated working-tree changes are mixed into RESTART1.

---

# P1. Repair CI and establish real repository gates

## P1.1 Replace stale workflow triggers

Update `.github/workflows/ci.yml` so the active branch is actually protected by CI.

- [ ] Add direct push coverage for `webapp`.
- [ ] Keep pull-request coverage for the branches that are valid targets in this repository.
- [ ] Add `workflow_dispatch` for manual verification.
- [ ] Do not claim a nightly workflow exists unless a corresponding workflow file is present.
- [ ] Either:
  - [ ] Keep KiCad integration tests in `.github/workflows/ci.yml` and update the README to say so, or
  - [ ] Create `.github/workflows/integration.yml` and move the integration job there.
- [ ] Do not leave the README referring to a nonexistent workflow.

Suggested trigger shape:

```yaml
on:
  push:
    branches: [webapp, main, master]
  pull_request:
    branches: [webapp, main, master]
  workflow_dispatch:
```

Remove branches that do not exist or are not valid integration targets after checking the repository. Do not cargo-cult this exact list.

## P1.2 Create a reliable Python quality job

- [ ] Use Python 3.11, matching `pyproject.toml`.
- [ ] Install `uv` and use `uv.lock` rather than resolving an unrelated dependency set with bare `pip install -e`.
- [ ] Run `uv sync --frozen --extra dev --extra web`.
- [ ] Install required unit-test system dependencies, including Graphviz and KiCad symbols where the unit suite expects them.
- [ ] Verify required commands and directories before tests:
  - [ ] `command -v dot`
  - [ ] Verify the configured KiCad symbol directory exists.
- [ ] Correct the stale mypy path.
  - [ ] Remove `mypy kicad-pcb/src`.
  - [ ] Run `uv run mypy src/kicad_pcb src/kicad_pcb_web`.
- [ ] Run formatting and lint checks:
  - [ ] `uv run ruff check .`
  - [ ] `uv run ruff format --check .`
- [ ] Run the unit suite with coverage.
  - [ ] Preserve the `fail_under = 70` project rule.
  - [ ] Emit a terminal report and XML artifact.
- [ ] Upload coverage even after a test failure, but do not convert the failed test job into success.

## P1.3 Add a first-class frontend quality job

- [ ] Set up a supported Node version compatible with the committed lockfile and package versions.
- [ ] Use `npm ci`, not `npm install`.
- [ ] Run all of the following as distinct visible steps:
  - [ ] `npm run lint`
  - [ ] `npm run test:run`
  - [ ] `npm run build`
- [ ] Add frontend coverage generation if Vitest coverage is already configured or can be added without weakening tests.
- [ ] Upload coverage/test artifacts on failure for debugging.
- [ ] Fail when TypeScript compilation fails.
- [ ] Fail when the Vite build fails.
- [ ] Do not use a pre-existing committed bundle as evidence that the current TypeScript source builds.

## P1.4 Keep the committed SPA bundle synchronized

The current deployment model commits `src/kicad_pcb_web/static/spa/` so wheels and source checkouts can serve the frontend without running Node during installation. Preserve that model during RESTART1.

- [ ] Run the frontend build in CI.
- [ ] After the build, run:

```bash
git diff --exit-code -- src/kicad_pcb_web/static/spa
```

- [ ] Fail CI if the committed bundle does not match `frontend/src`.
- [ ] Ensure Vite removes obsolete hashed assets through `emptyOutDir: true`.
- [ ] Confirm the build does not remove the bundle without recreating it.

## P1.5 Make local validation match CI

Update `scripts/validate.sh` so developers can run the same meaningful gates locally.

- [ ] Preserve the existing `--fast` behavior for skipping Python coverage only.
- [ ] Add explicit frontend checks, or add a documented flag such as `--python-only` if a fast Python-only loop is required.
- [ ] Default full validation must include:
  - [ ] Ruff lint.
  - [ ] Ruff format check.
  - [ ] Mypy for both Python packages.
  - [ ] Python unit tests.
  - [ ] `npm ci` or a clear prerequisite check for an already synchronized `node_modules` tree.
  - [ ] Frontend lint.
  - [ ] Frontend unit tests.
  - [ ] Frontend production build.
  - [ ] Committed-bundle diff check.
- [ ] A missing Node executable must fail with an actionable message, not silently skip the frontend.
- [ ] A missing Graphviz executable must fail gates that require Graphviz.

## P1.6 Keep KiCad-dependent integration tests honest

- [ ] Preserve the `requires_kicad` marker.
- [ ] Ensure unit jobs do not accidentally invoke tests that require a complete KiCad installation.
- [ ] Ensure integration jobs install and verify the intended KiCad major version.
- [ ] Run `kicad-cli version` and retain it in logs.
- [ ] Run integration tests with a JUnit report.
- [ ] Upload failure artifacts and logs without treating upload success as test success.
- [ ] Do not report electrical-equivalence checks as passing when KiCad CLI was absent or failed.

## P1.7 Add CI configuration tests where practical

- [ ] Add a lightweight script or test that validates required workflow commands/paths against the repository.
- [ ] At minimum, assert the workflow no longer contains `mypy kicad-pcb/src`.
- [ ] Assert the workflow invokes frontend lint, tests, and build.
- [ ] Assert `webapp` is covered by a push or pull-request trigger.

### P1 acceptance criteria

- [ ] A clean `webapp` checkout runs Python and frontend gates in CI.
- [ ] The active Python source paths are type-checked.
- [ ] The React application is linted, unit-tested, and built.
- [ ] A stale committed SPA bundle fails CI.
- [ ] README workflow descriptions match real files.
- [ ] No missing dependency or skipped command is presented as success.

---

# P2. Make job and wizard persistence durable and concurrency-safe

## P2.1 Introduce one atomic JSON persistence primitive

Create a shared module such as:

```text
src/kicad_pcb_web/services/atomic_io.py
```

- [ ] Implement `atomic_write_bytes()` and/or `atomic_write_json()` once.
- [ ] Serialize the complete JSON payload before touching the destination file.
- [ ] Create the temporary file in the destination directory so `os.replace()` remains atomic on the same filesystem.
- [ ] Write the complete payload.
- [ ] Flush the Python file object.
- [ ] Call `os.fsync()` on the temporary file before replacement.
- [ ] Use `os.replace(temp_path, destination)`.
- [ ] On POSIX, fsync the containing directory after replacement when supported.
- [ ] Remove an abandoned temporary file after any failure.
- [ ] Never truncate the existing canonical file before the replacement is ready.
- [ ] Raise a typed persistence error on failure. Do not fall back to a direct write.

Suggested implementation shape:

```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, payload: Any) -> None:
    encoded = (json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, path)
        temp_path = None

        if os.name == "posix":
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
```

- [ ] Adapt the directory fsync code for platform support and type checking.
- [ ] Do not silently ignore destination replacement failures.
- [ ] Add bounded, explicit retry only if a proven transient Windows replacement condition requires it. Do not add generic retry loops.

## P2.2 Use atomic writes for every canonical state file

- [ ] Replace direct `Path.write_text()` persistence in `src/kicad_pcb_web/services/jobs.py`.
- [ ] Replace direct JSON persistence in `src/kicad_pcb_web/services/_wizard_session_io.py`.
- [ ] Review other web state writes and use the shared primitive where the file is canonical or externally consumed.
- [ ] Keep generated KiCad-file transaction rules in the deterministic engine unchanged unless a test proves they are bypassed.

Canonical files requiring atomic replacement include:

- [ ] `data/jobs/<job_id>/job.json`
- [ ] `data/wizard_sessions/<session_id>/wizard.json`

## P2.3 Define one authoritative wizard state file

- [ ] Declare `wizard.json` as the sole authoritative wizard-session record.
- [ ] Do not read `spec.json` or `circuit_ir.json` to reconstruct authoritative state.
- [ ] Decide and document one of these derived-sidecar strategies:
  - [ ] Preferred: generate `spec.json` and `circuit_ir.json` as derived convenience exports from the committed `wizard.json` state, or
  - [ ] Remove the sidecars and update documentation if they have no supported consumer.
- [ ] If sidecars remain:
  - [ ] Write them atomically.
  - [ ] Include enough revision metadata to identify which `wizard.json` revision produced them.
  - [ ] Remove or replace stale `circuit_ir.json` when `ir_json` is cleared.
  - [ ] Remove or replace stale `spec.json` when no spec exists.
  - [ ] Never leave an old Circuit IR sidecar appearing current after a conversation revision invalidates IR.
- [ ] Reads and API responses must always trust `wizard.json` over a stale derived sidecar.

## P2.4 Add explicit mutation locking

Add a cross-process lock for each mutable job/session resource. A plain in-process `threading.Lock` is not sufficient if Uvicorn is ever started with multiple workers.

- [ ] Add an appropriate lock dependency to the `web` extra, such as `filelock`, and update `uv.lock`.
- [ ] Use a lock file outside downloadable artifact directories.
- [ ] Use one lock per wizard session.
- [ ] Use one lock per job when mutating job state.
- [ ] Acquire a wizard mutation lock before the initial read and keep it until the mutation has either committed or failed and persisted its failure state.
- [ ] Use a bounded timeout.
- [ ] Map lock contention to an explicit HTTP 409 conflict or equivalent typed error.
- [ ] Do not wait forever.
- [ ] Do not proceed without the lock.
- [ ] Do not silently drop a second request.

Suggested lock-path convention:

```text
data/.locks/wizard/<session_id>.lock
data/.locks/jobs/<job_id>.lock
```

- [ ] Validate IDs before constructing lock paths.
- [ ] Ensure lock files cannot be downloaded.

## P2.5 Collapse split wizard mutations into one locked transaction

The current message route reads the session, writes metadata, then calls another service that reads and writes again. Remove that lost-update window.

- [ ] Change the wizard message service to accept the optional `project_name` and `symbols_dir` fields together with the message.
- [ ] Under one session lock:
  - [ ] Read the current session.
  - [ ] Validate the allowed current state.
  - [ ] Apply metadata changes.
  - [ ] Invalidate approval, IR, validation, and latest-job state.
  - [ ] Append the user message.
  - [ ] Persist the intermediate or final state according to the failure policy.
  - [ ] Perform the LLM operation.
  - [ ] Persist the final result or explicit failed state.
- [ ] Remove route-level read/write sequences that can race with service-level writes.

## P2.6 Make debug-artifact names collision-safe

- [ ] Remove `len(list(glob(...))) + 1` numbering.
- [ ] Use a monotonic timestamp plus random suffix or a server-generated UUID.
- [ ] Write debug artifacts atomically.
- [ ] Keep debug capture disabled by default.
- [ ] Keep provider credentials and authorization headers out of debug artifacts.

Suggested filename shape:

```text
spec_20260723T201530.123456Z_ab12cd34.json
ir_20260723T201535.654321Z_98fedcba.json
```

## P2.7 Fail explicitly on malformed persisted state

- [ ] Add a typed error for unreadable, malformed, or schema-invalid persisted state.
- [ ] Include a safe resource identifier and path in server logs.
- [ ] Do not return an empty list when job records are malformed.
- [ ] Do not skip a malformed job directory quietly in `list_jobs()`.
- [ ] Do not replace malformed state with defaults.
- [ ] Do not overwrite a malformed canonical file until it has been preserved for diagnosis.
- [ ] If automatic quarantine is implemented:
  - [ ] Copy or atomically rename the bad file to a uniquely named `.corrupt-<timestamp>` file.
  - [ ] Log the quarantine path.
  - [ ] Return an explicit error.
  - [ ] Never call quarantine success equivalent to job/session success.
- [ ] Add a doctor/diagnostic result for corrupted persisted records if the existing doctor API can support it cleanly.

## P2.8 Add persistence failure-injection tests

Create focused tests under `tests/unit/` for the shared atomic I/O and service behavior.

- [ ] Atomic write creates a new file with deterministic JSON.
- [ ] Atomic write replaces an existing file.
- [ ] JSON serialization failure leaves the old destination unchanged.
- [ ] Temporary-file write failure leaves the old destination unchanged.
- [ ] `fsync` failure leaves the old destination unchanged where replacement has not occurred.
- [ ] `os.replace` failure leaves the old destination unchanged.
- [ ] Temporary files are removed after failure.
- [ ] A successful write leaves no `.tmp` files.
- [ ] Concurrent writes never produce truncated JSON.
- [ ] A second wizard mutation receives an explicit conflict while the first holds the lock.
- [ ] Stale Circuit IR is removed or clearly marked non-current after a spec revision.
- [ ] Malformed `job.json` is not silently omitted from the jobs endpoint.
- [ ] Malformed `wizard.json` is not silently reset.

### P2 acceptance criteria

- [ ] A process interruption cannot leave a partially written `job.json` or `wizard.json` in the normal write path.
- [ ] Concurrent wizard mutations cannot overwrite each other silently.
- [ ] Lock contention is visible and bounded.
- [ ] Stale spec/IR sidecars cannot masquerade as current state.
- [ ] Malformed persisted state is surfaced, not defaulted or skipped.

---

# P3. Correct wizard failure semantics and observability

## P3.1 Define the wizard error taxonomy

Add or refine typed errors so the API can distinguish these cases:

- [ ] Invalid wizard state transition: HTTP 409.
- [ ] Session mutation already in progress: HTTP 409.
- [ ] Missing wizard session: HTTP 404.
- [ ] LLM provider disabled or unavailable due to configuration: HTTP 503.
- [ ] Upstream provider transport failure: HTTP 502 or 503, chosen consistently.
- [ ] Provider returned malformed structured output after bounded repair attempts: HTTP 502.
- [ ] User-supplied unsupported circuit request: HTTP 422 or an explicit supported wizard state, preserving current product behavior.
- [ ] Unexpected internal exception: HTTP 500 with sanitized public detail.
- [ ] Persistence failure: HTTP 500 or 503 with a typed safe code.

- [ ] Preserve `POST /api/netlists/validate` HTTP 200 plus `valid: false` behavior for ordinary user-correctable Circuit IR validation.
- [ ] Do not conflate user validation errors with internal server errors.

## P3.2 Persist failure state before returning a failed mutation

For wizard operations that have a session ID:

- [ ] Persist a sanitized failed-state record before returning the API error when persistence is still functional.
- [ ] Include a safe machine-readable error code in the session.
- [ ] Preserve the last known good spec and IR according to the existing invalidation rules.
- [ ] Do not save a partially parsed provider response as approved/current state.
- [ ] Do not set `completed` after any failed generation step.
- [ ] If persistence itself fails, log both the original operation error and the persistence error.

## P3.3 Replace broad warning-only exception handling

Update `src/kicad_pcb_web/services/wizard.py` and helpers.

- [ ] Expected typed operational errors may be logged without a traceback at an appropriate level.
- [ ] Unexpected exceptions must use `LOGGER.exception(...)` while inside the exception handler.
- [ ] Include safe context:
  - [ ] Session ID.
  - [ ] Operation/stage.
  - [ ] Provider name.
  - [ ] Prompt version.
  - [ ] Elapsed time.
  - [ ] Exception type.
- [ ] Exclude:
  - [ ] API keys.
  - [ ] Authorization headers.
  - [ ] Full provider request bodies unless explicit redacted debug capture is enabled.
  - [ ] Raw provider response bodies from normal logs.
- [ ] Remove any `LOGGER.warning` use that is the only record of an unexpected exception and loses the traceback.

## P3.4 Sanitize public internal-error messages

The current `_set_error()` stores `str(exc)` for unexpected exceptions. Replace that behavior.

- [ ] Public internal errors must use a stable message such as `An unexpected internal error occurred.`
- [ ] Include a stable code such as `INTERNAL_SERVER_ERROR`.
- [ ] Include a server-generated correlation/error ID.
- [ ] Log the correlation ID with the traceback.
- [ ] Do not expose filesystem paths, provider response bodies, environment values, secrets, stack traces, or raw exception strings through the normal API.
- [ ] Preserve useful typed `UserError` messages when they are explicitly safe for users.

Suggested public shape:

```json
{
  "type": "internal_error",
  "code": "INTERNAL_SERVER_ERROR",
  "message": "An unexpected internal error occurred.",
  "details": {
    "error_id": "err_..."
  }
}
```

## P3.5 Return non-success HTTP status for failed wizard mutations

- [ ] A wizard mutation that does not complete its requested operation must not appear as a normal successful HTTP mutation solely because a failed session record exists.
- [ ] Persist the failed session, then raise the typed web-layer exception.
- [ ] Include the session ID and safe error code in the response detail so the frontend can refetch the persisted session.
- [ ] Update the frontend API layer to parse the structured failure.
- [ ] Update React Query mutation handlers to invalidate/refetch the wizard session after an operation failure.
- [ ] Display a clear error banner and preserve navigation to the recoverable blocking step.
- [ ] Do not automatically retry provider mutations unless the user explicitly retries.
- [ ] Do not silently switch providers or modes.

## P3.6 Preserve explicit non-fatal preview warnings

- [ ] Keep preview generation failure non-fatal only for the preview artifact.
- [ ] Continue returning `PREVIEW_GENERATION_SKIPPED` in the job warnings.
- [ ] Ensure the frontend displays the warning.
- [ ] Do not hide it because `WarningsPanel` returns `null` for an empty list; the warning list must actually contain the warning.
- [ ] Do not catch failures from project generation, validation, ZIP creation, or canonical job persistence under the preview-only exception handler.

## P3.7 Add error-semantics tests

- [ ] Unexpected spec-generation exception logs a traceback.
- [ ] Unexpected IR-generation exception logs a traceback.
- [ ] Public error payload does not contain the raw exception string.
- [ ] Public error contains an error ID that appears in captured logs.
- [ ] Provider-disabled wizard request returns the selected non-success status.
- [ ] Provider timeout returns the selected upstream-failure status.
- [ ] Malformed provider JSON after all repair attempts returns an explicit upstream failure.
- [ ] Invalid wizard transition returns HTTP 409.
- [ ] Lock contention returns HTTP 409.
- [ ] Failed mutation persists a failed session that can be fetched afterward.
- [ ] Frontend mutation error refetches and renders the persisted session state.
- [ ] Validation endpoint still returns HTTP 200 with `valid: false` for user-correctable netlist errors.

### P3 acceptance criteria

- [ ] Unexpected exceptions retain server tracebacks.
- [ ] Raw exception details are not exposed to clients.
- [ ] Failed wizard mutations use non-success HTTP status codes.
- [ ] The failed session remains inspectable and recoverable where possible.
- [ ] No implicit provider retry or provider fallback is introduced.

---

# P4. Make packaging and installed frontend delivery reliable

## P4.1 Explicitly include the SPA in Python package data

Update `pyproject.toml`.

- [ ] Add `kicad_pcb_web` static assets to `[tool.setuptools.package-data]`.
- [ ] Include at least:
  - [ ] `static/spa/index.html`
  - [ ] `static/spa/favicon.svg`
  - [ ] `static/spa/assets/*`
- [ ] Use a supported recursive pattern only after verifying it appears in the built wheel.
- [ ] Keep the existing symbol resources included for `kicad_pcb`.
- [ ] Do not remove one package's data while adding the other.

Possible shape:

```toml
[tool.setuptools.package-data]
kicad_pcb = ["resources/symbols/*.kicad_sym"]
kicad_pcb_web = [
    "static/spa/index.html",
    "static/spa/favicon.svg",
    "static/spa/assets/*",
]
```

## P4.2 Add source-distribution inclusion rules

- [ ] Add `MANIFEST.in` if needed by the selected setuptools configuration.
- [ ] Recursively include the built SPA files in the sdist.
- [ ] Include required README/license metadata.
- [ ] Do not include `frontend/node_modules/`, Playwright reports, coverage output, runtime `data/`, local config, or debug artifacts.

## P4.3 Define the supported frontend-build policy

- [ ] Keep `src/kicad_pcb_web/static/spa/` committed for RESTART1.
- [ ] Document that contributors must run `npm run build` after frontend changes.
- [ ] Document that package builds consume the committed bundle; they do not invoke Node implicitly from setuptools.
- [ ] Ensure CI detects a stale bundle.
- [ ] Do not add an opaque setuptools hook that downloads Node packages during wheel construction.

## P4.4 Correct production frontend metadata

- [ ] Change the generated page title from `frontend` to `KiCad PCB Web App` in the frontend source.
- [ ] Rebuild the committed bundle.
- [ ] Verify favicon and asset URLs use the `/static/spa/` base correctly.
- [ ] Verify direct browser loads for all SPA routes return the SPA shell:
  - [ ] `/`
  - [ ] `/wizard`
  - [ ] `/wizard/<session_id>`
  - [ ] `/wizard/<session_id>/<step>`
  - [ ] `/generate-json`
  - [ ] `/jobs`
  - [ ] `/jobs/<job_id>`
  - [ ] `/symbols`
  - [ ] `/setup`

## P4.5 Build and inspect wheel and sdist artifacts

- [ ] Build from a clean tree using `uv build` or the repository's documented equivalent.
- [ ] List wheel contents.
- [ ] Assert the SPA index, favicon, JavaScript, and CSS files are present.
- [ ] List sdist contents and assert the same source assets are present.
- [ ] Fail if the wheel contains runtime `data/`, API keys, local TOML config, test reports, `node_modules`, or debug files.

## P4.6 Add an installed-package smoke test

Create a script or pytest integration test that:

- [ ] Builds the wheel.
- [ ] Creates a fresh temporary virtual environment.
- [ ] Installs the wheel with the `web` extra and no editable source checkout.
- [ ] Changes the working directory outside the repository.
- [ ] Imports `kicad_pcb_web.main`.
- [ ] Creates a FastAPI `TestClient`.
- [ ] Verifies `GET /api/ui/bootstrap` returns HTTP 200.
- [ ] Verifies `GET /` returns HTTP 200 and the app title.
- [ ] Parses the returned HTML asset references.
- [ ] Verifies the referenced JavaScript and CSS return HTTP 200.
- [ ] Verifies at least one deep SPA route returns HTTP 200.
- [ ] Verifies missing static assets fail clearly rather than serving an unrelated page.

Run this smoke test in CI after the frontend build.

## P4.7 Update installation and startup documentation

- [ ] Document the source-checkout workflow.
- [ ] Document the installed-wheel workflow.
- [ ] State when Node is required and when it is not.
- [ ] State that core deterministic generation does not require an LLM provider.
- [ ] State that local/internal binding remains the default.
- [ ] Keep the warning against public exposure without authentication and sandboxing.

### P4 acceptance criteria

- [ ] A built wheel contains the current SPA.
- [ ] A built sdist contains the files needed to build/install correctly.
- [ ] The app serves its frontend after installation outside the repository.
- [ ] CI fails when the source and committed bundle diverge.
- [ ] No runtime data, secrets, or local configuration enters distribution artifacts.

---

# P5. Add end-to-end service and UI workflow tests

## P5.1 Replace shallow preview-warning tests with a real service test

The current preview test manually constructs the expected warning dictionary. Keep helper tests, but add a test that exercises production orchestration.

- [ ] Call `generate_project_from_netlist_job()` with a valid minimal request.
- [ ] Mock only the preview helper to raise `RuntimeError`.
- [ ] Allow the rest of the job orchestration to execute through the normal service boundaries, using deterministic fixtures/mocks only where external KiCad tooling is intentionally excluded.
- [ ] Assert the final job status is `succeeded`.
- [ ] Assert `project.zip` exists and is listed as an artifact.
- [ ] Assert the result contains `PREVIEW_GENERATION_SKIPPED`.
- [ ] Assert no preview artifact is listed.
- [ ] Assert a non-preview generation failure still produces a failed job.
- [ ] Remove or demote literal-dictionary-only tests that can pass when production integration is broken.

## P5.2 Add direct Circuit IR API workflow tests

Using FastAPI `TestClient` and temporary settings/data directories:

- [ ] `POST /api/netlists/validate` with valid Circuit IR.
- [ ] `POST /api/netlists/validate` with user-correctable invalid Circuit IR returns HTTP 200 and `valid: false`.
- [ ] `POST /api/jobs/from-netlist` creates an isolated job workspace.
- [ ] Successful generation returns a succeeded job and downloadable ZIP.
- [ ] Failed generation returns explicit failed job state and safe error details.
- [ ] `GET /api/jobs` lists persisted jobs newest first.
- [ ] `GET /api/jobs/<id>` returns the correct job.
- [ ] Artifact listing excludes `job.json`.
- [ ] Downloading `job.json` returns not found.
- [ ] Artifact traversal attempts are rejected.
- [ ] Invalid job IDs are rejected.
- [ ] Malformed persisted job state is surfaced explicitly.

## P5.3 Add a complete wizard service workflow with a fake LLM client

Use dependency injection or service-level fake clients. Do not add a fake provider mode to production configuration.

- [ ] Fake spec response produces a draft spec.
- [ ] User can revise the draft.
- [ ] Revision clears approval, IR, IR validation, and latest job ID.
- [ ] Approval is blocked when no spec exists.
- [ ] Approval is blocked when unsupported reasons exist.
- [ ] Approved spec generates Circuit IR through the fake provider.
- [ ] Generated IR is validated by the real deterministic validation service.
- [ ] Bounded repair calls include the prior IR and validation error.
- [ ] Valid repaired IR becomes current.
- [ ] Project generation uses `generate_project_from_netlist_job()`.
- [ ] Final session links to the generated job.
- [ ] Failed project generation sets the session to failed and preserves the job ID.
- [ ] The LLM client is closed after the request-scoped dependency completes.

## P5.4 Add wizard transition and concurrency tests

- [ ] Illegal deep-step access redirects or blocks at the server-authoritative step.
- [ ] Generate IR before spec approval fails explicitly.
- [ ] Generate project before valid IR fails explicitly.
- [ ] Clear IR before approval fails explicitly.
- [ ] Two simultaneous message mutations cannot both commit.
- [ ] A second mutation receives a conflict while the first is active.
- [ ] After the first mutation completes, a later mutation can proceed.
- [ ] No user message disappears after contention.
- [ ] No stale IR survives a conversation revision.

## P5.5 Add persistence recovery tests to API workflows

- [ ] Simulate a `job.json` replacement failure and assert the previous JSON remains valid.
- [ ] Simulate a `wizard.json` replacement failure and assert the previous JSON remains valid.
- [ ] Simulate malformed JSON and assert the endpoint returns an explicit persisted-state error.
- [ ] Verify no request reports success after its canonical state commit fails.
- [ ] Verify temporary files are cleaned.

## P5.6 Expand frontend unit tests

- [ ] Bootstrap loading state.
- [ ] Bootstrap failure and Retry button.
- [ ] Direct Circuit IR validation success.
- [ ] Direct Circuit IR validation `valid: false` response.
- [ ] Job generation success.
- [ ] Job generation failed state.
- [ ] Preview warning rendering.
- [ ] Wizard mutation non-success response and session refetch.
- [ ] HTTP 409 session-busy rendering.
- [ ] Sanitized internal error rendering with error ID.
- [ ] Artifact labels and URLs.
- [ ] Deep-route rendering.
- [ ] `WarningsPanel` renders nothing only when the list is genuinely empty.

## P5.7 Add browser-level Playwright smoke tests

- [ ] Install one supported browser in CI.
- [ ] Build the committed frontend before browser tests.
- [ ] Start Uvicorn with a temporary data directory and LLM provider disabled.
- [ ] Wait for `/api/ui/bootstrap` with a bounded health-check loop.
- [ ] Test home-page load.
- [ ] Test navigation among Circuit IR, Jobs, Symbols, Setup, and Wizard routes.
- [ ] Test direct JSON validation with the example payload.
- [ ] Test browser refresh on a deep route.
- [ ] Test the visible disabled-provider behavior on the wizard start page.
- [ ] Do not hide console errors; fail on uncaught page errors.
- [ ] Upload Playwright traces/screenshots only on failure unless debugging requires otherwise.

A server-backed wizard happy path may remain in Python integration tests with dependency overrides. Do not introduce an externally reachable fake LLM provider solely for Playwright.

## P5.8 Create one documented full verification command

- [ ] Update `scripts/validate.sh` or add `scripts/validate-all.sh` with an explicit command that runs:
  - [ ] Python static checks.
  - [ ] Python unit tests.
  - [ ] Frontend static checks.
  - [ ] Frontend unit tests.
  - [ ] Frontend build/bundle diff.
  - [ ] Installed-wheel smoke test.
  - [ ] Playwright smoke tests.
- [ ] Keep KiCad-required integration tests separately invokable and clearly labeled.

### P5 acceptance criteria

- [ ] Direct JSON generation has a real service/API happy-path test.
- [ ] Wizard generation has a real service/API happy-path test using a fake injected client.
- [ ] Preview degradation is tested through production orchestration.
- [ ] Persistence failures and mutation contention are tested.
- [ ] The installed frontend is exercised in a browser or TestClient outside the source tree.
- [ ] No production fake-provider fallback is added.

---

# P6. Correct layout-evaluation scoring semantics

## P6.1 Define explicit applicability and denominator rules

The newest `zone_positions` and `orientation_match` metrics must not award full credit when there is nothing comparable.

- [ ] Define the source component set as non-power source symbols.
- [ ] Treat a missing generated source reference as a mismatch, not as an omitted denominator entry.
- [ ] Use the number of expected source references as the denominator for reference-based placement/orientation metrics.
- [ ] Do not calculate only over `source_refs & generated_refs`, because one matching component out of ten must not receive full credit.
- [ ] Extra generated references must remain visible through role/count/electrical checks and must not improve placement/orientation scores.
- [ ] If the source has comparable symbols but none exist in generated output, score the relevant metric as zero and emit an explicit reason.
- [ ] If a fixture genuinely has no applicable non-power source symbols, represent the metric as not applicable rather than silently awarding maximum points.

## P6.2 Add structured sub-score details

Preserve compatibility where possible while making applicability visible.

- [ ] Introduce a structured internal result such as:

```python
@dataclass(frozen=True)
class SubScoreResult:
    score: float
    max_score: float
    applicable: bool
    reason: str | None = None
```

- [ ] Keep existing numeric `sub_scores` in serialized reports if downstream consumers require them.
- [ ] Add a `sub_score_details` or equivalent field with `score`, `max_score`, `applicable`, and reason.
- [ ] Compute the total from applicable metrics only if normalization is used.
- [ ] If no metrics are applicable, return score zero and an explicit failure reason; do not report 100.
- [ ] Update `_reports_fixture.py`, report models, JSON serialization, Markdown summaries, and tests together.
- [ ] Do not silently change the meaning of historical 0-100 thresholds without updating the documented threshold logic.

## P6.3 Correct zone-position comparison

- [ ] Calculate source zones using a bounding box derived from comparable non-power source symbols, not unrelated power symbols.
- [ ] Calculate generated zones using the corresponding expected generated symbols.
- [ ] Define behavior for missing generated references: mismatch.
- [ ] Define behavior for degenerate X or Y extents: middle column/row for that axis.
- [ ] Define and test boundary behavior at exact one-third and two-third positions.
- [ ] Ensure negative coordinates work.
- [ ] Ensure absolute translation and scale differences do not reduce the score when normalized zones remain equivalent.
- [ ] Ensure one matching reference cannot hide many missing references.

## P6.4 Make orientation quantization deterministic

Do not depend accidentally on Python's banker's-rounding behavior at exact half steps.

- [ ] Normalize angles into `[0, 360)`.
- [ ] Define exact 45-degree boundary behavior.
- [ ] Use an explicit formula, for example clockwise half-up quantization:

```python
def _quantize_rotation(rotation: float) -> int:
    normalized = rotation % 360.0
    return int((normalized + 45.0) // 90.0) % 4
```

- [ ] Confirm this policy matches intended KiCad orientation semantics.
- [ ] Test `-360`, `-315`, `-45`, `0`, `44.999`, `45`, `89.999`, `90`, `135`, `225`, `315`, `359.999`, `360`, and values above 360.
- [ ] A missing generated reference must count as an orientation mismatch.

## P6.5 Reassess score weights and reasons

- [ ] Confirm total maximum remains exactly 100 before any applicability normalization.
- [ ] Confirm each threshold for adding a reason scales with the metric's maximum.
- [ ] Confirm `zone_positions` severity and suggested files are appropriate.
- [ ] Confirm `orientation_match` has an explicit severity and suggested files.
- [ ] Ensure a no-common-reference condition produces a prominent reason rather than a clean report.
- [ ] Ensure electrical-equivalence failure can never be hidden by a high layout score.
- [ ] Ensure a full evaluation cannot pass when electrical equivalence failed.

## P6.6 Expand evaluation unit tests

Add tests for:

- [ ] Identical placement and orientation.
- [ ] Absolute translation with same normalized zones.
- [ ] Uniform scale with same normalized zones.
- [ ] No common references.
- [ ] One of many expected references present.
- [ ] Missing generated references.
- [ ] Extra generated references.
- [ ] Power-symbol-only differences.
- [ ] Degenerate X bounding box.
- [ ] Degenerate Y bounding box.
- [ ] Exact zone boundaries.
- [ ] Negative coordinates.
- [ ] Every rotation quantization boundary.
- [ ] Not-applicable metric serialization.
- [ ] Total-score normalization.
- [ ] Reasons and suggested-file mappings.
- [ ] Backward-compatible report fields if retained.

## P6.7 Re-run deterministic corpus evaluation

- [ ] Run unit tests without KiCad CLI.
- [ ] Run targeted `requires_kicad` evaluation tests where KiCad is available.
- [ ] Regenerate model-evaluation output under ignored `code_review/generated/model_eval/`.
- [ ] Compare summary changes caused by the corrected denominator/applicability rules.
- [ ] Investigate large score changes rather than immediately updating expected values.
- [ ] Commit only curated fixture/expected-data changes that are required and understood.
- [ ] Do not commit regenerable full generated projects or reports.

### P6 acceptance criteria

- [ ] No-comparison cases cannot receive full placement/orientation credit.
- [ ] Missing generated components reduce the score.
- [ ] Rotation boundaries are deterministic and explicitly tested.
- [ ] Metric applicability is visible in reports.
- [ ] Electrical failure cannot be masked by layout scoring.

---

# P7. Separate curated fixtures from generated output

## P7.1 Inventory currently tracked generated material

- [ ] Run `git ls-files` searches for:
  - [ ] `code_review/generated/`
  - [ ] `code_review/archive/**/generated/`
  - [ ] `evaluation_report.json`
  - [ ] `actionable_failures.md`
  - [ ] `generated.kicad_sch`
  - [ ] `generated_layout_features.json`
  - [ ] `generated_netlist.kicadxml`
  - [ ] Generated project ZIPs.
  - [ ] Playwright reports.
  - [ ] Frontend coverage.
  - [ ] `node_modules`.
- [ ] Produce a classification list before deleting anything:
  - [ ] Curated source fixture.
  - [ ] Curated expected result required by a test.
  - [ ] Historical review document.
  - [ ] Regenerable evaluation output.
  - [ ] Temporary local output.
  - [ ] Built SPA asset intentionally committed for packaging.

## P7.2 Preserve the fixture/evaluation boundary

- [ ] Keep curated fixtures in `tests/fixtures/model_corpus/<fixture_id>/`.
- [ ] Keep exact raw `source.kicad_sch` files unchanged.
- [ ] Keep deterministic normalized sources separate as `source_normalized.kicad_sch` where applicable.
- [ ] Preserve `source_embedded_symbols.sexpr` where required by the ingestion design.
- [ ] Do not create fixture directories for rejected corpus files; keep rejection information in ingestion reports/summaries.
- [ ] Keep fixture slug-collision behavior deterministic.
- [ ] Do not move regenerable evaluation output into fixture directories.

## P7.3 Remove regenerable output from the current tree

- [ ] Remove tracked copies of regenerable model-evaluation output from dated review archives unless a specific file is required as human-authored review evidence.
- [ ] Keep human-authored Markdown review conclusions where useful.
- [ ] Remove full generated KiCad projects, copied symbol libraries, generated netlists, and generated feature JSON when they can be recreated from committed fixtures.
- [ ] Do not delete a unique source fixture or license/provenance record.
- [ ] Do not rewrite Git history during RESTART1.

## P7.4 Strengthen `.gitignore`

- [ ] Preserve `/code_review/generated/`.
- [ ] Add an ignore rule for generated directories nested under `code_review/archive/` if those directories are not intended to be committed.
- [ ] Add explicit frontend rules:
  - [ ] `frontend/node_modules/`
  - [ ] `frontend/coverage/`
  - [ ] `frontend/test-results/`
  - [ ] `frontend/playwright-report/`
- [ ] Add package/build output rules if not already covered.
- [ ] Keep `src/kicad_pcb_web/static/spa/` tracked as the explicit packaging exception.
- [ ] Do not add a broad `*.json` or `*.kicad_sch` ignore rule that hides fixtures.

## P7.5 Add a generated-tree guard

Create a script such as `scripts/check-generated-tree.sh`.

- [ ] Fail if forbidden generated paths are tracked.
- [ ] Allow explicitly curated fixtures.
- [ ] Allow the committed SPA bundle.
- [ ] Print every offending path.
- [ ] Add the guard to local validation and CI.
- [ ] Test the guard with representative allowed and forbidden paths.

Possible implementation strategy:

```bash
#!/usr/bin/env bash
set -euo pipefail

forbidden="$(git ls-files \
  'code_review/archive/**/generated/**' \
  'frontend/node_modules/**' \
  'frontend/coverage/**' \
  'frontend/test-results/**' \
  'frontend/playwright-report/**')"

if [[ -n "$forbidden" ]]; then
  printf 'Forbidden generated files are tracked:\n%s\n' "$forbidden" >&2
  exit 1
fi
```

Adapt Git pathspec behavior and exemptions carefully; test it in the repository rather than assuming the example is complete.

## P7.6 Add deterministic cleanup commands

- [ ] Add or document a cleanup command for untracked evaluation output.
- [ ] The cleanup script must list what it will delete or be narrowly scoped.
- [ ] Do not delete curated fixtures.
- [ ] Do not delete the committed SPA bundle.
- [ ] Do not use an unrestricted `rm -rf` against a user-configurable path.
- [ ] Resolve and validate target paths before deletion.

## P7.7 Update corpus/evaluation documentation

- [ ] Document fixture inputs versus generated outputs.
- [ ] Document the canonical ignored output path.
- [ ] Document how to regenerate reports.
- [ ] Document which artifacts belong in CI rather than Git.
- [ ] Document that historical repository bloat remains in history and is not addressed by deleting current files.

### P7 acceptance criteria

- [ ] Curated fixtures remain complete and reproducible.
- [ ] Regenerable evaluation projects/reports are not tracked.
- [ ] The committed SPA bundle remains tracked and verified.
- [ ] CI prevents the same generated-output sprawl from returning.
- [ ] No Git-history rewrite is performed.

---

# P8. Integrated verification and closeout

## P8.1 Run all static and unit gates

- [ ] `uv sync --frozen --extra dev --extra web`
- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy src/kicad_pcb src/kicad_pcb_web`
- [ ] `uv run pytest tests/unit/ --cov --cov-report=term-missing`
- [ ] `cd frontend && npm ci`
- [ ] `npm run lint`
- [ ] `npm run test:run`
- [ ] `npm run build`
- [ ] `git diff --exit-code -- src/kicad_pcb_web/static/spa`

## P8.2 Run workflow and packaging gates

- [ ] Run the installed-wheel smoke test.
- [ ] Inspect wheel contents.
- [ ] Inspect sdist contents.
- [ ] Run the generated-tree guard.
- [ ] Run Playwright smoke tests.
- [ ] Run the updated local validation script from a clean tree.

## P8.3 Run KiCad-dependent gates where available

- [ ] Record `kicad-cli version`.
- [ ] Run `uv run pytest tests/integration/ -m requires_kicad` or the updated canonical command.
- [ ] Run targeted corpus evaluation.
- [ ] Record any test that cannot run and the exact missing dependency.
- [ ] Do not label unavailable KiCad verification as passed.

## P8.4 Manual workflow smoke test

Using a temporary data directory:

- [ ] Start the app bound to `127.0.0.1`.
- [ ] Load the home page.
- [ ] Validate the example Circuit IR.
- [ ] Generate and download a project ZIP.
- [ ] Confirm the downloaded ZIP opens and contains the expected KiCad project structure.
- [ ] Confirm preview absence is clearly warned when preview dependencies are intentionally unavailable.
- [ ] Configure a local LLM provider in a non-committed config.
- [ ] Create a wizard session.
- [ ] Revise and approve the spec.
- [ ] Generate and validate Circuit IR.
- [ ] Generate the project.
- [ ] Confirm a second concurrent mutation is rejected visibly rather than overwriting state.
- [ ] Confirm no credentials appear in logs or persisted debug artifacts.

## P8.5 Documentation consistency pass

- [ ] README commands match actual files and scripts.
- [ ] README workflow links point to existing workflows.
- [ ] README explains optional preview dependencies.
- [ ] README distinguishes unit, integration, package, frontend, and browser tests.
- [ ] README states the local/internal security boundary.
- [ ] No normal UI copy describes the archived OpenClaw skill as the current workflow.
- [ ] No documentation promises a fallback that the code does not implement.

## P8.6 Final dangerous-fallback audit

Search the final diff and affected modules for:

- [ ] `except Exception` without traceback logging or rethrow/typed conversion.
- [ ] `except ...: pass`.
- [ ] `return []` after persistence corruption.
- [ ] `return None` after required provider/configuration failures.
- [ ] Missing files treated as successful generation.
- [ ] Malformed persisted state replaced with defaults.
- [ ] Lock acquisition failure followed by unlocked mutation.
- [ ] Failed atomic replacement followed by an in-place-write fallback.
- [ ] Missing KiCad CLI reported as electrical-equivalence success.
- [ ] Missing Graphviz followed by an undocumented heuristic placement fallback.
- [ ] Provider failure followed by another provider automatically.
- [ ] Failed preview suppressing unrelated generation failures.
- [ ] Tests that assert a locally constructed constant rather than production behavior.
- [ ] TODO checkboxes marked complete without test evidence.

### P8 acceptance criteria

- [ ] All available required checks pass from a clean checkout.
- [ ] Unavailable external-tool checks are listed honestly.
- [ ] The application works from both a source checkout and an installed wheel.
- [ ] File-backed state is atomic, locked, and explicit on corruption.
- [ ] Wizard failures are observable, sanitized, and use correct HTTP semantics.
- [ ] Layout scoring no longer rewards missing comparisons.
- [ ] Generated output is separated from curated fixtures.

---

# Completion evidence

Fill this section during implementation. Do not mark RESTART1 complete with placeholders remaining.

## Baseline

- Starting branch:
- Starting commit:
- Initial working-tree state:
- Initial failing checks:

## Implementation commits

| Phase | Commit | Summary |
|---|---|---|
| P1 |  |  |
| P2 |  |  |
| P3 |  |  |
| P4 |  |  |
| P5 |  |  |
| P6 |  |  |
| P7 |  |  |
| P8 |  |  |

## Verification commands and results

| Command | Result | Evidence/log path |
|---|---|---|
| `uv run ruff check .` |  |  |
| `uv run ruff format --check .` |  |  |
| `uv run mypy src/kicad_pcb src/kicad_pcb_web` |  |  |
| Python unit tests with coverage |  |  |
| Frontend lint |  |  |
| Frontend unit tests |  |  |
| Frontend production build |  |  |
| SPA bundle diff check |  |  |
| Installed-wheel smoke test |  |  |
| Playwright smoke tests |  |  |
| KiCad integration tests |  |  |
| Corpus evaluation |  |  |
| Generated-tree guard |  |  |

## Skipped or unavailable checks

For every skipped check, record:

- Check:
- Exact reason:
- Missing dependency/tool:
- Whether the result is required before release:
- Follow-up owner/task:

## Remaining risks

- None recorded yet.

## Final readiness decision

- [ ] **RESTART1 complete:** all required tasks and available gates pass, unavailable gates are explicitly documented, and no known high-severity issue remains.
- [ ] **RESTART1 incomplete:** unresolved tasks or failures remain. Do not claim completion.
