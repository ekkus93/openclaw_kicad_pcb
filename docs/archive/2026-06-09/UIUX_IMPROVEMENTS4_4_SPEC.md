# UI/UX Improvements — Batch 4.4 Spec

## Purpose

Batch 4.3 successfully implemented the final code/test-infrastructure cleanup around the KiCad availability probe. The latest review found only one remaining issue: `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` still contains a fenced, runnable-looking command using the obsolete `-m kicad` marker.

This batch is a tiny documentation cleanup and validation pass. It should not change application behavior, production code, frontend code, backend code, pytest markers, or test semantics unless validation reveals an unexpected issue.

## Background

The current project uses the existing pytest marker:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

There must not be active/current runnable-looking command examples that use:

```text
pytest -m kicad
```

The previous Batch 4.3 patch fixed `docs/UIUX_IMPROVEMENTS4_2_SPEC.md`, but `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` still contains a stale fenced command block.

## Goals

1. Remove the fenced stale `pytest -m kicad` command from `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`.
2. Replace it with prose explaining that `-m kicad` is obsolete, or with the correct `-m requires_kicad` command.
3. Confirm active/current docs no longer show stale marker commands as runnable examples.
4. Re-run a small targeted validation set.
5. Preserve all code behavior.

## Non-Goals

- Do not add a new pytest marker named `kicad`.
- Do not rename `requires_kicad`.
- Do not change `tests/conftest.py` unless validation uncovers a real issue.
- Do not change production code.
- Do not change frontend code.
- Do not change backend artifact names.
- Do not rewrite historical notes unless they are current instructions or runnable-looking command examples.
- Do not create another broad refactor.

---

## 1. Remove stale runnable-looking command from Batch 4.3 spec

### Problem

`docs/UIUX_IMPROVEMENTS4_3_SPEC.md` previously showed the obsolete `-m kicad` marker in a runnable-looking code block, since replaced with prose in Batch 4.4. A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.

### Required behavior

The Batch 4.3 spec must not show `pytest -m kicad` as a runnable command.

### Implementation requirements

Open:

```text
docs/UIUX_IMPROVEMENTS4_3_SPEC.md
```

Find the fenced code block containing `-m kicad`.

Replace it with prose such as:

```text
A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

If a runnable command is shown, show only:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

Do not document a new `kicad` marker.

### Acceptance criteria

- `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` no longer contains a fenced runnable command using `-m kicad`.
- Any remaining `-m kicad` text in that file is prose-only and clearly marked obsolete, or removed entirely.
- The correct `requires_kicad` marker is used for runnable commands.

---

## 2. Search current docs for stale runnable marker examples

### Required behavior

No active/current docs should show `pytest -m kicad` or `-m kicad` as a runnable command.

### Implementation requirements

Search current docs for:

```text
pytest -m kicad
-m kicad
```

Classify any remaining hits:

1. Current runnable instruction — must be fixed.
2. Historical prose reference — acceptable only if clearly marked obsolete and not in a fenced shell block.
3. Task/TODO text describing the stale-command cleanup — acceptable if not presented as an instruction.

### Acceptance criteria

- No active/current docs contain runnable-looking `pytest -m kicad` instructions.
- Active validation commands use `pytest -m requires_kicad`.
- No new `kicad` marker is added or documented.

---

## 3. Re-run targeted validation

This batch should not need a full validation sweep, but run the focused checks that cover the recently modified test infrastructure and documentation-adjacent Python checks.

### Required commands

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py -q
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

### Optional command

If time permits, also run:

```bash
uv run --extra dev --extra web ruff format --check .
```

### Acceptance criteria

- `test_conftest_helpers.py` passes.
- Ruff passes.
- Mypy passes.
- Ruff format check passes if run.
- Any validation not run is explicitly called out in completion notes.

---

## 4. Artifact hygiene

Documentation edits and test validation can generate local cache files. Do not include generated artifacts in the final change.

### Commands

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

### Acceptance criteria

- No generated cache artifacts are tracked.
- The final changed file list is intentional.
- Ideally the only source/doc file changed is `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`, unless validation or formatting exposes a legitimate issue.

---

## Completion notes required

Claude Code should report:

- files changed,
- exact stale command removed,
- replacement text or corrected command,
- search results for remaining `-m kicad` occurrences,
- exact validation commands and results,
- whether full validation was skipped intentionally,
- artifact hygiene result,
- any remaining known issues.
