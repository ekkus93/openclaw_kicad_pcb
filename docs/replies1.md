# Replies to KICAD_WEB_REFACTOR_FIX3 Pre-Implementation Questions

Source questions file: `responses1(25).md`

## Phase 2 — Frontend API client: 204 No Content branch

Omit the `204 No Content` branch for now.

No current API endpoint returns `204`, and every existing route is expected to return JSON. Adding a defensive 204 branch would be dead code and could accidentally normalize an API behavior the app does not actually support yet.

### Required behavior for this patch

For successful responses:

- `response.ok` + valid JSON: return the parsed payload.
- `response.ok` + invalid/non-JSON body: throw `ApiError`.
- Do not silently return `null as T`.
- Do not add 204/no-content support unless a real endpoint needs it.

If a future endpoint intentionally returns `204`, add explicit support then, preferably with either:

- a separate `requestVoid()` helper, or
- an explicit `expectNoContent` option.

Do not make `requestJson<T>()` silently accept empty success responses by default.

---

## Phase 3 — Path sanitization: fix scope

Use **Option A — fix `errors.py` everywhere**.

That is the safer and more correct fix. The problem is not specific to netlist validation; the public error payload conversion path itself is insufficiently sanitized. If only `netlists.py` is patched, the same embedded-path leak can still happen through `handle_user_error`, `handle_kicad_error`, or any other caller of `kicad_error_to_payload`.

### Required implementation

Update `src/kicad_pcb_web/errors.py` so that:

1. `_sanitize_path_text` or its replacement redacts embedded private paths inside longer strings.
2. `kicad_error_to_payload()` sanitizes the top-level `message` derived from `str(exc)`.
3. Existing detail sanitization continues to work.
4. Sanitization is recursive for details:
   - strings
   - lists
   - dicts
   - nested values if applicable
5. Public error payloads do not leak paths like:
   - `/tmp/...`
   - `/var/folders/...`
   - `/private/var/...`
   - `/home/<user>/...`
   - `/Users/<user>/...`

Use a stable placeholder such as:

```text
<redacted-path>
```

### Tests to add

Add tests at the `errors.py` level, not only the netlist route level.

At minimum, test that `kicad_error_to_payload()` redacts embedded paths in:

- top-level `message`
- string details
- list details
- dict details

Also keep or add one validation-route test proving that invalid netlist validation responses inherit the sanitization.

This is a public error-contract fix, not just a direct JSON validation fix.

---

## Phase 5 — Manual smoke-test recording

Record smoke-test status in a new file:

```text
docs/KICAD_WEB_REFACTOR_FIX3_COMPLETION.md
```

Do not wait for the developer before implementing the automated fixes.

Claude Code should proceed with implementation and create the completion file with the honest status:

```text
Manual smoke tests not performed in this environment.
```

### Why use a completion file?

A separate completion file is cleaner than editing the TODO into a mixture of implementation instructions and local run results. It also gives future reviewers a single place to look for:

- exact validation commands,
- local pass/fail/skip counts,
- tool availability,
- manual smoke-test status,
- remaining manual steps.

### Required contents

The completion file should include:

- files changed,
- automated validation commands and results,
- whether `kicad-cli` was available,
- whether `rsvg-convert` was available,
- skipped tests and skip reasons,
- manual direct JSON smoke-test status,
- manual wizard-with-LLM smoke-test status.

For the two manual sections, use this if Claude Code did not run the browser/server flow:

```text
Manual smoke tests not performed in this environment.
```

Do not mark manual smoke tests as passed unless the developer actually performs them and reports the outcome.

### Should the developer run smoke tests first?

No need to block this patch on that. Claude Code should implement the automated hardening work now and record manual smoke tests as not performed. The developer can later run the manual smoke flows and update `docs/KICAD_WEB_REFACTOR_FIX3_COMPLETION.md` with PASS/FAIL details.

---

## Final instruction summary for Claude Code

Proceed with these decisions:

1. Do **not** add a `204 No Content` branch to `requestJson<T>()` in this patch.
2. Make successful invalid/non-JSON responses throw `ApiError`.
3. Fix embedded-path sanitization globally in `errors.py`, not narrowly in `netlists.py`.
4. Sanitize both public error `message` and `details`.
5. Add tests for `kicad_error_to_payload()` sanitization and at least one validation-route integration point.
6. Create `docs/KICAD_WEB_REFACTOR_FIX3_COMPLETION.md`.
7. Record manual smoke tests as `not performed in this environment` unless the developer has actually run them and provided results.
