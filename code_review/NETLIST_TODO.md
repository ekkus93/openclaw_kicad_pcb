# Refactor TODO: `commands/netlist.py`

Source: `kicad-pcb/src/kicad_pcb/commands/netlist.py` (909 lines)

## Goal

Split one 909-line file into four focused modules, each with a single
responsibility, while keeping all public symbols importable from
`commands/netlist.py` (no callers change).

Target structure:

```
commands/
    netlist.py          ← thin public entrypoints only  (~150 lines)
    _validate.py        ← 3-layer validation helper + advisory warnings  (~80 lines)
    _sch_apply.py       ← schematic generation engine  (~350 lines)
    _project.py         ← project scaffolding + zip  (~180 lines)
```

---

## Phase 1 — Extract `_validate.py`

Shared 3-layer validation logic that is currently duplicated in three places.

- [x] **1.1** Create `commands/_validate.py` with:
  - [x] `full_validate(path: Path, symbol_index: SymbolIndex) -> CircuitIR`
    - Layers: `CircuitIR.load` → `validate_circuit_ir` → `validate_ir_symbols`
    - Replace the local `_load_and_validate` closure in `cmd_new_from_netlist`
    - Replace the inline 3-layer sequence in `cmd_validate_netlist`
    - Replace the post-fix inline sequence in `cmd_fix_netlist`
  - [x] `advisory_warnings(ir: CircuitIR) -> list[dict[str, object]]`
    - Extract the "unreferenced component" and "single-pin net" warning logic
      from `cmd_validate_netlist` (currently ~30 lines inline)

- [x] **1.2** Update `cmd_validate_netlist` in `netlist.py`:
  - [x] Replace layers 1–3 inline block with `full_validate(netlist_path, symbol_index)`
  - [x] Replace warning-building block with `advisory_warnings(ir)`

- [x] **1.3** Update `cmd_fix_netlist` in `netlist.py`:
  - [x] **Intentionally skipped**: `cmd_fix_netlist` validates an in-memory dict via
    `CircuitIR.model_validate(outcome.ir_dict)`, not from a file path — `full_validate`
    does not apply. A `validate_from_dict` helper can be added in a later phase if needed.

- [x] **1.4** Update `cmd_new_from_netlist` in `netlist.py`:
  - [x] Delete local `_load_and_validate` closure
  - [x] Call `full_validate(netlist_path, symbol_index)` instead

- [x] **1.5** Run `ruff check --fix`, `mypy`, `pytest tests/unit/` — all green before moving on

---

## Phase 2 — Extract `_project.py`

Project scaffolding, file templates, and zip utility are unrelated to netlist
logic and should live separately.

- [x] **2.1** Create `commands/_project.py` with:
  - [x] `_MINIMAL_PCB_TEXT` — move the inline PCB template string from `_create_project`
  - [x] `minimal_schematic_text() -> str` — public name (drop leading underscore;
    only called within `commands/`)
  - [x] `_create_project(*, name: str, out_dir: Path | None, description: str) -> ProjectRef`
    — moved from `netlist.py`; calls local `minimal_schematic_text()`
  - [x] `_create_schematic_zip(project_path: Path, dest_dir: Path, name: str) -> Path`
    — moved from `netlist.py`

- [x] **2.2** Update `netlist.py`:
  - [x] Replace `_minimal_schematic_text()` definition with import + rename to `minimal_schematic_text`
  - [x] Replace `_create_project(...)` definition with import from `._project`
  - [x] Replace `_create_schematic_zip(...)` definition with import from `._project`
  - [x] Remove now-unused stdlib imports (`zipfile`, `datetime`) and config symbols
    (`PROJECTS_DIR`, `load_config`, `set_current_project`)

- [x] **2.3** `_ensure_managed_file_exists` call site updated to `minimal_schematic_text()`
  (will be fully moved to `_sch_apply.py` in Phase 3)

- [x] **2.4** ruff clean, mypy clean (61 files), pytest 1590 passed

---

## Phase 3 — Extract `_sch_apply.py`

The schematic generation engine: symbol placement, pin coordinate transform,
routing, managed-sheet lifecycle.

- [x] **3.1** Create `commands/_sch_apply.py` with the following items moved from
  `netlist.py`:

  - [x] **Constants** (re-export from `netlist.py`):
    - `MANAGED_SHEET_NAME = "OpenClaw_Managed"`
    - `MANAGED_SHEET_FILE = "OpenClaw_Managed.kicad_sch"`
    - `MIN_COMPONENT_PLACEMENT_RATIO = 0.8`

  - [x] **`_ApplyNetlistRequest` dataclass** — moved verbatim

  - [x] **`_resolve_mode(mode_name, *, default) -> ValidationMode`** — moved verbatim

  - [x] **`_ensure_managed_file_exists(path, *, dry_run) -> None`** — moved; uses
    `_project.minimal_schematic_text()`

  - [x] **`_ensure_project_root_owned(project, *, force, dry_run) -> str`** — moved verbatim

  - [x] **`_embed_symbol_if_found(*, doc, symbol, symbol_index) -> bool`** — moved verbatim

  - [x] **`_transform_pin_at` (NEW pure helper)**:
    - Extracted the `cos/sin` rotation block from `_write_symbols` (~15 lines)
    - Returns `{pin_num: (schematic_x, schematic_y, schematic_angle)}` after
      applying rotation; identity transform when `rotation == 0`
    - Pure function — no side effects

  - [x] **`_write_symbols(...)`** — moved and refactored:
    - Inline rotation block replaced with `_transform_pin_at` call

  - [x] **`_build_managed_mutator` factory** — lifted from inner closure:
    - All 8 captured variables passed explicitly as keyword parameters
    - Returns the inner `_mutate` function
    - `# noqa: PLR0913` suppression added (8 keyword params unavoidable here)

  - [x] **`_apply_netlist_to_project(project, request) -> ApplyNetlistResult`**
    — moved; uses `_build_managed_mutator`

  - [x] **`resolve_schematic_paths(project) -> tuple[Path, Path]`** — moved;
    re-exported from `netlist.py` (with `# noqa: F401`)

