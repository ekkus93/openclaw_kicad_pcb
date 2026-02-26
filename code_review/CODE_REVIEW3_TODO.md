# CODE_REVIEW3_TODO.md

Copilot-ready implementation plan for the **compiler-style pipeline**:

> **(A) LLM understanding the spec → (B) deterministic KiCad writer/validator**

This TODO is updated to incorporate decisions raised by Copilot review (command naming, ownership, update semantics, validation policy, idempotency, multi-unit handling, symbol resolution precedence, JSON error contract).

---

## Decisions (treat as requirements)

### D1) Command naming
- **Canonical:** `new-from-netlist`
- **Secondary:** `apply-netlist` (operates on current/open project)
- **Alias (optional):** `compile-netlist` as an alias of `new-from-netlist` (same args)

### D2) Update semantics
- `apply-netlist` is **authoritative sync** for **OpenClaw-managed content only**:
  - It may delete/replace stale generated symbols/wires/labels in the managed region.
  - It must not delete/modify user-authored items outside the managed region.
- IR removals (component/net removed from IR) should remove corresponding managed items.

### D3) Ownership marker
- Schematic must contain a durable marker indicating it is OpenClaw-managed.
- **Marker string (exact):** `OpenClaw:generated=v1`
- **Managed region mechanism (exact):** dedicated top-level sheet named `OpenClaw_Managed`.
- **Location for marker text:** off-canvas `text` item (e.g. x=-1000, y=-1000) so it doesn’t clutter.
- Default behavior:
  - If marker absent: **refuse** to modify schematic unless `--force`.
  - With `--force`: “adopt” by inserting marker(s) and creating/managing `OpenClaw_Managed`.

`apply-netlist` authoritative sync behavior:
- find/create sheet `OpenClaw_Managed`
- delete sheet contents
- rebuild sheet contents deterministically from IR
- never modify content outside this sheet

### D4) Validation policy (by mode)
- Default mode for `new-from-netlist`: **KICAD (strict)**.
  - If `kicad-cli` missing: **hard fail** with actionable message.
- Default mode for `apply-netlist`: **internal**.
- Provide `--mode internal` (or `SYNTAX_LINT`) that:
  - runs internal syntax + lint validation
  - does **not** require `kicad-cli`
  - emits warning (in JSON: `warnings[]`, and `kicad_cli_used=false`)

### D5) Idempotency criterion
- Require **structural/semantic idempotency**, not byte-identical output (MVP).
- Re-applying same IR must not create duplicates and must yield same normalized schematic model for managed region.

### D6) Multi-unit symbols (MVP)
- `PinRefIR.unit` must be **null/None** in v1.
- If a non-null unit is provided: fail with `UserError` explaining multi-unit not supported yet.
- Keep `unit` field in schema for future extension.

### D7) Symbol resolution precedence
- `--symbols-dir` (explicit CLI arg) wins.
- Then repo-local symbols dir `kicad_pcb/resources/symbols` (if present and has `.kicad_sym`).
- Then system KiCad symbols (if discoverable).
- Tests should pass an explicit `--symbols-dir` pointing at fixture symbols and not fall through.

### D8) JSON error contract
All commands that support `--json` must emit stable shapes:

On success:
```json
{ "ok": true, "result": {...}, "warnings": [ {"code":"...","message":"...","details":{...}} ] }
```

On failure:
```json
{ "ok": false, "error": {"code":"...","message":"...","details":{...}} }
```

`error.code` must be a stable enum (see P6.1).

---

## Goals

1. Introduce a **generic Circuit IR** (JSON) that represents:
   - components (refs, symbols, values; footprints optional)
   - nets (net name → pin-membership)
2. Add deterministic commands:
   - `info-sch --json` (introspection of current schematic; for generated schematics, includes reliable pin→net mapping)
   - `apply-netlist` (apply Circuit IR to `.kicad_sch` deterministically with authoritative sync in managed region)
   - `new-from-netlist` (create/open project, apply IR, validate, and fail hard on errors)
3. Ensure deterministic correctness:
   - schema validation + semantic validation
   - symbol/pin validation using library metadata
   - `kicad-cli sch validate` is required in strict mode before success
4. Add tests + fixtures so the pipeline is regression-proof.

