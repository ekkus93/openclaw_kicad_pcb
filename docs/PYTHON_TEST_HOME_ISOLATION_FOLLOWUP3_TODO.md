# Python Test Home-Directory Isolation — Follow-Up 3 TODO

Focused follow-up for the remaining deprecated config-constant guard hole. The guard must catch bare `import kicad_pcb.config` followed by module-qualified deprecated constant access such as `kicad_pcb.config.PROJECTS_DIR`.

---

## 1. Inspect the existing AST guard (P0)

### 1.1 Open the guard implementation

- [ ] Open `tests/unit/test_config_isolation.py`
- [ ] Locate `_check_source_for_deprecated_constants`
- [ ] Locate any existing helper tests for deprecated constant detection
- [ ] Confirm current behavior catches:
  - [ ] direct imports such as `from kicad_pcb.config import PROJECTS_DIR`
  - [ ] relative imports such as `from ..config import CONFIG_DIR`
  - [ ] aliased imports such as `import kicad_pcb.config as cfg` followed by `cfg.PROJECTS_DIR`
  - [ ] `from kicad_pcb import config` followed by `config.CONFIG_DIR`

### 1.2 Reproduce the remaining guard hole

Add a temporary/local check or reason from the current code that this currently fails to report a violation:

```python
import kicad_pcb.config

value = kicad_pcb.config.PROJECTS_DIR
```

- [ ] Confirm the existing guard misses this pattern before the fix
- [ ] Do not commit a bad runtime source file to reproduce it

---

## 2. Add dotted attribute-chain detection (P0)

### 2.1 Add an AST helper

Add a helper in `tests/unit/test_config_isolation.py` that converts an `ast.Attribute` chain into a dotted list or dotted string.

Example target behavior:

```python
kicad_pcb.config.PROJECTS_DIR
```

should become:

```python
["kicad_pcb", "config", "PROJECTS_DIR"]
```

or:

```python
"kicad_pcb.config.PROJECTS_DIR"
```

Checklist:

- [ ] Helper accepts an `ast.AST`
- [ ] Helper handles nested `ast.Attribute`
- [ ] Helper handles the base `ast.Name`
- [ ] Helper returns `None` or equivalent for unsupported expressions
- [ ] Helper is small and local to the guard test module

### 2.2 Detect bare module-qualified deprecated constant access

Update `_check_source_for_deprecated_constants` so it flags:

```python
kicad_pcb.config.CONFIG_DIR
kicad_pcb.config.CONFIG_FILE
kicad_pcb.config.PROJECTS_DIR
kicad_pcb.config.CURRENT_PROJECT_FILE
kicad_pcb.config.CURRENT_SESSION_FILE
```

Requirements:

- [ ] Detect dotted chains starting with `kicad_pcb.config`
- [ ] Detect final attribute names in the deprecated constant set
- [ ] Add violation messages that include the offending constant name
- [ ] Preserve existing alias-based detection
- [ ] Preserve existing direct-import detection
- [ ] Preserve allowed dynamic helper imports

### 2.3 Keep allowed files excluded

Ensure these compatibility files remain allowed:

- [ ] `src/kicad_pcb/config.py`
- [ ] `src/kicad_pcb/__init__.py`

Do not broaden exclusions beyond those compatibility files.

---

## 3. Add focused guard tests (P0)

### 3.1 Add test for `PROJECTS_DIR`

Add a test similar to:

```python
def test_guard_helper_catches_bare_import_module_qualified_access() -> None:
    source = '''
import kicad_pcb.config

value = kicad_pcb.config.PROJECTS_DIR
'''
    violations = _check_source_for_deprecated_constants(source, "example.py")
    assert any("PROJECTS_DIR" in violation for violation in violations)
```

- [ ] Test fails before the implementation fix
- [ ] Test passes after the implementation fix
- [ ] Test does not require adding bad runtime source files

### 3.2 Parametrize over all deprecated constants

Add or update a parametrized test for:

- [ ] `CONFIG_DIR`
- [ ] `CONFIG_FILE`
- [ ] `PROJECTS_DIR`
- [ ] `CURRENT_PROJECT_FILE`
- [ ] `CURRENT_SESSION_FILE`

Expected pattern:

```python
import kicad_pcb.config

value = kicad_pcb.config.<CONSTANT>
```

Each deprecated constant should produce a violation.

### 3.3 Preserve existing helper tests

Existing helper tests should still cover:

- [ ] direct deprecated constant import
- [ ] relative deprecated constant import
- [ ] aliased config import
- [ ] `from kicad_pcb import config` import
- [ ] allowed dynamic helper import
- [ ] allowed compatibility files, if currently tested

Do not delete coverage to make the new tests pass.

---

## 4. Run targeted validation (P0)

### 4.1 Config isolation tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [ ] Tests pass
- [ ] New bare module-qualified access test passes
- [ ] Existing guard tests pass

### 4.2 Python lint/type checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [ ] Ruff passes
- [ ] Format check passes
- [ ] Mypy passes

### 4.3 Related targeted tests

If time permits, run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
```

- [ ] Tests pass or KiCad-dependent tests skip cleanly
- [ ] Skip reasons are clear if any tests skip

---

## 5. Artifact hygiene (P1)

### 5.1 Check generated files

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Only intentional files changed
- [ ] No frontend files changed unless unexpectedly necessary

### 5.2 Keep scope narrow

- [ ] Do not change runtime config behavior
- [ ] Do not change production defaults
- [ ] Do not edit frontend code
- [ ] Do not add new pytest markers
- [ ] Do not remove compatibility exports from `config.py` or `__init__.py`

---

## 6. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Whether a dotted-chain helper was added
- [ ] How `kicad_pcb.config.PROJECTS_DIR` is now detected
- [ ] Tests added or updated
- [ ] Exact validation commands and results
- [ ] Whether any tests skipped and why
- [ ] Artifact hygiene result
- [ ] Confirmation that runtime behavior was not changed

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 4 |
| P1 — Hygiene/reporting | 5, 6 |
