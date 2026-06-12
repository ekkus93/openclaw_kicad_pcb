# Python Test Home-Directory Isolation Follow-Up Spec

## Purpose

The first Python test home-directory isolation batch implemented the right overall architecture: dynamic config/project path helpers, `KICAD_PCB_CONFIG_DIR`, `KICAD_PCB_PROJECTS_DIR`, an autouse unit-test fixture, and regression tests.

The latest review found that the implementation is close but not complete. Some runtime command modules still import and use deprecated module-level constants such as `CONFIG_DIR` and `PROJECTS_DIR`. Those constants are computed at import time from `Path.home()` and therefore do not honor the new environment-variable overrides. As a result, some commands can still touch real home-directory paths even when tests or callers set `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`.

This follow-up patch must finish the home-isolation work by routing remaining runtime command modules through dynamic helper functions, strengthening guardrails, and adding focused runtime tests.

## Problem Summary

The remaining problems are:

1. `src/kicad_pcb/commands/doctor.py` still imports and uses `CONFIG_DIR` and `PROJECTS_DIR`.
2. `src/kicad_pcb/commands/project.py` still uses `PROJECTS_DIR` as a fallback.
3. `src/kicad_pcb/commands/_project.py` still uses `PROJECTS_DIR` as a fallback.
4. The current guard test only catches direct `Path.home() / ".kicad-pcb"` construction. It misses deprecated constant imports/usages.
5. The developer docs show a manual isolation command that sets only `KICAD_PCB_CONFIG_DIR`, but the selected design requires both `KICAD_PCB_CONFIG_DIR` and `KICAD_PCB_PROJECTS_DIR`.
6. Focused tests do not yet prove that `cmd_doctor()` and project-command fallbacks honor the project-dir override.

## Goals

1. Remove runtime usage of deprecated config constants from command modules.
2. Ensure `doctor`, project creation, and project fallback logic honor dynamic helpers and environment overrides.
3. Strengthen tests so deprecated config constants cannot creep back into runtime modules.
4. Add focused runtime tests proving no real-home project/config directories are used when overrides are set.
5. Update developer documentation to set both config and projects overrides in manual isolation commands.
6. Preserve backward compatibility by keeping deprecated constants exported for now.
7. Preserve production default behavior when no overrides are set.

## Non-Goals

- Do not remove deprecated constants in this patch.
- Do not rename `KICAD_PCB_CONFIG_DIR`.
- Do not rename `KICAD_PCB_PROJECTS_DIR`.
- Do not change frontend behavior.
- Do not change public CLI semantics.
- Do not skip ordinary unit tests to hide home-directory issues.
- Do not mark these tests as KiCad-dependent.
- Do not introduce another broad config architecture rewrite.
- Do not catch and ignore real permission errors in production I/O paths unless the existing command semantics already handle them.

---

## 1. Remove deprecated constants from runtime command modules

### Problem

The deprecated compatibility constants are allowed to remain exported, but runtime code must not use them for I/O because they are computed once at import time and do not honor env overrides.

The key remaining modules are:

```text
src/kicad_pcb/commands/doctor.py
src/kicad_pcb/commands/project.py
src/kicad_pcb/commands/_project.py
```

### Required behavior

Runtime command modules should use dynamic helpers:

```python
get_config_dir()
get_projects_dir()
get_config_file()
get_current_project_file()
get_current_session_file()
```

as applicable.

### 1.1 `commands/doctor.py`

Replace imports like:

```python
from ..config import CONFIG_DIR, PROJECTS_DIR, discover_symbols_dir, get_current_project
```

with:

```python
from ..config import discover_symbols_dir, get_config_dir, get_current_project, get_projects_dir
```

Replace usages:

```python
CONFIG_DIR.exists()
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
probe = PROJECTS_DIR / ".write_probe"
```

with dynamic forms:

```python
config_dir = get_config_dir()
projects_dir = get_projects_dir()

config_dir.exists()
projects_dir.mkdir(parents=True, exist_ok=True)
probe = projects_dir / ".write_probe"
```

Use local variables if the command references the paths more than once.

### 1.2 `commands/project.py`

Replace fallback usage of `PROJECTS_DIR`, for example:

```python
projects_dir = Path(config.get("projects_dir", PROJECTS_DIR))
```

with:

```python
projects_dir = Path(config.get("projects_dir", str(get_projects_dir())))
```

or an equivalent helper-safe implementation.

Do not import `PROJECTS_DIR` in this runtime module.

### 1.3 `commands/_project.py`

Replace fallback usage of `PROJECTS_DIR`, for example:

```python
base = Path(cfg.get("projects_dir", PROJECTS_DIR))
```

with:

```python
base = Path(cfg.get("projects_dir", str(get_projects_dir())))
```

or an equivalent helper-safe implementation.

Do not import `PROJECTS_DIR` in this runtime module.

### Acceptance criteria

- No runtime module under `src/kicad_pcb/commands/` imports `CONFIG_DIR`, `CONFIG_FILE`, `PROJECTS_DIR`, `CURRENT_PROJECT_FILE`, or `CURRENT_SESSION_FILE`.
- Runtime command code uses dynamic helper functions.
- Env overrides are honored by `doctor` and project commands.
- Production defaults remain unchanged when env overrides are unset.

---