---

## P0 — Prep: confirm safe write primitives are correct (blocker)

> If your branch still contains single-call `os.write()` patterns, fix them first.

- [x] Ensure all internal writers are safe (no single-call `os.write()` for content).
  - `kicad_pcb/fs.py` `_atomic_write`
  - any temp writers in `pipeline.py` or adapters
- [x] Ensure atomic write path is:
  - write temp (`mkstemp` + `os.fdopen`)
  - `fsync(temp)` (`f.flush()` + `os.fsync(f.fileno())`)
  - `os.replace` (`tmp.replace(path)`)
  - optional directory `fsync` best-effort (POSIX only) — added `contextlib.suppress(OSError)` guard

**Acceptance**
- Large write tests pass; no truncation possible.

---

## P1 — Circuit IR (generic netlist format)

### P1.1 Add Circuit IR models (Pydantic v2) ✅
Created `kicad_pcb/circuit_ir.py`, using **Pydantic v2** models.

Minimum required schema:

- `CircuitIR`
  - `version: str` (e.g. `"1"`)
  - `components: list[ComponentIR]`
  - `nets: list[NetIR]`
  - optional: `options: OptionsIR | None`
- `ComponentIR`
  - `ref: str` (e.g. `"R1"`, `"U1"`)
  - `symbol: str` (KiCad lib id: `"Device:R"`, `"Amplifier_Operational:NE5532"`)
  - optional: `value: str | None`
  - optional: `footprint: str | None`
  - optional: `fields: dict[str,str] | None`
- `NetIR`
  - `name: str`
  - `pins: list[PinRefIR]`
- `PinRefIR` (structured form)
  - `ref: str`
  - `pin: str` (pin number as string)
  - `unit: str | None`  (**MVP requires None**, see D6)
- `OptionsIR` (minimal)
  - optional: `tech: Literal["THT","SMD"]`
  - optional: default packages (res/cap)
  - optional: `power_net_names: list[str]` default `["0V","GND"]`

Helpers:
- `CircuitIR.load(path: Path) -> CircuitIR`
- `CircuitIR.dumps() -> str`

Errors:
- On schema invalid: raise `UserError` with path + field locations.

**Acceptance**
- Invalid JSON produces actionable errors.
- Valid JSON yields typed model.

---

### P1.2 Semantic IR validation
Create `kicad_pcb/ir_validate.py` (or methods on `CircuitIR`) for:

- [x] Unique `ref` across `components`
- [x] Unique net names across `nets`
- [x] Every `PinRefIR.ref` exists in `components`
- [x] `pins` non-empty for each net
- [x] No pin appears in multiple nets (treat as error)

---

## P2 — Symbol metadata index (pins) for deterministic validation

### P2.1 Implement `SymbolIndex`
Add `kicad_pcb/symbol_index.py`:

- [x] Provide `SymbolIndex(symbols_dir: Path, fallback_dirs: list[Path])`
- [x] Memoize symbol pin lookups.
- [x] `get_pins(symbol_id: str) -> set[str]`

Implementation:
- Reuse existing `read_lib_symbol_pins(symbol_id, symbols_dir=...)` helper.
- Apply precedence per D7.

Repo-local fallback path:
- `kicad_pcb/resources/symbols`

**Acceptance**
- Missing symbol errors are clear and actionable.

---

### P2.2 Validate Circuit IR against `SymbolIndex`
Before writing:
- [x] Verify each `ComponentIR.symbol` exists.
- [x] Verify each `PinRefIR.pin` exists in symbol pins.
- [x] Enforce D6: `unit` must be None; else error.
continue with P3.2
---

## P3 — Schematic ownership + introspection: `info-sch --json`

### P3.1 Implement ownership marker helpers in `SchematicDoc`
Add to `SchematicDoc`:

- [x] `has_openclaw_marker() -> bool`
- [x] `ensure_openclaw_marker()` (inserts marker text items off-canvas)
- [x] `ensure_managed_sheet(name="OpenClaw_Managed")`
- [x] `clear_managed_sheet_contents(name="OpenClaw_Managed")` — implemented as full managed-file rebuild (no in-place clear method; acceptance criterion met via authoritative sync)
- [x] `get_managed_sheet(name="OpenClaw_Managed")` — `has_managed_sheet()` covers existence check; full get not required by acceptance

