# Replies to Batch 4 Questions and Issues

Source questions file: `repsonses6.md`

## Issue 1 — Task 1: `@requires_kicad()` syntax does not match the existing API

Use **Option A**: use the existing decorator exactly as the project already uses it.

Do **not** change the public decorator API just to support parentheses. Do **not** add a new decorator form unless a broader cleanup later requires it.

Use:

```python
@requires_kicad
def test_read_lib_symbol_def_flat_falls_through_to_system_library() -> None:
    ...
```

Do **not** use:

```python
@requires_kicad()
def test_read_lib_symbol_def_flat_falls_through_to_system_library() -> None:
    ...
```

### Test intent

I agree with your read: this particular test is explicitly named and written to verify real system-library fallback:

```python
read_lib_symbol_def_flat("power", "+5V", symbols_dir=None)
```

A hermetic temp fixture would not exercise that `symbols_dir=None` system-library fallthrough path. So for this test, marking it as KiCad/system-installation dependent is the right approach.

### Important caveat

Make sure the skip condition actually covers the thing this test depends on.

If the existing `requires_kicad` decorator only checks `kicad-cli >= 9.0.0`, that may not be enough. A machine can have `kicad-cli` installed but still lack the expected system symbol libraries, which is exactly the kind of failure this patch is trying to avoid.

So the implementation guidance is:

1. Use `@requires_kicad` with no parentheses.
2. Do not create a new `kicad` marker.
3. Do not use a hermetic fixture for this specific test.
4. If needed, minimally improve the existing availability check behind `requires_kicad` so tests that require system KiCad assets skip cleanly when those assets are missing.

Do this without changing the decorator call style. The public usage should remain:

```python
@requires_kicad
```

A focused helper such as `kicad_system_symbols_available()` is acceptable if the current skip logic needs to distinguish “kicad-cli exists” from “KiCad system libraries are usable.” Keep it internal to the test infrastructure.

---

## Issue 2 — Task 3: Duplicate `handleClearIr` may already be gone

Do a quick full-file search/confirmation, then mark Task 3 as already complete if there is only one return entry.

Recommended check:

```bash
grep -n "handleClearIr" frontend/src/routes/wizard/useWizardController.ts
```

Expected acceptable result:

- one function definition, and
- one entry in the returned controller object.

If that is what you see, do **not** make a code change just to touch the file. In the completion notes, say:

```text
Task 3 was already resolved before this patch. Verified by full-file search: one handleClearIr definition and one return entry remain.
```

If you find a second return entry, remove only the duplicate and leave behavior unchanged.

---

## Issue 3 — Task 4: Controller spy assertion uses positional args

Yes. Assert on the real positional signature.

`useWizardController` takes:

```ts
export function useWizardController(
  sessionId: string | undefined,
  routeStep: WizardStep | undefined,
)
```

So the invalid-route assertion should be:

```ts
expect(useWizardControllerSpy).toHaveBeenCalledWith('abc123', undefined)
```

or, if there are multiple calls and you need to avoid brittle ordering:

```ts
expect(useWizardControllerSpy).toHaveBeenLastCalledWith('abc123', undefined)
```

Use the exact Vitest matcher that fits the test setup.

For a valid control route, assert something like:

```ts
expect(useWizardControllerSpy).toHaveBeenCalledWith('abc123', 'spec')
```

Do **not** use `expect.objectContaining({ routeStep: undefined })` unless the hook is refactored to accept an options object, which it currently does not.

---

## Final instruction summary for Claude Code

Proceed with Batch 4 using these decisions:

1. Use `@requires_kicad` with no parentheses.
2. Treat `test_read_lib_symbol_def_flat_falls_through_to_system_library` as a real system-library fallback test, not a hermetic fixture test.
3. Ensure the `requires_kicad` skip logic covers missing system KiCad symbol libraries if the current check only covers `kicad-cli`.
4. For Task 3, search/confirm whether the duplicate `handleClearIr` return entry is already gone. If it is already gone, mark the task complete with no code change.
5. For Task 4, assert the controller spy using positional arguments: `('abc123', undefined)` for invalid steps and `('abc123', 'spec')` for valid steps.
