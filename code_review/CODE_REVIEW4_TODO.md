# CODE_REVIEW4 TODO

Comprehensive task list derived from [CODE_REVIEW4.md](CODE_REVIEW4.md).
Tasks are ordered by priority: P0 must land before P1; P2 can be done any time.

---

## P0-A — Fix `_parse_file_to_cached` to use `read_lib_symbol_pins` for `extends` symbols

**File:** `kicad-pcb/src/kicad_pcb/commands/search.py`

The `_count_pins_in_block` regex returns 0 for any symbol that inherits all its
pins via `(extends "BaseName")`. Replace it with `read_lib_symbol_pins`, which
already walks the extends chain correctly.

### Tasks

- [x] **P0-A-1** Import `read_lib_symbol_pins` in `search.py`
  - Superseded by the in-memory approach: instead of calling `read_lib_symbol_pins`
    (which re-parses the file via the s-expression parser — O(N²) on large libs),
    `_resolve_pin_count(block_text, sym_blocks)` was added. It walks the extends
    chain using the already-extracted `sym_blocks` dict and `_EXTENDS_NAME_RE`.
    No external import needed; goal fully achieved.

- [x] **P0-A-2** Modify `_parse_file_to_cached` to call `read_lib_symbol_pins`
  - `_parse_file_to_cached` now builds a `sym_blocks: dict[str, str]` from all
    extracted blocks and calls `_resolve_pin_count(block_text, sym_blocks)` which
    follows `(extends ...)` chains in-memory. `_count_pins_in_block` is retained
    as the leaf-level counter used by `_resolve_pin_count`.

- [x] **P0-A-3** Bump the SQLite cache schema version to force eviction of stale entries
  - `CACHE_VERSION = 2` added to `symbol_cache.py`.
  - New `meta` table with a `version` row added to `_SCHEMA`.
  - `_get_conn` checks the stored version; on mismatch it `DELETE`s all rows in
    `symbol_cache` and `indexed_files`, then inserts the new version. Stale
    0-pin cached entries are evicted transparently on first open.

- [x] **P0-A-4** Add an `extends`-based fixture symbol to the test fixtures
  - `tests/fixtures/symbols/TestLib.kicad_sym` already contained `OpAmp` (4 pins:
    1, 2, 3, 6) and `DerivedOpAmp (extends "OpAmp")` with no own pins. The
    fixture was already suitable; no changes needed. Tests assert `pin_count == 4`.

- [x] **P0-A-5** Unit test: `_parse_file_to_cached` reports correct pin count for `extends` symbol
  - `test_parse_extends_symbol_reports_parent_pin_count` added to
    `TestExtendsSymbolPinCount` in `tests/unit/test_symbol_cache.py`.
  - Asserts `DerivedOpAmp.pin_count == 4` (fixture base `OpAmp` has 4 pins).

- [x] **P0-A-6** Unit test: `search-symbols` returns non-zero pin count for an `extends` symbol
  - `test_search_symbols_extends_pin_count` added to `TestExtendsSymbolPinCount`.
  - Queries `"derivedopamp"` against the fixture dir; asserts `pin_count == 4`.

- [x] **P0-A-7** Unit test: `build-symbol-index` correctly indexes an `extends` symbol
  - `test_build_index_then_search_extends_pin_count` added to `TestExtendsSymbolPinCount`.
  - Asserts `files_updated >= 1` after build, then `pin_count == 4` on warm search.

- [x] **P0-A-8** Verify against the real KiCad libraries
  - `Amplifier_Operational:NE5532  (8 pins)` confirmed after fix.
  - Full extends-chain resolution is general (any depth up to `max_depth=8`).

---

## P0-B — Add 0-pin / invalid-pin preflight guard to `apply-netlist`

**File:** `kicad-pcb/src/kicad_pcb/commands/netlist.py`

Before writing `OpenClaw_Managed.kicad_sch`, validate that every symbol in the
Circuit IR resolves to a non-empty pin list and that every referenced pin exists.

### Tasks

- [x] **P0-B-1** Add error types for pin validation failures
  - Added `SYMBOL_HAS_NO_PINS = "SYMBOL_HAS_NO_PINS"` to `ErrorCode` in
    `errors.py`. No separate exception classes were needed — the codebase
    uniformly uses `UserError(code=ErrorCode.X)`; a new error code gives
    callers machine-readable discrimination between "not in file" vs
    "in file but 0 pins" without adding new exception hierarchy.

