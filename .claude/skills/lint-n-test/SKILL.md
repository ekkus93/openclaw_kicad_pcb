---
description: Lint the files and run all tests for the KiCad PCB Web App. Use only when the user explicitly invokes this skill. Invoked as /lint-n-test.
model: haiku
effort: low
disable-model-invocation: true
allowed-tools:
  - Bash(uv run *)
  - Bash(npm --prefix frontend *)
  - Bash(git status *)
  - Bash(git diff *)
  - Read
---

# Lint and Test

Run the project's lint/format/type gates and the full test suite, then report a
concise pass/fail summary. Run every step even if an earlier one fails — collect
all results so the user sees the complete picture in one pass.

## Steps

### 1. Python lint, format, and types

Run each gate and record its result:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

### 2. Python tests

Use `python -m pytest` explicitly — a bare `uv run pytest` resolves to the wrong
(mambaforge 3.10) interpreter:

```bash
uv run --extra dev --extra web python -m pytest tests/unit/
```

Note the pass/skip counts and any skip reasons. Test counts are
environment-dependent: tests marked `requires_kicad` or
`requires_generation_pipeline` skip when `kicad-cli`, KiCad system symbol
libraries, or `rsvg-convert` are unavailable. Do not treat those skips as
failures.

### 3. Frontend (only if `frontend/src/` changed)

Check whether frontend source changed:

```bash
git status --short frontend/src
```

If there are changes there, run the frontend lint, tests, and build:

```bash
npm --prefix frontend run lint
npm --prefix frontend run test:run
npm --prefix frontend run build
```

If `frontend/src/` is unchanged, skip this step and say so.

## Output

Report a short summary with one line per gate: ✅ pass or ❌ fail, plus the test
pass/skip counts and skip reasons. If anything failed, quote the key error lines
so the user can act. Do not attempt to fix failures unless the user asks — this
skill only reports.
