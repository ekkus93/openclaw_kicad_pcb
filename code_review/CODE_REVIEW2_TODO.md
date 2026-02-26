# CODE_REVIEW2_TODO.md

This TODO list is based on the latest code review feedback for the OpenClaw KiCad PCB repo updates (Copilot changes). It is written as an implementation checklist for GitHub Copilot.

---

## P0 — Data integrity / file corruption risks (must fix first)

### P0.1 Fix partial-write risk in all low-level writers
**Problem:** `os.write(fd, bytes)` may write only part of the buffer; ignoring the return value can silently truncate files (corrupting KiCad `.kicad_pcb` / `.kicad_sch`).  
**Goal:** Ensure all writes are “write all bytes” and are flushed correctly.

**Tasks**
- [x] Search repo for any use of:
  - `os.write(`
  - `mkstemp(` with manual `os.write` and `os.close`
- [x] Replace with one of:
  - `with open(tmp_path, "wb") as f: f.write(data)` (binary)
  - or `Path(tmp_path).write_text(text, encoding="utf-8")` (text)
  - or a safe loop that keeps writing until all bytes are written
- [x] Ensure the atomic writer:
  - writes to temp file
  - `fsync()` temp file
  - `os.replace()` into final path
  - `fsync()` parent directory (best practice on Linux) for strong durability
- [x] Add unit tests:
  - [x] “Large content” write test (e.g., multi-MB string) to ensure no truncation
  - [x] Verify output length equals input length
  - [x] Verify content hash matches

**Acceptance criteria**
- No occurrences of single-call `os.write()` used for file content remain.
- Added tests pass.

---

## P0.2 Harden temp file handling (no FD leaks, consistent cleanup)
**Problem:** manual `mkstemp()` + `os.close()` flows can leak file descriptors if exceptions occur between operations.

**Tasks**
- [x] Wrap temp creation/writing in a helper:
  - `write_temp_text(dir, prefix, suffix, content, encoding="utf-8") -> Path`
  - uses `NamedTemporaryFile(delete=False)` or `mkstemp()` but always closes in `finally`
- [x] Ensure cleanup:
  - [x] on validation failure / exceptions, delete temp file if it won’t be reused
  - [x] on success, allow `os.replace()` to move into place
- [x] Add tests:
  - [x] simulate exceptions during write/validate and ensure temp files cleaned (or tracked)
  - [x] verify no stray temp files in configured temp dir after failure (as feasible)

**Acceptance criteria**
- All temp writers go through a single safe helper.
- Temp file descriptors always closed.

---

## P1 — Correctness of syntax checking / validation

### P1.1 Replace bespoke `_check_sexp()` character scanner with tokenizer-based validation
**Problem:** A quote/escape heuristic that only checks the previous character can mis-handle sequences like `\\\"` and miscount parentheses; can incorrectly reject valid files or accept invalid ones.

**Tasks**
- [x] Identify current “cheap” S-expression checker function(s) (e.g., `_check_sexp()`).
- [x] Replace with one of the following (choose 1 and implement consistently):
  1. **Tokenize + paren-balance using tokens** (recommended):
     - run tokenizer over the entire input
     - count `LPAREN`/`RPAREN` tokens only (strings/comments already tokenized)
     - ensure balance never goes negative and ends at zero
     - optionally also require first token is `LPAREN`
  2. **Full parse** for syntax check:
     - call `parse()` and catch errors
- [x] Ensure comments and string escapes are handled correctly by relying on tokenizer rules, not raw char scanning.
- [x] Add tests:
  - [x] tricky escapes with backslashes and quotes
  - [x] nested parentheses inside strings (should not count)
  - [x] optional: comment-like constructs if supported by tokenizer

**Acceptance criteria**
- No char-level quote toggling remains in validation.
- New tests demonstrate correct behavior.

---

### P1.2 Make root-node validation explicit and extensible
**Problem:** Current checks may hard-fail if KiCad introduces new root keys or if you later support other KiCad S-expression formats.

**Tasks**
- [x] Centralize supported root keys in a constant, e.g.:
  - `SUPPORTED_ROOTS = {"kicad_sch", "kicad_pcb"}`
- [x] In document loaders, validate root key using the constant.
- [x] Provide a clear error message:
  - include expected roots
  - include actual root key
  - include file path
- [x] Optional: allow “strict” vs “lenient” mode via configuration/flag:
  - strict (default): requires supported roots
  - lenient: parse but mark doc “unknown” / disable certain mutators

**Acceptance criteria**
- Root validation is consistent and easy to extend.

---

