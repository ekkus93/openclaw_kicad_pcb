# Replies to Batch 3 Questions and Issues

Source questions file: `repsonses5.md`

## Q1 — Task 5: KiCad marker already exists

Use **Option A**.

Do **not** add a new `@pytest.mark.kicad` marker. The project already has the right infrastructure:

- `@pytest.mark.requires_kicad` registered in `pyproject.toml`
- `requires_kicad()` decorator in `tests/conftest.py`
- skip logic for `kicad-cli >= 9.0.0`

Adding a second marker would create unnecessary duplication and confusion. Treat `requires_kicad` as the canonical marker for this project.

### Implementation guidance

- Keep the existing `requires_kicad` marker.
- Keep the existing `requires_kicad()` decorator.
- Apply `@requires_kicad()` to every test that truly requires:
  - real `kicad-cli`
  - real KiCad system symbol libraries
  - real KiCad project validation/rendering behavior
- Do not mark tests as `requires_kicad` merely to hide unrelated failures.
- Do not introduce a second marker named `kicad`.
- Do not migrate or alias the existing marker unless there is a broader naming cleanup later.

### Documentation updates

Update the Batch 3 docs/TODO language from `kicad` to `requires_kicad`.

Use this command for explicitly running KiCad-dependent tests:

```bash
uv run --extra dev --extra web python -m pytest -m requires_kicad
```

For normal validation, either run the full suite and let `requires_kicad` tests skip automatically when KiCad is unavailable, or document an explicit non-KiCad run such as:

```bash
uv run --extra dev --extra web python -m pytest -m "not requires_kicad"
```

The exact test path can be narrower if the repo already has a standard test command.

---

## Q2 — Task 4: `uv run --extra dev --extra web` syntax confirmed

Good. Proceed with the preferred validation command form from the spec.

Use these commands in docs and completion notes where applicable:

```bash
uv run --extra dev --extra web ruff check .
uv run --extra dev --extra web ruff format --check .
uv run --extra dev --extra web mypy src/kicad_pcb src/kicad_pcb_web
uv run --extra dev --extra web python -m pytest tests/unit/
```

If a command is intentionally narrowed or expanded, record the exact command that was actually run in the completion notes.

---

## Q3 — Task 1.4: Invalid-route test approach

Agreed.

If the current app does not perform a literal URL redirect for invalid wizard steps, do not force one just for the test. The important behavior is that the invalid route is normalized to `undefined` and the page renders the canonical step content safely.

### Test expectation

For a route such as:

```text
/wizard/abc123/not-a-step
```

Mock the session query to return a known session state, then assert that:

- the canonical step content renders,
- the page is not blank,
- the invalid step does not produce a broken state,
- no invalid step UI is selected.

Do not assert that the URL changes unless the implementation is deliberately changed to use `<Navigate>`.

This is consistent with the real product behavior and avoids adding unnecessary routing churn.

---

## Q4 — Task 3: “Edit project details” link safety

Show the **Edit project details** link only when no wizard action is in progress.

Navigating back to Describe is technically safe, but it is confusing if the user can do it while generation or another mutation is pending. During a busy state, do not provide an active link.

### Preferred implementation

When `busyMessage` is falsy:

```tsx
<Link to={`/wizard/${session.id}/describe`}>Edit project details</Link>
```

When `busyMessage` is truthy, either:

1. Render disabled-looking text/button copy, not a clickable `<Link>`:

```tsx
<span aria-disabled="true">Edit project details</span>
```

with a short note such as:

```text
Project details cannot be edited while this action is running.
```

or:

2. Omit the link entirely while busy.

I prefer option 1 because it explains why the edit affordance is temporarily unavailable.

### Acceptance criteria

- The read-only summary is always visible on Generate.
- The edit link is active only when no action is pending.
- There is no clickable disabled-looking link.
- Existing generation, regenerate confirmation, and failed-job retry behavior remains unchanged.

---

## Q5 — Task 6: Replacement copy for `OpenClaw_Managed.kicad_sch`

Use:

```text
Generated managed schematic
```

That is the preferred neutral replacement for normal user-facing UI.

### Implementation guidance

- Replace normal UI appearances of `OpenClaw_Managed.kicad_sch` with `Generated managed schematic`.
- Remove other normal user-facing legacy `OpenClaw` copy if found.
- Preserve literal backend artifact filenames only where required for:
  - actual download paths,
  - archive contents,
  - developer diagnostics,
  - low-level debug output,
  - tests that intentionally validate backend artifact names.
- Do not rename backend artifacts in this batch unless the artifact name is purely cosmetic and no compatibility risk exists.

### Test expectations

Update frontend tests to assert the neutral label:

```text
Generated managed schematic
```

Do not change backend filename tests unless the backend filename itself is intentionally changed.

---

## Final instruction summary for Claude Code

Proceed with Batch 3 using these decisions:

1. Reuse existing `requires_kicad`; do not add `kicad`.
2. Use `uv run --extra dev --extra web ...` validation commands.
3. Invalid wizard step tests should assert canonical content rendering, not URL redirect.
4. Add the Generate-step read-only project settings summary.
5. Show/enable “Edit project details” only when no busy action is pending.
6. Replace normal UI `OpenClaw_Managed.kicad_sch` copy with `Generated managed schematic`.
