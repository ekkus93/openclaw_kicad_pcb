# Unit Tests — Batch 1 TODO

Coverage gaps identified on 2026-06-03 across recently changed and historically
under-tested modules.

---

## Summary of gaps

| Area | Gap | Risk if untested |
|---|---|---|
| `validate_circuit_ir` | New unqualified-symbol check has no tests | Regression silently removes the guard |
| `find_pin_membership_collisions` | Only called indirectly; no direct tests | Edge-case collision logic regresses unseen |
| `_format_ir_repair_error` | Not tested at all; each branch is its own code path | Wrong repair prompt sent to LLM; user never escapes repair loop |
| `clear_wizard_ir` | Zero tests; mutates session state | Wrong fields cleared; session left in inconsistent state |
| `_validate_with_optional_autofix` | Unit-level branches untested; only integration coverage | Silent re-raise of wrong error; "fixed but still failing" message corrupted |
| `_parse_pin_membership_token` | Not directly tested | Edge-case input silently drops pins from nets |
| `_embed_symbol_if_found` (placeholder path) | New `placeholder` parameter has no test | Symbols missing from generated schematics without error |

---

## 1. Extend `tests/unit/test_circuit_ir.py`

Add to the existing file.  No new file needed.

### 1.1 `validate_circuit_ir` — unqualified symbol ID check (new guard)
- [x] Single component with unqualified symbol `"CD4017"` (no colon) raises
      `IR_SEMANTIC_INVALID`
- [x] Error details contain `"unqualified_symbols"` key
- [x] `unqualified_symbols` list includes `{"ref": "U1", "symbol": "CD4017"}`
- [x] Multiple components with unqualified symbols are all reported in one error,
      not just the first
- [x] A mix of qualified and unqualified symbols correctly reports only the
      unqualified ones
- [x] A fully-qualified symbol `"4xxx:CD4017BE"` does NOT trigger the check

### 1.2 `find_pin_membership_collisions` — direct tests
- [x] No collisions when every `(ref, pin)` pair appears in exactly one net →
      returns empty list
- [x] Single collision: one `(ref, pin)` pair in two nets → returns one entry with
      correct `ref`, `pin`, `nets` (sorted), and both `assignments`
- [x] Multiple independent collisions in one IR → all reported, sorted by ref then pin
- [x] Collision with three nets (not just two) → `nets` list has all three, sorted
- [x] Assignment metadata is correct: `net_index`, `pin_index`, `unit` all populated

### 1.3 `build_pin_membership_index` — edge cases
- [x] Pin that appears in exactly one net → single-item tuple in the index
- [x] Pin with `unit` value populated → `unit` is preserved in the assignment
- [x] Two different pins of the same component each in their own net → two keys,
      no collision
- [x] Empty IR (no nets) → empty index

---

## 2. New file: `tests/unit/test_ir_repair_error.py`

Tests for `_format_ir_repair_error` in
`src/kicad_pcb_web/services/wizard.py`.

Each test constructs a `UserError` with specific `details` and asserts the
formatted string contains the expected content.

### 2.1 Baseline — message and code always appear
- [x] A bare `UserError` with no detail keys → output contains the exception
      message string and the error code
- [x] `exc.details` is `None` → does not raise; returns message + code only
- [x] `exc.details` is a non-dict type → does not raise; returns message + code only

### 2.2 `pin_collisions` branch
- [x] Single collision entry → output names the ref, pin, and both net names
- [x] Output instructs the user to keep the pin in exactly ONE net
- [x] Multiple collisions → all listed, each on its own line
- [x] More than 12 collisions → output is truncated to 12 entries (no crash)
- [x] Collision entry with non-dict value in list → skipped gracefully

### 2.3 `unqualified_symbols` branch
- [x] Single unqualified symbol → output names the ref and the bare symbol string
- [x] Output suggests adding a library prefix
- [x] Multiple unqualified symbols → all listed
- [x] More than 12 entries → truncated without crash

