# Python Test Home-Directory Isolation — Follow-Up 2 TODO

Derived from review of the latest Python home-directory isolation follow-up. The core isolation patch is good, but a small cleanup pass is needed for documentation precision, a stronger deprecated-constant guard, stale `CLAUDE.md` frontend guidance, and environment-aware validation notes.

---

## 1. Clarify real-home-directory wording (P1 — documentation precision)

The previous notes say “Real home directory touched: no,” which is too broad. The code may legitimately probe `Path.home()` for optional tools. The important guarantee is that config/project tests do not write to the real default config/project locations.

### 1.1 Search for overbroad wording

- [x] Search current docs and TODO/completion notes for `Real home directory touched`
- [x] Search for similar wording such as:
  - [x] `real home touched`
  - [x] `home directory touched`
  - [x] `does not touch home`
  - [x] `no home access`

### 1.2 Replace with precise wording

Replace overbroad claims with wording like:

```text
No real ~/.kicad-pcb or ~/kicad-projects config/project writes.
```

or:

```text
Config/project test isolation is preserved: tests write under KICAD_PCB_CONFIG_DIR and KICAD_PCB_PROJECTS_DIR overrides, not the user's real config/project defaults.
```

### 1.3 Preserve production-default docs

- [x] Keep production defaults documented:
  - [x] config default: `~/.kicad-pcb`
  - [x] projects default: `~/kicad-projects`
- [x] Do not claim the whole codebase never calls `Path.home()`
- [x] Do not remove legitimate optional home probes unless separately required

---

## 2. Strengthen deprecated config-constant guard (P0 — regression prevention)

The current guard catches direct imports of deprecated constants. Strengthen it so module-qualified use is also caught.

Deprecated constants:

```text
CONFIG_DIR
CONFIG_FILE
PROJECTS_DIR
CURRENT_PROJECT_FILE
CURRENT_SESSION_FILE
```

Allowed compatibility files:

```text
src/kicad_pcb/config.py
src/kicad_pcb/__init__.py
```

### 2.1 Inspect existing guard

- [x] Open `tests/unit/test_config_isolation.py`
- [x] Locate `test_source_guard_no_deprecated_constants_in_runtime_modules`
- [x] Confirm current scan catches direct import-line violations
- [x] Confirm current scan does not catch `config.PROJECTS_DIR`-style access

### 2.2 Extend guard to catch module-qualified access

Preferred: AST-based implementation.

- [x] Parse runtime source files with `ast`
- [x] Exclude `src/kicad_pcb/config.py`
- [x] Exclude `src/kicad_pcb/__init__.py`
- [x] Detect `ImportFrom` nodes importing deprecated constants from config modules
- [x] Detect config module aliases, such as:
  - [x] `import kicad_pcb.config as config`
  - [x] `import kicad_pcb.config`
  - [x] `from kicad_pcb import config`
  - [x] relative equivalent imports, if present
- [x] Detect `Attribute` nodes that access deprecated constants through those aliases:
  - [x] `config.PROJECTS_DIR`
  - [x] `config.CONFIG_DIR`
  - [x] equivalent alias names
- [x] Report offending file and constant name

Regex-based implementation is acceptable only if it is readable and covered by tests.

### 2.3 Keep allowed usage allowed

- [x] `src/kicad_pcb/config.py` may define the constants
- [x] `src/kicad_pcb/__init__.py` may re-export compatibility constants
- [x] Runtime source modules may import/use dynamic helpers:
  - [x] `get_config_dir`
  - [x] `get_config_file`
  - [x] `get_projects_dir`
  - [x] `get_current_project_file`
  - [x] `get_current_session_file`

### 2.4 Verify guard catches old and indirect patterns

Add or update tests/helper assertions so the guard would catch:

- [x] `from kicad_pcb.config import PROJECTS_DIR`
- [x] `from ..config import CONFIG_DIR`
- [x] `import kicad_pcb.config as config` followed by `config.PROJECTS_DIR`
- [x] `from kicad_pcb import config` followed by `config.CONFIG_DIR`

