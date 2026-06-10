# Replies to Python Test Home-Directory Isolation Questions

Source questions file: `repsonses8.md`

## Q1 — `PROJECTS_DIR` scope

Use **Option 3: add a separate `KICAD_PCB_PROJECTS_DIR` environment variable**.

`KICAD_PCB_CONFIG_DIR` should only mean “where config/session/current-project state lives.” It should not also silently redefine the projects directory, because that overloads the meaning of the variable and can surprise users later.

### Required behavior

Add dynamic helpers in `src/kicad_pcb/config.py`:

```python
def get_config_dir() -> Path:
    override = os.environ.get("KICAD_PCB_CONFIG_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".kicad-pcb"


def get_projects_dir() -> Path:
    override = os.environ.get("KICAD_PCB_PROJECTS_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / "kicad-projects"
```

Then derive file paths dynamically:

```python
def get_current_project_file() -> Path:
    return get_config_dir() / "current_project.json"


def get_current_session_file() -> Path:
    return get_config_dir() / "current_session.json"


def get_config_file() -> Path:
    return get_config_dir() / "config.json"
```

`ensure_dirs()` should create:

```python
get_config_dir().mkdir(...)
get_projects_dir().mkdir(...)
```

### Test fixture requirement

The unit-test isolation fixture should set both variables:

```python
@pytest.fixture(autouse=True)
def isolated_config_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(tmp_path / ".kicad-pcb"))
    monkeypatch.setenv("KICAD_PCB_PROJECTS_DIR", str(tmp_path / "kicad-projects"))
    yield
```

This prevents both `~/.kicad-pcb` and `~/kicad-projects` writes during tests.

### Why not Option 1?

Skipping `PROJECTS_DIR` creation when `KICAD_PCB_CONFIG_DIR` is active changes `ensure_dirs()` semantics in a special-case way and can hide problems in tests.

### Why not Option 2?

Putting projects under `get_config_dir() / "projects"` is convenient for tests, but semantically weird for production and advanced users. Projects are user data, not config state.

### Acceptance criteria

- Production defaults remain:
  - `~/.kicad-pcb`
  - `~/kicad-projects`
- Tests set both overrides.
- No test writes to the real home directory.
- `ensure_dirs()` uses dynamic helpers only.

---

## Q2 — Module-level constants after the refactor

Use **Option 2: keep them for compatibility but mark them deprecated**.

Do **not** remove them in this patch. Removing exported constants from `__init__.py` is a breaking API change and is unnecessary for the current goal.

Do **not** keep them silently as-is without a warning/comment either. After dynamic overrides are added, import-time constants like `CONFIG_DIR` and `CURRENT_PROJECT_FILE` are stale whenever env overrides are active. They should be explicitly labelled as compatibility constants.

### Required behavior

Keep the existing exported constants for now:

```python
CONFIG_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
CONFIG_FILE
PROJECTS_DIR
```

But update comments/docstrings to make their status clear:

```python
# Deprecated compatibility constants.
# Runtime I/O must use get_config_dir(), get_current_project_file(),
# get_current_session_file(), get_config_file(), and get_projects_dir()
# so test/env overrides are honored.
```

If the project exposes these constants in `__init__.py`, keep exporting them for now, but prefer exporting the new helpers too.

### Important implementation rule

All I/O functions must stop using these constants.

For example:

```python
def get_current_project() -> Path | None:
    path = get_current_project_file()
    ...
```

not:

```python
def get_current_project() -> Path | None:
    path = CURRENT_PROJECT_FILE
    ...
```

### Optional follow-up

Later, in a separate API-cleanup batch, you can remove these constants or replace them with a lazy/proxy design. Do not do that now.

### Acceptance criteria

- Existing imports do not break.
- Constants are clearly deprecated/compatibility-only.
- Runtime reads/writes use dynamic helpers.
- Env overrides work even if constants still exist.

---

## Q3 — Existing `monkeypatch.setattr` workarounds

Remove the redundant per-test `monkeypatch.setattr("kicad_pcb.config.CURRENT_PROJECT_FILE", ...)` workarounds **after** the dynamic helpers and autouse isolation fixture are in place and passing.

The old monkeypatches target stale constants. Once I/O uses dynamic helper functions, patching `CURRENT_PROJECT_FILE` will either do nothing or create misleading tests. Leaving those patches in place would make the test suite harder to reason about.

### Required behavior

For these files:

```text
tests/unit/test_project_scaffold.py
tests/unit/test_session.py
tests/unit/test_model_corpus_evaluate_command.py
```

remove direct patches of:

```python
kicad_pcb.config.CURRENT_PROJECT_FILE
```

and replace them with one of these approaches:

1. Rely on the autouse fixture that sets `KICAD_PCB_CONFIG_DIR`.
2. If a test needs a specific config path, use `monkeypatch.setenv("KICAD_PCB_CONFIG_DIR", str(tmp_path / "config"))`.
3. Assert through the dynamic helper:

```python
from kicad_pcb.config import get_current_project_file
```

### Tests that call `set_current_project()`

For `test_patterns.py` and `test_env_resolution.py`, rely on the autouse fixture unless they need special setup. The important thing is that `set_current_project()` writes under the test temp config dir, not real home.

### Guardrail

Add or update tests so that patching the old constants is no longer needed. If any test still needs to patch a config path, patch the environment variable or a dynamic helper, not the deprecated constant.

### Acceptance criteria

- No unit test patches `CURRENT_PROJECT_FILE`.
- Tests use env overrides or fixtures.
- Config/current-project tests assert paths via dynamic helpers.
- No test writes to real `~/.kicad-pcb`.

---

## Final instruction summary for Claude Code

Proceed with these decisions:

1. Add both `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`.
2. Add dynamic helpers for config dir, projects dir, config file, current project file, and current session file.
3. Keep existing module-level constants for compatibility, but mark them deprecated/compatibility-only.
4. Route all runtime I/O through the new dynamic helpers.
5. Add an autouse unit-test fixture that sets both config and projects dir overrides to `tmp_path`.
6. Remove old `monkeypatch.setattr(...CURRENT_PROJECT_FILE...)` workarounds after the fixture works.
7. Update tests to assert against dynamic helper paths where needed.
8. Validate that unit tests do not write to real `~/.kicad-pcb` or `~/kicad-projects`.