### 2.4 `duplicate_refs` and `duplicate_nets` branches
- [x] `duplicate_refs` present → output names the duplicate refs
- [x] `duplicate_nets` present → output names the duplicate net names
- [x] Both present → both appear in output

### 2.5 `missing_component_refs` branch
- [x] Single missing ref → output names the net, ref, and pin
- [x] Multiple entries → all listed up to 8
- [x] More than 8 → truncated without crash

### 2.6 `errors` (schema-level) branch
- [x] Entry with `loc` and `msg` → output shows formatted location and message
- [x] Entry without `loc` → message shown without location prefix
- [x] More than 12 entries → truncated without crash
- [x] Entry that is not a dict → skipped gracefully

### 2.7 Combined details
- [x] A `UserError` with both `pin_collisions` and `unqualified_symbols` → both
      sections appear in the output

---

## 3. New file: `tests/unit/test_wizard_clear_ir.py`

Tests for `clear_wizard_ir` in `src/kicad_pcb_web/services/wizard.py`.
Uses `tmp_path` to create a real file-backed session; no mocks.

### 3.1 Happy path
- [x] After `clear_wizard_ir`, returned session has `status == "spec_approved"`
- [x] `ir_json` is `None`
- [x] `ir_validation` is `None`
- [x] `latest_job_id` is `None`
- [x] `error` is `None`
- [x] `updated_at` is newer than before the call (timestamp advances)

### 3.2 Preserved fields
- [x] `spec` is unchanged after clearing IR
- [x] `messages` conversation list is unchanged
- [x] `project_name` is unchanged
- [x] `spec_approved` remains `True`
- [x] `spec_approved_at` is unchanged

### 3.3 Persistence
- [x] Session is written to disk; re-reading with `read_wizard_session` reflects
      the cleared state
- [x] `circuit_ir.json` artifact file is NOT deleted (the service only updates
      `wizard.json`; stale artifact is acceptable)

### 3.4 Precondition guard
- [x] Calling `clear_wizard_ir` on a session whose `spec_approved` is `False`
      raises `UserError`
- [x] Calling on a non-existent session ID raises `FileNotFoundError`

### 3.5 Idempotency
- [x] Clearing IR on a session that already has no IR does not raise; returns
      session in `spec_approved` state unchanged

---

## 4. New file: `tests/unit/test_validate_with_autofix.py`

Tests for `_validate_with_optional_autofix` in
`src/kicad_pcb_web/services/netlists.py`.
Uses `tmp_path` and real IR JSON; no mocks needed for most cases.

### 4.1 Valid IR — passes through untouched
- [x] Valid IR with `auto_fix=False` → returns same path, empty fixes list
- [x] Valid IR with `auto_fix=True` → returns same path, empty fixes list
      (autofix is never called when first validation succeeds)

### 4.2 `auto_fix=False` — raises original error immediately
- [x] IR with pin-collision error, `auto_fix=False` → raises the original
      `UserError` with `IR_SEMANTIC_INVALID` code
- [x] Does not call `autofix_circuit_ir`

### 4.3 `auto_fix=True` — autofix fixes the error
- [x] IR with a fixable schema error (e.g. integer pin value) →
      `fixes_applied` is non-empty, returned path is the `.autofix.json` file,
      returned `ir` is valid

### 4.4 `auto_fix=True` — autofix applies no fixes → re-raises original error
- [x] IR with an error autofix cannot address (e.g. duplicate refs) →
      raises the **original** error, not a wrapped "auto-fix applied" error

### 4.5 `auto_fix=True` — autofix changed something but result still invalid
- [x] Construct an IR where autofix changes the dict but the result still fails
      validation → raises a `UserError` that contains `"Auto-fix applied"` in
      the message and includes the fixes summary and remaining error

---

## 5. Extend `tests/unit/test_ir_autofix.py`

Add `_parse_pin_membership_token` tests to the existing file.