## P2 — Round-trip fidelity and diff minimization (design decision + incremental work)

### P2.1 Decide and document: “basic AST” vs “round-trip CST”
**Problem:** Current parser/serializer likely drops comments and rewrites formatting (line breaks/indentation/float formatting), creating noisy diffs.

**Tasks**
- [x] Add a short decision doc section in README or `docs/formatting.md`:
  - clarify expected behavior on write:
    - “format diffs expected” (basic AST), **or**
    - “minimal diffs / preserve formatting” (round-trip)
- [x] If choosing basic AST:
  - [x] ensure serializer is deterministic
  - [x] add a “format stable” test: parse->serialize->parse is idempotent structurally
- [x] If choosing round-trip (longer-term):
  - [x] extend tokenizer to retain “trivia” (whitespace/comments) as tokens
  - [x] build CST nodes that keep leading/trailing trivia
  - [x] serializer writes original trivia where possible
  - [x] add “minimal diff” regression tests using real KiCad fixtures

**Acceptance criteria**
- Repository states the expected output formatting behavior.
- Tests align with that expectation.

---

### P2.2 Preserve numeric lexemes when possible (optional, but helpful)
**Problem:** Normalizing floats (e.g., `10.000` -> `10.0`) increases diffs.

**Tasks**
- [ ] If round-trip is desired, store numeric tokens as:
  - parsed numeric value + original lexeme string
- [ ] Serializer uses original lexeme unless value changed.

**Acceptance criteria**
- Unchanged numbers remain text-identical after round-trip.

---

## P3 — Reliability improvements and tooling ergonomics

### P3.1 Make UTF-8 encoding explicit everywhere
**Tasks**
- [ ] Replace implicit `.encode()` calls with `.encode("utf-8")`
- [ ] Replace `open(path, "w")` with `open(path, "w", encoding="utf-8", newline="\n")` where appropriate
- [ ] Ensure consistent newline policy (prefer `\n`) for deterministic diffs.

**Acceptance criteria**
- Grep for `.encode()` without args returns none (or justified exceptions).
- All text writes specify encoding.

---

### P3.2 Improve error messages and exception types for parse/validate failures
**Tasks**
- [ ] Define a small exception hierarchy:
  - `SExprTokenizeError`, `SExprParseError`
  - `DocSyntaxError`, `DocLintError`, `KicadCliValidationError`
- [ ] Ensure exceptions include:
  - file path
  - line/column where available
  - a short “what to do next” hint (e.g., run validation command)
- [ ] Add tests verifying error message contains line/col for malformed input.

**Acceptance criteria**
- Failures are actionable and pinpoint locations.

---

### P3.3 Align local validation script with CI checks
**Tasks**
- [x] Add `scripts/validate.sh` (or `make validate`) that runs:
  - ruff check
  - ruff format (check)
  - mypy
  - pytest
- [x] Ensure CI uses the same script (single source of truth).
- [x] Document contributor setup: `pip install -e ".[dev]"`.

**Acceptance criteria**
- “Run one command locally” matches CI behavior.

---

## P4 — Regression fixtures & tests (capture known bad KiCad cases)

### P4.1 Add regression fixtures for real broken files encountered
**Tasks**
- [ ] Create `tests/fixtures/broken/` and `tests/fixtures/valid/`.
- [ ] Add fixtures for the known issues mentioned:
  - [ ] `(id N)` formatting issue
  - [ ] sub-symbol naming issue
  - [ ] indentation/formatting issue (if relevant)
- [ ] For each fixture, add a test:
  - [ ] parsing result (pass/fail expectation)
  - [ ] validator expectation (syntax/lint/kicad-cli)
  - [ ] error message contains helpful context

**Acceptance criteria**
- Fixtures are committed and tests cover them.

---

## P5 — Nice-to-have hardening

### P5.1 Add structured logging hooks for pipeline operations
**Tasks**
- [ ] Emit structured logs at:
  - read
  - parse
  - mutate
  - serialize
  - validate
  - write/replace
- [ ] Include task context: file path, validation mode, elapsed time.

### P5.2 Add “dry-run diff” output option
**Tasks**
- [ ] Add option to print unified diff (before/after) in dry-run mode.
- [ ] Useful for review and debugging formatting churn.

---

## Definition of Done (global)
- [ ] No file corruption possible from partial writes.
- [ ] Validation uses tokenizer/parser, not brittle char scanning.
- [ ] Formatting behavior is explicitly documented and tested.
- [ ] CI and local validation are aligned.
- [ ] Fixtures exist for known real-world failure modes.
