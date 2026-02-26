# EMPTY_SCHEMATIC_FIX_TODO.md

Copilot-ready TODO list to fix the “netlist → empty .kicad_sch” problem in `openclaw_kicad_pcb`.

## Problem statement

When running `new-from-netlist` (or JSON → schematic generation), the command reports success, but the generated `.kicad_sch` on disk contains only the header (no `(symbol ...)`, no sheet items). This can also be confused by the **two-file** model:

- Root schematic: `<project>/<name>.kicad_sch` (often contains only a sheet pointing to managed sheet)
- Managed sheet: `<project>/OpenClaw_Managed.kicad_sch` (should contain the generated symbols/wires/labels)

We need to:
1) eliminate “false success”
2) make failures actionable (error codes + details)
3) harden symbol-library discovery so the writer can actually place valid symbols/pins
4) add regression tests

---

## P0 — Reproduce + confirm which file is empty (root vs managed)

- [ ] Add a short debug helper function used by commands:
  - `resolve_schematic_paths(project) -> (root_sch_path, managed_sch_path)`
- [x] In `new-from-netlist` and `apply-netlist`, log/return both paths.
- [ ] Update docs/help text: root schematic may be “thin” (sheet only); managed sheet contains content.

**Acceptance**
- CLI JSON output includes `root_schematic_path` and `managed_schematic_path`.

---

## P1 — Make “success with empty schematic” impossible (fail-loud invariants)

### P1.1 Add post-mutation invariants in the pipeline
After applying netlist mutations (inside the mutation closure or immediately after), compute **actual** counts from the AST and enforce:

- [x] If `IR.components` is non-empty → schematic must contain at least 1 symbol node
- [x] If `IR.nets` has any pins → schematic must contain at least 1 label node (or whatever your net binding primitive is)

Add stronger checks if feasible:
- [x] expected symbol refs ⊆ found refs
- [x] expected number of labels ≥ number of pin bindings (or at least > 0)
- [x] managed sheet exists and has items

If any invariant fails, raise `UserError(code="EMPTY_GENERATION", ...)` with details.

**Details to include in error.details**
- `root_schematic_path`
- `managed_schematic_path`
- `expected_symbols`
- `found_symbols`
- `expected_pin_bindings`
- `found_labels_or_bindings`
- `dry_run` flag

**Acceptance**
- When the tool would previously write header-only, it now fails with `ok=false` and `error.code=EMPTY_GENERATION`.

---

### P1.2 Ensure dry-run cannot be mistaken for success
- [x] Ensure every command result includes `dry_run: bool`.
- [ ] If `dry_run=True`, do not claim “written”; include warning `DRY_RUN_NO_WRITE`.

**Acceptance**
- In JSON output, dry-run is explicit and includes a warning.

---

## P2 — Fix symbol library discovery / symbol lookup (most common root cause)

### P2.1 Add hard guard when symbols dirs are empty
In `SymbolIndex` (or wherever symbol pins are resolved):
- [x] If no valid symbol directories exist (no `*.kicad_sym` found), raise:
  - `UserError(code="SYMBOL_DIR_MISSING", message="No KiCad symbol libraries found...", details={...})`

**details should include**
- searched paths
- how to fix (provide `--symbols-dir`, set config env var, install KiCad libs)

**Acceptance**
- Running without symbols dirs fails early, before writing any schematic.

---

### P2.2 Improve symbol resolution error message
When `symbol_id` not found:
- [x] Raise `UserError(code="SYMBOL_NOT_FOUND", ...)` with:
  - `symbol_id`
  - directories searched
  - suggestion: run `kicad-cli sym list` (if applicable) or check library install

---

### P2.3 Add deterministic precedence and make it visible
- [x] Implement precedence:
  1) `--symbols-dir` (explicit)
  2) repo-local `kicad_pcb/resources/symbols` (if exists)
  3) system/discovered KiCad symbol dirs
- [x] In `--json` output and logs, include `symbols_dirs_used: [...]`.

**Acceptance**
- JSON output shows exactly where symbols were loaded from.

---

## P3 — Ensure writer is actually modifying the intended schematic (wrong path bugs)