### 5.1 Valid token formats
- [x] `"U1.8"` → `{"ref": "U1", "pin": "8"}`
- [x] `"R1-2"` → `{"ref": "R1", "pin": "2"}`
- [x] `" U1.8 "` (leading/trailing whitespace) → `{"ref": "U1", "pin": "8"}`
- [x] `"J1.A"` (non-numeric pin) → `{"ref": "J1", "pin": "A"}`

### 5.2 `:` separator — note: conflicts with `lib:name` format
- [x] `"U1:8"` → `{"ref": "U1", "pin": "8"}` (colon is a valid separator for
      compact tokens when both sides are non-empty)
- [x] `"Device:R"` (library-qualified symbol id, NOT a compact token) → returns a
      result — document whether this is treated as `{"ref": "Device", "pin": "R"}`
      or something else; the test pins down the actual behaviour so regressions are
      caught

### 5.3 Invalid / empty inputs
- [x] Empty string `""` → `None`
- [x] Whitespace-only `"   "` → `None`
- [x] No separator present `"U1"` → `None`
- [x] Token where one side is empty after split `".8"` → `None`
- [x] Token where the other side is empty `"U1."` → `None`

### 5.4 Integration — compact tokens survive `autofix_circuit_ir`
- [x] IR with `"nodes"` key containing compact tokens like `"U1.8"` →
      autofix converts them to proper `{"ref": "U1", "pin": "8"}` entries and
      reports a fix string

---

## 6. New file: `tests/unit/test_embed_symbol.py`

Tests for `_embed_symbol_if_found` in
`src/kicad_pcb/commands/_sch_apply.py`.
Build a minimal `SchematicDoc` in memory rather than reading from disk.

### 6.1 Real symbol found in library
- [x] Qualified symbol present in the test fixtures symbol directory →
      `_embed_symbol_if_found` returns `True` and `doc.root` contains the
      symbol definition in `lib_symbols`
- [x] A second call with the same symbol → returns `True` without duplicating
      the definition (idempotent embed)

### 6.2 Symbol not found, no placeholder
- [x] Qualified symbol that doesn't exist in any library, `placeholder=None` →
      returns `False`, `lib_symbols` section is unchanged

### 6.3 Symbol not found, placeholder provided
- [x] Qualified symbol not in library, `placeholder` is a real `PlaceholderSymbol`
      → returns `True`, placeholder definition is embedded in `lib_symbols`
- [x] Subsequent call with same symbol and placeholder → idempotent, still returns
      `True` without duplicating

### 6.4 Unqualified symbol ID (no `:` in symbol string)
- [x] Unqualified symbol `"CD4017"`, `placeholder=None` → returns `False` without
      raising (guard path)
- [x] Unqualified symbol `"CD4017"`, placeholder provided → returns `True` and
      embeds the placeholder definition

---

## 7. Extend `tests/unit/test_symbol_index.py`

### 7.1 `register_placeholder` — cache population
- [x] After `register_placeholder`, `get_pins(sym_id)` returns the registered
      pin set without raising
- [x] After `register_placeholder`, `get_unit_pins(sym_id)` returns a single-unit
      map containing all registered pins
- [x] After `register_placeholder`, `get_unit_pin_at(sym_id)` returns the
      registered `pin_at` dict
- [x] `register_placeholder` on a symbol that already exists in the library
      overwrites the cache (placeholder takes precedence when explicitly set)

### 7.2 `register_placeholder` — downstream behaviour
- [x] `validate_ir_symbols` called after `register_placeholder` no longer reports
      the symbol as unknown (already tested in `test_ir_validate_placeholder.py`,
      but confirm the `get_pins` path specifically)

---

## Priority order

| Priority | Items |
|---|---|
| P0 — Catches recent regressions | 1.1, 2.1–2.7, 3.1–3.5 |
| P1 — Guards complex logic | 4.1–4.5, 6.1–6.4 |
| P2 — Fills direct-call gaps | 1.2, 1.3, 5.1–5.4 |
| P3 — Completeness | 7.1–7.2 |
