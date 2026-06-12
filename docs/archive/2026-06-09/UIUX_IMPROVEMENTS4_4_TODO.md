# UI/UX Improvements — Batch 4.4 TODO

Derived from the Batch 4.3 review. Batch 4.3 code/test-infrastructure work is good. The only remaining issue is that `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` still contains a fenced, runnable-looking stale `pytest -m kicad` command. This batch removes that stale command and performs a small targeted validation pass.

---

## 1. Remove stale runnable-looking `pytest -m kicad` from Batch 4.3 spec (P0 — documentation accuracy)

### 1.1 Open the Batch 4.3 spec

- [x] Open `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`
- [x] Search for `pytest -m kicad` — found at Section 1 "Problem"
- [x] Search for `-m kicad` — same location
- [x] Identified fenced `bash` block containing the obsolete marker

### 1.2 Replace stale command

- [x] Removed the fenced `bash` block containing the stale command
- [x] Replaced with prose: "`docs/UIUX_IMPROVEMENTS4_2_SPEC.md` previously showed the obsolete `-m kicad` marker in a runnable-looking code block, since replaced with prose in Batch 4.3. A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`."
- [x] Do not add or document a new `kicad` marker
- [x] Do not rename `requires_kicad`

### 1.3 Confirm the file is clean

- [x] `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` no longer shows `pytest -m kicad` in a runnable fenced block
- [x] Remaining `-m kicad` occurrences in that file are prose-only and clearly identified as obsolete
- [x] Runnable KiCad-dependent test commands use `-m requires_kicad`

---

## 2. Search current docs for other stale runnable marker examples (P0 — consistency)

### 2.1 Search docs

- [x] Searched all docs and CLAUDE.md for `pytest -m kicad` and `-m kicad`

Also fixed `docs/UIUX_IMPROVEMENTS4_4_SPEC.md` in the same commit — its own Section 1 "Problem" contained the stale command in a fenced `bash` block (the same cascade-creating pattern). Replaced with prose per replies7.md instructions.

### 2.2 Classify remaining occurrences

All remaining hits classified as:

- [x] `docs/UIUX_IMPROVEMENTS4_4_TODO.md` — task description showing what to remove (not a runnable instruction)
- [x] `docs/UIUX_IMPROVEMENTS4_4_SPEC.md:20,99` — `text` blocks (not `bash`), explicitly marking the pattern as obsolete
- [x] `docs/replies7.md` — replies file showing the command inside a `text` block
- [x] All other hits — prose references, completion notes, or search-task descriptions (all acceptable)
- [x] No active runnable `bash`/`sh` block with `-m kicad` remains in any doc

### 2.3 Confirm canonical marker

- [x] Active docs use `pytest -m requires_kicad`
- [x] No new `kicad` marker added
- [x] `requires_kicad` remains canonical

---

## 3. Run targeted validation (P0 — must pass)

### 3.1 Test KiCad helper coverage

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py -q
```

- [x] 4 tests pass

### 3.2 Run lint and type checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [x] Ruff passes — all checks passed
- [x] Mypy passes — no issues found in 109 source files

### 3.3 Optional format check

```bash
uv run --extra dev --extra web ruff format --check .
```

- [x] Ruff format check passes — 217 files already formatted

---

## 4. Artifact hygiene (P1 — packaging cleanliness)

### 4.1 Check generated artifacts

- [x] No generated cache artifacts tracked
- [x] `git status --short` shows only intentional changes

### 4.2 Expected changed files

- [x] `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` — stale bash block replaced with prose
- [x] `docs/UIUX_IMPROVEMENTS4_4_SPEC.md` — stale bash block replaced with prose (cascade prevention)
- [x] `docs/UIUX_IMPROVEMENTS4_4_TODO.md` — this file

---

## 5. Completion notes required

- [x] Files changed: `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`, `docs/UIUX_IMPROVEMENTS4_4_SPEC.md`, `docs/UIUX_IMPROVEMENTS4_4_TODO.md`
- [x] Stale command removed from both specs — fenced `bash` blocks showing `uv run ... python -m pytest -m kicad`
- [x] Replacement wording: "previously showed the obsolete `-m kicad` marker in a runnable-looking code block, since replaced with prose in Batch 4.4. A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`."
- [x] Search results: no active runnable `bash`/`sh` block with `-m kicad` remains anywhere in docs or CLAUDE.md
- [x] Active docs consistently use `requires_kicad`
- [x] Validation: conftest helpers (4 passed), ruff (clean), mypy (clean), format check (clean)
- [x] Full unit suite not re-run (no production code changed; targeted validation sufficient)
- [x] Artifact hygiene: clean

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3 |
| P1 — Hygiene/reporting | 4, 5 |