- [x] **P0-B-2** Add `_validate_ir_pins` helper function in `netlist.py`
  - The proposed `_validate_ir_pins` was not needed as a new function.
    `validate_ir_symbols(ir, symbol_index)` in `ir_validate.py` already
    performs both checks: (1) calls `symbol_index.get_pins()` which raises
    on empty pin sets, and (2) validates every pin reference in `ir.nets`
    against the resolved pin set, raising `PIN_INVALID` on mismatch.
  - `SymbolIndex.get_pins()` in `symbol_index.py` was enhanced: after the
    empty-result fall-through, a fast regex scan checks whether the symbol
    name IS present in the library file; if so, raises
    `UserError(SYMBOL_HAS_NO_PINS)` (broken extends chain); otherwise raises
    `UserError(SYMBOL_NOT_FOUND)` (name absent from all dirs).

- [x] **P0-B-3** Call `_validate_ir_pins` from `cmd_apply_netlist` / `cmd_new_from_netlist`
  - The call site already existed: `_apply_netlist_to_project` calls
    `validate_ir_symbols(ir, symbol_index)` before any file writes. No code
    change was needed here — errors raised by `validate_ir_symbols` (and by
    `get_pins()` within it) propagate as `UserError` with structured `code`
    fields, surfacing as non-zero exit / JSON error field via the CLI layer.

- [x] **P0-B-4** Unit test: `apply-netlist` aborts on 0-pin symbol
  - `test_broken_extends_chain_raises_symbol_not_found` (from P0-A) was
    renamed to `test_broken_extends_chain_raises_symbol_has_no_pins` and its
    assertion updated from `SYMBOL_NOT_FOUND` to `SYMBOL_HAS_NO_PINS`.
    The fixture uses a patched `read_lib_symbol_pins` that returns `[]` for
    `TestLib:DerivedOpAmp` (simulating the broken-chain case); the test
    verifies the new, more specific error code is raised.

- [x] **P0-B-5** Unit test: `apply-netlist` aborts on unknown pin reference
  - `test_apply_netlist_aborts_on_invalid_pin_ref` added to
    `tests/unit/test_netlist_commands.py`. Uses `TestLib:R` with pin `"99"`
    (non-existent); asserts `UserError` with `code == ErrorCode.PIN_INVALID`
    is raised and that the managed schematic file was **not** written to disk.

---

## P1 — Investigate and fix blank SVG output from `apply-netlist`

**File:** `kicad-pcb/src/kicad_pcb/commands/netlist.py`

The `_write_nets` function currently stores binding markers as hidden text rather
than as real KiCad wire connections. This is the likely cause of the blank SVG
preview. This needs investigation before a fix can be scoped.

### Investigation tasks

- [x] **P1-1** Run `apply-netlist` on a minimal 2-component, 1-net test circuit
  - Ran Python-based investigation with a 2-resistor (R1, R2) series circuit
    using `TestLib:R` from the fixture library.
  - Component symbols ARE present and correctly embedded in `lib_symbols`.
  - `(wire ...)` and `(label ...)` nodes ARE present — the TODO's claim that
    only hidden text markers were written was describing an earlier code state.
  - **Root cause identified**: wires start at `(sym_x + 5.08, sym_y + 2.54*idx)`
    — a hardcoded offset that is only a coincidence for one pin of one symbol.
    For `TestLib:R` at (50.80, 76.20), pin 1 should start at (50.80, 76.20)
    and pin 2 at (55.88, 76.20); the old code placed them at
    (55.88, 78.74) and (55.88, 81.28) respectively — wrong for all cases.

- [x] **P1-2** Determine the correct KiCad schematic data model for net connections
  - KiCad requires wires to start **exactly** at the pin connection endpoint
    for DRC to recognise an electrical connection.
  - Each `(pin ... (at X Y angle) ...)` in a `.kicad_sym` file specifies the
    endpoint in library-local coordinates; `angle` points **from** the endpoint
    **toward** the symbol body.  Wire stubs should extend in the opposite
    direction (angle + 180°).
  - Using `(wire ...)` + `(label ...)` at the wire far-end is the correct
    KiCad pattern for associating a net name with a pin; two labels with the
    same name on different wires are electrically equivalent across the sheet.
  - `doc.add_symbol` already writes `(pin N (uuid ...))` children correctly;
    no change needed there.

