# UI/UX Improvements — Batch 4.3 TODO

Derived from the Batch 4.2 review. The core Batch 4.2 code fix is good: `SubprocessRunner.run()` now handles missing/non-executable command launch failures correctly. This final cleanup removes a stale runnable-looking `-m kicad` command from current docs and optionally hardens the KiCad test skip helper against `OSError`.

---

## 1. Remove stale runnable-looking `pytest -m kicad` command from current docs (P0 — documentation accuracy)

`docs/UIUX_IMPROVEMENTS4_2_SPEC.md` still shows the obsolete marker command in a fenced command block.

### 1.1 Find stale command block

- [x] Open `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`
- [x] Search for `pytest -m kicad` — found at line 179 in Section 4 "Problem"
- [x] Search for `-m kicad` — same location
- [x] Identified fenced `bash` block containing the obsolete marker

### 1.2 Replace with prose or correct command

- [x] Removed the runnable-looking stale command block
- [x] Replaced with prose: "`docs/UIUX_IMPROVEMENTS4_1_SPEC.md` previously showed the obsolete `-m kicad` marker command in a runnable-looking code block, since replaced with prose in Batch 4.2. A previous Batch 4 spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`."
- [x] Do not add or document a new `kicad` marker

### 1.3 Search active docs for other stale runnable examples

- [x] Searched active/current docs for `pytest -m kicad` and `-m kicad`
- [x] No active/current doc shows the obsolete command as a runnable instruction — all remaining occurrences are prose descriptions or the spec/TODO files that document the tasks themselves
- [x] Active validation instructions consistently use `requires_kicad`

---

## 2. Harden KiCad skip-helper probe against `OSError` (P1 — test infrastructure robustness)

The KiCad availability helper should treat unlaunchable `kicad-cli` as unavailable, not crash.

### 2.1 Inspect skip-helper code

- [x] Open `tests/conftest.py`
- [x] Located `kicad_cli_version()`
- [x] Located `subprocess.run([kicad_cli, "--version"], ...)` call
- [x] Confirmed: no `OSError` handling was present — gap confirmed

### 2.2 Add `OSError` handling if missing

- [x] Wrapped `subprocess.run(...)` in `try/except OSError: return None`
- [x] Preserved existing behavior when KiCad runs successfully
- [x] Preserved the minimum KiCad version check
- [x] Preserved system KiCad symbol library checks
- [x] Did not catch broad `Exception`
- [x] Did not add a new pytest marker
- [x] Public decorator usage unchanged: `@requires_kicad`

### 2.3 Add or document validation

- [x] Added `tests/unit/test_conftest_helpers.py` with 4 tests:
  - `test_returns_none_when_not_on_path` — verifies `shutil.which` returning `None` → `None`
  - `test_returns_none_on_permission_error` — monkeypatches `subprocess.run` to raise `PermissionError` → `None`
  - `test_returns_none_on_oserror` — monkeypatches `subprocess.run` to raise `OSError` → `None`
  - `test_supports_repo_schematics_false_on_oserror` — same OSError scenario, confirms `kicad_cli_supports_repo_schematics()` returns `False`
- [x] All 4 new tests pass

---

## 3. Run targeted validation (P0 — must pass)

### 3.1 Targeted regression tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [x] Adapter tests pass — 75 passed
- [x] Model-corpus ingestion tests pass — 5 passed
- [x] Model-corpus evaluate command tests pass — 6 passed

### 3.2 Lint/type validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [x] Ruff passes — all checks passed
- [x] Ruff format check passes — 217 files already formatted
- [x] Mypy passes — no issues found in 109 source files

### 3.3 Optional broader validation

Run if feasible:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [x] Full unit suite passes (exit code 0)
- [x] Web tests pass — 44 passed, 1 skipped (live-provider probe: `Set RUN_LIVE_PROVIDER_TESTS=1 to run live provider probes`)
- [x] KiCad-dependent tests: 26 passed, 3 failed (pre-existing integration failures in `TestNewFromNetlistKicadMode`, unrelated to Batch 4.3)

---

## 4. Artifact hygiene (P1 — packaging cleanliness)

### 4.1 Check generated artifacts

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [x] No generated cache artifacts tracked
- [x] `git status --short` shows only intentional files: `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`, `docs/UIUX_IMPROVEMENTS4_3_TODO.md`, `tests/conftest.py`, `tests/unit/test_conftest_helpers.py`

---

## 5. Completion notes required

- [x] Files changed: `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`, `tests/conftest.py`, `tests/unit/test_conftest_helpers.py`, `docs/UIUX_IMPROVEMENTS4_3_TODO.md`
- [x] Stale docs command removed from `docs/UIUX_IMPROVEMENTS4_2_SPEC.md` Section 4 "Problem"; replaced with prose
- [x] `kicad_cli_version()` updated: `subprocess.run(...)` now wrapped in `try/except OSError: return None`; unlaunchable binary returns `None` and causes clean skip via `kicad_cli_supports_repo_schematics() → False`
- [x] Test added: `tests/unit/test_conftest_helpers.py` — 4 tests covering `None` path, `PermissionError`, `OSError`, and downstream `kicad_cli_supports_repo_schematics` behavior
- [x] Targeted validation: adapter (75), ingestion (5), evaluate (6) — all passed
- [x] Full unit suite: all passed
- [x] kicad-cli: available (kicad-cli >= 9.0.0)
- [x] System KiCad symbols: available (`/usr/share/kicad/symbols`)
- [x] rsvg-convert: available
- [x] Tests skipped: 1 live-provider probe in web tests
- [x] Artifact hygiene: clean

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 3 |
| P1 — Test infrastructure robustness / hygiene | 2, 4 |
| P2 — Completion reporting | 5 |
