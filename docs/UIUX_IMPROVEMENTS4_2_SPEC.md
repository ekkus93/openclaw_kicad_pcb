# UI/UX Improvements — Batch 4.2 Spec

## Purpose

Batch 4.1 fixed most of the remaining no-KiCad validation problems, but the latest review found one narrow blocker: the new missing-command handling in `SubprocessRunner.run()` catches `FileNotFoundError` only, while `subprocess.run()` can raise other `OSError` subclasses during command launch, including `PermissionError`. Because the new tests use a synthetic missing/unlaunchable binary, the adapter tests can still fail before returning a non-ok `RunResult`.

This batch closes that final reliability gap and cleans one remaining stale marker-command example in the Batch 4.1 spec.

## Background

The Batch 4.1 review found:

1. Frontend build/lint/tests pass.
2. Python ruff, format check, and mypy pass.
3. Web tests pass with expected skips.
4. The evaluator `"partial"` behavior is correct for missing KiCad when useful report artifacts are still generated.
5. The `requires_kicad` marker description is improved.
6. The new `SubprocessRunner` missing-binary tests fail in an environment where command launch raises `PermissionError`, not `FileNotFoundError`.
7. `docs/UIUX_IMPROVEMENTS4_1_SPEC.md` still displays the stale `-m kicad` command in a runnable-looking code block.

## Goals

1. Make `SubprocessRunner.run()` robust against command-launch failures, not only `FileNotFoundError`.
2. Ensure missing or unlaunchable external tools return a non-ok `RunResult` instead of raising directly.
3. Make the new `TestSubprocessRunner` missing-binary tests pass reliably.
4. Keep downstream callers able to handle unavailable tools through existing result/status logic.
5. Remove stale runnable-looking `pytest -m kicad` examples from current docs.
6. Re-run and record the full validation suite.

## Non-Goals

- Do not add a new pytest marker named `kicad`.
- Do not change the canonical marker away from `requires_kicad`.
- Do not change production behavior for successful subprocess calls.
- Do not hide command failures as successes.
- Do not broadly catch unrelated exceptions outside subprocess launch errors.
- Do not change frontend behavior.
- Do not change backend artifact names.
- Do not introduce a new CLI abstraction or broad refactor.

---

## 1. Harden `SubprocessRunner.run()` launch-error handling

### Problem

The current implementation catches `FileNotFoundError` only. In practice, launching an external command can fail with other `OSError` subclasses:

- `FileNotFoundError`: binary not found
- `PermissionError`: path found or resolved but not executable / permission denied
- other `OSError`: environment/path/exec failure

The current implementation can still raise instead of returning a non-ok `RunResult`.

### Required behavior

When a command cannot be launched because of an OS-level exec failure, `SubprocessRunner.run()` must return a non-ok `RunResult` instead of raising the raw exception.

Successful command execution behavior must remain unchanged.

Timeout behavior must remain unchanged unless the existing code already converts timeouts to a `RunResult`.

### Recommended implementation

In `src/kicad_pcb/adapters.py`, update `SubprocessRunner.run()` to catch `OSError` around `subprocess.run(...)`.

A simple robust approach:

```python
except OSError as exc:
    return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")
```

Alternative if you want POSIX-style distinction:

```python
except FileNotFoundError:
    return RunResult(127, "", f"command not found: {cmd[0]}")
except PermissionError as exc:
    return RunResult(126, "", f"command not executable: {cmd[0]}: {exc}")
except OSError as exc:
    return RunResult(127, "", f"command launch failed: {cmd[0]}: {exc}")
```

Either is acceptable if tests and downstream checks only rely on `result.ok == False`.

### Requirements

- Preserve the `RunResult` type.
- Preserve stdout/stderr capture behavior for successful commands.
- Preserve existing timeout handling.
- Do not return `ok=True` for launch failures.
- Include the failed command name in the error message.
- Do not swallow non-launch exceptions from later parsing/business logic outside this adapter.

### Acceptance criteria

