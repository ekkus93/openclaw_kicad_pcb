# Replies to Batch 4.4 Questions and Issues

Source questions file: `repsonses7.md`

## Q1 — Fix both the 4.3 spec and the 4.4 spec in the same commit?

Yes. Fix **both** files in the same Batch 4.4 commit.

Do not fix only `docs/UIUX_IMPROVEMENTS4_3_SPEC.md`. The whole point of Batch 4.4 is to stop the stale `-m kicad` command from continuing to propagate through active docs. If `docs/UIUX_IMPROVEMENTS4_4_SPEC.md` itself contains a fenced runnable-looking command block with the obsolete marker, then it falls under Task 2’s broader instruction to search active/current docs and fix any stale runnable examples.

### Files to update

Update both:

```text
docs/UIUX_IMPROVEMENTS4_3_SPEC.md
docs/UIUX_IMPROVEMENTS4_4_SPEC.md
```

### Required replacement pattern

Remove fenced command blocks that show:

```text
uv run --extra dev --extra web python -m pytest -m kicad
```

Replace them with prose, not another stale runnable block.

Use wording like:

```text
A previous spec referenced the obsolete `-m kicad` marker. Active commands must use `-m requires_kicad`.
```

If a runnable command is needed, show only the correct canonical command:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

### Search requirement

After editing both files, run a search across active docs:

```bash
grep -R "pytest -m kicad" docs CLAUDE.md -n
grep -R -- "-m kicad" docs CLAUDE.md -n
```

Classify any remaining hits:

- Active runnable instruction: fix it.
- Historical prose reference clearly saying obsolete/stale: acceptable.
- TODO/spec text describing the cleanup task: acceptable only if it is not a runnable fenced shell block.

### Do not

- Do not add a new `kicad` marker.
- Do not rename `requires_kicad`.
- Do not leave the stale command inside a fenced `bash`, `sh`, or generic code block.
- Do not create a Batch 4.5 for the same issue if it can be fixed now.

## Final instruction summary for Claude Code

Proceed with Batch 4.4 by fixing both `UIUX_IMPROVEMENTS4_3_SPEC.md` and `UIUX_IMPROVEMENTS4_4_SPEC.md` in the same commit. The acceptance standard is: no active/current doc should present `pytest -m kicad` or `-m kicad` as a runnable command. Active runnable commands must use `pytest -m requires_kicad`.
