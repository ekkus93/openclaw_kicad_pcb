# Batch 3 Questions and Issues

## Task 5 — KiCad marker already exists

The project already has a `@pytest.mark.requires_kicad` marker registered in
`pyproject.toml` and a `requires_kicad()` decorator in `tests/conftest.py` that
wraps both the marker and a `skipif` check for `kicad-cli >= 9.0.0`. Several unit
tests in `tests/unit/` already use this infrastructure.

The spec and TODO prescribe adding `@pytest.mark.kicad`. That would be a second
marker for the same purpose.

**Options:**

A) Use the existing `requires_kicad` marker and decorator — just ensure all
   affected tests are decorated and the skip logic works end-to-end. No new marker.

B) Add `kicad` as the new canonical marker and migrate/alias the existing
   `requires_kicad` uses across the codebase.

**Which do you prefer?**

---

## Task 4 — `uv run --extra dev --extra web` syntax confirmed

Verified: `uv run --extra dev --extra web python -c "..."` works on this machine.
The preferred commands from the spec are valid. Docs can be updated to use that form.
No blocking issue — just confirming this is safe to proceed.

---

## Task 1.4 — Invalid-route test approach (no redirect, renders canonical step)

Looking at `WizardPage.tsx`: there is no explicit `<Navigate>` redirect component.
When `normalizeWizardStep` returns `undefined` for an invalid route, the controller
derives the canonical step from the session state and the page renders that step's
content directly. The URL does not change.

So the test for task 1.4 cannot assert on a URL change. It should instead verify
that rendering `/wizard/abc123/not-a-step` shows the canonical step content (e.g.
the Describe step heading) and not a blank/empty page. I will mock the session
query to return a known session state and verify the expected content renders.

No question — just confirming the test strategy so there are no surprises.

---

## Task 3 — "Edit project details" link safety

`session.project_name` and `session.symbols_dir` are already on the `session` prop
passed to `WizardGenerateStep`. I will use those with the existing `displaySymbolsDir`
helper and a `"Not set"` fallback.

For the "Edit project details" link: navigating to `/wizard/:sessionId/describe` is
a plain `<Link>` — no mutations, no state wipe. But one concern: if the user has a
pending IR generation in progress (busy state), navigating away mid-flight could be
confusing even if it is technically safe.

**Question:** Should the "Edit project details" link always be shown, or should it be
hidden or disabled when `busyMessage` is non-null (i.e. a generation is in progress)?

---

## Task 6 — Preferred replacement copy for `OpenClaw_Managed.kicad_sch`

The spec suggests `"Generated managed schematic"` as the neutral replacement.
I will search `frontend/src` for all occurrences and classify each as user-facing
UI, artifact filename, or diagnostics.

**Question:** Is `"Generated managed schematic"` the copy you want, or do you have a
different label in mind?
