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

- [x] Remove imports of `CONFIG_DIR`
- [x] Remove imports of `PROJECTS_DIR`
- [x] Import `get_config_dir`
- [x] Import `get_projects_dir`
- [x] Replace `CONFIG_DIR.exists()` with local `config_dir = get_config_dir()`
- [x] Replace `PROJECTS_DIR.mkdir(...)` with local `projects_dir = get_projects_dir()`
- [x] Replace `PROJECTS_DIR / ".write_probe"` with `projects_dir / ".write_probe"`
- [x] Preserve existing doctor command output/semantics

### 1.2 Fix `src/kicad_pcb/commands/project.py`

- [x] Remove imports of `PROJECTS_DIR`
- [x] Import `get_projects_dir`
- [x] Replace fallback `PROJECTS_DIR` with `str(get_projects_dir())`
- [x] Preserve existing behavior when config explicitly contains `projects_dir`

### 1.3 Fix `src/kicad_pcb/commands/_project.py`

- [x] Remove imports of `PROJECTS_DIR`
- [x] Import `get_projects_dir`
- [x] Replace fallback `PROJECTS_DIR` with `str(get_projects_dir())`
- [x] Preserve existing behavior when config explicitly contains `projects_dir`

### 1.4 Search for remaining runtime usage

- [x] Confirmed only `src/kicad_pcb/config.py` and `src/kicad_pcb/__init__.py` contain deprecated constants
- [x] Confirmed runtime command modules do not use deprecated constants
- [x] Confirmed production defaults still work when env overrides are absent

---

## 2. Strengthen config-isolation guard tests (P0 — regression prevention)

### 2.1 Update source guard test

In `tests/unit/test_config_isolation.py`:

- [x] Kept existing guard against direct `Path.home() / ".kicad-pcb"` outside config utilities
- [x] Added `test_source_guard_no_deprecated_constants_in_runtime_modules` — scans import
  lines in all `src/kicad_pcb/**/*.py` files (excluding `config.py` and `__init__.py`)
  and fails if any import a deprecated constant

### 2.2 Allow compatibility locations

- [x] `src/kicad_pcb/config.py` excluded
- [x] `src/kicad_pcb/__init__.py` excluded
- [x] Docs not scanned
- [x] Import-line-only check avoids false positives from comments or local variable names

### 2.3 Verify guard behavior

- [x] Guard would have failed on old `commands/doctor.py` (which imported `CONFIG_DIR, PROJECTS_DIR`)
- [x] Guard passes after command modules are fixed
- [x] Guard message clearly identifies offending file and constant name

---

## 3. Add focused runtime tests for project-dir override usage (P0 — behavioral coverage)

### 3.1 Test `cmd_doctor()` honors `KICAD_PCB_PROJECTS_DIR`

- [x] `test_cmd_doctor_projects_dir_uses_override` added to `test_config_isolation.py`
- [x] Sets `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects-override"`
- [x] Uses `FakeRunner({})` so no external tools required
- [x] Asserts "Projects dir writable" check message contains the override path
- [x] Asserts override dir was created
- [x] Not KiCad-dependent

### 3.2 Test project command fallback honors `get_projects_dir()`

- [x] `test_create_project_fallback_uses_projects_dir_override` added to `test_config_isolation.py`
- [x] Sets `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects-override"`
- [x] Calls `_create_project(name="MyProj", out_dir=None, description="")`
- [x] Asserts `ref.path.parent == projects_override`

### 3.3 Preserve existing config tests

- [x] All existing tests kept: `test_set_current_project_writes_under_override_dir`,
  default path tests, override path tests, current-session path tests

---

## 4. Update developer docs (P1 — documentation correctness)

### 4.1 Update manual isolation command

- [x] `CLAUDE.md` updated — manual isolation command now sets both `KICAD_PCB_CONFIG_DIR`
  and `KICAD_PCB_PROJECTS_DIR`

### 4.2 Confirm guidance

- [x] Docs mention both `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`
- [x] Docs say production defaults remain `~/.kicad-pcb` and `~/kicad-projects`
- [x] Docs warn tests should not patch deprecated constants like `CURRENT_PROJECT_FILE`

---

## 5. Run validation (P0 — must pass)

### 5.1 Required checks

- [x] Ruff passes — all checks passed
- [x] Format check passes — 219 files already formatted
- [x] Mypy passes — no issues found in 109 source files
- [x] Config isolation tests pass — 11 passed
- [x] Updated current-project/session tests pass — 37 passed (all targeted tests)

### 5.2 Explicit override validation

- [x] `KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" pytest tests/unit/test_config_isolation.py` — 11 passed

### 5.3 Broader validation

- [x] Full unit suite: 2542 passed
- [x] Web tests: 48 passed, 1 skipped (documented external-tool check)

### 5.4 Artifact hygiene

- [x] No generated cache artifacts tracked
- [x] Only intentional files changed

---

## 6. Completion notes

- [x] **Files changed**: `src/kicad_pcb/commands/doctor.py`, `src/kicad_pcb/commands/project.py`, `src/kicad_pcb/commands/_project.py`, `tests/unit/test_config_isolation.py`, `CLAUDE.md`
- [x] **Deprecated constants removed**: `CONFIG_DIR` and `PROJECTS_DIR` from `doctor.py`; `PROJECTS_DIR` from `project.py` and `_project.py`
- [x] **Dynamic helpers used**: `get_config_dir()` and `get_projects_dir()` in `doctor.py`; `get_projects_dir()` fallback in `project.py` and `_project.py`
- [x] **Guard test added**: `test_source_guard_no_deprecated_constants_in_runtime_modules` — import-line scan of all runtime source files
- [x] **Focused override tests added**: `test_cmd_doctor_projects_dir_uses_override`, `test_create_project_fallback_uses_projects_dir_override`
- [x] **Docs updated**: `CLAUDE.md` manual isolation command now sets both env vars
- [x] **Full unit suite**: 2542 passed
- [x] **Config/project test isolation**: preserved — tests write under `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR` overrides, not the user's real `~/.kicad-pcb` or `~/kicad-projects`.
- [x] **Artifact hygiene**: clean
- [x] **Remaining known issues**: none

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 5 |
| P1 — Docs/reporting | 4, 6 |
