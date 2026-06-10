# Python Test Home-Directory Isolation — Follow-Up 2 Spec

## Purpose

The previous Python home-directory isolation follow-up successfully moved runtime command modules away from deprecated module-level config constants and added tests for config/project directory overrides. This second follow-up is a smaller precision and regression-hardening patch.

The core isolation behavior is already working. This batch should improve documentation accuracy, strengthen the guard test against future regressions, and remove stale developer guidance that could mislead future frontend or test work.

This spec is derived from review of `docs/PYTHON_TEST_HOME_ISOLATION_FOLLOWUP_TODO(1).md` and the latest code snapshot.

---

## Goals

1. Make completion/docs wording precise about real home-directory access.
2. Strengthen the deprecated config-constant guard beyond direct import-line checks.
3. Update stale frontend architecture guidance in `CLAUDE.md`.
4. Make validation notes environment-aware, especially for KiCad/rsvg-dependent tests.
5. Preserve all current runtime behavior.

---

## Non-Goals

- Do not redesign the config module.
- Do not remove deprecated compatibility constants from `config.py` or `__init__.py`.
- Do not remove legitimate compatibility exports.
- Do not change production defaults:
  - config default remains `~/.kicad-pcb`
  - projects default remains `~/kicad-projects`
- Do not remove legitimate non-mutating home probes, such as optional Freerouting lookup paths, unless separately requested.
- Do not require KiCad/rsvg tools for ordinary non-integration tests.
- Do not perform another frontend refactor.

---

## 1. Clarify “real home directory touched” wording

### Problem

The previous completion notes say:

```text
Real home directory touched: no
```

That is too broad. The codebase may legitimately probe `Path.home()` for optional tools, such as Freerouting JAR locations. Those probes are not the same as writing config or project files into the real home directory.

### Required wording

Use precise language:

```text
No real ~/.kicad-pcb or ~/kicad-projects config/project writes.
```

or:

```text
Config/project test isolation is preserved: tests write under KICAD_PCB_CONFIG_DIR and KICAD_PCB_PROJECTS_DIR overrides, not the user's real config/project defaults.
```

### Implementation requirements

Update current docs/completion notes where this claim appears, especially any active follow-up TODO/docs or developer guidance that says “real home directory touched: no.”

Do not claim that the whole codebase never calls or probes `Path.home()` unless that is actually true.

### Acceptance criteria

- Docs no longer overclaim that the real home directory is never touched.
- Docs accurately distinguish config/project writes from harmless optional home probes.
- Production defaults remain documented.

---

## 2. Strengthen deprecated config-constant guard

### Problem

The current guard catches direct import-line violations such as:

```python
from kicad_pcb.config import PROJECTS_DIR
```

or:

```python
from ..config import CONFIG_DIR, PROJECTS_DIR
```

But future code could evade the guard by importing the config module and then using deprecated constants indirectly:

```python
import kicad_pcb.config as config

config.PROJECTS_DIR
```

The current code does not appear to use this pattern, but the guard should catch it before it regresses.

### Deprecated constants

The guard should protect against runtime use of:

```text
CONFIG_DIR
CONFIG_FILE
PROJECTS_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
```

### Allowed locations

These compatibility locations may still define/export deprecated constants:

- `src/kicad_pcb/config.py`
- `src/kicad_pcb/__init__.py`

Tests and docs may mention the constant names when explicitly testing or documenting compatibility behavior. The guard should focus on runtime source modules under `src/kicad_pcb`.

### Required behavior

The guard should fail if runtime source modules use deprecated constants either by direct import or module-qualified access.

Examples that should fail in runtime modules:

```python
from kicad_pcb.config import PROJECTS_DIR
from ..config import CONFIG_DIR
import kicad_pcb.config as config
config.PROJECTS_DIR
```

Examples that should remain allowed:

```python
from kicad_pcb.config import get_projects_dir
from ..config import get_config_dir
```