Do this without permanently adding bad runtime source files. Options:

- [x] Extract the guard logic into a helper that can be unit-tested with temporary source text
- [x] Or add focused helper tests using `tmp_path`

### 2.5 Run guard tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [x] Guard tests pass
- [x] Error messages identify offending file and constant

---

## 3. Update stale frontend architecture guidance in `CLAUDE.md` (P1 — developer guidance)

`CLAUDE.md` still says or implies that all wizard/job UI lives in `frontend/src/App.tsx`. That is stale after the wizard refactor.

### 3.1 Find stale frontend guidance

- [x] Open `CLAUDE.md`
- [x] Search for `all wizard and job UI lives`
- [x] Search for `App.tsx`
- [x] Identify any stale frontend architecture statements

### 3.2 Replace with current architecture guidance

Update the guidance to reflect the current structure:

```text
Frontend state: the app shell and top-level routing live in frontend/src/App.tsx. Wizard UI is split across frontend/src/routes/WizardPage.tsx and frontend/src/routes/wizard/*. Job UI lives in frontend/src/routes/JobPage.tsx and related route modules. Shared UI utilities/components live under frontend/src/components and frontend/src/utils*. Keep route components focused and avoid rebuilding a monolithic App.tsx.
```

Adjust paths if the current repo differs.

### 3.3 Avoid behavior changes

- [x] Do not change frontend code for this task
- [x] Do not perform another frontend refactor
- [x] Do not reintroduce a monolithic `App.tsx`

---

## 4. Make validation notes environment-aware (P1 — reproducibility)

Validation results vary depending on whether `kicad-cli`, KiCad system libraries, and `rsvg-convert` are installed.

### 4.1 Search for fixed pass/skip counts

- [x] Search docs/completion notes for fixed web-test counts such as `48 passed, 1 skipped`
- [x] Search for statements implying skip counts are universal
- [x] Search for validation notes that omit tool availability

### 4.2 Add environment-aware language

Add wording like:

```text
Web test pass/skip counts are environment-dependent. Tests marked requires_kicad or requires_generation_pipeline may skip when kicad-cli, KiCad system symbol libraries, or rsvg-convert are unavailable. Record the exact local counts and skip reasons in completion notes.
```

### 4.3 Update completion-notes template

Ensure future completion notes ask Claude Code to report:

- [x] exact command run
- [x] local pass/fail/skip count
- [x] whether `kicad-cli` was available
- [x] whether KiCad system symbol libraries were available, if checked
- [x] whether `rsvg-convert` was available
- [x] skipped tests and skip reasons

### 4.4 Keep normal validation clear

Normal non-KiCad validation should remain easy to run. Keep or document commands like:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
uv run --extra dev --extra web python -m pytest tests/unit/
```

Do not make ordinary tests require KiCad/rsvg.

---

## 5. Run validation (P0 — must pass)

### 5.1 Python checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [x] Ruff passes
- [x] Format check passes
- [x] Mypy passes

### 5.2 Isolation/guard tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [x] Isolation tests pass
- [x] Strengthened guard tests pass

### 5.3 Related targeted tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
```

- [x] Tests pass or KiCad-dependent tests skip cleanly
- [x] Skip reasons are clear

### 5.4 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [x] Web tests pass
- [x] Environment-dependent skips are documented

### 5.5 Frontend checks if frontend files are touched

Only required if frontend source/config/test files are changed.

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [x] Build passes if run
- [x] Lint passes if run
- [x] Tests pass if run

### 5.6 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [x] No generated cache artifacts are tracked
- [x] Only intentional files changed

---

## 6. Completion notes required

Claude Code should report:

- [x] Files changed
- [x] Exact wording changed for home-directory isolation claims
- [x] How deprecated constant guard was strengthened
- [x] Whether AST or regex scanning was used
- [x] Evidence the guard catches `config.PROJECTS_DIR`-style access
- [x] `CLAUDE.md` frontend architecture guidance update
- [x] Exact validation commands and local results
- [x] Whether `kicad-cli` was available
- [x] Whether KiCad system symbol libraries were available, if checked
- [x] Whether `rsvg-convert` was available
- [x] Skipped tests and skip reasons
- [x] Artifact hygiene result

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must pass | 2, 5 |
| P1 — Documentation/maintainer clarity | 1, 3, 4, 6 |

---

## Completion notes

**Files changed** (commit `4561d43` on `webapp`):
- `tests/unit/test_config_isolation.py` — AST-based guard + 6 helper unit tests
- `CLAUDE.md` — updated frontend architecture guidance and added environment-aware web-test note
- `docs/PYTHON_TEST_HOME_ISOLATION_TODO.md` — precise isolation wording
- `docs/PYTHON_TEST_HOME_ISOLATION_FOLLOWUP_TODO.md` — precise isolation wording

**Home-directory isolation wording** (Tasks 1):
Replaced `Real home directory touched: no` in both completion docs with:
> Config/project test isolation is preserved — tests write under `KICAD_PCB_CONFIG_DIR`
> and `KICAD_PCB_PROJECTS_DIR` overrides, not the user's real `~/.kicad-pcb` or
> `~/kicad-projects`. (Note added in `PYTHON_TEST_HOME_ISOLATION_TODO.md` that
> `doctor.py` legitimately probes `Path.home()` for the Freerouting JAR; that probe
> is read-only and is not a config/project write.)

**Deprecated-constant guard** (Task 2): AST scanning via `ast.parse()`.
- Extracted `_check_source_for_deprecated_constants(source, filename) -> list[str]`.
- Pass 1 (`ast.walk`): collect config-module aliases from `ImportFrom`/`Import` nodes;
  flag any `ImportFrom` that directly imports a deprecated constant from the config module.
- Pass 2 (`ast.walk`): flag any `Attribute` node whose value is a `Name` in
  `config_aliases` and whose `attr` is a deprecated constant.
- Fixed `_ALLOWED_FILES` from filename-based to full resolved-path matching
  (`src/kicad_pcb/config.py` and `src/kicad_pcb/__init__.py` only).
- `config.PROJECTS_DIR`-style access: confirmed caught by
  `test_guard_helper_catches_from_import_then_qualified_access` and
  `test_guard_helper_catches_module_qualified_access`.

**CLAUDE.md frontend guidance** (Task 3): replaced one-line stale note with
multi-line guidance pointing to `routes/WizardPage.tsx`, `routes/wizard/*`,
`routes/JobPage.tsx`, `components/`, `utils*`.

**Environment-aware web-test note** (Task 4): added bullet to CLAUDE.md Testing
section noting that `requires_kicad` / `requires_generation_pipeline` tests skip
when `kicad-cli`, KiCad symbol libraries, or `rsvg-convert` are unavailable, and
instructing Claude Code to record exact local counts.

**Validation** (Task 5):
- `ruff check .` — clean
- `ruff format --check .` — clean (219 files already formatted after auto-format)
- `mypy src/kicad_pcb src/kicad_pcb_web` — clean (109 files)
- `pytest tests/unit/test_config_isolation.py` — **17 passed** (11 original + 6 new)
- `pytest tests/unit/` — all passed (exit code 0)
- `pytest tests/web/ -q -rs` — **44 passed, 1 skipped**
  - Skip: `test_web_llm_clients.py:265` — requires `RUN_LIVE_PROVIDER_TESTS=1`
- `kicad-cli`: **present** (`/usr/bin/kicad-cli`)
- `rsvg-convert`: **present** (`/usr/bin/rsvg-convert`)
- Frontend not touched; `npm run build` not required.

**Artifact hygiene**: only the 4 intentional source files changed; no `__pycache__`
or `.pyc` artifacts tracked.
