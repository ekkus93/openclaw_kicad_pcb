# Python Test Home-Directory Isolation Spec

## Purpose

The latest review found that the UI/UX Batch 4.4 work is complete, but the full Python unit suite can still fail in sandboxed or CI-like environments because some tests or code paths try to read/write real user-home config files such as:

```text
~/.kicad-pcb/current_project.json
```

This is a repo-hygiene and test-isolation task. The goal is to make the Python unit suite independent of the real home directory, filesystem permissions under `$HOME`, and any developer-specific local config state.

## Problem

A full unit test run in a restricted environment failed with:

```text
PermissionError: [Errno 13] Permission denied: '/home/oai/.kicad-pcb/current_project.json'
```

That indicates at least one code path writes to `Path.home() / ".kicad-pcb"` during tests. Unit tests should not mutate or depend on real user-home state.

This is especially important for:

- CI containers,
- sandboxed review environments,
- read-only or restricted home directories,
- developer machines where local config should not affect tests,
- reproducible test results.

## Goals

1. Ensure the full Python unit suite does not read from or write to the real user home directory.
2. Provide a clean, centralized mechanism to override config/state paths in tests.
3. Preserve production behavior for real users.
4. Keep CLI behavior unchanged unless a test-specific environment variable is intentionally added.
5. Make config path handling explicit, testable, and documented.
6. Add regression tests proving home-directory isolation works.
7. Re-run the full Python validation suite.

## Non-Goals

- Do not change user-facing app behavior.
- Do not change frontend behavior.
- Do not remove persistent config functionality.
- Do not hardcode `/tmp` or `/mnt/data` in production code.
- Do not make tests pass by skipping ordinary unit tests.
- Do not catch `PermissionError` and silently ignore config writes in production paths.
- Do not weaken assertions or mark unrelated tests as KiCad-dependent.
- Do not introduce global mutable state that leaks between tests.

---

## 1. Audit current config/home-directory usage

### Required investigation

Search the Python source and tests for home-directory and config-path usage.

Suggested commands:

```bash
grep -R "Path.home" src tests -n
grep -R "\.kicad-pcb" src tests -n
grep -R "current_project.json" src tests -n
grep -R "HOME" src tests -n
grep -R "XDG" src tests -n
```

Inspect likely files such as:

```text
src/kicad_pcb/config.py
src/kicad_pcb/cli.py
src/kicad_pcb_web/*
tests/unit/*
tests/conftest.py
```

### Expected findings

There is likely a config module that resolves a path like:

```python
Path.home() / ".kicad-pcb" / "current_project.json"
```

or an equivalent helper for current project state.

### Acceptance criteria

- All source-level real-home access points are identified.
- All tests that touch config/current-project state are identified.
- The implementation plan is based on actual call sites, not assumptions.

---

## 2. Introduce testable config path resolution

### Required behavior

Production code should still use the existing default config location under the user’s home directory.

Tests must be able to redirect config paths to a temporary directory without monkeypatching `Path.home()` globally unless that is the least invasive option.

### Preferred design

Centralize path resolution in `src/kicad_pcb/config.py`.

Add or update helper functions such as:

```python
def get_config_dir() -> Path:
    ...

def get_current_project_path() -> Path:
    ...
```

Support an environment variable override for tests and advanced users, for example:

```text
KICAD_PCB_CONFIG_DIR
```

If the project already has a naming convention for environment variables, follow it.

Suggested behavior:

```python
def get_config_dir() -> Path:
    override = os.environ.get("KICAD_PCB_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kicad-pcb"
```

Then all config-path users should call:

```python
get_current_project_path()
```

instead of constructing `Path.home() / ".kicad-pcb" / "current_project.json"` directly.

### Requirements

- Use one canonical path resolver.
- Do not scatter environment-variable reads across the codebase.
- Do not cache the config directory at import time unless tests can safely reset it.
- Preserve production default path.
- Ensure parent directories are created only at the point of writing, not merely when resolving paths.
- Make failure behavior explicit for real permission errors in production.

### Acceptance criteria

- All source code config-path access goes through the central helper.
- Tests can override config location with `KICAD_PCB_CONFIG_DIR`.
- Production default remains `~/.kicad-pcb`.
- No test writes to the real home directory.

---

## 3. Add pytest fixture for isolated config state

### Required behavior

Tests should get a temporary config directory by default, or at least all tests that touch config state should use a fixture.

### Preferred fixture

In `tests/conftest.py`, add an autouse fixture if it is safe:

```python
@pytest.fixture(autouse=True)
def isolated_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    config_dir = tmp_path / ".kicad-pcb"
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(config_dir))
    yield config_dir
```