### Implementation guidance

Prefer an AST-based guard if practical. It should detect:

1. `ImportFrom` nodes that import deprecated constant names from config modules.
2. `Import` / `ImportFrom` aliases that bind the config module, followed by attribute access to deprecated constants.

A regex-based extension is acceptable for this small patch if it is clear and covered by tests, but AST is less brittle.

### Acceptance criteria

- Direct deprecated constant imports still fail the guard.
- `config.PROJECTS_DIR`-style runtime usage fails the guard.
- Compatibility exports in `config.py` and `__init__.py` remain allowed.
- The guard message identifies the offending file and constant.
- Existing isolation tests pass.

---

## 3. Update stale frontend architecture guidance in `CLAUDE.md`

### Problem

`CLAUDE.md` still contains stale guidance similar to:

```text
Frontend state: all wizard and job UI lives in frontend/src/App.tsx — one large component file.
```

That is no longer true after the wizard refactor. The wizard is split into route modules and shared components.

### Required behavior

Update the developer guidance so future Claude Code runs do not assume the frontend is still centralized in `App.tsx`.

### Suggested replacement

Use guidance like:

```text
Frontend state: the app shell and top-level routing live in frontend/src/App.tsx. Wizard UI is split across frontend/src/routes/WizardPage.tsx and frontend/src/routes/wizard/*. Job UI lives in frontend/src/routes/JobPage.tsx and related route modules. Shared UI utilities/components live under frontend/src/components and frontend/src/utils*. Keep route components focused and avoid rebuilding a monolithic App.tsx.
```

Adjust the exact text to match the current repo structure.

### Acceptance criteria

- `CLAUDE.md` no longer says all wizard/job UI lives in `App.tsx`.
- New guidance points to the current wizard/job route structure.
- Guidance warns against reintroducing a monolithic frontend component.
- No code behavior changes.

---

## 4. Make validation notes environment-aware

### Problem

Validation counts can differ depending on environment tooling. For example:

- Web tests may skip more cases when `kicad-cli` or `rsvg-convert` is unavailable.
- Full unit-suite timing can vary.
- KiCad-dependent tests may pass on one machine and skip on another.

Completion notes should report exact local results without implying fixed universal pass/skip counts.

### Required behavior

Validation docs/completion notes should state:

- exact command run,
- pass/fail/skip result,
- whether `kicad-cli` was available,
- whether `rsvg-convert` was available,
- which test groups skipped and why, when relevant.

### Suggested wording

```text
Web test pass/skip counts are environment-dependent. Tests marked requires_kicad or requires_generation_pipeline may skip when kicad-cli, KiCad system symbol libraries, or rsvg-convert are unavailable. Record the exact local counts and skip reasons in completion notes.
```

### Acceptance criteria

- Docs do not hard-code web pass/skip counts as universal.
- Docs explain why KiCad/rsvg-dependent tests may skip.
- Completion notes template asks for exact commands and local results.
- Normal non-KiCad validation remains clearly documented.

---

## 5. Preserve current behavior and validation

### Requirements

Run the relevant validation after changes.

Preferred commands:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

Frontend docs changed only if necessary, but if frontend files are touched, run:

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

### Acceptance criteria

- Python lint, format check, mypy, and targeted tests pass.
- Web tests pass or skip only expected environment-dependent cases.
- No generated cache artifacts are tracked.
- No production config/project path behavior changes.

---

## Completion notes required

Claude Code should report:

- Files changed.
- Exact wording changed for home-directory isolation claims.
- How the deprecated constant guard was strengthened.
- Whether AST or regex scanning was used.
- Evidence that `config.PROJECTS_DIR`-style access is caught.
- `CLAUDE.md` frontend architecture guidance update.
- Exact validation commands and local results.
- Availability of `kicad-cli`.
- Availability of `rsvg-convert`.
- Any skips and skip reasons.
