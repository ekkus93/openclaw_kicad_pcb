# UI/UX Improvements — Batch 4.3 TODO

Derived from the Batch 4.2 review. The core Batch 4.2 code fix is good: `SubprocessRunner.run()` now handles missing/non-executable command launch failures correctly. This final cleanup removes a stale runnable-looking `-m kicad` command from current docs and optionally hardens the KiCad test skip helper against `OSError`.

---

## 1. Remove stale runnable-looking `pytest -m kicad` command from current docs (P0 — documentation accuracy)

`docs/UIUX_IMPROVEMENTS4_2_SPEC.md` still shows the obsolete marker command in a fenced command block.

### 1.1 Find stale command block

- [ ] Open `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`
- [ ] Search for `pytest -m kicad`
- [ ] Search for `-m kicad`
- [ ] Identify any fenced shell/code block that makes the obsolete marker look runnable

### 1.2 Replace with prose or correct command

- [ ] Remove the runnable-looking stale command block
- [ ] Replace it with prose such as:

```text
A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

- [ ] If showing a runnable command, use only:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

- [ ] Do not add or document a new `kicad` marker

### 1.3 Search active docs for other stale runnable examples

- [ ] Search active/current docs for `pytest -m kicad`
- [ ] Search active/current docs for `-m kicad`
- [ ] Confirm any remaining occurrences are prose-only historical references, not runnable instructions
- [ ] Confirm active validation instructions use `requires_kicad`

---

## 2. Harden KiCad skip-helper probe against `OSError` (P1 — test infrastructure robustness)

The KiCad availability helper should treat unlaunchable `kicad-cli` as unavailable, not crash.

### 2.1 Inspect skip-helper code

- [ ] Open `tests/conftest.py`
- [ ] Locate `kicad_cli_version()` or equivalent helper
- [ ] Locate the `subprocess.run([kicad_cli, "--version"], ...)` call
- [ ] Check whether it catches `OSError`

### 2.2 Add `OSError` handling if missing

If the helper does not already handle launch errors:

- [ ] Wrap the `subprocess.run` call in `try/except OSError`
- [ ] Return `None` when `OSError` occurs
- [ ] Preserve existing behavior when KiCad runs successfully
- [ ] Preserve the minimum KiCad version check
- [ ] Preserve system KiCad symbol library checks
- [ ] Do not catch broad `Exception`
- [ ] Do not add a new pytest marker
- [ ] Keep public decorator usage as `@requires_kicad`

Suggested pattern:

```python
try:
    result = subprocess.run([...], ...)
except OSError:
    return None
```

### 2.3 Add or document validation

Preferred if easy:

- [ ] Add a small test that monkeypatches the KiCad probe so `subprocess.run` raises `PermissionError` or `OSError`
- [ ] Assert the helper returns `None` or otherwise treats KiCad as unavailable
- [ ] Assert KiCad-dependent tests would skip cleanly

If a clean test is awkward because this lives in `tests/conftest.py`:

- [ ] Document manual verification in completion notes

---

## 3. Run targeted validation (P0 — must pass)

### 3.1 Targeted regression tests

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_adapters.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q
uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q
```

- [ ] Adapter tests pass
- [ ] Model-corpus ingestion tests pass
- [ ] Model-corpus evaluate command tests pass

### 3.2 Lint/type validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [ ] Ruff passes
- [ ] Ruff format check passes
- [ ] Mypy passes

### 3.3 Optional broader validation

Run if feasible:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [ ] Full unit suite passes, or any timeout/failure is honestly reported
- [ ] Web tests pass or skip clearly
- [ ] KiCad-dependent tests pass or skip clearly

---

## 4. Artifact hygiene (P1 — packaging cleanliness)

### 4.1 Check generated artifacts

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Remove generated cache artifacts before packaging if necessary
- [ ] `git status --short` shows only intentional files

---

## 5. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Stale docs command removed
- [ ] Exact replacement wording or correct command used
- [ ] Whether `kicad_cli_version()` or equivalent helper was changed
- [ ] Exact behavior when `kicad-cli --version` raises `OSError`
- [ ] Whether a test was added for skip-helper `OSError` behavior
- [ ] Exact targeted validation commands and results
- [ ] Whether full unit tests were run or skipped/timed out
- [ ] Whether `kicad-cli` was available
- [ ] Whether system KiCad symbols were available
- [ ] Whether `rsvg-convert` was available
- [ ] Tests skipped and skip reasons
- [ ] Artifact hygiene result

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 3 |
| P1 — Test infrastructure robustness / hygiene | 2, 4 |
| P2 — Completion reporting | 5 |
