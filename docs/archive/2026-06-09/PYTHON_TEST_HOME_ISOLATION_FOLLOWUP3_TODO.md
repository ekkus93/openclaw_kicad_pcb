# Python Test Home-Directory Isolation — Follow-Up 3 TODO

Focused follow-up for the remaining deprecated config-constant guard hole. The guard must catch bare `import kicad_pcb.config` followed by module-qualified deprecated constant access such as `kicad_pcb.config.PROJECTS_DIR`.

---

## 1. Inspect the existing AST guard (P0)

### 1.1 Open the guard implementation

- [x] Open `tests/unit/test_config_isolation.py`
- [x] Locate `_check_source_for_deprecated_constants`
- [x] Locate any existing helper tests for deprecated constant detection
- [x] Confirm current behavior catches:
  - [x] direct imports such as `from kicad_pcb.config import PROJECTS_DIR`
  - [x] relative imports such as `from ..config import CONFIG_DIR`
  - [x] aliased imports such as `import kicad_pcb.config as cfg` followed by `cfg.PROJECTS_DIR`
  - [x] `from kicad_pcb import config` followed by `config.CONFIG_DIR`

### 1.2 Reproduce the remaining guard hole

Add a temporary/local check or reason from the current code that this currently fails to report a violation:

```python
import kicad_pcb.config

value = kicad_pcb.config.PROJECTS_DIR
```

- [x] Confirm the existing guard misses this pattern before the fix
- [x] Do not commit a bad runtime source file to reproduce it

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

- [x] Helper accepts an `ast.AST`
- [x] Helper handles nested `ast.Attribute`
- [x] Helper handles the base `ast.Name`
- [x] Helper returns `None` or equivalent for unsupported expressions
- [x] Helper is small and local to the guard test module

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

- [x] Detect dotted chains starting with `kicad_pcb.config`
- [x] Detect final attribute names in the deprecated constant set
- [x] Add violation messages that include the offending constant name
- [x] Preserve existing alias-based detection
- [x] Preserve existing direct-import detection
- [x] Preserve allowed dynamic helper imports

### 2.3 Keep allowed files excluded

Ensure these compatibility files remain allowed:

- [x] `src/kicad_pcb/config.py`
- [x] `src/kicad_pcb/__init__.py`

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

- [x] Test fails before the implementation fix
- [x] Test passes after the implementation fix
- [x] Test does not require adding bad runtime source files

### 3.2 Parametrize over all deprecated constants

Add or update a parametrized test for:

- [x] `CONFIG_DIR`
- [x] `CONFIG_FILE`
- [x] `PROJECTS_DIR`
- [x] `CURRENT_PROJECT_FILE`
- [x] `CURRENT_SESSION_FILE`

Expected pattern:

```python
import kicad_pcb.config

value = kicad_pcb.config.<CONSTANT>
```

Each deprecated constant should produce a violation.

### 3.3 Preserve existing helper tests

Existing helper tests should still cover:

- [x] direct deprecated constant import
- [x] relative deprecated constant import
- [x] aliased config import
- [x] `from kicad_pcb import config` import
- [x] allowed dynamic helper import
- [x] allowed compatibility files, if currently tested

Do not delete coverage to make the new tests pass.

---

## 4. Run targeted validation (P0)

### 4.1 Config isolation tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
```

- [x] Tests pass
- [x] New bare module-qualified access test passes
- [x] Existing guard tests pass

### 4.2 Python lint/type checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [x] Ruff passes
- [x] Format check passes
- [x] Mypy passes

### 4.3 Related targeted tests

If time permits, run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
```

- [x] Tests pass or KiCad-dependent tests skip cleanly
- [x] Skip reasons are clear if any tests skip

---

## 5. Artifact hygiene (P1)

### 5.1 Check generated files

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [x] No generated cache artifacts are tracked
- [x] Only intentional files changed
- [x] No frontend files changed unless unexpectedly necessary

### 5.2 Keep scope narrow

- [x] Do not change runtime config behavior
- [x] Do not change production defaults
- [x] Do not edit frontend code
- [x] Do not add new pytest markers
- [x] Do not remove compatibility exports from `config.py` or `__init__.py`

---

## 6. Completion notes required

Claude Code should report:

- [x] Files changed
- [x] Whether a dotted-chain helper was added
- [x] How `kicad_pcb.config.PROJECTS_DIR` is now detected
- [x] Tests added or updated
- [x] Exact validation commands and results
- [x] Whether any tests skipped and why
- [x] Artifact hygiene result
- [x] Confirmation that runtime behavior was not changed

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 4 |
| P1 — Hygiene/reporting | 5, 6 |

---

## Completion notes

**Files changed** (commit `ba28a3f` on `webapp`):
- `tests/unit/test_config_isolation.py` — `_dotted_attr_chain` helper, updated pass-2 loop, 6 new tests

**Dotted-chain helper added**: `_dotted_attr_chain(node: ast.AST) -> list[str] | None`
walks a nested `ast.Attribute` chain to its root `ast.Name` and returns the reversed
parts as a flat list (e.g. `["kicad_pcb", "config", "PROJECTS_DIR"]`). Returns `None`
for chains that don't terminate in a plain `Name`.

**How `kicad_pcb.config.PROJECTS_DIR` is now detected**:
Pass 2 of `_check_source_for_deprecated_constants` now has two branches on each
`ast.Attribute` node:
1. (existing) alias-based: `node.value` is a `Name` in `config_aliases` and
   `node.attr` is a deprecated constant.
2. (new) dotted-chain: `_dotted_attr_chain(node)` returns a list whose first two
   elements are `["kicad_pcb", "config"]` and whose last element is a deprecated
   constant.

**Tests added** (17 → 23):
- `test_guard_helper_catches_bare_import_module_qualified_access` — focused test for
  `PROJECTS_DIR` via bare `import kicad_pcb.config`.
- `test_guard_helper_catches_bare_import_module_qualified_deprecated_constants` —
  parametrized over all five deprecated constants.

**Validation**:
- `ruff check .` — clean
- `ruff format --check .` — clean (219 files already formatted)
- `mypy src/kicad_pcb src/kicad_pcb_web` — clean (109 files)
- `pytest tests/unit/test_config_isolation.py` — **23 passed**
- No tests skipped.

**Runtime behavior**: unchanged — only `tests/unit/test_config_isolation.py` was modified.

**Artifact hygiene**: clean — only the one test file changed.
