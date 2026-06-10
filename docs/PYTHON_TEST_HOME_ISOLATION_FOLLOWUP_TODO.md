# Python Test Home-Directory Isolation Follow-Up TODO

Derived from the latest review of the Python home-directory isolation implementation. The first isolation patch added dynamic helpers and an autouse fixture, but some runtime command modules still use deprecated module-level constants that point to real home-directory paths.

Goal: finish routing runtime command code through dynamic helpers and strengthen tests so this does not regress.

---

## 1. Remove deprecated config constants from runtime command modules (P0 — must fix)

Deprecated compatibility constants may remain exported, but runtime command modules must not use them for I/O.

Deprecated constants:

```text
CONFIG_DIR
CONFIG_FILE
PROJECTS_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
```

### 1.1 Fix `src/kicad_pcb/commands/doctor.py`

- [ ] Remove imports of `CONFIG_DIR`
- [ ] Remove imports of `PROJECTS_DIR`
- [ ] Import `get_config_dir`
- [ ] Import `get_projects_dir`
- [ ] Replace `CONFIG_DIR.exists()` with `get_config_dir().exists()` or a local `config_dir`
- [ ] Replace `PROJECTS_DIR.mkdir(...)` with `get_projects_dir().mkdir(...)` or a local `projects_dir`
- [ ] Replace `PROJECTS_DIR / ".write_probe"` with `get_projects_dir() / ".write_probe"` or a local `projects_dir`
- [ ] Preserve existing doctor command output/semantics

### 1.2 Fix `src/kicad_pcb/commands/project.py`

- [ ] Remove imports of `PROJECTS_DIR`
- [ ] Import `get_projects_dir`
- [ ] Replace fallback usage of `PROJECTS_DIR` with `get_projects_dir()`
- [ ] If using `dict.get`, prefer `str(get_projects_dir())` when values are later wrapped in `Path(...)`
- [ ] Preserve existing behavior when config explicitly contains `projects_dir`

Example:

```python
projects_dir = Path(config.get("projects_dir", str(get_projects_dir())))
```

### 1.3 Fix `src/kicad_pcb/commands/_project.py`

- [ ] Remove imports of `PROJECTS_DIR`
- [ ] Import `get_projects_dir`
- [ ] Replace fallback usage of `PROJECTS_DIR` with `get_projects_dir()`
- [ ] Preserve existing behavior when config explicitly contains `projects_dir`

Example:

```python
base = Path(cfg.get("projects_dir", str(get_projects_dir())))
```

### 1.4 Search for remaining runtime usage

Run:

```bash
grep -R "from .*config import .*CONFIG_DIR" src/kicad_pcb -n
grep -R "from .*config import .*PROJECTS_DIR" src/kicad_pcb -n
grep -R "CURRENT_PROJECT_FILE\|CURRENT_SESSION_FILE\|CONFIG_FILE\|CONFIG_DIR\|PROJECTS_DIR" src/kicad_pcb -n
```

- [ ] Confirm only `src/kicad_pcb/config.py` and `src/kicad_pcb/__init__.py` contain compatibility constants
- [ ] Confirm runtime command modules do not use deprecated constants
- [ ] Confirm production defaults still work when env overrides are absent

---

## 2. Strengthen config-isolation guard tests (P0 — regression prevention)

### 2.1 Update source guard test

In `tests/unit/test_config_isolation.py`:

- [ ] Keep existing guard against direct `Path.home() / ".kicad-pcb"` outside config utilities
- [ ] Add guard against deprecated constant usage in runtime source files
- [ ] Disallow runtime imports/usages of:
  - [ ] `CONFIG_DIR`
  - [ ] `CONFIG_FILE`
  - [ ] `PROJECTS_DIR`
  - [ ] `CURRENT_PROJECT_FILE`
  - [ ] `CURRENT_SESSION_FILE`

### 2.2 Allow compatibility locations

- [ ] Allow `src/kicad_pcb/config.py`
- [ ] Allow `src/kicad_pcb/__init__.py`
- [ ] Do not scan docs
- [ ] Avoid brittle checks against comments if possible

### 2.3 Verify guard behavior

- [ ] Guard would have failed on old `commands/doctor.py`
- [ ] Guard passes after command modules are fixed
- [ ] Guard message clearly identifies offending file and constant

---

## 3. Add focused runtime tests for project-dir override usage (P0 — behavioral coverage)

### 3.1 Test `cmd_doctor()` honors `KICAD_PCB_PROJECTS_DIR`

Add a focused test proving doctor’s project-dir writability check uses the override.

- [ ] Set `KICAD_PCB_CONFIG_DIR` to `tmp_path / "config"`
- [ ] Set `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects"`
- [ ] Monkeypatch expensive/external probes if needed
- [ ] Run `cmd_doctor()` or extracted helper
- [ ] Assert project-dir write probe uses the override path
- [ ] Assert stale home-derived `PROJECTS_DIR` path is not created or used
- [ ] Test is not KiCad-dependent

### 3.2 Test project command fallback honors `get_projects_dir()`

Add a focused test for config without `projects_dir`.

- [ ] Set `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects-override"`
- [ ] Ensure loaded config lacks a `projects_dir` key
- [ ] Run relevant project command/helper path resolution
- [ ] Assert fallback base path is the override projects dir
- [ ] Assert stale `PROJECTS_DIR` constant is not used

### 3.3 Preserve existing config tests

- [ ] Keep `test_set_current_project_writes_under_override_dir`
- [ ] Keep default path tests
- [ ] Keep override path tests
- [ ] Keep current-session path tests

---

## 4. Update developer docs (P1 — documentation correctness)

### 4.1 Update manual isolation command

In `CLAUDE.md` or the relevant testing doc, replace commands that set only:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)"
```

with commands that set both:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

### 4.2 Confirm guidance

- [ ] Docs mention both `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`
- [ ] Docs say production defaults remain `~/.kicad-pcb` and `~/kicad-projects`
- [ ] Docs warn tests should not patch deprecated constants like `CURRENT_PROJECT_FILE`

---

## 5. Run validation (P0 — must pass)

### 5.1 Required checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_project_scaffold.py tests/unit/test_session.py tests/unit/test_model_corpus_evaluate_command.py -q
```

- [ ] Ruff passes
- [ ] Format check passes
- [ ] Mypy passes
- [ ] Config isolation tests pass
- [ ] Updated current-project/session tests pass

### 5.2 Explicit override validation

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py -q
```

- [ ] Explicit override validation passes

### 5.3 Broader validation if feasible

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Full unit suite passes, or timeout/failure is honestly reported
- [ ] Web tests pass or skip only documented external-tool checks

### 5.4 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Only intentional files are changed

---

## 6. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Deprecated constants removed from runtime command modules
- [ ] Dynamic helpers used in `doctor`, `project`, and `_project`
- [ ] Guard test added or strengthened
- [ ] Focused override tests added
- [ ] Docs updated with both env vars
- [ ] Validation commands and results
- [ ] Whether full unit suite was run or skipped/timed out
- [ ] Whether real home directory was touched
- [ ] Artifact hygiene result
- [ ] Remaining known issues, if any

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 5 |
| P1 — Docs/reporting | 4, 6 |
