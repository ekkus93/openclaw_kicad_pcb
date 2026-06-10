# UI/UX Improvements — Batch 4.4 TODO

Derived from the Batch 4.3 review. Batch 4.3 code/test-infrastructure work is good. The only remaining issue is that `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` still contains a fenced, runnable-looking stale `pytest -m kicad` command. This batch removes that stale command and performs a small targeted validation pass.

---

## 1. Remove stale runnable-looking `pytest -m kicad` from Batch 4.3 spec (P0 — documentation accuracy)

### 1.1 Open the Batch 4.3 spec

- [ ] Open `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`
- [ ] Search for `pytest -m kicad`
- [ ] Search for `-m kicad`
- [ ] Find any fenced code block that shows the obsolete marker as a runnable command

### 1.2 Replace stale command

- [ ] Remove the fenced command block containing:

```bash
uv run --extra dev --extra web python -m pytest -m kicad
```

- [ ] Replace it with prose, such as:

```text
A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

- [ ] If a runnable command is needed, use only:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

- [ ] Do not add or document a new `kicad` marker
- [ ] Do not rename `requires_kicad`

### 1.3 Confirm the file is clean

- [ ] `docs/UIUX_IMPROVEMENTS4_3_SPEC.md` no longer shows `pytest -m kicad` in a runnable fenced block
- [ ] Any remaining `-m kicad` text is prose-only and clearly identified as obsolete, or removed entirely
- [ ] Runnable KiCad-dependent test commands use `-m requires_kicad`

---

## 2. Search current docs for other stale runnable marker examples (P0 — consistency)

### 2.1 Search docs

Run equivalent searches:

```bash
grep -R "pytest -m kicad" docs CLAUDE.md -n
grep -R -- "-m kicad" docs CLAUDE.md -n
```

- [ ] Search results reviewed
- [ ] Current runnable instructions using `pytest -m kicad` are fixed
- [ ] Current runnable instructions using `-m kicad` are fixed

### 2.2 Classify remaining occurrences

For each remaining hit, classify it as:

- [ ] active runnable instruction — must be fixed
- [ ] historical prose reference — acceptable only if clearly obsolete
- [ ] TODO/spec text describing this cleanup — acceptable if not runnable

### 2.3 Confirm canonical marker

- [ ] Active docs use `pytest -m requires_kicad`
- [ ] No new `kicad` marker is added
- [ ] `requires_kicad` remains canonical

---

## 3. Run targeted validation (P0 — must pass)

### 3.1 Test KiCad helper coverage

```bash
uv run --extra dev --extra web python -m pytest tests/unit/test_conftest_helpers.py -q
```

- [ ] Test passes

### 3.2 Run lint and type checks

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
```

- [ ] Ruff passes
- [ ] Mypy passes

### 3.3 Optional format check

```bash
uv run --extra dev --extra web ruff format --check .
```

- [ ] Ruff format check passes, or
- [ ] If not run, completion notes explain why

---

## 4. Artifact hygiene (P1 — packaging cleanliness)

### 4.1 Check generated artifacts

```bash
find . -type d -name '__pycache__' -print
find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print
git status --short
```

- [ ] No generated cache artifacts are tracked
- [ ] Generated cache artifacts are removed before packaging if necessary
- [ ] `git status --short` shows only intentional changes

### 4.2 Expected changed files

Ideally, the only source/doc change is:

- [ ] `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`

If any other file changes, document why.

---

## 5. Completion notes required

Claude Code should report:

- [ ] Files changed
- [ ] Exact stale command removed
- [ ] Replacement prose or corrected command
- [ ] Search results for remaining `-m kicad` occurrences
- [ ] Confirmation that active docs use `requires_kicad`
- [ ] Exact validation commands and results
- [ ] Whether broader/full validation was intentionally skipped
- [ ] Artifact hygiene result
- [ ] Any remaining known issues

---

## Priority Order

| Priority | Tasks |
|---|---|
| P0 — Must fix | 1, 2, 3 |
| P1 — Hygiene/reporting | 4, 5 |
