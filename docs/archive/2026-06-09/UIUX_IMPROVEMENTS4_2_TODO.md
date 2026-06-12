# UI/UX Improvements — Batch 4.2 TODO

Derived from the Batch 4.1 review. Batch 4.1 fixed most no-KiCad validation issues, but the new missing-binary tests can still fail because `SubprocessRunner.run()` catches only `FileNotFoundError`. This batch hardens subprocess launch handling and removes one remaining stale runnable-looking `-m kicad` doc example.

---

## 1. Harden `SubprocessRunner.run()` launch-error handling (P0 — no-KiCad reliability)

`SubprocessRunner.run()` should return a non-ok `RunResult` when an external command cannot be launched. It must not let `FileNotFoundError`, `PermissionError`, or related `OSError` launch failures escape.

### 1.1 Inspect current implementation

- [x] Open `src/kicad_pcb/adapters.py`
- [x] Locate `SubprocessRunner.run()`
- [x] Identify the current exception handling around `subprocess.run(...)`
- [x] Confirm whether it catches only `FileNotFoundError` — confirmed, only `FileNotFoundError` was caught

### 1.2 Catch OS-level launch failures

- [x] Update exception handling to catch launch-related `OSError` failures
- [x] Ensure missing binaries return a non-ok `RunResult`
- [x] Ensure permission/executable errors return a non-ok `RunResult`
- [x] Include the command name in the error message
- [x] Preserve existing successful command behavior
- [x] Preserve existing timeout behavior
- [x] Do not return success for launch failures

Implemented POSIX-specific form:

```python
except FileNotFoundError:
    return RunResult(127, "", f"command not found: {cmd[0]}")
except PermissionError as exc:
    return RunResult(126, "", f"command not executable: {cmd[0]}: {exc}")
except OSError as exc:
    return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")
```

### 1.3 Keep behavior scoped

- [x] Do not catch broad `Exception`
- [x] Do not hide parsing/business-logic failures outside `SubprocessRunner`
- [x] Do not change adapter public types
- [x] Do not add a new subprocess abstraction

---

## 2. Fix and strengthen adapter missing-command tests (P0 — regression coverage)

The tests in `tests/unit/test_adapters.py` should prove missing/unlaunchable commands return non-ok `RunResult`.

### 2.1 Inspect current tests

- [x] Open `tests/unit/test_adapters.py`
- [x] Locate `TestSubprocessRunner`
- [x] Locate missing-binary tests:
  - [x] `test_missing_binary_returns_nonzero_result`
  - [x] `test_missing_binary_capture_false_returns_nonzero_result`

### 2.2 Update assertions if needed

- [x] Assert `result.returncode != 0` (was `== 127`; now accepts 126 for PermissionError)
- [x] Assert `not result.ok`
- [x] Assert the error text includes the command name (`"__no_such_binary_exists_xyz__" in r.stderr`)
- [x] Accept either not-found, not-executable, permission, or launch-failure wording (command name check is sufficient)
- [x] Do not assert a specific OS exception class
- [x] Cover both `capture=True` and `capture=False` — both tests now assert command name in stderr

### 2.3 Validate adapter tests

Run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
```

- [x] Adapter tests pass — 75 passed
- [x] Tests fail if `OSError` launch failures escape
- [x] Tests do not require KiCad

---

## 3. Re-validate model-corpus hermetic behavior (P0 — regression safety)

After changing `SubprocessRunner`, confirm the Batch 4.1 model-corpus fixes still work.

### 3.1 Run ingestion tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
```

- [x] Tests pass — 5 passed
- [x] `test_list_command_reads_fixture_metadata` does not require real `kicad-cli`
- [x] Missing `kicad-cli` is handled as a non-ok runner result, not a crash

### 3.2 Run evaluate command tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [x] Tests pass — 6 passed
- [x] Missing KiCad simulation still produces `"partial"` / `"not_run"` where intended
- [x] No real evaluator failure is hidden

---

## 4. Remove stale runnable-looking `-m kicad` command from current docs (P1 — documentation accuracy)

`docs/UIUX_IMPROVEMENTS4_1_SPEC.md` should not show the obsolete marker command as a runnable code block.

### 4.1 Update Batch 4.1 spec docs

- [x] Open `docs/UIUX_IMPROVEMENTS4_1_SPEC.md`
- [x] Found the code block containing the stale `-m kicad` command in Section 3 "Problem"
- [x] Replaced with prose: "previously included a stale command referencing the obsolete `-m kicad` marker, since fixed in Batch 4. A previous Batch 4 spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`."

### 4.2 Search active docs

- [x] Searched docs for `pytest -m kicad` and `-m kicad`
- [x] No active/current doc shows the obsolete command as a runnable instruction — all remaining occurrences are prose descriptions (historical notes) or spec/TODO files documenting the tasks themselves
- [x] Do not create or document a new `kicad` marker

---

## 5. Run final validation (P0 — must pass)

### 5.1 Frontend validation

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [x] No frontend source changed — build/lint/test not required

### 5.2 Python/backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [x] Ruff passes — all checks passed
- [x] Ruff format check passes — 216 files already formatted
- [x] Mypy passes — no issues found in 109 source files
- [x] Full unit suite passes — all passed (exit code 0)

### 5.3 Targeted regression tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [x] Adapter tests pass — 75 passed
- [x] Ingestion tests pass — 5 passed
- [x] Evaluate command tests pass — 6 passed

### 5.4 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [x] Web tests pass — 44 passed, 1 skipped (live-provider probe)
- [x] Skip reason: `Set RUN_LIVE_PROVIDER_TESTS=1 to run live provider probes`

### 5.5 KiCad-dependent tests

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [x] 26 passed, 3 failed (pre-existing integration failures unrelated to Batch 4.2 — `TestNewFromNetlistKicadMode` in `tests/integration/test_phase0_smoke.py`, failing before and after this batch)

### 5.6 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [x] No generated cache artifacts tracked
- [x] Only intentional files modified: `src/kicad_pcb/adapters.py`, `tests/unit/test_adapters.py`, `docs/UIUX_IMPROVEMENTS4_1_SPEC.md`, `docs/UIUX_IMPROVEMENTS4_2_TODO.md`

---

## 6. Completion notes required

- [x] Files changed: `src/kicad_pcb/adapters.py`, `tests/unit/test_adapters.py`, `docs/UIUX_IMPROVEMENTS4_1_SPEC.md`, `docs/UIUX_IMPROVEMENTS4_2_TODO.md`
- [x] `SubprocessRunner.run()` now catches `FileNotFoundError` (127), `PermissionError` (126), and `OSError` (127) — POSIX-specific form, all return non-ok `RunResult` with command name in message
- [x] Adapter test assertions updated: `r.returncode == 127` → `r.returncode != 0`; `"command not found" in r.stderr` → `"__no_such_binary_exists_xyz__" in r.stderr`; `capture=False` test gains stderr assertion
- [x] Targeted adapter tests pass: 75 passed
- [x] Model-corpus ingestion/evaluate tests still pass: 5 + 6 passed
- [x] Docs: removed runnable-looking `-m kicad` code block from `docs/UIUX_IMPROVEMENTS4_1_SPEC.md` Section 3; replaced with prose
- [x] kicad-cli: available (kicad-cli >= 9.0.0)
- [x] System KiCad symbols: available (`/usr/share/kicad/symbols`)
- [x] rsvg-convert: available
- [x] Tests skipped: 1 live-provider probe in web tests
- [x] Artifact hygiene: clean — no unintended files modified

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 5 |
| P1 — Documentation accuracy | 4 |
| P2 — Completion reporting | 6 |