However, use autouse only if it does not break tests that intentionally verify production default behavior.

Alternative:

```python
@pytest.fixture
def isolated_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    config_dir = tmp_path / ".kicad-pcb"
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(config_dir))
    return config_dir
```

Then apply it to tests that touch config state.

### Recommendation

Prefer an **autouse fixture for unit tests** if feasible, because it prevents future accidental writes to real home. If the repository has integration tests that intentionally verify user-home behavior, scope the fixture to `tests/unit/conftest.py` or conditionally apply it only to unit tests.

### Requirements

- The fixture must not leak environment variables between tests.
- The fixture must not reuse the same config directory across tests unless intentional.
- Tests that verify default production path should explicitly delete the env var with `monkeypatch.delenv("KICAD_PCB_CONFIG_DIR", raising=False)`.
- Avoid monkeypatching `Path.home()` globally unless environment override is insufficient.

### Acceptance criteria

- Unit tests have isolated config state.
- No unit test writes to `/home/<user>/.kicad-pcb`.
- Tests remain deterministic and order-independent.

---

## 4. Refactor affected tests

### Required behavior

Any test currently depending on real-home config must be updated to use isolated config paths.

### Likely patterns to fix

Tests may do one or more of the following:

- call CLI commands that read/write the current project file,
- assume no existing current project exists,
- assume a pre-existing current project exists,
- mutate config state across tests,
- write directly to `Path.home() / ".kicad-pcb"`.

### Implementation requirements

Update tests to:

- use the isolated config fixture,
- set up expected current-project state explicitly,
- assert against the temp config path,
- avoid relying on a developer’s existing config file,
- avoid modifying the real home directory.

### Regression tests to add

Add tests for config path behavior:

1. Default path resolver returns `Path.home() / ".kicad-pcb"` when env override is absent.
2. Env override path is used when `KICAD_PCB_CONFIG_DIR` is set.
3. Current project path is under the overridden config dir.
4. Writing current project creates parent directory under the override path.
5. CLI/current-project commands use the override path in tests.

### Acceptance criteria

- A restricted or read-only home directory does not break ordinary unit tests.
- Test setup and assertions are explicit.
- No hidden dependency on local developer config remains.

---

## 5. Add guardrail test against real-home writes

### Purpose

Prevent regressions where future tests accidentally write to the real home directory again.

### Possible approaches

#### Option A — Autouse fixture with sentinel home

Use `monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(tmp_path / "config"))` for unit tests and add a test that verifies config writes land there.

#### Option B — Monkeypatch `Path.home()` in selected tests

For specific tests, monkeypatch `Path.home()` to an unwritable or sentinel path and verify no writes occur there when the env override is set.

#### Option C — Static-ish test for direct usage

Add a test or lint-like check that searches source files for direct occurrences of:

```text
Path.home() / ".kicad-pcb"
```

and fails if found outside `config.py`.

This can be a simple unit test that reads source text.

### Recommendation

Use A plus C if practical:

- A protects runtime behavior.
- C prevents direct path construction from creeping back into source.

### Acceptance criteria

- Direct source construction of `Path.home() / ".kicad-pcb"` is prevented or documented.
- Runtime tests prove override paths are honored.
- Regression risk is reduced.

---

## 6. Documentation updates

### Required docs

Update developer docs or validation docs to mention the test config override.

Potential files:

```text
CLAUDE.md
README.md
docs/*
```

### Suggested content

```text
Python tests must not read or write real user-home config. Tests use `KICAD_PCB_CONFIG_DIR` to redirect config state to a temporary directory. Production defaults remain `~/.kicad-pcb`.
```

Also document manual validation:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/
```

### Acceptance criteria

- Future agents know not to use real home config in tests.
- The override variable is documented.
- Production behavior is clearly distinguished from test behavior.

---

## 7. Validation

Run the full relevant validation set.

### Python/backend

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

### Optional home-isolation validation

Run tests with an explicit temp config dir:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

If possible, also simulate an unwritable home:

```bash
HOME="$(mktemp -d)" KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

### Frontend

No frontend changes are expected, but if touched:

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

### Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- Full unit suite passes without writing to real home.
- Web tests pass or skip only documented external-tool checks.
- Ruff, format, and mypy pass.
- No generated cache artifacts are tracked.
- Only intentional files are changed.

---

## Completion notes required

Claude Code should report:

- files changed,
- all direct home/config path call sites found,
- chosen config override variable name,
- whether fixture is autouse or explicit,
- tests updated,
- new regression tests added,
- validation commands and results,
- whether real home directory was touched,
- artifact hygiene result,
- any remaining known issues.