- Missing or unlaunchable binaries produce `RunResult(ok=False)`.
- The return code is non-zero.
- The error message identifies the command.
- No raw `FileNotFoundError` or `PermissionError` escapes from `SubprocessRunner.run()` for launch failures.
- Existing adapter tests still pass.

---

## 2. Fix and strengthen `TestSubprocessRunner` missing-command tests

### Problem

The Batch 4.1 tests intended to prove that missing binaries return non-ok `RunResult`, but they failed in the review environment because `PermissionError` escaped.

### Required behavior

The tests should pass regardless of whether the OS reports the synthetic command as not found or not executable.

### Implementation requirements

Update or keep tests in `tests/unit/test_adapters.py` so they assert behavior, not exact OS exception class.

Required assertions:

- `result.returncode != 0`
- `not result.ok`
- error text includes either:
  - the command name, and
  - some indication of not found / not executable / launch failure
- behavior is covered for both:
  - `capture=True`
  - `capture=False`

### Test-command choice

Use a command name that is extremely unlikely to exist, such as:

```text
__no_such_binary_exists_xyz__
```

If the environment still resolves this to a permission problem, the implementation should handle it as an `OSError` launch failure.

### Acceptance criteria

- `python -m pytest tests/unit/test_adapters.py -q` passes.
- The tests fail if `SubprocessRunner.run()` lets `OSError` escape.
- The tests do not require KiCad.

---

## 3. Verify model-corpus hermetic tests still pass

### Problem

Batch 4.1 updated model-corpus tests and production subprocess behavior. After hardening `SubprocessRunner`, those tests should still pass.

### Required validation

Run:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

### Acceptance criteria

- The ingestion metadata test no longer crashes due to missing `kicad-cli`.
- The evaluate test deterministically simulates unavailable KiCad and expects `"partial"` / `"not_run"`.
- No test relies on a host KiCad installation unless explicitly marked.

---

## 4. Remove stale runnable-looking `-m kicad` command from current docs

### Problem

`docs/UIUX_IMPROVEMENTS4_1_SPEC.md` still shows this stale command in a code block:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

Even if the surrounding prose says it is stale, putting it in a code block makes it look runnable and can confuse future agents.

### Required behavior

Current docs should not show the obsolete marker command as a runnable command.

### Implementation requirements

- Open `docs/UIUX_IMPROVEMENTS4_1_SPEC.md`.
- Replace the code block containing `-m kicad` with prose.
- Use wording such as:

```text
A previous Batch 4 spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

- If showing a runnable command, show only:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

- Search active docs for any other runnable-looking `pytest -m kicad` or `-m kicad` examples.
- Do not add a new `kicad` marker.

### Acceptance criteria

- No current docs show `pytest -m kicad` as a runnable code block.
- Active docs consistently use `requires_kicad`.
- Historical mentions, if any, are clearly prose-only and not instructions.

---

## 5. Final validation

Run the full validation set after implementation.

### Frontend

Even though no frontend changes are expected, run the frontend checks to ensure the repo remains clean:

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

### Python/backend

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

### Targeted tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

### Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

### KiCad-dependent tests

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

### Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- Frontend build, lint, and tests pass.
- Ruff, format check, mypy, and full unit tests pass.
- Targeted adapter/model-corpus tests pass.
- Web tests pass or skip only explicitly external-tool-dependent tests.
- KiCad-dependent tests pass or skip cleanly.
- No generated cache artifacts are tracked.
- Only intentional files are modified.

---

## Completion notes required

Claude Code should report:

- files changed,
- exact `SubprocessRunner.run()` exception-handling behavior,
- whether `FileNotFoundError`, `PermissionError`, or broader `OSError` is caught,
- updated/added adapter test assertions,
- confirmation that model-corpus ingestion/evaluate tests still pass,
- docs changed to remove stale runnable-looking `-m kicad`,
- exact validation commands and results,
- whether `kicad-cli` was available,
- whether system KiCad symbols were available,
- whether `rsvg-convert` was available,
- tests skipped and skip reasons,
- artifact hygiene result.
