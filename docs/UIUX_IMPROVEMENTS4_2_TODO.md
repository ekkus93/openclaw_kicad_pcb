# UI/UX Improvements — Batch 4.2 TODO

Derived from the Batch 4.1 review. Batch 4.1 fixed most no-KiCad validation issues, but the new missing-binary tests can still fail because `SubprocessRunner.run()` catches only `FileNotFoundError`. This batch hardens subprocess launch handling and removes one remaining stale runnable-looking `-m kicad` doc example.

---

## 1. Harden `SubprocessRunner.run()` launch-error handling (P0 — no-KiCad reliability)

`SubprocessRunner.run()` should return a non-ok `RunResult` when an external command cannot be launched. It must not let `FileNotFoundError`, `PermissionError`, or related `OSError` launch failures escape.

### 1.1 Inspect current implementation

- [ ] Open `src/kicad_pcb/adapters.py`
- [ ] Locate `SubprocessRunner.run()`
- [ ] Identify the current exception handling around `subprocess.run(...)`
- [ ] Confirm whether it catches only `FileNotFoundError`

### 1.2 Catch OS-level launch failures

- [ ] Update exception handling to catch launch-related `OSError` failures
- [ ] Ensure missing binaries return a non-ok `RunResult`
- [ ] Ensure permission/executable errors return a non-ok `RunResult`
- [ ] Include the command name in the error message
- [ ] Preserve existing successful command behavior
- [ ] Preserve existing timeout behavior
- [ ] Do not return success for launch failures

Acceptable simple implementation:

```python
except OSError as exc:
    return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")
```

Acceptable more specific implementation:

```python
except FileNotFoundError:
    return RunResult(127, "", f"command not found: {cmd[0]}")
except PermissionError as exc:
    return RunResult(126, "", f"command not executable: {cmd[0]}: {exc}")
except OSError as exc:
    return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")
```

### 1.3 Keep behavior scoped

- [ ] Do not catch broad `Exception`
- [ ] Do not hide parsing/business-logic failures outside `SubprocessRunner`
- [ ] Do not change adapter public types
- [ ] Do not add a new subprocess abstraction

---

## 2. Fix and strengthen adapter missing-command tests (P0 — regression coverage)

The tests in `tests/unit/test_adapters.py` should prove missing/unlaunchable commands return non-ok `RunResult`.

### 2.1 Inspect current tests

- [ ] Open `tests/unit/test_adapters.py`
- [ ] Locate `TestSubprocessRunner`
- [ ] Locate missing-binary tests:
  - [ ] `test_missing_binary_returns_nonzero_result`
  - [ ] `test_missing_binary_capture_false_returns_nonzero_result`

### 2.2 Update assertions if needed

- [ ] Assert `result.returncode != 0`
- [ ] Assert `not result.ok`
- [ ] Assert the error text includes the command name
- [ ] Accept either not-found, not-executable, permission, or launch-failure wording
- [ ] Do not assert a specific OS exception class
- [ ] Cover both `capture=True` and `capture=False`

### 2.3 Validate adapter tests

Run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
```

- [ ] Adapter tests pass
- [ ] Tests fail if `OSError` launch failures escape
- [ ] Tests do not require KiCad

---

## 3. Re-validate model-corpus hermetic behavior (P0 — regression safety)

After changing `SubprocessRunner`, confirm the Batch 4.1 model-corpus fixes still work.

### 3.1 Run ingestion tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
```

- [ ] Tests pass
- [ ] `test_list_command_reads_fixture_metadata` does not require real `kicad-cli`
- [ ] Missing `kicad-cli` is handled as a non-ok runner result, not a crash

### 3.2 Run evaluate command tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [ ] Tests pass
- [ ] Missing KiCad simulation still produces `"partial"` / `"not_run"` where intended
- [ ] No real evaluator failure is hidden

---

## 4. Remove stale runnable-looking `-m kicad` command from current docs (P1 — documentation accuracy)

`docs/UIUX_IMPROVEMENTS4_1_SPEC.md` should not show the obsolete marker command as a runnable code block.

### 4.1 Update Batch 4.1 spec docs

- [ ] Open `docs/UIUX_IMPROVEMENTS4_1_SPEC.md`
- [ ] Find the code block containing:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

- [ ] Replace it with prose stating that a previous spec referenced the obsolete `-m kicad` marker
- [ ] If a runnable command is shown, use only:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

### 4.2 Search active docs

- [ ] Search docs for `pytest -m kicad`
- [ ] Search docs for `-m kicad`
- [ ] Ensure no active/current docs show the obsolete command as a runnable instruction
- [ ] Do not create or document a new `kicad` marker

---

## 5. Run final validation (P0 — must pass)

### 5.1 Frontend validation

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Build passes
- [ ] Lint passes
- [ ] Tests pass

### 5.2 Python/backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Ruff passes
- [ ] Ruff format check passes
- [ ] Mypy passes
- [ ] Full unit suite passes

### 5.3 Targeted regression tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [ ] Adapter tests pass
- [ ] Ingestion tests pass
- [ ] Evaluate command tests pass

### 5.4 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Web tests pass
- [ ] External-tool-dependent tests skip cleanly when tools are missing
- [ ] Skip reasons are clear

### 5.5 KiCad-dependent tests

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [ ] KiCad-dependent tests pass when tools are available, or
- [ ] KiCad-dependent tests skip cleanly when tools are unavailable

### 5.6 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] No unintended files changed
- [ ] Only intentional files are modified

---

## 6. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Exact `SubprocessRunner.run()` exception handling implemented
- [ ] Whether it catches `FileNotFoundError`, `PermissionError`, or broader `OSError`
- [ ] Adapter test assertion changes
- [ ] Confirmation that targeted adapter tests pass
- [ ] Confirmation that model-corpus ingestion/evaluate tests pass
- [ ] Docs changed to remove stale runnable-looking `-m kicad`
- [ ] Exact validation commands and results
- [ ] Whether `kicad-cli` was available
- [ ] Whether system KiCad symbols were available
- [ ] Whether `rsvg-convert` was available
- [ ] Tests skipped and skip reasons
- [ ] Artifact hygiene result

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3, 5 |
| P1 — Documentation accuracy | 4 |
| P2 — Completion reporting | 6 |