## 2. Strengthen guard tests for deprecated constant usage

### Problem

The existing source guard test only checks for direct hardcoded home paths such as:

```python
Path.home() / ".kicad-pcb"
```

This misses imports/usages of stale constants.

### Required behavior

Add or update a guard test so runtime source files cannot import deprecated config constants.

### Deprecated constants to guard

```text
CONFIG_DIR
CONFIG_FILE
PROJECTS_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
```

### Allowed files

The constants may remain in:

```text
src/kicad_pcb/config.py
src/kicad_pcb/__init__.py
```

They may also appear in documentation or tests that explicitly discuss deprecation, but runtime source modules should not rely on them.

### Recommended implementation

In `tests/unit/test_config_isolation.py`, add a guard that scans runtime Python source files under `src/kicad_pcb/`.

Exclude:

```text
src/kicad_pcb/config.py
src/kicad_pcb/__init__.py
```

Fail if any source file imports or uses deprecated constants from config.

Suggested patterns to check:

```text
from ..config import CONFIG_DIR
from ..config import PROJECTS_DIR
from kicad_pcb.config import CONFIG_DIR
CONFIG_DIR
CONFIG_FILE
PROJECTS_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
```

To avoid false positives, focus on source files and import/runtime usage rather than docs. If the test scans all source text, include a short allowlist for files where the names are legitimately discussed in comments. Prefer no comments referencing these names in runtime modules.

### Acceptance criteria

- The guard fails before command modules are fixed.
- The guard passes after command modules use dynamic helpers.
- The guard does not scan docs.
- The guard does not ban the compatibility constants from `config.py` or `__init__.py`.

---

## 3. Add focused runtime tests for overrides

### Problem

The current tests prove the config helpers work, but do not fully prove that command modules use the helpers.

### Required tests

Add tests to `tests/unit/test_config_isolation.py` or the relevant command test modules.

### 3.1 `cmd_doctor()` honors project-dir override

Test that `cmd_doctor()` uses `KICAD_PCB_PROJECTS_DIR` rather than the deprecated `PROJECTS_DIR` constant.

Suggested approach:

1. Use `tmp_path` and `monkeypatch`.
2. Set:
   - `KICAD_PCB_CONFIG_DIR` to `tmp_path / "config"`
   - `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects"`
3. Monkeypatch/avoid expensive external probes if needed.
4. Run `cmd_doctor()` or a minimal internal doctor check function.
5. Assert the write probe or created directory is under the override project dir.
6. Assert no directory was created under a monkeypatched/sentinel home dir.

If direct `cmd_doctor()` testing is hard because it probes many tools, isolate the project-dir writability check into a helper and test that helper. Keep behavior unchanged.

### 3.2 Project command fallback honors `get_projects_dir()`

Add a test where config lacks a `projects_dir` key.

Suggested approach:

1. Set `KICAD_PCB_PROJECTS_DIR` to `tmp_path / "projects-override"`.
2. Make `load_config()` or the config file return no `projects_dir`.
3. Run the relevant project command/helper path resolution.
4. Assert the fallback base path is the override projects dir, not the stale `PROJECTS_DIR` constant.

### 3.3 Existing `set_current_project()` regression remains valid

Keep the existing regression test that verifies `set_current_project()` writes under `KICAD_PCB_CONFIG_DIR`.

### Acceptance criteria

- Tests fail if command modules use deprecated constants.
- Tests pass when command modules use dynamic helpers.
- Tests do not touch the real home directory.
- Tests are not KiCad-dependent.

---

## 4. Update developer documentation

### Problem

The current manual isolation command in `CLAUDE.md` sets only:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)"
```

The chosen design requires both:

```text
KICAD_PCB_CONFIG_DIR
KICAD_PCB_PROJECTS_DIR
```

### Required update

Update `CLAUDE.md` or the relevant developer/testing doc to show:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

If there is a second isolated-home example, also set both variables there.

### Acceptance criteria

- Docs mention both env vars.
- Manual isolation command sets both env vars.
- Docs distinguish production defaults from test overrides.
- Docs continue to warn against patching deprecated constants in tests.

---

## 5. Re-run validation

### Required validation

Run:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_project_scaffold.py tests/unit/test_session.py tests/unit/test_model_corpus_evaluate_command.py -q
```

### Broader validation

Run if feasible:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

If full unit tests are slow in the environment, at least run the targeted tests and state that broader validation was skipped or timed out.

### Explicit override validation

Run:

```bash
KICAD_PCB_CONFIG_DIR="$(mktemp -d)" KICAD_PCB_PROJECTS_DIR="$(mktemp -d)" uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py -q
```

### Artifact hygiene

Run:

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- Ruff passes.
- Format check passes.
- Mypy passes.
- Targeted isolation tests pass.
- Updated command tests pass.
- Web tests pass or skip only documented external-tool checks.
- No generated cache artifacts are tracked.
- Only intentional files are changed.

---

## Completion notes required

Claude Code should report:

- files changed,
- deprecated constants removed from runtime command modules,
- dynamic helpers used in `doctor`, `project`, and `_project`,
- guard test added or strengthened,
- focused override tests added,
- docs updated with both env vars,
- validation commands and results,
- whether full unit suite was run or skipped/timed out,
- whether real home directory was touched,
- artifact hygiene result,
- remaining known issues, if any.
