# Python Test Home-Directory Isolation — Follow-Up 3 Spec

## Purpose

Follow-Up 2 successfully improved the home-directory isolation documentation, updated `CLAUDE.md`, and strengthened the deprecated config-constant guard with AST scanning. One remaining guard hole was found during review:

```python
import kicad_pcb.config
value = kicad_pcb.config.PROJECTS_DIR
```

The guard catches direct imports and common aliases, but it does not yet catch bare module-qualified access through the full dotted chain `kicad_pcb.config.<DEPRECATED_CONSTANT>`.

This follow-up is intentionally narrow. It should only close that guard hole, add focused tests, and run targeted validation.

---

## Goals

1. Detect deprecated config constants accessed through bare module-qualified imports.
2. Preserve the existing direct-import and alias-detection behavior.
3. Keep compatibility exports in `config.py` and `__init__.py` allowed.
4. Add focused tests for dotted attribute-chain detection.
5. Avoid behavior changes outside the test guard.

---

## Non-Goals

- Do not redesign `src/kicad_pcb/config.py`.
- Do not remove deprecated compatibility constants from `config.py` or `__init__.py`.
- Do not change runtime config/project path behavior.
- Do not change production defaults:
  - config default remains `~/.kicad-pcb`
  - projects default remains `~/kicad-projects`
- Do not introduce new pytest markers.
- Do not modify frontend code.
- Do not broaden the task into general AST linting.
- Do not weaken or delete existing guard tests.

---

## Existing guard behavior to preserve

The current guard should continue to catch these patterns in runtime source files:

```python
from kicad_pcb.config import PROJECTS_DIR
from ..config import CONFIG_DIR
import kicad_pcb.config as cfg
value = cfg.PROJECTS_DIR
from kicad_pcb import config
value = config.CONFIG_DIR
```

The current guard should continue to allow dynamic helper imports:

```python
from kicad_pcb.config import get_projects_dir
from ..config import get_config_dir
```

Allowed compatibility files remain:

```text
src/kicad_pcb/config.py
src/kicad_pcb/__init__.py
```

---

## Required new behavior

The guard must also catch bare module-qualified deprecated constant access:

```python
import kicad_pcb.config
value = kicad_pcb.config.PROJECTS_DIR
```

It should detect all deprecated constants through that dotted path:

```text
kicad_pcb.config.CONFIG_DIR
kicad_pcb.config.CONFIG_FILE
kicad_pcb.config.PROJECTS_DIR
kicad_pcb.config.CURRENT_PROJECT_FILE
kicad_pcb.config.CURRENT_SESSION_FILE
```

The diagnostic should identify:

- offending file,
- deprecated constant name,
- enough context to understand that module-qualified access is disallowed.

---

## Implementation guidance

### Preferred approach: dotted-chain helper

Add a small AST helper that converts an `ast.Attribute` chain into a list or string.

For example:

```python
kicad_pcb.config.PROJECTS_DIR
```

should become either:

```python
["kicad_pcb", "config", "PROJECTS_DIR"]
```

or:

```python
"kicad_pcb.config.PROJECTS_DIR"
```

Then the guard can flag a node when:

1. the dotted chain starts with `kicad_pcb.config`, and
2. the final attribute is one of the deprecated constants.

### Suggested helper shape

A helper like this is acceptable:

```python
def _dotted_attr_chain(node: ast.AST) -> list[str] | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
        return list(reversed(parts))
    return None
```

Then in the guard:

```python
chain = _dotted_attr_chain(node)
if chain and chain[:2] == ["kicad_pcb", "config"] and chain[-1] in DEPRECATED_CONSTANTS:
    ...
```

Adjust names to match the existing helper style.

### Edge cases

The guard only needs to catch straightforward source-level patterns. It does not need to perform full data-flow analysis.

The guard does **not** need to catch intentionally obscure patterns like:

```python
getattr(kicad_pcb.config, "PROJECTS_DIR")
```

unless that is easy and already consistent with the implementation. Keep this patch focused.

---

## Tests required

Add focused tests in `tests/unit/test_config_isolation.py`.

### New required test

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

### Additional recommended parametrized coverage

If practical, parametrize over all deprecated constants:

```python
@pytest.mark.parametrize(
    "constant",
    [
        "CONFIG_DIR",
        "CONFIG_FILE",
        "PROJECTS_DIR",
        "CURRENT_PROJECT_FILE",
        "CURRENT_SESSION_FILE",
    ],
)
def test_guard_helper_catches_bare_import_module_qualified_deprecated_constants(constant: str) -> None:
    source = f'''
import kicad_pcb.config

value = kicad_pcb.config.{constant}
'''
    violations = _check_source_for_deprecated_constants(source, "example.py")
    assert any(constant in violation for violation in violations)
```

### Existing tests must continue to pass

Do not remove tests that already verify:

- direct deprecated constant imports,
- relative deprecated constant imports,
- aliased config-module imports,
- `from kicad_pcb import config` followed by `config.CONSTANT`,
- allowed dynamic helper imports,
- allowed compatibility files.

---

## Validation

Run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_config_isolation.py
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

If time permits, also run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py tests/unit/test_lib_symbol.py
```

Frontend validation is not required unless frontend files are touched.

---

## Acceptance criteria

- The guard catches `kicad_pcb.config.PROJECTS_DIR`.
- The guard catches all deprecated constants accessed through `kicad_pcb.config.<CONSTANT>`.
- Existing direct-import and alias guard tests still pass.
- Compatibility files remain allowed.
- Dynamic helper imports remain allowed.
- No runtime code behavior changes.
- Targeted Python validation passes.
- No generated cache artifacts are tracked.

---

## Completion notes required

Claude Code should report:

- files changed,
- helper added or modified,
- whether dotted-chain detection was implemented,
- tests added,
- evidence that `kicad_pcb.config.PROJECTS_DIR` is caught,
- validation commands and results,
- whether any non-target files were changed,
- artifact hygiene result.
