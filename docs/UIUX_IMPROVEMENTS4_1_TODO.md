# UI/UX Improvements — Batch 4.1 TODO

Derived from the Batch 4 review. Batch 4 fixed the originally targeted symbol fallback test and frontend route-test issue, but the full ordinary unit test suite can still fail on machines without KiCad tooling. This batch fixes the remaining unit-test hermeticity and documentation accuracy issues.

---

## 1. Fix `test_model_corpus_ingestion.py` KiCad CLI dependency (P0 — unit-test hermeticity)

`tests/unit/test_model_corpus_ingestion.py::test_list_command_reads_fixture_metadata` can fail with `FileNotFoundError: kicad-cli` when KiCad is not installed.

### 1.1 Inspect the test and code path

- [ ] Open `tests/unit/test_model_corpus_ingestion.py`
- [ ] Locate `test_list_command_reads_fixture_metadata`
- [ ] Identify why it reaches `kicad-cli`
- [ ] Identify whether the test is intended to validate:
  - [ ] fixture metadata reading, or
  - [ ] real KiCad CLI integration

### 1.2 Prefer a hermetic unit-test fix

If the test is primarily about fixture metadata reading:

- [ ] Prevent the test from calling real `kicad-cli`
- [ ] Use fixture metadata that does not trigger CLI validation, or
- [ ] Mock the narrow function that invokes `kicad-cli`, or
- [ ] Inject a fake CLI runner if the code supports it
- [ ] Preserve strong assertions about the metadata being listed/read
- [ ] Do not weaken the test to merely “does not crash”
- [ ] Do not require KiCad for this ordinary unit test

### 1.3 Mark only if truly KiCad-dependent

If inspection proves the test is explicitly validating real KiCad CLI behavior:

- [ ] Decorate the test with existing `@requires_kicad`
- [ ] Use no parentheses: `@requires_kicad`
- [ ] Do not use `@pytest.mark.kicad`
- [ ] Do not add a new marker
- [ ] Explain in completion notes why this is truly KiCad-dependent

### 1.4 Validate the fix

- [ ] Run the specific test without relying on host KiCad
- [ ] Run `uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_ingestion.py -q`
- [ ] Confirm no unmarked unit test calls real `kicad-cli`

---

## 2. Resolve `test_model_corpus_evaluate_command.py` status expectation (P0 — behavior/test contract)

`tests/unit/test_model_corpus_evaluate_command.py::test_model_corpus_evaluate_generates_partial_reports_without_repo_kicad` observed `partial` but expected `fail`.

### 2.1 Inspect intended status semantics

- [ ] Open `tests/unit/test_model_corpus_evaluate_command.py`
- [ ] Locate `test_model_corpus_evaluate_generates_partial_reports_without_repo_kicad`
- [ ] Inspect the evaluator code that emits `partial` or `fail`
- [ ] Search docs/tests for the intended meaning of:
  - [ ] `partial`
  - [ ] `fail`
- [ ] Identify whether missing KiCad should mean partial output or total failure

### 2.2 Choose and document the correct behavior

Choose one:

- [ ] If useful reports are generated but KiCad-dependent work is unavailable, expected status should be `partial`
- [ ] If missing KiCad prevents the command from producing valid output, implementation should return `fail`

Preferred: use `partial` if report generation succeeds but optional/host-dependent KiCad validation is unavailable.

### 2.3 Update test or implementation

If `partial` is correct:

- [ ] Update the expected status from `fail` to `partial`
- [ ] Add or update assertions for warnings/diagnostics explaining why the result is partial, if available
- [ ] Make the test name/comment clear

If `fail` is correct:

- [ ] Fix evaluator implementation to return `fail`
- [ ] Add assertions showing why this is a core failure rather than a partial result

### 2.4 Validate the fix

- [ ] Run `uv run --extra dev --extra web python -m pytest tests/unit/test_model_corpus_evaluate_command.py -q`
- [ ] Confirm the test no longer fails due to KiCad availability
- [ ] Confirm no real evaluator failure is hidden by accepting any status

---

## 3. Fix stale `pytest -m kicad` command in active docs (P1 — documentation correctness)

The project uses `requires_kicad`, not `kicad`.

### 3.1 Search active docs

