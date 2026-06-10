# Questions and Issues — Python Test Home-Directory Isolation Pre-Implementation

## Background

After auditing the code, the core issue is structural: `config.py` defines five module-level
constants computed once at import time:

```python
CONFIG_DIR = Path.home() / ".kicad-pcb"
CURRENT_PROJECT_FILE = CONFIG_DIR / "current_project.json"
CURRENT_SESSION_FILE = CONFIG_DIR / "current_session.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
PROJECTS_DIR = Path.home() / "kicad-projects"
```

All five are exported in `__init__.py`. Every I/O function (`ensure_dirs`, `load_config`,
`get_current_project`, `set_current_project`, etc.) uses these cached constants directly.
Setting `KICAD_PCB_CONFIG_DIR` has no effect until the I/O functions are updated to call
a dynamic `get_config_dir()` instead.

Also: `discover_symbols_dir()` → `load_config()` → `ensure_dirs()` → `CONFIG_DIR.mkdir()`,
so even tests that only call `discover_symbols_dir()` (very common) transitively try to
create `~/.kicad-pcb`. The autouse fixture fixes this once `ensure_dirs()` uses the
dynamic helper.

Tests that currently call `set_current_project()` directly (writing to real home):
`test_patterns.py` (5 calls) and `test_env_resolution.py` (1 call). Three test files
already use `monkeypatch.setattr("kicad_pcb.config.CURRENT_PROJECT_FILE", ...)` as a
workaround: `test_project_scaffold.py`, `test_session.py`, and
`test_model_corpus_evaluate_command.py`.

The `KICAD_PCB_CACHE_DIR` env var in `symbol_cache.py` is the existing precedent for
this pattern.

---

## Q1 — PROJECTS_DIR scope

`ensure_dirs()` creates both `~/.kicad-pcb` and `~/kicad-projects`. The spec focuses on
`KICAD_PCB_CONFIG_DIR` as the override for `~/.kicad-pcb`, but `PROJECTS_DIR` is also
created by `ensure_dirs()` and points to a real home-relative path.

Three options:

1. `KICAD_PCB_CONFIG_DIR` only redirects `~/.kicad-pcb`; `ensure_dirs()` skips creating
   `~/kicad-projects` when the override is active.
2. `KICAD_PCB_CONFIG_DIR` redirects both — `PROJECTS_DIR` becomes
   `get_config_dir() / "projects"` or similar under the override.
3. Add a separate `KICAD_PCB_PROJECTS_DIR` env var.

Which approach is preferred?

---

## Q2 — Module-level constants after the refactor

After adding `get_config_dir()` and routing all I/O through it, the five module-level
constants still exist and are exported in `__init__.py`. They will remain pointing to the
real home even when the override is active, making them misleading.

Three options:

1. Keep them as-is, add a comment saying they are informational only and I/O functions
   use the dynamic helpers.
2. Mark them deprecated.
3. Remove them (breaking API change for any caller that imports them directly).

Which is preferred?

---

## Q3 — Existing monkeypatch.setattr workarounds

Three test files already manually patch `CURRENT_PROJECT_FILE` to redirect config writes:
`test_project_scaffold.py`, `test_session.py`, and `test_model_corpus_evaluate_command.py`.

Once the autouse fixture handles isolation automatically via the env var, those per-test
patches become redundant.

Should I remove them (cleaner, relies on the new fixture) or leave them in place
(conservative, belt-and-suspenders)?
