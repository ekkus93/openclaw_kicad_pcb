# Python Test Home-Directory Isolation — Follow-Up 2 TODO

Derived from review of the latest Python home-directory isolation follow-up. The core isolation patch is good, but a small cleanup pass is needed for documentation precision, a stronger deprecated-constant guard, stale `CLAUDE.md` frontend guidance, and environment-aware validation notes.

---

## 1. Clarify real-home-directory wording (P1 — documentation precision)

The previous notes say “Real home directory touched: no,” which is too broad. The code may legitimately probe `Path.home()` for optional tools. The important guarantee is that config/project tests do not write to the real default config/project locations.

### 1.1 Search for overbroad wording

- [ ] Search current docs and TODO/completion notes for `Real home directory touched`
- [ ] Search for similar wording such as:
  - [ ] `real home touched`
  - [ ] `home directory touched`
  - [ ] `does not touch home`
  - [ ] `no home access`

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

- [ ] Keep production defaults documented:
  - [ ] config default: `~/.kicad-pcb`
  - [ ] projects default: `~/kicad-projects`
- [ ] Do not claim the whole codebase never calls `Path.home()`
- [ ] Do not remove legitimate optional home probes unless separately required

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

- [ ] Open `tests/unit/test_config_isolation.py`
- [ ] Locate `test_source_guard_no_deprecated_constants_in_runtime_modules`
- [ ] Confirm current scan catches direct import-line violations
- [ ] Confirm current scan does not catch `config.PROJECTS_DIR`-style access

### 2.2 Extend guard to catch module-qualified access

Preferred: AST-based implementation.

- [ ] Parse runtime source files with `ast`
- [ ] Exclude `src/kicad_pcb/config.py`
- [ ] Exclude `src/kicad_pcb/__init__.py`
- [ ] Detect `ImportFrom` nodes importing deprecated constants from config modules
- [ ] Detect config module aliases, such as:
  - [ ] `import kicad_pcb.config as config`
  - [ ] `import kicad_pcb.config`
  - [ ] `from kicad_pcb import config`
  - [ ] relative equivalent imports, if present
- [ ] Detect `Attribute` nodes that access deprecated constants through those aliases:
  - [ ] `config.PROJECTS_DIR`
  - [ ] `config.CONFIG_DIR`
  - [ ] equivalent alias names
- [ ] Report offending file and constant name

Regex-based implementation is acceptable only if it is readable and covered by tests.

### 2.3 Keep allowed usage allowed

- [ ] `src/kicad_pcb/config.py` may define the constants
- [ ] `src/kicad_pcb/__init__.py` may re-export compatibility constants
- [ ] Runtime source modules may import/use dynamic helpers:
  - [ ] `get_config_dir`
  - [ ] `get_config_file`
  - [ ] `get_projects_dir`
  - [ ] `get_current_project_file`
  - [ ] `get_current_session_file`

### 2.4 Verify guard catches old and indirect patterns

Add or update tests/helper assertions so the guard would catch:

- [ ] `from kicad_pcb.config import PROJECTS_DIR`
- [ ] `from ..config import CONFIG_DIR`
- [ ] `import kicad_pcb.config as config` followed by `config.PROJECTS_DIR`
- [ ] `from kicad_pcb import config` followed by `config.CONFIG_DIR`

Do this without permanently adding bad runtime source files. Options:

- [ ] Extract the guard logic into a helper that can be unit-tested with temporary source text
- [ ] Or add focused helper tests using `tmp_path`

### 2.5 Run guard tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [ ] Guard tests pass
- [ ] Error messages identify offending file and constant

---

## 3. Update stale frontend architecture guidance in `CLAUDE.md` (P1 — developer guidance)

`CLAUDE.md` still says or implies that all wizard/job UI lives in `frontend/src/App.tsx`. That is stale after the wizard refactor.

### 3.1 Find stale frontend guidance

- [ ] Open `CLAUDE.md`
- [ ] Search for `all wizard and job UI lives`
- [ ] Search for `App.tsx`
- [ ] Identify any stale frontend architecture statements

### 3.2 Replace with current architecture guidance

Update the guidance to reflect the current structure:

```text
Frontend state: the app shell and top-level routing live in frontend/src/App.tsx. Wizard UI is split across frontend/src/routes/WizardPage.tsx and frontend/src/routes/wizard/*. Job UI lives in frontend/src/routes/JobPage.tsx and related route modules. Shared UI utilities/components live under frontend/src/components and frontend/src/utils*. Keep route components focused and avoid rebuilding a monolithic App.tsx.
```

Adjust paths if the current repo differs.

### 3.3 Avoid behavior changes

- [ ] Do not change frontend code for this task
- [ ] Do not perform another frontend refactor
- [ ] Do not reintroduce a monolithic `App.tsx`

---

## 4. Make validation notes environment-aware (P1 — reproducibility)

Validation results vary depending on whether `kicad-cli`, KiCad system libraries, and `rsvg-convert` are installed.

### 4.1 Search for fixed pass/skip counts

- [ ] Search docs/completion notes for fixed web-test counts such as `48 passed, 1 skipped`
- [ ] Search for statements implying skip counts are universal
- [ ] Search for validation notes that omit tool availability

### 4.2 Add environment-aware language

Add wording like:

```text
Web test pass/skip counts are environment-dependent. Tests marked requires_kicad or requires_generation_pipeline may skip when kicad-cli, KiCad system symbol libraries, or rsvg-convert are unavailable. Record the exact local counts and skip reasons in completion notes.
```

### 4.3 Update completion-notes template

Ensure future completion notes ask Claude Code to report:

- [ ] exact command run
- [ ] local pass/fail/skip count
- [ ] whether `kicad-cli` was available
- [ ] whether KiCad system symbol libraries were available, if checked
- [ ] whether `rsvg-convert` was available
- [ ] skipped tests and skip reasons

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

- [ ] Ruff passes
- [ ] Format check passes
- [ ] Mypy passes

### 5.2 Isolation/guard tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [ ] Isolation tests pass
- [ ] Strengthened guard tests pass

### 5.3 Related targeted tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
```

- [ ] Tests pass or KiCad-dependent tests skip cleanly
- [ ] Skip reasons are clear

### 5.4 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Web tests pass
- [ ] Environment-dependent skips are documented

### 5.5 Frontend checks if frontend files are touched

Only required if frontend source/config/test files are changed.

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Build passes if run
- [ ] Lint passes if run
- [ ] Tests pass if run

### 5.6 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Only intentional files changed

---

## 6. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Exact wording changed for home-directory isolation claims
- [ ] How deprecated constant guard was strengthened
- [ ] Whether AST or regex scanning was used
- [ ] Evidence the guard catches `config.PROJECTS_DIR`-style access
- [ ] `CLAUDE.md` frontend architecture guidance update
- [ ] Exact validation commands and local results
- [ ] Whether `kicad-cli` was available
- [ ] Whether KiCad system symbol libraries were available, if checked
- [ ] Whether `rsvg-convert` was available
- [ ] Skipped tests and skip reasons
- [ ] Artifact hygiene result

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must pass | 2, 5 |
| P1 — Documentation/maintainer clarity | 1, 3, 4, 6 |
