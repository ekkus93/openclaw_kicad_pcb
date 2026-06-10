# UI/UX Improvements — Batch 4.3 Spec

## Purpose

Batch 4.2 successfully fixed the core subprocess launch-error handling issue. `SubprocessRunner.run()` now converts missing, non-executable, and other OS-level launch failures into non-ok `RunResult` values instead of allowing raw subprocess launch exceptions to escape.

The latest review found only a small remaining cleanup set:

1. One current spec doc still shows the obsolete `pytest -m kicad` marker command in a runnable-looking code block.
2. The KiCad test skip helper can be made more robust by treating `OSError` while probing `kicad-cli --version` as “KiCad unavailable” instead of allowing the helper itself to raise.
3. Targeted validation should be re-run and documented.

This batch should be a tiny documentation/test-infrastructure polish patch. Do not perform another broad refactor.

## Goals

1. Remove runnable-looking stale `pytest -m kicad` command examples from current docs.
2. Ensure current docs consistently use `pytest -m requires_kicad`.
3. Optionally harden `tests/conftest.py::kicad_cli_version()` or equivalent helper so `OSError` during `kicad-cli --version` returns `None` and causes a clean skip.
4. Re-run targeted adapter/model-corpus validation and core lint/type checks.
5. Preserve all application behavior.

## Non-Goals

- Do not add a new pytest marker named `kicad`.
- Do not rename `requires_kicad`.
- Do not change production app behavior.
- Do not change backend artifact names.
- Do not change frontend behavior.
- Do not broaden subprocess catching beyond the already-correct `SubprocessRunner.run()` behavior.
- Do not mark ordinary tests as KiCad-dependent merely to hide failures.
- Do not rewrite historical completion notes unless they are being used as active instructions.

---

## 1. Remove stale runnable-looking `pytest -m kicad` docs

### Problem

`docs/UIUX_IMPROVEMENTS4_2_SPEC.md` still contains the obsolete marker command in a code block:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

Even when used as an example of a stale command, a fenced shell block makes it look runnable and can confuse future agents or developers.

### Required behavior

Current docs must not show `pytest -m kicad` or `-m kicad` as a runnable command.

The canonical marker command is:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

### Implementation requirements

- Open `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`.
- Find the fenced code block containing the stale `-m kicad` command.
- Replace it with prose that identifies the old marker as historical/stale.
- If a runnable command is shown, show only the correct `-m requires_kicad` command.
- Search active/current docs for other runnable-looking stale marker examples.
- Do not add or document a new `kicad` marker.

### Acceptable wording

```text
A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

Or:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

### Acceptance criteria

- No active/current docs show `pytest -m kicad` in a runnable-looking fenced command block.
- Current validation instructions consistently use `requires_kicad`.
- Historical mentions, if retained, are prose-only and clearly labelled as obsolete.

---

## 2. Harden KiCad skip-helper probe against `OSError`

### Problem

The `requires_kicad` test infrastructure checks whether KiCad is available. If `shutil.which("kicad-cli")` returns a path but running `kicad-cli --version` raises an `OSError` such as `PermissionError`, the skip helper may raise instead of treating KiCad as unavailable.

This is less urgent than the already-fixed `SubprocessRunner.run()` issue, but it is the same class of robustness problem in test infrastructure.

### Required behavior

The KiCad availability probe should never crash merely because `kicad-cli --version` cannot be launched. It should return unavailable and cause `requires_kicad` tests to skip cleanly.

### Implementation requirements

In `tests/conftest.py`, inspect the helper responsible for probing KiCad CLI version, likely named `kicad_cli_version()`.

If it calls:

```python
subprocess.run([kicad_cli, "--version"], ...)
```

wrap that call so OS-level launch failures return `None`.

Recommended behavior:

```python
try:
    result = subprocess.run(...)
except OSError:
    return None
```

If the helper already handles this, no code change is required; document that it was verified.

### Requirements

- Do not catch broad `Exception`.
- Do not convert real assertion failures into skips.
- Do not change the public decorator usage: keep `@requires_kicad`.
- Do not introduce a new marker.
- Preserve the existing version check, including the minimum KiCad version requirement.
- Preserve existing checks for system KiCad symbol libraries.

### Tests

Add a targeted test if practical, or document manual verification.

Preferred test approach:

- Monkeypatch `shutil.which("kicad-cli")` or the helper’s path lookup to return a fake path.
- Monkeypatch `subprocess.run` to raise `PermissionError` or `OSError`.
- Assert `kicad_cli_version()` returns `None`.
- Assert the skip condition treats KiCad as unavailable.

If importing the helper directly is awkward because it lives in `tests/conftest.py`, a small test in the test suite is still acceptable if it can be done cleanly. Otherwise, manual validation plus clear completion notes is acceptable for this tiny infrastructure change.

### Acceptance criteria

- An unlaunchable `kicad-cli` probe does not raise from the skip helper.
- KiCad-dependent tests skip cleanly when the probe cannot launch KiCad.
- Existing KiCad-dependent tests still run when KiCad is available.
- No new pytest marker is added.

---

## 3. Re-run targeted validation

### Required validation commands

Run the targeted checks that prove Batch 4.2 behavior remains intact:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

Also run format check:

```bash
uv run --extra dev --extra web ruff format --check .
```

### Optional broader validation

If feasible:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

If full unit tests are slow, at minimum run the targeted adapter/model-corpus tests listed above and state that broader validation was not run or timed out.

### Acceptance criteria

- Targeted adapter/model-corpus tests pass.
- Ruff and mypy pass.
- Any broader test failures are clearly identified as unrelated or pre-existing, not hidden.
- Completion notes accurately distinguish targeted validation from full-suite validation.

---

## 4. Artifact hygiene

After validation, confirm generated cache artifacts are not tracked.

### Commands

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Requirements

- Do not commit `__pycache__`, `.pyc`, or `.pyo`.
- `git status --short` should show only intentional source/doc/test changes.
- If validation generates cache files locally, remove them before packaging.

---

## Completion notes required

Claude Code should report:

- files changed,
- stale docs command removed and exact replacement wording,
- whether `kicad_cli_version()` or equivalent skip helper was changed or verified unchanged,
- exact behavior when `kicad-cli --version` raises `OSError`,
- whether a test was added for skip-helper `OSError` behavior,
- exact validation commands and results,
- whether full unit tests were run or only targeted tests,
- whether `kicad-cli` was available,
- whether system KiCad symbols were available,
- whether `rsvg-convert` was available,
- any skipped tests and skip reasons,
- artifact hygiene result.
