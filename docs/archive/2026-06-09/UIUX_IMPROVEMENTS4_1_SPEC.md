# UI/UX Improvements — Batch 4.1 Spec

## Purpose

Batch 4 fixed the originally targeted `test_lib_symbol.py` system-library fallback issue and improved the invalid wizard route test, but the latest review found that the full ordinary unit test suite is still not hermetic on a machine without KiCad tooling. This follow-up batch closes the remaining validation/documentation gap.

This is a small test and documentation reliability patch. It should not change normal user-facing web app behavior.

## Background

The Batch 4 review found:

1. Frontend build/lint/tests pass.
2. Python ruff, format check, and mypy pass.
3. `tests/web/` passes with expected skips.
4. `pytest -m requires_kicad` skips cleanly when KiCad is unavailable.
5. The full `tests/unit/` suite can still fail on machines without KiCad tooling.
6. `docs/UIUX_IMPROVEMENTS4_SPEC.md` still contains the stale marker command `pytest -m kicad`.
7. The `requires_kicad` marker description in `pyproject.toml` is now incomplete because the decorator also checks for system KiCad assets and the full generation pipeline may require additional tools such as `rsvg-convert`.

## Goals

1. Make the ordinary unit suite pass without real `kicad-cli` or host KiCad system libraries.
2. Fix or correctly mark the remaining unit tests that currently depend on KiCad tooling.
3. Resolve the expected-result ambiguity in the model corpus evaluate test.
4. Standardize all active/current docs on `requires_kicad`.
5. Update pytest marker documentation to accurately describe current behavior.
6. Re-run validation and record exact results, including no-KiCad behavior.

## Non-Goals

- Do not add a new pytest marker named `kicad`.
- Do not replace the existing `requires_kicad` infrastructure.
- Do not make preview generation non-fatal in production.
- Do not add preview mocking broadly unless a specific unit test needs a narrow mock.
- Do not weaken assertions just to make tests pass.
- Do not hide real failures by marking ordinary tests as KiCad-dependent without justification.
- Do not change frontend behavior.
- Do not change backend artifact names.

---

## 1. Fix `test_model_corpus_ingestion.py` KiCad CLI dependency

### Problem

The unit test:

```text
tests/unit/test_model_corpus_ingestion.py::test_list_command_reads_fixture_metadata
```

can fail with:

```text
FileNotFoundError: [Errno 2] No such file or directory: 'kicad-cli'
```

This means an ordinary unit test is calling code that reaches real KiCad CLI. That is not hermetic.

### Required behavior

This test should pass without real `kicad-cli`.

Because this is a unit test for listing/reading fixture metadata, it should not require full KiCad CLI integration unless the test name and assertions explicitly state that purpose.

### Preferred implementation

Refactor the test or code path so the list/metadata behavior can be tested without invoking real `kicad-cli`.

Acceptable approaches:

1. Use fixture metadata that does not trigger KiCad CLI validation.
2. Mock the narrow function responsible for calling `kicad-cli`.
3. Inject a fake CLI runner into the tested code, if the code already supports dependency injection.
4. Split the test into:
   - a hermetic unit test for fixture metadata reading, and
   - a separate KiCad integration test for real CLI-backed validation.

### When marking is acceptable

Only mark this test with `@requires_kicad` if inspection proves its explicit purpose is to validate real KiCad CLI integration. Do not mark it merely because that is the quickest way to avoid the failure.

### Requirements

- Preserve meaningful assertions about metadata reading.
- Do not call real `kicad-cli` from ordinary unit tests.
- If a real KiCad path is still needed, move or duplicate that coverage under `@requires_kicad`.
- Do not weaken the test to only assert that no exception was raised.
- Ensure the test passes when `kicad-cli` is absent.

### Acceptance criteria

- `test_list_command_reads_fixture_metadata` passes without `kicad-cli`, or is clearly and justifiably marked `@requires_kicad`.
- Ordinary unit tests do not fail with `FileNotFoundError: kicad-cli`.
- Any real KiCad coverage is explicitly marked.

---

## 2. Resolve `test_model_corpus_evaluate_command.py` expected result ambiguity

### Problem

The unit test:

```text
tests/unit/test_model_corpus_evaluate_command.py::test_model_corpus_evaluate_generates_partial_reports_without_repo_kicad
```

failed with:

```text
AssertionError: assert 'partial' == 'fail'
```

The observed result is `partial`, while the test expected `fail`.

### Required behavior

Determine which status is correct for a missing or unavailable repo KiCad environment:

- If the evaluator successfully produces some report outputs but cannot complete KiCad-dependent work, `partial` may be the correct status.
- If missing repo KiCad should make the whole evaluation fail, then the evaluator should return `fail` and the code should be fixed.

### Implementation requirements

Inspect:

- the evaluator status model,
- the CLI command behavior,
- existing docs or help text,
- any downstream consumers of `partial` vs `fail`,
- tests that assert related behavior.

Then choose the correct behavior and update either the test or the implementation.

### Preferred interpretation

