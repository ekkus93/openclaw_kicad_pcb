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

- [x] Identify every source file that constructs a home/config path
- [x] Identify every test file that reads/writes current-project config
- [x] Identify any direct `Path.home() / ".kicad-pcb"` construction outside config utilities
- [x] Identify CLI commands that read/write current project state

### 1.2 Inspect likely files

- [x] Inspect `src/kicad_pcb/config.py`
- [x] Inspect CLI modules that use config/current-project state
- [x] Inspect `tests/conftest.py`
- [x] Inspect affected `tests/unit/*` files
- [x] Record which tests currently touch real home

---

## 2. Centralize config path resolution (P0 — production-safe design)

### 2.1 Add or update config helpers

In `src/kicad_pcb/config.py`, ensure there are canonical helpers such as:

- [x] `get_config_dir() -> Path`
- [x] `get_current_project_file() -> Path` (spec called it `get_current_project_path`)

Use existing names if equivalent helpers already exist.

### 2.2 Add environment override

- [x] Add support for `KICAD_PCB_CONFIG_DIR` override env var
- [x] Add support for `KICAD_PCB_PROJECTS_DIR` override env var (per replies8.md Q1)
- [x] If `KICAD_PCB_CONFIG_DIR` is set, config paths resolve under that directory
- [x] If unset, production default remains `Path.home() / ".kicad-pcb"`
- [x] Do not read the env var in scattered call sites
- [x] Do not cache the path at import time (dynamic helpers called at use-time)

### 2.3 Route all config/current-project path usage through helpers

- [x] Replace direct home-path construction with helper calls in all I/O functions
- [x] `ensure_dirs()`, `load_config()`, `save_config()` updated
- [x] `get_current_project()`, `set_current_project()` updated
- [x] `get_current_session()`, `set_current_session()`, `clear_current_session()` updated
- [x] `get_sessions_base_dir()` updated
- [x] Preserve production default behavior

---

## 3. Add isolated config fixture for tests (P0 — test isolation)

### 3.1 Decide fixture scope

- [x] Preferred: autouse fixture for unit tests (in `tests/unit/conftest.py`)

### 3.2 Implement fixture

- [x] Created `tests/unit/conftest.py` with `isolated_config_dirs` autouse fixture
- [x] Sets `KICAD_PCB_CONFIG_DIR` to `tmp_path / ".kicad-pcb"`
- [x] Sets `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "kicad-projects"`
- [x] Uses `monkeypatch.setenv` so env var is restored after each test
- [x] Yields (Iterator[None]) so cleanup happens via monkeypatch teardown

### 3.3 Avoid fixture leaks

- [x] Env var restored after each test via `monkeypatch`
- [x] Each test gets an isolated temp config dir via `tmp_path`
- [x] Tests that verify production default explicitly use `monkeypatch.delenv` first

---

## 4. Refactor affected tests (P0 — remove real-home dependency)

### 4.1 Update tests that read/write current project state

- [x] `tests/unit/test_project_scaffold.py` — removed two `monkeypatch.setattr(CURRENT_PROJECT_FILE)` calls; rely on autouse fixture
- [x] `tests/unit/test_session.py` — simplified `session_env` fixture (removed all `setattr` patches); replaced all `cfg_mod.CURRENT_SESSION_FILE` refs with `cfg_mod.get_current_session_file()`; fixed `test_get_current_project_malformed_state_raises` to use dynamic helper
- [x] `tests/unit/test_model_corpus_evaluate_command.py` — removed `monkeypatch.setattr(CURRENT_PROJECT_FILE)`, assert via `config_mod.get_current_project_file()`
- [x] `tests/unit/test_patterns.py` — already works via autouse fixture (5 `set_current_project()` calls now write to tmp_path)
- [x] `tests/unit/test_env_resolution.py` — already works via autouse fixture

### 4.2 Add config path tests

- [x] Default config dir is `Path.home() / ".kicad-pcb"` when override is absent
- [x] `KICAD_PCB_CONFIG_DIR` override is honoured
- [x] Default projects dir is `Path.home() / "kicad-projects"` when override is absent
- [x] `KICAD_PCB_PROJECTS_DIR` override is honoured
- [x] Current project path is under the overridden config dir
- [x] Current session path is under the overridden config dir
- [x] Writing current project creates the file under the override path

### 4.3 Add CLI/current-project regression tests

- [x] `test_set_current_project_writes_under_override_dir` — verifies override dir used

---

## 5. Add guardrail against future real-home writes (P1 — regression prevention)

### 5.1 Add source guard test

- [x] `test_source_guard_no_hardcoded_home_kicad_pcb_outside_config` — searches `src/kicad_pcb/**/*.py` and fails if `Path.home() / ".kicad-pcb"` appears outside `config.py`
- [x] Config.py itself is excluded (that's the one allowed location)

### 5.2 Add runtime guard test

- [x] `test_set_current_project_writes_under_override_dir` — sets explicit override, calls `set_current_project()`, asserts file appears in override dir

### 5.3 Keep guardrails maintainable

- [x] Static check restricted to `src/kicad_pcb/` source files only
- [x] No brittle string matching against docs or comments

---

## 6. Update docs (P1 — developer guidance)

### 6.1 Document test config isolation

- [x] `CLAUDE.md` updated with note in Testing section: describes both env vars, the autouse fixture, rule against patching deprecated constants

### 6.2 Document manual validation

- [x] `CLAUDE.md` includes manual isolation check command:
  `KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q`

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

- [x] Ruff passes — all checks passed
- [x] Format check passes — 219 files already formatted
- [x] Mypy passes — no issues found in 109 source files
- [x] Full unit suite passes — 2539 passed
- [x] Web tests pass — 48 passed, 1 skipped (documented external-tool check)

### 7.2 Explicit home-isolation validation

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

- [x] Unit suite passes with explicit temp config dir — 2539 passed

### 7.3 Frontend validation

- [x] Frontend validation skipped because no frontend files changed

### 7.4 Artifact hygiene

- [x] No generated cache artifacts tracked
- [x] Only intentional files changed

---

## 8. Completion notes

- **Files changed**: `src/kicad_pcb/config.py`, `src/kicad_pcb/__init__.py`, `CLAUDE.md`, `tests/unit/conftest.py` (new), `tests/unit/test_config_isolation.py` (new), `tests/unit/test_project_scaffold.py`, `tests/unit/test_session.py`, `tests/unit/test_model_corpus_evaluate_command.py`
- **All direct home/config path call sites found**: 5 module-level constants + 9 I/O functions in `config.py`; 3 test files using `monkeypatch.setattr(CURRENT_PROJECT_FILE)`
- **Chosen config override variable names**: `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`
- **Test fixture**: autouse in `tests/unit/conftest.py` — applies to all unit tests
- **Tests updated**: `test_project_scaffold.py`, `test_session.py`, `test_model_corpus_evaluate_command.py`
- **Regression tests added**: 8 tests in `test_config_isolation.py` (default path, override, file location, runtime write, source guard)
- **Validation**: ruff clean, format clean, mypy clean, 2539 unit tests passed, 48 web tests passed (1 skipped), isolation run passed
- **Real home directory touched**: no (the existing `~/.kicad-pcb/current_project.json` on dev machine was pre-existing, not created by our changes)
- **Artifact hygiene**: clean
- **Remaining known issues**: none

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 4, 7 |
| P1 — Guardrails/docs | 5, 6, 8 |
