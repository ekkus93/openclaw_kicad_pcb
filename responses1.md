# KICAD_WEB_REFACTOR_FIX3 — Pre-implementation questions

Questions from Claude Code before starting implementation.

---

## Phase 2 — Frontend API client: 204 No Content branch

No current API endpoint returns `204 No Content`. Every route returns JSON.
The spec says "support 204 only if explicitly supported."

**Question:** Should the 204 branch be omitted entirely (nothing needs it, adding it
would be dead code), or should it be added defensively for future-proofing?

---

## Phase 3 — Path sanitization: fix scope

The sanitization already lives in `errors.py` — `_sanitize_path_text`,
`_sanitize_detail_value`, `_public_error_details`. The current problems are:

1. `_sanitize_path_text` handles only values that *are themselves* a standalone
   absolute path (`Path(value).is_absolute()`). It does not redact embedded paths
   inside longer strings like
   `"Invalid JSON in /tmp/kicad-pcb-web-prepare-abc/circuit_ir.json"`.

2. `message` (= `str(exc)`) is not sanitized at all in `kicad_error_to_payload` —
   only `details` passes through `_public_error_details`.

Two implementation options:

**Option A — Fix `errors.py` everywhere:** Update `_sanitize_path_text` to use a
regex that redacts embedded private paths, and apply the same sanitizer to
`message` inside `kicad_error_to_payload`. This makes *all* public error payloads
safe everywhere — including the HTTP exception handlers (`handle_user_error`,
`handle_kicad_error`) that also call `kicad_error_to_payload`.

**Option B — Fix `netlists.py` only:** Add a separate sanitizer applied only inside
`_validation_issue_from_user_error`. Leaves the HTTP exception handlers and any
other `kicad_error_to_payload` callers unchanged (potentially still leaking
embedded paths in other contexts).

**Preference:** Option A — fixing `errors.py` is safer and avoids the same bug
appearing elsewhere. Is that acceptable, or should the fix be narrowly scoped to
`netlists.py` only?

---

## Phase 5 — Manual smoke-test recording

Claude Code cannot run the web server or interact with the browser, so the only
honest record available is "Not performed in this environment."

**Question 1:** Where should the smoke-test status be recorded?
- (a) Directly in `docs/KICAD_WEB_REFACTOR_FIX3_TODO.md` (Phase 5 section)
- (b) A new short `docs/KICAD_WEB_REFACTOR_FIX3_COMPLETION.md` file
- (c) Somewhere else

**Question 2:** Should the smoke tests be run manually by the developer first so
the record reflects an actual pass/fail result, rather than "not performed"?
If so, Claude Code can create the completion record after the developer reports
the outcome.