If the command produces useful report artifacts but some KiCad-dependent validation is unavailable, prefer `partial`. This is more informative than a total `fail` and matches the observed result.

However, do not update the expected value blindly. Confirm by reading the evaluator contract.

### Tests

Add or update assertions to make the intended distinction explicit:

- `partial` means report generation completed with missing optional/host-dependent validation.
- `fail` means the command could not produce the expected report or encountered a core failure.
- Warnings or diagnostics explain why the result is partial, if such diagnostics exist.

### Acceptance criteria

- The test expectation matches the intended evaluator contract.
- The test no longer fails just because KiCad tooling is unavailable.
- The result semantics are documented in the test name, test comments, or relevant docs.
- No real evaluator failure is hidden by accepting any status.

---

## 3. Fix stale marker command in active docs

### Problem

`docs/UIUX_IMPROVEMENTS4_SPEC.md` previously included a stale command referencing the obsolete `-m kicad` marker, since fixed in Batch 4. A previous Batch 4 spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.

The project uses the existing marker:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

### Required behavior

All active/current validation instructions must use `requires_kicad`, not `kicad`.

### Requirements

- Search active docs for `pytest -m kicad`, `-m kicad`, and similar stale marker commands.
- Replace active/current instructions with `-m requires_kicad`.
- Do not rewrite historical notes unless they function as current instructions.
- Do not add or document a new `kicad` marker.

### Acceptance criteria

- No active/current docs instruct developers to run `pytest -m kicad`.
- `requires_kicad` is consistently documented as the canonical marker.
- Batch 4 and Batch 4.1 docs are internally consistent.

---

## 4. Update `requires_kicad` marker description

### Problem

The pytest marker description currently describes only `kicad-cli`, but the helper now also checks system KiCad symbol libraries. Generation-pipeline tests may also depend on `rsvg-convert`.

### Required behavior

The marker documentation should accurately describe what it covers.

### Recommended wording

In `pyproject.toml`, update the marker description to something like:

```text
requires_kicad: marks tests that need KiCad CLI, system KiCad assets, or the full KiCad generation toolchain
```

If `rsvg-convert` is covered by a separate helper that reuses the marker, the description may mention “full KiCad generation toolchain” rather than enumerating every tool.

### Requirements

- Keep the marker name `requires_kicad`.
- Do not add a new marker unless there is a separate long-term decision to split markers.
- Ensure the description matches the behavior of `requires_kicad` and `requires_generation_pipeline`.

### Acceptance criteria

- `pyproject.toml` marker description is no longer misleading.
- Developer docs and pytest marker docs agree.

---

## 5. Strengthen no-KiCad validation confidence

### Problem

Running validation on a machine that happens to have KiCad installed can hide non-hermetic unit tests.

### Required behavior

The project should have a clear way to validate that ordinary unit tests do not require KiCad.

### Implementation options

Choose the smallest reliable option.

#### Option A — Environment simulation in tests

Use monkeypatching in specific tests to simulate missing `kicad-cli` or missing symbol libraries where appropriate.

#### Option B — Validation command documentation

Document a manual no-KiCad validation approach, such as running in a container or environment without KiCad.

#### Option C — Test helper coverage

Add tests for the skip helpers themselves, verifying behavior when `shutil.which("kicad-cli")` returns `None`.

### Requirements

- The final validation notes must state whether KiCad CLI was available.
- If KiCad was available, still document how no-KiCad behavior was verified.
- Do not rely solely on “passes on my machine with KiCad installed” for hermeticity claims.

### Acceptance criteria

- There is a credible verification path for ordinary unit tests without KiCad.
- Completion notes state how no-KiCad behavior was validated or why it could not be fully validated.
- Any remaining KiCad-dependent tests are explicitly marked.

---

## 6. Final validation

Run the validation set after implementation.

### Frontend

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

### Web tests

```bash
uv run --extra dev --extra web python -m pytest tests/web/ -q -rs
```

### KiCad-dependent tests

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad -q -rs
```

### Optional explicit non-KiCad check

If feasible, also run in an environment without `kicad-cli`:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/ -q
```

If the developer machine has KiCad installed, simulate missing KiCad in targeted tests or document that a no-KiCad CI/container run is still required.

### Acceptance criteria

- Frontend build, lint, and tests pass.
- Python ruff, format, mypy, and unit tests pass.
- Unit tests do not require real KiCad unless explicitly marked.
- Web tests pass or skip only explicitly marked external-tool tests.
- KiCad-dependent tests pass or skip cleanly.
- Active docs use `requires_kicad`.
- Marker description is accurate.
- No generated cache artifacts are tracked.

---

## Completion notes required

Claude Code should report:

- files changed,
- how `test_model_corpus_ingestion.py` was fixed,
- how `test_model_corpus_evaluate_command.py` was resolved,
- whether `partial` or `fail` is the intended status and why,
- docs changed from `kicad` to `requires_kicad`,
- marker description update,
- no-KiCad validation approach,
- exact validation commands run,
- whether `kicad-cli` was available,
- whether system KiCad symbols were available,
- whether `rsvg-convert` was available,
- skipped tests and skip reasons.