**Acceptance**
- `apply-netlist` can clear/rebuild managed sheet content without touching user content outside that sheet. ✅ (managed `.kicad_sch` is completely reconstructed from IR each run)

---

### P3.2 Add deterministic pin→net mapping extractor for generated schematics
Because full connectivity graph solving is hard, the writer must follow a convention:

- [x] For each pin-net binding written by tool:
  - place a short wire stub from pin anchor
  - place a net label at stub end
- [x] Implement `extract_pin_label_bindings()` that:
  - finds stubs + labels created by tool
  - returns mapping: `{ref, pin} -> net_name`
  - **Status: implemented via deterministic `OpenClaw:bind=<json>` markers emitted by writer and parsed by `SchematicDoc.extract_pin_label_bindings()`**

**Acceptance**
- `info-sch --json` reports deterministic pin→net mapping for OpenClaw-generated schematics.

---

### P3.3 Wire CLI command `info-sch`
- [x] Add `info-sch [--json]`
- [x] JSON result includes:
  - `project_path`
  - `schematic_path`
  - `owned_by_openclaw: bool`
  - `symbols: [...]`
  - `pin_net_bindings: [...]`
  - `warnings: [...]`

---

## P4 — Deterministic netlist application: `apply-netlist`

### P4.1 Add CLI command `apply-netlist` ✅
Created `commands/netlist.py`:

Inputs:
- IR path (JSON)
- `--symbols-dir` optional
- `--mode` (ValidationMode) default `internal`
- `--dry-run`
- `--force` (adopt non-owned schematic)

Behavior:
1. Load current/open project
2. Load CircuitIR JSON
3. Schema + semantic validation
4. Symbol/pin validation via SymbolIndex
5. Load schematic doc
6. Ownership check:
   - if marker absent and not `--force`: fail with `NOT_OWNED`
  - if `--force`: insert marker and create/adopt `OpenClaw_Managed`
7. **Authoritative sync**:
  - clear `OpenClaw_Managed` contents
  - rebuild `OpenClaw_Managed` from IR deterministically
8. Validate using pipeline:
   - internal syntax/lint always
   - `kicad-cli` only if mode requires and available (see D4)
9. Write atomically only if validation passes

---

### P4.2 Deterministic placement + wiring algorithm (MVP)
- [x] Place symbols in deterministic sorted ref order on a grid (6-column mm grid, 50.8/76.2mm origin, 30.48mm spacing).
- [x] For each pin-net binding:
  - compute pin anchor location (based on symbol position + pin metadata if available)
  - draw stub and label at deterministic offset
- [x] Ensure no duplicate labels or wires created across runs (clearing region makes this easy — managed file is fully rebuilt each run).

**Acceptance**
- Applying same IR twice yields same normalized model (D5).

---

### P4.3 Result shape ✅
Add `ApplyNetlistResult` including:
- [x] counts: symbols_added, symbols_updated, managed_items_written, nets_applied
- [x] paths: schematic_path, managed_schematic_path
- [x] flags: `kicad_cli_used: bool`, `dry_run: bool`
- [x] warnings

---

## P5 — Deterministic compilation: `new-from-netlist`

### P5.1 Add CLI command `new-from-netlist` ✅
Inputs:
- `--name` (project name)
- `--out-dir`
- `--description` optional
- `--netlist <circuit.json>`
- `--symbols-dir`
- `--mode` default `KICAD` (strict) per D4

Steps:
1. Create new project dir and baseline `.kicad_pro` / schematic file (reuse existing `new` logic)
2. Set current project (explicit `open` concept or pass path through)
3. Insert ownership markers
4. Apply netlist (same implementation as apply-netlist)
5. Validate per mode; in strict mode require `kicad-cli`
6. Return file paths

Add `NewFromNetlistResult`.

---

### P5.2 Optional alias `compile-netlist`
- [x] Added as a separate `cli.py` subparser pointing to `cmd_new_from_netlist` with identical args and default `--mode kicad`.

---

## P6 — Validation & error reporting

### P6.1 Add stable error codes enum ✅
Extend existing `kicad_pcb/errors.py` (do not replace existing exception hierarchy):