- [ ] Search for `pytest -m kicad`
- [ ] Search for `-m kicad`
- [ ] Check `docs/UIUX_IMPROVEMENTS4_SPEC.md`
- [ ] Check `docs/UIUX_IMPROVEMENTS4_TODO.md`
- [ ] Check `CLAUDE.md`
- [ ] Check current validation docs

### 3.2 Replace stale command

- [ ] Replace active/current examples of:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

with:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

- [ ] Do not add or document a new `kicad` marker
- [ ] Leave historical notes alone unless they are used as current instructions

### 3.3 Confirm consistency

- [ ] No active/current docs instruct developers to run `pytest -m kicad`
- [ ] Docs identify `requires_kicad` as canonical
- [ ] Docs distinguish normal unit tests from KiCad-dependent tests

---

## 4. Update `requires_kicad` marker description (P1 — pytest metadata accuracy)

The marker now covers more than just `kicad-cli`.

### 4.1 Update `pyproject.toml`

- [ ] Open `pyproject.toml`
- [ ] Find the pytest marker registration for `requires_kicad`
- [ ] Update the description to mention KiCad CLI and system KiCad assets
- [ ] If generation-pipeline tests reuse this marker, mention full KiCad generation toolchain

Suggested wording:

```text
requires_kicad: marks tests that need KiCad CLI, system KiCad assets, or the full KiCad generation toolchain
```

### 4.2 Confirm no new marker is added

- [ ] Do not add a `kicad` marker
- [ ] Do not rename `requires_kicad`
- [ ] Confirm existing decorators still work

---

## 5. Strengthen no-KiCad validation confidence (P1 — validation reliability)

A machine with KiCad installed can hide non-hermetic tests. Add or document a credible no-KiCad validation path.

### 5.1 Choose validation approach

Choose at least one:

- [ ] Add targeted tests that monkeypatch `shutil.which("kicad-cli")` to simulate missing KiCad
- [ ] Add tests for skip helpers such as `requires_kicad`
- [ ] Document a no-KiCad container/CI validation path
- [ ] Confirm by running on an environment without KiCad

### 5.2 Verify ordinary tests do not require KiCad

- [ ] Run `tests/unit/` in an environment without KiCad, or
- [ ] Simulate missing KiCad for the previously failing code paths
- [ ] Confirm unmarked unit tests do not call real `kicad-cli`

### 5.3 Document validation result

- [ ] Completion notes state whether `kicad-cli` was available
- [ ] Completion notes state whether system KiCad symbols were available
- [ ] Completion notes state whether `rsvg-convert` was available
- [ ] Completion notes explain how no-KiCad behavior was verified

---

## 6. Run final validation (P0 — must pass)

### 6.1 Frontend validation

```bash
cd frontend
npm run build
npm run lint
npm test -- --run
```

- [ ] Build passes
- [ ] Lint passes
- [ ] Tests pass

### 6.2 Python/backend validation

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

- [ ] Ruff passes
- [ ] Ruff format check passes
- [ ] Mypy passes
- [ ] Unit tests pass

### 6.3 Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

- [ ] Web tests pass
- [ ] Tool-dependent tests skip cleanly when tools are missing
- [ ] Skip reasons are clear

### 6.4 KiCad-dependent tests

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

- [ ] KiCad-dependent tests pass when tools are available, or
- [ ] KiCad-dependent tests skip cleanly when tools are unavailable

### 6.5 Artifact hygiene

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] No unintended files changed

---

## 7. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] How `test_model_corpus_ingestion.py` was fixed
- [ ] Whether `test_model_corpus_ingestion.py` is hermetic or marked `requires_kicad`
- [ ] How `test_model_corpus_evaluate_command.py` was resolved
- [ ] Whether `partial` or `fail` is the intended status and why
- [ ] Docs changed from `kicad` to `requires_kicad`
- [ ] Marker description update
- [ ] No-KiCad validation approach
- [ ] Exact validation commands and results
- [ ] Whether `kicad-cli` was available
- [ ] Whether system KiCad symbols were available
- [ ] Whether `rsvg-convert` was available
- [ ] Tests skipped and skip reasons

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 6 |
| P1 — Docs/metadata/no-KiCad confidence | 3, 4, 5 |
| P2 — Completion reporting | 7 |