- [x] **P1-3** Based on investigation output, fix `_write_nets` (or equivalent)
  - Added `_collect_pin_at(sym_node)` helper in `sch_doc.py` — walks a symbol
    AST and returns `{pin_num: (x, y, angle)}` for all pins.
  - Added `read_lib_symbol_pin_at(lib_name, sym_name, *, symbols_dir)` in
    `sch_doc.py` — follows `(extends ...)` chains (base-first); returns
    `{pin_num: (x, y, angle)}` in library-local coordinates.
  - Updated `make_label_node` and `SchematicDoc.add_label` to accept an
    `angle: int = 0` parameter so labels are oriented to match the wire
    direction.
  - Modified `_write_symbols` in `netlist.py` to also compute and return
    `pin_endpoints: dict[tuple[str,str], tuple[float,float,float]]` — actual
    pin connection coordinates in schematic space (library coords translated
    by symbol placement position).
  - Rewrote `_write_nets` to:
    - Start each wire from `pin_endpoints[(ref, pin)]` (exact endpoint).
    - Extend the wire 5.08 mm outward (direction = pin_angle + 180°).
    - Place the net label at the wire's far end with matching label angle.
    - Graceful off-canvas fallback if a pin endpoint is missing (should not
      happen after `validate_ir_symbols` passes).

- [x] **P1-4** Add or update integration test for `apply-netlist` + `preview-schematic`
  - `test_wires_connect_at_pin_endpoints` added to
    `tests/unit/test_netlist_commands.py`.
  - Uses a 2-resistor series circuit with `TestLib:R`; calls
    `read_lib_symbol_pin_at` to get the ground-truth pin positions; extracts
    all wire start points from the managed schematic AST; asserts every
    expected pin endpoint has a wire starting there.
  - No `kicad-cli` required — the test validates the `.kicad_sch` AST directly.

---

## P2 — Add `debug-symbol` command

**Files:** `kicad-pcb/src/kicad_pcb/commands/search.py`,
`kicad-pcb/src/kicad_pcb/results.py`,
`kicad-pcb/src/kicad_pcb/__init__.py`,
`kicad-pcb/src/kicad_pcb/cli.py`

### Tasks

- [ ] **P2-1** Add `DebugSymbolResult` dataclass to `results.py`
  ```python
  @dataclass(frozen=True)
  class DebugSymbolResult:
      symbol_id: str          # "Lib:Name"
      extends_base: str | None  # "Lib:BaseName" if it uses extends, else None
      pin_numbers: tuple[str, ...]
      pin_count: int
  ```

- [ ] **P2-2** Add `cmd_debug_symbol(args) -> DebugSymbolResult` in `commands/search.py`
  - Parse `args.symbol` as `"lib_name:sym_name"`.
  - Resolve `symbols_dir` from `args.symbols_dir` or discovery chain.
  - Call `read_lib_symbol_pins(lib_name, sym_name, symbols_dir=...)` for pin list.
  - For the `extends_base` field: read the raw symbol block and call
    `_get_extends_name` (already in `sch_doc.py`) to extract the base name,
    qualifying it as `lib_name:base_name`.
  - Return `DebugSymbolResult`.

- [ ] **P2-3** Export `DebugSymbolResult` and `cmd_debug_symbol` from `__init__.py`

- [ ] **P2-4** Add `debug-symbol` subparser to `cli.py`
  ```
  kicad_pcb debug-symbol <lib:name> [--symbols-dir DIR]
  ```
  - Set `func=cmd_debug_symbol`.

- [ ] **P2-5** Add human-readable formatting for `DebugSymbolResult` in `formatting.py`
  - When `--json` is not set, print something like:
    ```
    Symbol:  Amplifier_Operational:NE5532
    Extends: Amplifier_Operational:LM2904
    Pins (8): 1  2  3  4  5  6  7  8
    ```

- [ ] **P2-6** Unit tests for `cmd_debug_symbol`
  - `test_debug_symbol_standalone` — a symbol with its own pins, no extends.
  - `test_debug_symbol_extends` — an `extends` symbol; assert `extends_base`
    is populated and `pin_count` equals the parent's pin count.
  - `test_debug_symbol_not_found` — non-existent symbol; assert graceful error.

---

## Cross-cutting / wrap-up

- [ ] **CC-1** Update `SKILL.md`
  - Note that `extends`-based symbols (common for op-amp families) now report
    correct pin counts.
  - Add `debug-symbol` to the command reference table once P2 is done.

- [ ] **CC-2** Run full test suite: `pytest tests/unit/`
  - All existing tests must continue to pass.

- [ ] **CC-3** Run lint: `ruff check` + `ruff format --check`

- [ ] **CC-4** Run mypy on touched files

- [ ] **CC-5** Commit and push
  - Suggested commit messages:
    - `fix: use read_lib_symbol_pins for extends symbols in search cache (P0-A)`
    - `fix: add 0-pin preflight guard to apply-netlist (P0-B)`
    - `fix: write real KiCad wiring in _write_nets (P1)` — after investigation
    - `feat: add debug-symbol command (P2)`