- [x] `UserError(code: str, message: str, details: dict | None)`
- [x] Define stable codes (all present in `ErrorCode` enum):
  - `IR_SCHEMA_INVALID`
  - `IR_SEMANTIC_INVALID`
  - `SYMBOL_NOT_FOUND`
  - `PIN_INVALID`
  - `MULTI_UNIT_UNSUPPORTED`
  - `KICAD_CLI_MISSING`
  - `VALIDATION_FAILED`
  - `NOT_OWNED`
  - `PROJECT_NOT_OPEN`
  - `IO_ERROR`

- [x] CLI `--json` uses D8 shapes (`{ ok, result, warnings }` / `{ ok, error }`).

---

### P6.2 Mode behavior when `kicad-cli` missing ✅
- [x] If mode is `KICAD` and cli missing → raise `KICAD_CLI_MISSING` (hard fail with actionable message)
- [x] If mode is `internal` and cli missing → ok, warning added to result

---

## P7 — Tests

### P7.1 Unit tests: Circuit IR schema + semantic validation ✅
- [x] Valid IR loads
- [x] Invalid schema fails with `IR_SCHEMA_INVALID`
- [x] Duplicate refs fails with `IR_SEMANTIC_INVALID`
- [x] Unknown symbol fails with `SYMBOL_NOT_FOUND`
- [x] Unknown pin fails with `PIN_INVALID`
- [x] Non-null unit fails with `MULTI_UNIT_UNSUPPORTED`

- [x] `tests/fixtures/symbols/TestLib.kicad_sym` (no mocks) — covers all symbol/pin validation cases

---

### P7.2 Integration test: `new-from-netlist` in internal mode
- [x] Create temp project dir
- [x] Run `new-from-netlist --mode internal`
- [x] Verify schematic exists and parses via your parser
- [x] Verify marker present and managed region populated
- [x] Verify `info-sch --json` returns expected pin→net bindings.

---

### P7.3 Optional integration test with `kicad-cli` ✅
- [x] `tests/integration/test_phase0_smoke.py` has `@requires_kicad` skip guard and tests kicad-cli schematic loading
- [x] Strict-mode `new-from-netlist` + `compile-netlist` integration test added: `TestNewFromNetlistKicadMode` class with 4 tests

---

### P7.4 Idempotency test (structural/semantic) ✅
- [x] Apply same IR twice (`test_apply_netlist_idempotent_apply_twice`)
- [x] Extract normalized model for managed region (sorted refs + symbol_ids)
- [x] Assert equality; assert no duplicates
- [x] Two independent `new-from-netlist` calls produce equivalent managed regions (`test_new_from_netlist_idempotency_via_two_projects`)

Note: `(ref, pin, net_name)` binding assertion is now unblocked by P3.2; full idempotency binding-shape assertion can be added as an enhancement.

---

## P8 — CLI wiring & docs ✅

- [x] Wire subcommands in `cli.py`:
  - `info-sch`
  - `apply-netlist`
  - `new-from-netlist`
  - `compile-netlist` (alias)
- [x] Update README / SKILL docs:
  - explain pipeline: Spec → IR → KiCad
  - include minimal IR example
  - document ownership/refusal + `--force`
  - document validation modes and `kicad-cli` requirement in strict mode
- [x] `scripts/validate.sh` exists and aligns with CI (ruff, format check, mypy, pytest with coverage)

---

## Definition of Done

- [x] Given a Circuit IR JSON, `new-from-netlist` deterministically generates a KiCad project and schematic that:
  - opens in KiCad — ✅ schematic passes internal parser; kicad-cli load untested without kicad-cli installed
  - passes internal syntax + lint checks always ✅
  - passes `kicad-cli sch validate` in strict mode (and fails hard if `kicad-cli` missing) ✅ wired; runtime-tested only when kicad-cli present
- [x] LLM never draws wires by XY; it outputs IR only.
- [x] `apply-netlist` supports authoritative sync within managed region and respects ownership policy.
- [x] `info-sch --json` returns enough data to verify generation (symbols, ownership, pin→net bindings).
- [x] Tests cover schema, semantic validation, symbol/pin validation, internal-mode generation, and idempotency.
