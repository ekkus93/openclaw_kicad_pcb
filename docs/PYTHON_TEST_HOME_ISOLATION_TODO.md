# Python Test Home-Directory Isolation TODO

Derived from the Batch 4.4 review. The UI/UX and KiCad marker cleanup work is complete, but the full Python unit suite can still fail in sandboxed environments when tests or code write to real user-home config, especially `~/.kicad-pcb/current_project.json`.

Goal: make Python unit tests independent of the real home directory.

---

## 1. Audit current home/config path usage (P0 — discovery)

### 1.1 Search source and tests

Run:

```bash
grep -R "Path.home" src tests -n
grep -R "\.kicad-pcb" src tests -n
grep -R "current_project.json" src tests -n
grep -R "HOME" src tests -n
grep -R "XDG" src tests -n
```

- [ ] Identify every source file that constructs a home/config path
- [ ] Identify every test file that reads/writes current-project config
- [ ] Identify any direct `Path.home() / ".kicad-pcb"` construction outside config utilities
- [ ] Identify CLI commands that read/write current project state

### 1.2 Inspect likely files

- [ ] Inspect `src/kicad_pcb/config.py`
- [ ] Inspect CLI modules that use config/current-project state
- [ ] Inspect `tests/conftest.py`
- [ ] Inspect affected `tests/unit/*` files
- [ ] Record which tests currently touch real home

---

## 2. Centralize config path resolution (P0 — production-safe design)

### 2.1 Add or update config helpers

In `src/kicad_pcb/config.py`, ensure there are canonical helpers such as:

- [ ] `get_config_dir() -> Path`
- [ ] `get_current_project_path() -> Path`

Use existing names if equivalent helpers already exist.

### 2.2 Add environment override

- [ ] Add support for an override environment variable, preferably `KICAD_PCB_CONFIG_DIR`
- [ ] If `KICAD_PCB_CONFIG_DIR` is set, config paths resolve under that directory
- [ ] If unset, production default remains `Path.home() / ".kicad-pcb"`
- [ ] Do not read the env var in scattered call sites
- [ ] Do not cache the path at import time unless tests can reset it safely

Suggested behavior:

```python
def get_config_dir() -> Path:
    override = os.environ.get("KICAD_PCB_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kicad-pcb"
```

### 2.3 Route all config/current-project path usage through helpers

- [ ] Replace direct home-path construction with helper calls
- [ ] Ensure current-project reads use `get_current_project_path()`
- [ ] Ensure current-project writes use `get_current_project_path()`
- [ ] Create parent directories only when writing
- [ ] Preserve production default behavior

---

## 3. Add isolated config fixture for tests (P0 — test isolation)

### 3.1 Decide fixture scope

Choose one:

- [ ] Preferred: autouse fixture for unit tests
- [ ] Alternative: explicit fixture applied only to config-touching tests

Prefer autouse if it does not break tests that intentionally verify production default path.

### 3.2 Implement fixture

Add to the appropriate conftest file:

- [ ] `tests/conftest.py`, or
- [ ] `tests/unit/conftest.py` if scope should be unit-only

Suggested fixture:

```python
@pytest.fixture(autouse=True)
def isolated_config_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    config_dir = tmp_path / ".kicad-pcb"
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(config_dir))
    yield config_dir
```

Adjust return/yield typing to match project style.

### 3.3 Avoid fixture leaks

- [ ] Ensure env var is restored after each test via `monkeypatch`
- [ ] Ensure each test gets an isolated temp config dir
- [ ] Tests that verify production default explicitly remove the env var
- [ ] Do not monkeypatch `Path.home()` globally unless necessary

---

## 4. Refactor affected tests (P0 — remove real-home dependency)

### 4.1 Update tests that read/write current project state

For each affected test:

- [ ] Use the isolated config fixture
- [ ] Set up current-project state explicitly if needed
- [ ] Assert files are written under the temp config dir
- [ ] Do not assume developer-local config exists
- [ ] Do not write to real `~/.kicad-pcb`

### 4.2 Add config path tests

Add or update tests for:

- [ ] Default config dir is `Path.home() / ".kicad-pcb"` when override is absent
- [ ] `KICAD_PCB_CONFIG_DIR` override is honored
- [ ] Current project path is under the overridden config dir
- [ ] Writing current project creates parent directory under the override path
- [ ] Reading current project uses the override path

### 4.3 Add CLI/current-project regression tests

If CLI commands interact with current-project state:

- [ ] Test CLI write path with `KICAD_PCB_CONFIG_DIR` set
- [ ] Test CLI read path with `KICAD_PCB_CONFIG_DIR` set
- [ ] Verify no real home writes occur

---

## 5. Add guardrail against future real-home writes (P1 — regression prevention)

### 5.1 Add source guard test if practical

Add a test that searches source files and fails if direct config-home construction appears outside the config module.

Example intent:

- [ ] Allow `Path.home()` in `src/kicad_pcb/config.py`
- [ ] Disallow direct `Path.home() / ".kicad-pcb"` elsewhere
- [ ] Disallow hardcoded `current_project.json` paths outside config helpers unless justified

### 5.2 Add runtime guard test

- [ ] Set `KICAD_PCB_CONFIG_DIR` to a temp directory
- [ ] Run a current-project write operation
- [ ] Assert the file appears under the temp config dir
- [ ] Assert no file appears under a monkeypatched/sentinel home dir

### 5.3 Keep guardrails maintainable

- [ ] Do not make brittle tests that fail on harmless docs/comments
- [ ] Restrict static checks to source files where possible
- [ ] Document any allowed exceptions

---

## 6. Update docs (P1 — developer guidance)

### 6.1 Document test config isolation

Update an appropriate developer doc such as `CLAUDE.md`, `README.md`, or a testing doc:

- [ ] Tests must not read/write real user-home config
- [ ] Tests use `KICAD_PCB_CONFIG_DIR` to redirect config state
- [ ] Production default remains `~/.kicad-pcb`

### 6.2 Document manual validation

Add a command like:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

Optionally:

```bash
HOME="$(mktemp -d)" KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

---

## 7. Run validation (P0 — must pass)

### 7.1 Python/backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Ruff passes
- [ ] Format check passes
- [ ] Mypy passes
- [ ] Full unit suite passes
- [ ] Web tests pass or skip only documented external-tool checks

### 7.2 Explicit home-isolation validation

Run:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

- [ ] Unit suite passes with explicit temp config dir

If feasible:

```bash
HOME="$(mktemp -d)" KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

- [ ] Unit suite passes with isolated HOME and config dir

### 7.3 Frontend validation

Only required if frontend files changed:

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Frontend validation skipped because no frontend files changed, or
- [ ] Frontend validation passes

### 7.4 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Only intentional files changed

---

## 8. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] All direct home/config path call sites found
- [ ] Chosen config override variable name
- [ ] Whether test fixture is autouse or explicit
- [ ] Tests updated
- [ ] Regression tests added
- [ ] Validation commands and results
- [ ] Whether real home directory was touched
- [ ] Artifact hygiene result
- [ ] Any remaining known issues

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 4, 7 |
| P1 — Guardrails/docs | 5, 6, 8 |