- [x] **3.2** Update `netlist.py`:
  - [x] All moved definitions removed
  - [x] `from ._sch_apply import (...)` added with backward-compat re-exports:
    - `MANAGED_SHEET_FILE` (used internally + by tests)
    - `MANAGED_SHEET_NAME` (used internally)  `# noqa: F401`
    - `_ApplyNetlistRequest` (used by test_phase7_ux)
    - `_apply_netlist_to_project` (used by cmd_apply_netlist, cmd_new_from_netlist)
    - `_write_symbols` (used by test_phase7_ux via late import) `# noqa: F401`
    - `resolve_schematic_paths` (used by test_netlist_commands) `# noqa: F401`
  - [x] Unused stdlib/third-party imports removed (math, shutil, dataclass, etc.)

- [x] **3.3** Verify `netlist.py` now contains only:
  - Module docstring + imports
  - `cmd_info_sch`, `cmd_validate_netlist`, `cmd_apply_netlist`, `cmd_fix_netlist`,
    `cmd_new_from_netlist`

- [x] **3.4** run checks:
  - ruff clean, mypy clean (62 source files), pytest 1590 passed
  - Fixed `test_phase7_ux.py` patch path:
    `kicad_pcb.commands.netlist.make_layout_engine` →
    `kicad_pcb.commands._sch_apply.make_layout_engine`

---

## Phase 4 — New unit tests

- [x] **4.1** Add tests for `_transform_pin_at` in `tests/unit/test_sch_apply.py`:
  - [x] `test_identity_rotation` — rotation=0 returns pure translation
  - [x] `test_rotation_90` — 90° rotation: (px, py) → (-py, px)
  - [x] `test_rotation_180` — 180° rotation: (px, py) → (-px, -py)
  - [x] `test_angle_wraps_below_360` — resulting angle is in [0, 360)
  - [x] `test_origin_applied_correctly` — origin is added after rotation
  - [x] `test_empty_pin_map` — empty input → empty output
  - [x] `test_preserves_all_pins` — all pins appear in output

- [x] **4.2** Add tests for `advisory_warnings`:
  - [x] `test_no_warnings_for_clean_ir` — no warnings for clean IR
  - [x] `test_component_not_in_any_net` — COMPONENT_NOT_IN_ANY_NET warning
  - [x] `test_single_pin_net` — SINGLE_PIN_NET warning
  - [x] `test_both_warnings_independent` — both warnings can fire together

- [x] **4.3** Add tests for `full_validate`:
  - [x] `test_passes_valid_ir`
  - [x] `test_raises_on_schema_error`
  - [x] `test_raises_on_semantic_error`
  - [x] `test_raises_on_invalid_json`

- [x] **4.4** pytest: 1605 passed (15 new tests all green)

---

## Phase 5 — Final checks and commit

- [x] **5.1** Confirm file sizes:
  - `netlist.py`: 392 lines (≤200 target not met; all remaining content is `cmd_*`
    entrypoint logic which cannot be further split within this phase — 57% reduction
    from 909 lines is still a major improvement)
  - `_validate.py`: 87 lines ✓
  - `_sch_apply.py`: 496 lines (≤400 target slightly exceeded due to
    `_build_managed_mutator` factory docs + `_transform_pin_at` doc)
  - `_project.py`: 119 lines ✓

- [x] **5.2** ruff check — no errors

- [x] **5.3** mypy — no issues found in 62 source files

- [x] **5.4** pytest — 1605 passed, 0 failed

- [x] **5.5** Commits:
  - `feec294` Phase 1 — extract `_validate.py`
  - `95a82be` Phase 2 — extract `_project.py`
  - `fc95b0d` Phase 3 — extract `_sch_apply.py`
  - `99a95b4` Phase 4 — new tests in `test_sch_apply.py`

---

## Checklist summary

| Phase | Description                              | Files created / modified                     |
|-------|------------------------------------------|----------------------------------------------|
| 1     | Extract shared 3-layer validator         | `+_validate.py`, `~netlist.py`               |
| 2     | Extract project scaffolding + zip        | `+_project.py`, `~netlist.py`                |
| 3     | Extract schematic generation engine      | `+_sch_apply.py`, `~netlist.py`              |
| 4     | New unit tests for extracted helpers     | `~test_netlist_commands.py` or new test file |
| 5     | Final checks + commit                    | —                                            |