### P3.1 Make “project open” explicit
If you have “current project” state:
- [x] Add/require `open <path>` or ensure commands accept `--project <path>`.
- [x] If no project is active, fail with `PROJECT_NOT_OPEN`.

**Acceptance**
- It’s impossible to accidentally write to a temp test project silently.

---

### P3.2 Always write into the managed sheet file (and reference it)
- [x] Ensure root schematic creation includes a `(sheet ...)` referencing `OpenClaw_Managed.kicad_sch`.
- [x] Ensure netlist application always writes the generated items into `OpenClaw_Managed.kicad_sch`.
- [x] If managed file is missing, create it (with marker).

**Acceptance**
- Root schematic references managed sheet.
- Managed sheet contains generated content.

---

## P4 — Validation policy: don’t let “valid empty” pass for non-empty IR

### P4.1 Keep kicad-cli validate, but add semantic checks first
- [x] Run post-mutation invariants (P1) BEFORE returning success.
- [x] Run `kicad-cli sch validate` only after invariants pass (strict mode).
- [x] If `kicad-cli` is missing in strict mode:
  - fail with `KICAD_CLI_MISSING`

**Acceptance**
- “Empty but syntactically valid” can’t be considered success.

---

## P5 — Improve introspection: `info-sch --json` should reveal emptiness

- [x] Ensure `info-sch --json` includes:
  - `owned_by_openclaw`
  - root and managed schematic paths
  - `symbol_count`, `label_count`
  - list of symbol refs (maybe truncated)
- [x] If managed sheet exists but has 0 symbols, return warning `MANAGED_SHEET_EMPTY`.

---

## P6 — Tests (regression-proof the fix)

### P6.1 Fixture symbol library for tests (no mocks)
- [x] Add `tests/fixtures/symbols/TestLib.kicad_sym` with at least:
  - one resistor symbol with pins 1/2
  - one IC symbol with pins 1..N
- [x] Force tests to use `--symbols-dir tests/fixtures/symbols`.

### P6.2 Unit tests: symbol discovery + error codes
- [x] When symbols dirs empty → `SYMBOL_DIR_MISSING`
- [x] When symbol id missing → `SYMBOL_NOT_FOUND`
- [x] When pin missing → `PIN_INVALID`

### P6.3 Integration test: generate non-empty managed schematic
- [x] Create a minimal IR with 2 components + 1 net.
- [x] Run `new-from-netlist --mode internal --symbols-dir tests/fixtures/symbols`
- [x] Assert:
  - managed schematic exists
  - contains `(symbol` at least once
  - contains net label/binding representation
  - post-mutation invariants pass

### P6.4 Regression test: empty generation triggers fail-loud
Simulate the bug path by forcing the writer to skip adding symbols (e.g., by providing IR with symbol not found and ensuring it errors), or by calling the invariant checker with a synthetic empty doc:
- [x] Assert `error.code == "EMPTY_GENERATION"` when IR non-empty but AST has zero symbols.

---

## P7 — CLI output contract improvements (debuggable in OpenClaw)

- [x] Ensure every relevant command supports `--json` and returns:
  - `root_schematic_path`
  - `managed_schematic_path`
  - `symbols_dirs_used`
  - counts found from AST (`symbol_count`, `label_count`)
  - `kicad_cli_used`
  - `dry_run`
- [x] Ensure error codes are stable:
  - `EMPTY_GENERATION`
  - `SYMBOL_DIR_MISSING`
  - `SYMBOL_NOT_FOUND`
  - `PIN_INVALID`
  - `PROJECT_NOT_OPEN`
  - `KICAD_CLI_MISSING`
  - `VALIDATION_FAILED`

---

## Definition of Done

- [x] If IR is non-empty, the tool can **never** "succeed" while producing a header-only managed schematic.
- [x] If the root schematic is thin (sheet only), the tool explains that and reports the managed sheet path.
- [x] If symbol libs are missing/misconfigured, the tool fails early with `SYMBOL_DIR_MISSING` and tells how to fix it.
- [x] `new-from-netlist` in internal mode produces a managed schematic containing symbols and net bindings using fixture libs in tests.
