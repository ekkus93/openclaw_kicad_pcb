# kicad-pcb Skill — Memory File

_Last updated: 2026-02-27T00:00:00Z_

---

## 2026-02-27T01:00:00Z - EMPTY_SCHEMATIC_FIX_TODO round 2: P5 warning + P6.4 test
- Added `MANAGED_SHEET_EMPTY` warning in `cmd_info_sch` when managed sheet exists but has 0 placed symbols (after computing `managed_symbol_count`).
- Added P6.4 regression test `test_empty_generation_invariant_raises_coded_error` in `tests/unit/test_netlist_commands.py`:
  - Monkeypatches `SchematicDoc.add_symbol` to no-op so AST stays empty while pipeline runs normally.
  - Asserts `UserError.code == EMPTY_GENERATION` and details contain `expected_components` + `found_symbols == 0`.
- Added assertions for new P5/P7 fields in `test_new_from_netlist_info_sch_returns_owned_and_symbols`: `managed_schematic_path is not None`, `managed_symbol_count >= 1`, `managed_label_count >= 1`, `symbol_count == 0` (root is thin).
- Added `assert result.symbols_dirs_used` in `test_cmd_apply_netlist_creates_managed_schematic`.
- Committed as `0b4f960` and pushed to origin/master.

---

## 2026-02-27T00:00:00Z - EMPTY_SCHEMATIC_FIX_TODO items P1.1/P2.1/P5/P6.4/P7 implemented
- Implemented all 5 items from `EMPTY_SCHEMATIC_FIX_TODO.md` per user/ChatGPT direction.
- **P6.4 (`errors.py`)**: Added `EMPTY_GENERATION = "EMPTY_GENERATION"` and `SYMBOL_DIR_MISSING = "SYMBOL_DIR_MISSING"` to `ErrorCode` StrEnum.
- **P5 (`sch_doc.py`)**: Added `count_nodes(key: str) -> int` helper on `SchematicDoc` — counts direct-children of root with given S-expression key. Used for symbol/label AST counting without going through public `list_symbols()` list.
- **P2.1 (`symbol_index.py`)**: Added guard in `SymbolIndex.__init__` — if `self._dirs` is empty after resolution, raises `UserError(code=SYMBOL_DIR_MISSING, details={searched_candidates, repo_local_dir, hint})`.
- **P7 (`results.py`)**: Added `symbols_dirs_used: tuple[str, ...]` to `ApplyNetlistResult` and `NewFromNetlistResult`; added `managed_schematic_path: Path | None`, `symbol_count`, `label_count`, `managed_symbol_count`, `managed_label_count` to `InfoSchResult`.
- **P1.1 (`commands/netlist.py`)**: Added post-mutation AST invariant in `_mutate_managed` closure (after `_write_nets`): queries `len(doc.list_symbols())` — if `ir.components and found_symbols == 0 and not dry_run`, raises `UserError(EMPTY_GENERATION)`; if dry_run, appends warning. Threaded `symbols_dirs_used` into `ApplyNetlistResult` and `NewFromNetlistResult`. Enhanced `cmd_info_sch` to compute and return AST counts plus `managed_schematic_path`.
- **Formatting (`formatting.py`)**: Updated `_fmt_info_sch` (shows root/managed counts separately), `_fmt_apply_netlist` and `_fmt_new_from_netlist` (show `symbols_dirs_used` bullets).
- **Test (`tests/unit/test_symbol_index.py`)**: Added `test_symbol_index_raises_symbol_dir_missing_when_no_dirs` using monkeypatch on `si_mod.REPO_LOCAL_SYMBOLS_DIR` and `si_mod.SYMBOLS_CANDIDATES`.
- All unit tests pass (52 presentation, 83 golden+phase6+dry_run_diff, 9 netlist+symbol_index, 2 info_sch, 42 compat). Ruff clean on all modified files.

---

## 2026-02-26T05:45:53Z - CODE_REVIEW3 P7.3 complete (all items done)
- Implemented `TestNewFromNetlistKicadMode` class in `tests/integration/test_phase0_smoke.py` with 4 tests:
  1. `test_new_from_netlist_kicad_mode_succeeds` — exit 0, files created.
  2. `test_new_from_netlist_kicad_mode_main_sch_loadable` — kicad-cli sch export netlist succeeds.
  3. `test_compile_netlist_alias_succeeds` — alias wired correctly.
  4. `test_both_commands_produce_equivalent_bindings` — both produce identical `OpenClaw:bind=` markers.
- Fixed two pre-existing bugs found during P7.3 implementation:
  1. **`kicad-pcb/scripts/kicad_pcb.py`**: with editable install, `kicad-pcb/src` is already in sys.path via `.pth` file, so the old `if str(_src) not in sys.path: insert(0, ...)` guard was a no-op, leaving `kicad-pcb/scripts/` at sys.path[0] and shadowing the `kicad_pcb` package. Fix: remove+reinsert at 0 unconditionally using `contextlib.suppress(ValueError)` + `sys.path.insert(0, _src_str)`.
  2. **`kicad-pcb/src/kicad_pcb/pipeline.py`**: temp files for kicad-cli validation used `.kicad_sch.tmp` / `.kicad_pcb.tmp` extensions. kicad-cli refuses to load files without `.kicad_sch` / `.kicad_pcb` extension (`Failed to load schematic`, exit 3). Fix: use `.kicad_sch` / `.kicad_pcb` as the mkstemp suffix.
- All 4 P7.3 tests pass; 43 related unit tests pass; ruff clean on all modified files.
- CODE_REVIEW3 is now fully complete — no remaining unchecked items.

---

## 2026-02-26T04:57:14Z - CODE_REVIEW3 P3.2 verified complete in existing .venv
- Confirmed the repo already contains a real deterministic pin→net extractor:
  - writer emits hidden `OpenClaw:bind=<json>` markers in `commands/netlist.py`.
  - `SchematicDoc.extract_pin_label_bindings()` parses/sorts those markers in `sch_doc.py`.
- Verified with existing environment (`.venv`):
  - `.venv/bin/pytest -q tests/unit/test_sch_doc.py::TestExtractPinLabelBindings tests/unit/test_netlist_commands.py::test_new_from_netlist_info_sch_returns_owned_and_symbols` passed.
  - `.venv/bin/ruff check kicad-pcb/src/kicad_pcb/sch_doc.py kicad-pcb/src/kicad_pcb/commands/netlist.py tests/unit/test_sch_doc.py tests/unit/test_netlist_commands.py` passed.
- Updated `CODE_REVIEW3_TODO.md` to mark P3.2 and the P7.2 pin→net assertion complete.

---

## 2026-02-26T04:44:20Z - Clarified Python environment status
- Confirmed the "Python 3.11 venv" note in Phase 0 is historical (initial bootstrap context).
- Current and recent sessions reuse the existing `.venv` / existing Python environment; no new environment creation is required.

---

## 2026-02-26T04:11:14Z - CODE_REVIEW3 Phase 2 completion
Completed all remaining CODE_REVIEW3 TODO items except P3.2 (pin→net extractor, deferred as complex):

### P0 — atomic write hardening
- Added best-effort directory `fsync` (POSIX only, wrapped in `contextlib.suppress`) to `_atomic_write` in `fs.py`.
- Verified no bare `os.write()` anywhere in source.

### P5.2 — compile-netlist alias
- Added `compile-netlist` subparser to `cli.py` pointing to `cmd_new_from_netlist`.
- Same args as `new-from-netlist`, default mode `kicad`.

### P7.2 — Integration tests for new-from-netlist
- `test_new_from_netlist_schematic_parses_and_ownership_marker_present`: verifies schema parses, marker present, managed sheet exists, R1 in symbols.
- `test_new_from_netlist_info_sch_returns_owned_and_symbols`: verifies `info-sch` reports `owned_by_openclaw=True` for a freshly generated project.

### P7.4 — Idempotency tests
- `test_apply_netlist_idempotent_apply_twice`: apply same IR twice → same normalized (refs, symbol_ids) + no duplicates.
- `test_new_from_netlist_idempotency_via_two_projects`: two independent `new-from-netlist` calls → equivalent managed regions.

### P8 — Docs
- `README.md`: added "Circuit IR Pipeline" section with IR example, command descriptions, ownership model, and validation mode table.
- `SKILL.md`: updated Commands table (added `info-sch`, circuit IR commands), added new "Circuit IR Pipeline Workflow" section with JSON example, usage instructions, and mode table.
- `scripts/validate.sh` already existed and is up to date.

### Lint sweeps
- Fixed import ordering in `test_circuit_ir.py`, `test_info_sch.py`, `test_symbol_index.py`.
- Fixed line-length issue in `symbol_index.py` (`__init__` signature wrapped).
- `ruff check kicad-pcb/src/ tests/unit/` now reports: All checks passed.

### Deferred
- P3.2 (`extract_pin_label_bindings` real implementation): placeholder still returns `[]` with a warning in `info-sch`. Deferred for post-MVP; full graph extraction requires wire-stub-matching logic.

---

## 2026-02-26T04:32:51Z - memory.md timestamp audit against git history
- Audited `memory.md` heading timestamps against `git show --format=%cI` for referenced commits.
- Corrected mismatched commit-linked headings (including future-dated and placeholder dates).
- For non-committed local notes (no git object yet), retained session-derived times.

---

## 2026-02-26T03:25:00Z - CODE_REVIEW3 netlist/schematic lint-hardening refactor
- Continued implementation on managed-sheet and netlist command slice, then resolved Ruff findings without changing behavior.
- `commands/netlist.py`:
  - Introduced `_ApplyNetlistRequest` dataclass to reduce parameter count and simplify command handoff.
  - Split `_apply_netlist_to_project` internals into focused helpers:
    - `_write_symbols(...)`
    - `_write_nets(...)`
    - `_embed_symbol_if_found(...)`
    - `_symbol_position(...)`
  - Wrapped long `_atomic_write(...)` calls for style compliance.
- `sch_doc.py`:
  - Introduced `ManagedSheetSpec` dataclass and changed `make_managed_sheet_node(...)` to accept it.
  - Reduced `list_symbols()` complexity by extracting `_symbol_metadata(...)` and `_parse_float_atom(...)` helpers.
  - Simplified nested conditionals in managed-sheet/property helpers to satisfy SIM102.
- `formatting.py`:
  - Applied import-order fix (`ruff --fix`) for I001.
- Validation status after refactor:
  - Ruff on touched files: clean.
  - Focused tests passing:
    - `tests/unit/test_netlist_commands.py`
    - `tests/unit/test_info_sch.py`
    - `tests/unit/test_circuit_ir.py`

---

## 2026-02-26T03:10:00Z - CODE_REVIEW3 implementation started (slice 1)
- Confirmed existing Python 3.11 environment at `.venv`; no new env created.
- Extended IR validation foundation:
  - `ir_validate.py`: improved duplicate detection via `Counter`.
  - Added `validate_ir_symbols(ir, symbol_index)` enforcing:
    - symbol existence,
    - valid pin membership,
    - `PinRefIR.unit is None` (MVP, else `MULTI_UNIT_UNSUPPORTED`).
  - Exported `validate_ir_symbols` via package `__init__.py`.
- Added/updated fixtures + tests:
  - Added `tests/fixtures/symbols/TestLib.kicad_sym` fixture.
  - Expanded `tests/unit/test_circuit_ir.py` to cover unknown refs, invalid pin, unsupported unit.
  - Existing `tests/unit/test_symbol_index.py` now passes with fixture.
- Added first new command slice from CODE_REVIEW3:
  - New `commands/netlist.py` with `cmd_info_sch`.
  - New result type `InfoSchResult` in `results.py`.
  - Wired CLI subcommand `info-sch` in `cli.py`.
  - Added formatter for `InfoSchResult` in `formatting.py`.
  - Exported command/result via `__init__.py`.
- Added SchematicDoc introspection helpers in `sch_doc.py`:
  - `has_openclaw_marker()`, `ensure_openclaw_marker()`.
  - `list_symbols()`.
  - `extract_pin_label_bindings()` placeholder currently returns `[]` (full mapping deferred).
  - Added `make_text_node()` helper for off-canvas markers.
- Added tests:
  - `tests/unit/test_info_sch.py` (project-required behavior + symbol/marker introspection).
- Validation run:
  - `pytest -q tests/unit/test_info_sch.py tests/unit/test_cli.py tests/unit/test_presentation.py tests/unit/test_circuit_ir.py tests/unit/test_symbol_index.py`
  - Result: all passed.

---

## 2026-02-26T02:40:00Z - Finalized CODE_REVIEW3 implementation choices applied to docs
- Updated `code_review/CODE_REVIEW3.md` and `code_review/CODE_REVIEW3_TODO.md` with locked choices from user decision set:
  - Managed region = dedicated top-level sheet `OpenClaw_Managed`.
  - Keep off-canvas ownership marker `OpenClaw:generated=v1`.
  - Repo-local symbol fallback path = `kicad_pcb/resources/symbols`.
  - Mode defaults: `apply-netlist` => `internal`, `new-from-netlist` => `kicad`.
  - Error architecture: extend existing exceptions; do not replace hierarchy.
- TODO file now removes alternative managed-region strategies and points to the single sheet-based approach.

---

## 2026-02-26T02:25:00Z - Reviewed revised CODE_REVIEW3_TODO.md
- User provided an updated TODO that includes explicit decisions D1-D8.
- Assessment: plan is now largely implementation-ready.
- Remaining clarifications before coding:
  - choose one exact managed-region mechanism (node tag/property vs reserved coordinate box), currently options are listed but not locked.
  - define exact repo-local symbol directory path for D7 precedence.
  - confirm whether `apply-netlist` default mode should be internal (as noted in P4.1) or explicit required argument.
  - confirm how new error-code enum integrates with existing `errors.py` exception hierarchy (extend vs replace).

---

## 2026-02-26T02:15:00Z - Reviewed CODE_REVIEW3 design docs (no code changes)
- Reviewed code review docs for compiler-style pipeline:
  - `code_review/CODE_REVIEW3.md`
  - `code_review/CODE_REVIEW3_TODO.md`
- User requested analysis only, explicitly no code modifications yet.
- Key clarifications to request before implementation: command naming (`compile-netlist` vs `new-from-netlist`), ownership/update policy for existing schematics, deletion behavior for removed IR items, strictness policy when `kicad-cli` unavailable, and idempotency assertion mode (byte-identical vs structural).

---

## 2026-02-26T02:05:00Z - Clarified private skill install/runtime environment checks
- Verified script execution works with host `python3`: `python3 kicad-pcb/scripts/kicad_pcb.py --help`.
- Verified environment health check works: `python3 kicad-pcb/scripts/kicad_pcb.py doctor` (all core checks passed on this machine).
- Verified optional Python modules import in same interpreter: `python3 -c "import cairosvg, PIL"`.
- Updated `kicad-pcb/SKILL.md`:
  - Python deps command now uses explicit interpreter: `python3 -m pip install --user cairosvg pillow`.
  - Added note that OpenClaw runs skill commands in the gateway host environment, so deps must be installed in that same `python3`.
  - Added explicit verification step for Python module imports.

---

## 2026-02-26T01:50:00Z - Switched project + skill to GPL v3
- Created root `LICENSE` file with official GNU GPL v3 text (`https://www.gnu.org/licenses/gpl-3.0.txt`).
- Updated `kicad-pcb/SKILL.md` frontmatter license: `MIT` -> `GPL-3.0-or-later`.
- Updated `kicad-pcb/skill.json` license: `MIT` -> `GPL-3.0-or-later`.
- Updated `pyproject.toml` project metadata with `license = { text = "GPL-3.0-or-later" }`.
- Verification: `LICENSE` exists at repo root (`/home/ubo/work/openclaw_kicad_pcb/LICENSE`).

---

## 2026-02-26T01:35:24Z — SKILL.md review and corrections (commit `7a5dd7e`)

### Problem
`kicad-pcb/SKILL.md` had several correctness issues that would prevent users and agents from successfully installing and using the skill.

### Changes
- **`author`**: Changed `Phillip Chin` → `PaxSwarm` to match `skill.json` and the footer.
- **Hardcoded paths**: Replaced all 21 occurrences of `/home/ubo/.openclaw/skills/kicad-pcb/scripts/kicad_pcb.py` with `{baseDir}/scripts/kicad_pcb.py` (the OpenClaw `{baseDir}` placeholder is expanded at runtime to the installed skill folder).
- **Config example**: Changed hardcoded `"kicad_path": "/home/ubo/.local/bin/kicad-cli"` to the generic `/usr/bin/kicad-cli`.
- **New `## Installation` section**: Added `clawhub install kicad-pcb` workflow (correct install CLI is `clawhub`, not `openclaw skill install`). Also noted that `clawhub` installs into `./skills/<slug>/` under the working directory by default.
- **New `## Verifying Installation` section**: Added `python3 {baseDir}/scripts/kicad_pcb.py --help`, `doctor`, and `kicad-cli --version` checks with expected output descriptions.

### Key facts about OpenClaw skill system
- Install CLI: `clawhub install <slug>` (not `openclaw skill install`)
- `clawhub` installs into `./skills/<slug>/` relative to working directory
- Shared skills (all agents): copy to `~/.openclaw/skills/` or change working dir
- `{baseDir}` is the correct OpenClaw placeholder for the skill folder path (see `docs.openclaw.ai/tools/skills`)
- SKILL.md format: frontmatter requires `name:` and `description:` at minimum
- Skills are picked up on the next new session after install

### HEAD after commit
`7a5dd7e` — docs: fix SKILL.md installation instructions and hardcoded paths

---

## 2026-02-26T00:45:35Z — CODE_REVIEW2 P2.2 (numeric lexeme preservation)

### Changes
- **`sexpr/nodes.py`**: Added `lexeme: str | None = field(default=None, compare=False, hash=False)` to `AtomNode`. Excluded from `__eq__`/`__hash__` so existing code unaffected.
- **`sexpr/parser.py`**: Parser sets `lexeme=tok.value` for every atom token.
- **`sexpr/serializer.py`**: `_inline()` and `serialize()` emit `node.lexeme` when present, else `node.value`. Pure round-trip already preserved; this guarantees precision even across mutation paths.
- **`sexpr/builder.py`**: Added `fnum_or_keep(f, original, decimals)` helper. Returns `original` unchanged when `float(original.lexeme) == f`; otherwise calls `fnum()`. Exported from `sexpr/__init__.py`.
- **`pcb_doc.py`**: `_update_footprint_at` uses `fnum_or_keep` so unmoved footprints produce zero diff even with 4-decimal source coordinates.
- **`tests/unit/test_p22_numeric_lexeme.py`**: 35 tests covering all 5 layers of the contract.

### Key design decision
`lexeme` is `compare=False, hash=False` — completely transparent to equality/hash. For parsed atoms, `value == lexeme` (both are the raw token text). For synthesized atoms (`fnum`, `atom`), `lexeme=None` → serializer uses `value`.

### Pending CODE_REVIEW2 items:
- P3.2: Richer exception hierarchy
- P5.1: Structured logging
- P5.2: Dry-run diff output

---

## 2026-02-26T00:27:50Z — CODE_REVIEW2 P3.1 + P4.1 (commits 8513253, next)

### P3.1 — Explicit UTF-8 encoding (commit `8513253`)
- Fixed 8 bare `open()`/`read_text()`/`write_text()` calls across `adapters.py`, `config.py`, `commands/doctor.py`
- `grep` for bare encoding calls now clean

### P4.1 — Regression fixture infra
- Created `tests/fixtures/valid/minimal.kicad_sch` and `tests/fixtures/valid/minimal.kicad_pcb`
- Created `tests/unit/test_p41_regression_fixtures.py` (43 tests, 6 classes):
  - `TestValidFixtureDirectory` — valid/ dir + both files exist (3 tests)
  - `TestValidSchematicParseLint` — parse/lint/validate on valid+golden+working fixtures (9 tests)
  - `TestValidPcbParseLint` — PCB equivalents (5 tests)
  - `TestBrokenFixturesParsing` — all 4 broken fixtures parse OK (semantic bugs, not syntactic) (16 tests)
  - `TestBrokenFixtureBugPatterns` — text-level regression anchors (4 tests)
  - `TestErrorMessageContext` — ParseError carries path, wrong root → syntax_ok=False, etc. (5 tests)
- Key finding: all 4 broken fixtures (`bug1`–`bug4`) are syntactically valid S-expressions; bugs are semantic (sub-symbol names, old `id` format, paren indentation, missing `instances` block), only kicad-cli would flag them
- Tests confirmed: `syntax_ok=True` for broken fixtures; regression anchors use text matching
- All tests pass (43 passed); lint clean after moving `cmd_validate_pcb` to top-level import

### Pending CODE_REVIEW2 items:
- P2.2: Preserve numeric lexemes (optional)
- P3.2: Richer exception hierarchy
- P5.1: Structured logging
- P5.2: Dry-run diff output

---

## 2026-02-25T22:46:30Z — Phase 10.2: CI test pipeline (commits be210fe, 7ef713a)

### Summary
Added GitHub Actions CI. Tidy commit first (ruff format applied to 58 files,
no logic changes), then feature commit with two workflow files and pyproject.toml
mypy addition.

### New: `.github/workflows/ci.yml`
- Trigger: push/PR to `main` or `master`
- Steps: ruff check → ruff format --check → mypy → pytest tests/unit/ --cov
- Coverage: uses `[tool.coverage.run] source` + `fail_under=70` from pyproject.toml
- Artifacts: uploads `coverage.xml` (14-day retention)

### New: `.github/workflows/integration.yml`
- Trigger: nightly cron `0 3 * * *` + manual `workflow_dispatch`
- Installs KiCad from `ppa:kicad/kicad-9-releases`
- Steps: install kicad → pytest tests/integration/ -m requires_kicad
- Artifacts: uploads `integration-results.xml` + `/tmp/pytest-*/` on failure (7-day)

### Modified: `pyproject.toml`
- Added `mypy>=1.10` to `[project.optional-dependencies] dev`
  (was being used in the project but not declared as a dependency)

### Tidy commit: `be210fe`
- `ruff format .` applied to 58 files (formatting-only, no logic changes)
- All subsequent ruff format --check runs pass cleanly

### Notes
- `ruff check` and `ruff format --check` now both enforced in CI
- `fail_under=70` already in pyproject.toml; pytest-cov reads it automatically
- KiCad install step uses `--no-install-recommends` to reduce image bloat

---

## 2026-02-25T22:27:33Z — Phase 9.3: Preflight semantic checks (commit 9958042)

### Summary
Adds `kicad_pcb.preflight` module with 7 public functions that run before any
document mutation to surface common mistakes early. Wired into all 4 pattern
functions. 78 new unit tests. Total: 912 unit tests, 0 skipped.

### New: `kicad-pcb/src/kicad_pcb/preflight.py`
- `collect_existing_refs(doc)` → `frozenset[str]`: scan placed symbols for refs
- `collect_existing_net_names(doc)` → `frozenset[str]`: scan labels for net names
- `check_no_duplicate_refs(requested, existing)`: raise UserError on ref collision
- `check_refs_unique_in_request(requested)`: raise UserError on duplicate within call
- `check_net_names_valid(net_names)`: raise UserError for empty or
  forbidden-char names. **Digit-start names (3V3, +5V, 1V8) are ALLOWED.**
- `check_symbol_accessible(lib_sym, *, symbols_dir)`: raise UserError when
  library is available but symbol is not found. Skipped when `symbols_dir=None`.
- `check_footprints_assigned(refs_and_footprints, *, require=False)`: raise
  UserError when `require=True` and any footprint is empty.

### Modified: `patterns.py`
- All 4 pattern functions call preflight checks at the top (before mutation)
- Added `require_footprints: bool = False` kwarg to each pattern function
- `pattern_connector_breakout` also validates `net_prefix` is not empty
  (separate from net-name validation since empty prefix generates digit-only names)
- Added `from .errors import UserError` import

### Modified: `commands/patterns.py`
- `_dispatch_pattern` gains `require_footprints: bool = False` kwarg
- Each pattern branch passes `require_footprints=require_footprints`
- `cmd_apply_pattern` extracts `require_footprints` from args

### Modified: `cli.py`
- `--require-footprints` flag added to `apply-pattern` subparser

### Modified: `__init__.py`
- All 7 preflight symbols exported in both imports and `__all__`

### Tests: `tests/unit/test_preflight.py` (78 tests)
- `TestCollectExistingRefs` / `TestCollectExistingNetNames`: introspection
- `TestCheckNoDuplicateRefs` / `TestCheckRefsUniqueInRequest`: ref checks
- `TestCheckNetNamesValid`: covers valid cases, empty, whitespace, comma,
  semicolon, quote, paren, and the digit-start-ALLOWED case
- `TestCheckSymbolAccessible`: None skips, /nonexistent raises, real lib tests
  (skipped if KiCad not installed)
- `TestCheckFootprintsAssigned`: require=False noop, require=True fail/pass
- `TestPreflightIntegration*`: 4 pattern classes testing early-error before mutation
- `TestNoMutationOnPreflightFailure`: doc untouched when preflight fails

### Updated: `tests/unit/test_patterns.py`
- `test_fallback_pins_when_no_library`: now calls pattern with `symbols_dir=None`
  (the correct offline mode). Explicit invalid path now raises UserError (correct).

### Key design decisions
- Net names starting with digits are ALLOWED (3V3, +5V are real KiCad nets)
- `check_symbol_accessible` is only run if `symbols_dir` is not None, preserving
  offline/test usage via `symbols_dir=None` default
- Empty `net_prefix` in connector_breakout is caught separately with a clear
  UserError message (not via check_net_names_valid)

---

## 2026-02-25T21:47:08Z — Phase 9.2: Validated circuit pattern library (commit d59a980)

### Summary
Adds a small library of known-good KiCad schematic patterns that emit validated
SchematicDoc mutations through the IR pipeline. Four patterns, one `apply-pattern`
CLI command, 80 new unit tests. Total test count: 833 unit tests (753 + 80), 0 skipped.

### Phase 9.1 skipped
User explicitly requested 9.1 be skipped for now; jumped straight to 9.2.

### New: `kicad-pcb/src/kicad_pcb/patterns.py`
Four pattern functions, each taking a `SchematicDoc` + origin coords + keyword args:
- `pattern_resistor_divider`: two `Device:R` in series, VIN/VOUT/GND labels
- `pattern_led_resistor`: `Device:R` + `Device:LED`, VCC/GND labels
- `pattern_connector_breakout`: `Connector_Generic:Conn_01x{n:02d}`, IO<n> labels
  - **Important**: uses `Connector_Generic` library, NOT `Device` (connectors are not in Device.kicad_sym)
- `pattern_decoupling_cap`: `Device:C`, VCC/GND labels
- `PATTERNS` registry dict maps CLI names to callables
- Private `_place_component` helper: calls `read_lib_symbol_pins`/`read_lib_symbol_def`;
  falls back to `["1","2"]` pins when library unavailable; embeds lib def if found

### New: `kicad-pcb/src/kicad_pcb/commands/patterns.py`
- `cmd_apply_pattern(args)` — dispatches to pattern via PATTERNS registry
- Calls `discover_symbols_dir(explicit=path)` then `mutate_and_validate_sch`
- `_dispatch_pattern` private helper: passes all kwargs explicitly (not **kw spread,
  which caused mypy to lose type precision on `symbols_dir: Path | None`)

### Modified: results.py, formatting.py, __init__.py, cli.py
- `ApplyPatternResult` frozen dataclass: pattern, components, nets, dry_run=False
- `apply-pattern` subparser with --pattern, --r1/--r2/--r-value/--d/etc, --dry-run, --symbols-dir
- PLR0913 noqa on pattern function defs (many kw-only args by design)

### Tests: `tests/unit/test_patterns.py` (80 tests)
Key design decisions:
1. `_no_sym_library` autouse fixture: patches `_sch_doc._DEFAULT_SYMBOLS_DIR` to
   `/nonexistent` for all tests, making pure pattern tests fast (fallback ["1","2"])
2. `TestCmdApplyPattern` OVERRIDES `_no_sym_library` as a no-op: cmd tests need
   `discover_symbols_dir` to find the real `/usr/share/kicad/symbols` so placed
   symbols get embedded in lib_symbols and pass SCH009 lint validation
3. `test_no_project_raises_user_error`: mocks `get_current_project` via monkeypatch
   instead of calling `set_current_project(None)` (which doesn't accept None)

### Performance insight
`Device.kicad_sym` (2.2MB) and `Connector_Generic.kicad_sym` (3.7MB) take ~10-40s
to parse per cmd test when real library is used. Pure pattern tests are fast (<0.1s)
because the `/nonexistent` redirect returns [] immediately.

### Lint/type status
- ruff: clean (PLR0913 noqa on all pattern defs, noqa on _dispatch_pattern)
- mypy: clean (explicit kwarg passing avoids dict[str, str|Path|None] spread issue)
- pytest: 833 unit tests, 0 skipped

## 2026-02-25T20:29:42Z — Phase 8.3: Runtime environment resolution (commit b275e14)

### Summary
Eliminates the import-time `shutil.which("kicad-cli")` freeze. All path
resolution now happens lazily at call time.

### Changed: `runner.py`
- Added `find_kicad_cli() -> str` — calls `shutil.which("kicad-cli")` at
  invocation time; falls back to bare `"kicad-cli"` (OS resolves at
  subprocess-spawn time) instead of the old hard-coded `/usr/bin/kicad-cli`
- `run_kicad_cli()` now calls `find_kicad_cli()` on each invocation
- Removed module-level `_runner = SubprocessRunner()` singleton (replaced with
  local instantiation inside `run_kicad_cli()`)
- `KICAD_CLI` retained as a backward-compat str (frozen at import time to
  `find_kicad_cli()` result; new code should call `find_kicad_cli()` directly)

### Changed: `commands/validation.py`, `commands/export.py`,
  `commands/preview.py`, `commands/pcb.py`
- Import changed from `from ..runner import KICAD_CLI, check_kicad` to
  `from ..runner import check_kicad, find_kicad_cli`
- All `KicadCliAdapter(kicad_cli=KICAD_CLI)` replaced with
  `KicadCliAdapter(kicad_cli=find_kicad_cli())` — path resolved inside function
  body at call time, not at module-import time

### Changed: `__init__.py`
- `find_kicad_cli` added to imports and `__all__`

### Tests
- `tests/unit/test_env_resolution.py` — 22 new tests covering:
  - Core laziness (shutil.which patch after import, no freeze, no caching)
  - Fallback to bare "kicad-cli" when not on PATH
  - run_kicad_cli argv uses find_kicad_cli() at invocation time
  - KICAD_CLI backward-compat constant is a str, not /usr/bin/kicad-cli
  - Source-level assertions that command modules contain no KICAD_CLI freeze
  - Adapter injection tests
- Total: 752 unit tests passing, 1 skipped

---

## 2026-02-25T20:02:31Z — Phase 8.2: Symbol library path discovery (commit 00c4689)

### Summary
Replaces hardcoded `/usr/share/kicad/symbols` with a runtime discovery chain.

### New: `config.py` additions
- `SYMBOLS_CANDIDATES` — 7 platform paths (Linux system, local, user, Flatpak 7/8/9, macOS)
- `SymbolsDir(path, source)` — frozen dataclass, `source` describes how path was found
- `discover_symbols_dir(*, explicit=None)` — priority: explicit > `KICAD_SYMBOLS_DIR` env > `config.json` `symbols_dir` key > platform candidates; returns `SymbolsDir | None`
- `get_symbols_dir_config()` / `set_symbols_dir_config(path)` — read/write config.json key

### Modified: `sch.py`
- `cmd_add_component` calls `discover_symbols_dir(explicit=args.symbols_dir)` at call time
- `KICAD_SYMBOLS_DIR` constant retained as fallback for backward compat

### Modified: `cli.py`
- `add-component` subcommand gains `--symbols-dir PATH` option

### Modified: `doctor.py`
- Symbols check uses `discover_symbols_dir()` instead of hardcoded path
- Detail shows discovery source in brackets, e.g. `3 libs  [env:KICAD_SYMBOLS_DIR]`
- Error message when nothing found: "set KICAD_SYMBOLS_DIR or symbols_dir in config"

### Tests
- `tests/unit/test_symbols_discovery.py` — 28 new tests covering all priority levels
- Total: 731 unit tests passing

---

## 2026-02-25T19:48:06Z — Phase 8.1: KiCad CLI version compatibility layer (commit ca50af3)

### Summary
Implements runtime version detection and capability gating for kicad-cli.

### New: `kicad-pcb/src/kicad_pcb/compat.py`
- `KiCadVersion(major, minor, patch)` — frozen/ordered dataclass, free comparison ops
- `MINIMUM_VERSION = KiCadVersion(7, 0, 0)` — kicad-cli 7.0 was the first with structured JSON DRC/ERC output
- `parse_version(s)` — extracts first X.Y.Z triple via regex from any string; raises `ValueError` if not found
- `CliCapability(StrEnum)` — 13 members documenting command/option availability
- `CAPABILITY_MAP` — most capabilities at 7.0.0; `PCB_EXPORT_STEP_NO_UNSPECIFIED` + `PCB_EXPORT_GLB` at 8.0.0
- `require_capability(version, cap)` — raises `ToolError` with download URL if version is too old

### Modified: `adapters.py`
- `KicadCliAdapter.__init__` gains `version: KiCadVersion | None = None` for test injection
- `detected_version` lazy property (uses `contextlib.suppress` so FakeRunner → None → tests unchanged)
- `require_capability(cap)` delegates to `require_capability` from compat
- `export_step` and `export_glb` now gate on `PCB_EXPORT_STEP_NO_UNSPECIFIED` / `PCB_EXPORT_GLB` (min 8.0)

### Modified: `doctor.py`
- Shows `[supported (>= 7.0.0)]` or `[UNSUPPORTED — minimum required: 7.0.0]` in kicad-cli check
- `overall_ok=False` if version below minimum

### Modified: `__init__.py`
- All new symbols exported in `__all__`

### Tests
- `tests/unit/test_compat.py` — 42 new tests covering all public API
- Total: 703 unit tests passing

---

## 2026-02-25T17:50:37Z — Phase 6: CLI and Skill UX Improvements (commit 099266e)

### Summary
Phase 6 adds `--dry-run`, `--json`, structured lint error display, and 6 new standalone file commands.

### Changes
- **`--dry-run`** on `add-component`, `add-net`, `connect`, `set-board-size`, `auto-place`: pipeline (`mutate_and_validate_sch`/`mutate_and_validate_pcb`) now accepts `dry_run=True` which skips `_atomic_write`; 5 result types carry `dry_run: bool = False`
- **`--json`** global flag on root parser: calls `format_result_json()` (dataclasses.asdict + `_ResultEncoder` for Path/Enum)
- **`LINT_SUGGESTIONS: dict[str, str]`** in `lint.py` — 19 entries (SCH001-9, PCB001-9) — shown in CLI error output and formatters
- **3 new result types** in `results.py`: `LintFileResult`, `ValidateFileResult`, `FormatFileResult`
- **New module `commands/lint.py`**: `cmd_lint_sch`, `cmd_lint_pcb`, `cmd_validate_sch`, `cmd_validate_pcb`, `cmd_format_sch`, `cmd_format_pcb` — standalone, no project context required
- **`formatting.py`**: 3 new formatters + dry_run prefix on 5 existing formatters + `format_result_json()`
- **`cli.py`**: `--json`, `--dry-run` on 5 parsers, 6 new subcommands, structured `LintError` display (code + suggestion), exit non-zero for lint/validate failures
- **46 new tests** in `tests/unit/test_phase6.py`; **584 total tests**, all passing

### Key design decisions
- `dry_run` in pipeline: guard around `_atomic_write` only; all validation still runs
- `format_result_json`: uses `dataclasses.asdict` + custom encoder; raises `TypeError` for unhandled types (no silent fallback)
- Exit codes: `LintFileResult.ok==False` or `ValidateFileResult.ok==False` → `sys.exit(1)`
- `commands/lint.py` helpers: `_parse_or_raise()` returns `None` on parse failure (callers decide to raise or return `syntax_ok=False`)

---

## 2026-02-25T17:29:28Z — Phase 5: Lint framework & validation pipeline (commit 5e24bd7)

### New modules
- **`kicad_pcb.lint`** (614 lines): 18 structural rules — SCH001–SCH009 + PCB001–PCB009
  - `LintSeverity(Enum)`: ERROR / WARNING
  - `LintIssue(frozen dataclass)`: severity, code, message, path
  - `LintError(KiCadError)`: carries `issues: list[LintIssue]`
  - `lint_schematic(root: ListNode) → list[LintIssue]`
  - `lint_pcb(root: ListNode) → list[LintIssue]`
  - Key design: `lint.LintIssue` is NOT re-exported from `__init__.py` (name conflict with `models.LintIssue`)
- **`kicad_pcb.pipeline`** (324 lines): transactional mutate-and-validate
  - `ValidationMode(IntEnum)`: NONE=0 / SYNTAX=1 / LINT=2 / KICAD=3 / FULL=4 — IntEnum enables `>=` comparisons
  - `ValidationMode.default()` → LINT
  - `mutate_and_validate_sch(path, mutator, *, mode=LINT, cli=None, backup=False, operation=None, strict=False)`
  - `mutate_and_validate_pcb(path, mutator, *, mode=LINT, cli=None, backup=False, operation=None, strict=False)`
  - Pipeline: load doc → mutator(doc) → serialize → (SYNTAX) _syntax_check → (LINT) lint_xxx → (KICAD) CLI → _atomic_write
  - `strict=True` or `mode >= FULL` treats warnings as errors

### Commands wired
- `commands/sch.py`: cmd_add_component, cmd_add_net, cmd_connect — all use `mutate_and_validate_sch`
- `commands/pcb.py`: cmd_set_board_size, cmd_auto_place — use `mutate_and_validate_pcb`

### Exports added to `kicad_pcb/__init__.py`
- `LintError`, `LintSeverity`, `lint_schematic`, `lint_pcb`
- `ValidationMode`, `mutate_and_validate_sch`, `mutate_and_validate_pcb`

### Tests: 538 total (89 new)
- `tests/unit/test_lint.py` — 14 test classes, all 18 rules tested
- `tests/unit/test_pipeline.py` — ValidationMode ordering, backup, strict, mode gating, roundtrip

### Key design decisions
- `lint.LintIssue` NOT re-exported at top-level (name clash with models.LintIssue); use `from kicad_pcb.lint import LintIssue`
- PCB005 (no Edge.Cuts) fires as WARNING, not ERROR: valid for mid-edit states
- `_atomic_write` always checks root node regardless of ValidationMode; NONE mode skips pipeline's extra checks
- `ValidationMode.FULL` implies `strict=True`; LINT is the safe default (no KiCad CLI required)

---

## 2026-02-25T08:52:49Z — Phase 4: KiCad document wrappers (AST-based editing)

- Added `kicad-pcb/src/kicad_pcb/sexpr/builder.py`:
  - `atom(value)→AtomNode`, `string(value)→StringNode`, `L(*items)→ListNode`, `fnum(f, decimals=3)→AtomNode`
  - All nodes use `NO_POS` sentinel; thin wrappers for programmatic AST tree building
- Added `kicad-pcb/src/kicad_pcb/sch_doc.py` — `SchematicDoc` wrapper for `.kicad_sch`:
  - `load(path)`, `save(path, *, backup=False)`
  - `ensure_lib_symbols_section()`, `embed_lib_symbol(sym_def_node)`
  - `add_symbol(lib_sym, ref, value, footprint, x, y, sym_uuid, pin_nums, pin_uuids, project_name)`
  - `add_wire(x1, y1, x2, y2, wire_uuid)`, `add_label(name, x, y, label_uuid)`
  - `next_component_position() → (x, y)` — scans existing symbols to compute next slot
  - Public helpers: `read_lib_symbol_def(lib, sym, *, symbols_dir)`, `read_lib_symbol_pins(lib, sym, *, symbols_dir)`
  - AST emitters: `make_symbol_node(...)`, `make_wire_node(...)`, `make_label_node(...)`
  - `_DEFAULT_SYMBOLS_DIR = Path("/usr/share/kicad/symbols")` — avoids circular import with `commands/sch.py`
- Added `kicad-pcb/src/kicad_pcb/pcb_doc.py` — `PcbDoc` wrapper for `.kicad_pcb`:
  - `load(path)`, `save(path, *, backup=False)`
  - `clear_generated_outline()`, `set_rect_outline(width, height)`
  - `find_footprint_by_ref(ref)`, `all_footprints() → list[tuple[str, ListNode]]`, `move_footprint(ref, x, y)`
  - `all_footprints()` falls back to footprint lib name when `Reference` property missing
  - `_update_footprint_at` preserves rotation (extra items after x,y in `at` node)
  - AST emitter: `make_gr_line_node(sx, sy, ex, ey, uuid, *, layer, width)`
- Refactored `commands/sch.py`: removed all 6 regex helpers; rewritten with `SchematicDoc.load()` → mutate → `.save()`
- Refactored `commands/pcb.py`: `cmd_set_board_size` and `cmd_auto_place` rewritten with `PcbDoc`
- Updated `kicad_pcb/__init__.py` and `kicad_pcb/sexpr/__init__.py` to export all new Phase 4 symbols
- Added 96 new tests: `test_sch_doc.py` (56) + `test_pcb_doc.py` (40); total suite: 449 pass
- Committed `f1c4527` — 449/449 tests pass; ruff 0; mypy 0 errors in 3 source files

---

## 2026-02-25T08:14:46Z — Phase 3: S-expression parsing/serialization

- Added `kicad-pcb/src/kicad_pcb/sexpr/` sub-package (6 modules):
  - `nodes.py`: `Position`, `NO_POS`, `AtomNode`, `StringNode`, `ListNode`, `Node` — all `@dataclass(frozen=True)`; `ListNode.key` / `ListNode.head` convenience properties
  - `tokenizer.py`: `Token(kind, value, line, col)`; `tokenize(src) -> list[Token]`; handles parens, atoms, strings with escape sequences, whitespace, `;` line comments; raises `ParseError` on unterminated strings
  - `parser.py`: `parse(src) -> ListNode`; `parse_file(path) -> ListNode`; discards comments; validates single top-level list, balanced nesting, no trailing content
  - `serializer.py`: `serialize(node, indent) -> str`; `serialize_file(path, node)`; inline if ≤80 chars at current indent, else block-indented (2 spaces); `_escape_string()` helper; round-trip stable
  - `utils.py`: `walk(node)` depth-first iterator; `find_first(root, key)`, `find_all(root, key)` — direct children only; `replace_section(root, key, new)` — replaces or appends; `append_to_section(root, key, item)` — raises `KeyError` if section missing; `node_path(root, *keys)` → dot-notation string
  - `__init__.py`: re-exports all 20 public symbols
- Updated main `__init__.py` and `__all__` with all 20 new sexpr symbols
- 159 new tests: `test_sexpr_tokenizer.py`, `test_sexpr_parser.py`, `test_sexpr_serializer.py`, `test_sexpr_utils.py`
- Committed `f658eb2` — 361/361 tests pass; ruff 0; mypy 0 errors in 25 files

---

## 2026-02-25T07:55:42Z — Phase 2.4: separate CLI presentation from business logic

- Added `kicad-pcb/src/kicad_pcb/results.py` — 22 `@dataclass(frozen=True)` result types:
  - project: `NewProjectResult`, `InfoResult`, `OpenResult`
  - validation: `DrcResult`, `ErcResult`
  - export: `ExportGerbersResult`, `ExportDrillResult`, `ExportBomResult`, `PackageFabResult`, `ExportPosResult`, `Export3dResult`
  - preview: `PreviewSchematicResult`, `PreviewPcbResult`
  - pcb: `SetBoardSizeResult`, `ImportNetlistResult`, `AutoPlaceResult`, `AutoRouteResult`
  - sch: `AddComponentResult`, `AddNetResult`, `ConnectResult`
  - doctor: `DoctorCheckItem`, `DoctorResult`
  - external: `PcbwayQuoteResult`
- Added `kicad-pcb/src/kicad_pcb/formatting.py` — formatter registry pattern:
  - `_FORMATTERS: dict[type, callable]` + `@_register(cls)` decorator
  - `format_result(result) -> list[str]` — dispatches by type; unknown types return `[repr(result)]`
  - 22 `_fmt_*` functions, one per result type
- Updated all 8 command modules: zero `print()` calls; all commands return typed results
  - Silent print+return failure paths converted to `raise UserError/ToolError`
  - `cmd_doctor` changed from `raise UserError` at end → returns `DoctorResult(overall_ok, checks=tuple)`
- Updated `cli.py` dispatcher: `result = args.func(args); for line in format_result(result): print(line)`;
  exit-code guard: `if isinstance(result, DoctorResult) and not result.overall_ok: sys.exit(1)`
- Updated `__init__.py`: exports all 23 result types + `format_result`
- Added 52 new tests in `tests/unit/test_presentation.py`
- Updated existing `TestCmdDoctor` in `test_phase1_reliability.py` to check `DoctorResult` shape
- Committed `1ec2d8c` — 202/202 tests pass; ruff 0; mypy 0 errors in 19 files

---

## 2026-02-25T07:03:36Z — Phase 2.3: injectable adapters

- Added `kicad-pcb/src/kicad_pcb/adapters.py` with:
  - `RunResult(returncode, stdout, stderr)` — frozen dataclass replacing `CompletedProcess`; `.ok` property, `.output_text()` helper
  - `RunnerProtocol` — `@runtime_checkable` Protocol with `run(cmd, *, capture) -> RunResult`
  - `SubprocessRunner` — real implementation delegating to `subprocess.run`
  - `FakeRunner` — configurable test fake; maps `"pcb drc"`/`"sch erc"`/etc. keys → `RunResult`; records calls in `.calls`
  - `FsProtocol` — `@runtime_checkable` Protocol with `read_text/write_text/exists/mkdir/glob/iterdir/unlink/stat_size`
  - `RealFs` — thin delegate to `pathlib.Path`
  - `FakeFs` — in-memory filesystem; pre-loaded from `files` dict + `dirs` set; supports `glob` via `fnmatch`
  - `KicadCliAdapter` — typed wrapper for all kicad-cli sub-commands; injected `runner` + `fs`; methods: `version`, `drc`, `erc`, `export_gerbers`, `export_drill`, `export_bom`, `export_netlist`, `export_pos`, `export_step`, `export_svg_sch`, `export_svg_pcb`, `export_glb`, `export_specctra_dsn`, `import_specctra_ses`
- Updated `runner.py`: `run_kicad_cli()` now returns `RunResult` (via `SubprocessRunner`); old `subprocess.CompletedProcess[str]` removed; backward-compat preserved (all callers unchanged)
- Updated 5 command modules to accept optional injected `KicadCliAdapter`:
  - `commands/validation.py`: `cmd_drc(args, *, cli=None)`, `cmd_erc(args, *, cli=None)`
  - `commands/export.py`: all 5 kicad-cli-using commands accept `cli=None` kwarg
  - `commands/preview.py`: `cmd_preview_schematic`, `cmd_preview_pcb` accept `cli=None`
  - `commands/pcb.py`: `cmd_import_netlist`, `cmd_auto_route` accept `cli=None`
  - `commands/doctor.py`: `cmd_doctor(args, *, runner=None)` — injects `RunnerProtocol` for `kicad-cli --version` + `java -version` subprocess calls; `subprocess` import removed
- Updated `__init__.py` to export all 8 adapter symbols
- 70 new unit tests in `tests/unit/test_adapters.py`
- Committed `bf50288` — 150/150 tests pass; ruff 0; mypy 0 errors in 17 files

---

## 2026-02-25T06:36:44Z — Phase 2.2: typed domain models

- Added `kicad-pcb/src/kicad_pcb/models.py` with 8 frozen dataclasses:
  - `ProjectRef` — typed project reference (replaces raw dict); file-path properties `.sch_file`, `.pcb_file`, `.pro_file`; `from_dict`/`to_dict` for JSON compat
  - `ComponentSpec` — lib_sym/ref/value/footprint; `.lib_name`/`.sym_name` properties; `from_args()` factory
  - `WireSegment` — x1/y1/x2/y2 coordinates; `from_args()` factory parses `--from X,Y --to X,Y`
  - `NetLabelSpec` — name/x/y; `from_args()` factory with coordinate defaults (50.8)
  - `BoardOutlineRect` — width/height; `from_args()` factory parses `WxH`; `.corners` property
  - `FootprintMoveSpec` — ref/x/y for auto-place results
  - `LintIssue` — severity/description; `from_dict()` factory for DRC/ERC JSON entries
  - `ValidationResult` — passed/issues; `from_report()` factory; `.error_count`/`.warning_count`
- `config.get_current_project()` now returns `ProjectRef | None` (was `dict | None`)
- `config.set_current_project()` accepts `ProjectRef` (calls `.to_dict()` before JSON write)
- All 10 command modules updated: `project["name"]`/`project["path"]` → `project.name`/`project.path`/`project.sch_file`/`project.pcb_file`; `Path(project["path"])` constructors removed
- `validation.cmd_drc` uses `ValidationResult.from_report()` to display typed issues
- 31 new unit tests in `tests/unit/test_models.py`
- Committed `44030f8` — 80/80 tests pass; ruff 0; mypy 0 errors in 16 files

## 2026-02-25T06:05:33Z — Phase 2.1: module split (Tidy First)

- Split the 1553-line monolithic `kicad-pcb/scripts/kicad_pcb.py` into a proper
  Python package at `kicad-pcb/src/kicad_pcb/` (src-layout).
- Package structure:
  - `errors.py` — exception hierarchy
  - `config.py` — constants + config R/W
  - `runner.py` — KICAD_CLI, check_kicad, run_kicad_cli
  - `fs.py` — _check_sexp, _atomic_write, _new_uuid
  - `commands/project.py`, `validation.py`, `export.py`, `preview.py`, `sch.py`, `pcb.py`, `external.py`, `doctor.py`
  - `cli.py` — main() with argparse
  - `__init__.py` — re-exports all public symbols for backward compat
- `kicad-pcb/scripts/kicad_pcb.py` → 17-line thin wrapper (adds src/ to sys.path, calls main())
- `pyproject.toml`: pythonpath changed from `kicad-pcb/scripts` to `kicad-pcb/src`; coverage source updated; `[tool.setuptools.packages.find]` added
- 49/49 tests pass; ruff 0 violations; mypy 0 errors in 15 files
- Committed `01809c6` — "tidy: Phase 2.1 — split monolithic kicad_pcb.py into src/kicad_pcb/ package"

---

## 2026-02-25T05:03:29Z — Lint clean pass (ruff + mypy)

- ruff auto-fixed 53 violations (F401, F541, UP006, UP045, I001)
- Manual fixed 33 remaining violations:
  - PTH123 (8x): `open()` → `Path.open()` throughout kicad_pcb.py
  - PLW1510 (6x): added `check=False` to all `subprocess.run` calls
  - PLC0415 (1x): `import cairosvg` noqa (intentional optional dep)
  - PTH105/PTH108/SIM105: refactored `_atomic_write` cleanup to use `Path.replace/unlink` + `contextlib.suppress`
  - PLR0912 (2x): `#noqa` on `cmd_auto_route` and `cmd_doctor`
  - PLR0915 (1x): `#noqa` on `def main()`
  - E501 (4x): wrapped long lines in cmd_doctor print, p_conn.add_argument, p_nl subparser, Freerouting subprocess args
  - Tests: moved inner imports (`sys`, `os`, `re`) to top level; combined nested `with` (SIM117×2); replaced try/except/pass with `contextlib.suppress` (SIM105)
- mypy: clean pass (exit 0, no errors); 3 annotation-unchecked notes (expected)
- All 45 tests pass
- Committed as `b7645fc` — "tidy: ruff + mypy clean pass (PTH, SIM, PLW, PLC, E501)"

---

## Project Identity

- **Repo**: `/home/ubo/work/openclaw_kicad_pcb` (GitHub repo)
- **Skill symlink**: `/home/ubo/.openclaw/skills/kicad_pcb` → `/home/ubo/work/openclaw_kicad_pcb/kicad-pcb/`
- **Script** (thin wrapper): `kicad-pcb/scripts/kicad_pcb.py` (17 lines — delegates to package)
- **Package** (src-layout): `kicad-pcb/src/kicad_pcb/` (15 modules)
- **KiCad projects dir**: `/home/ubo/kicad-projects/`
- **KiCad CLI**: `/usr/bin/kicad-cli` v9.0.7
- **KiCad symbol libraries**: `/usr/share/kicad/symbols/*.kicad_sym`, format version `20211014`
- **Python env**: conda base

---

## 2026-02-25T03:15:00Z — Architecture Decisions

### 1. KiCad file parser
- **Choice**: `kiutils` (KiCad-specific parser, PyPI: `kiutils`)
- **Why**: KiCad-aware (not just generic S-expr), supports `.kicad_sch` and `.kicad_pcb`, SCM-friendly diffs
- **License**: GPLv3 — noted and accepted for this project
- **Status**: Not yet installed (TODO: `pip install kiutils`)

### 2. "No regex" scope
- **Decision**: Replace ALL regex-based structural edits of KiCad files — both reads AND mutations
- `_find_symbol_def`, `_find_symbol_pins`, `_embed_lib_symbol` etc. all to be replaced with kiutils-based parsing
- Regex is still fine for non-KiCad-file concerns (e.g., version string extraction, config parsing)

### 3. Typed domain models
- **Choice**: Pydantic v2
- Used for `ProjectRef`, `ComponentSpec`, `NetSpec`, `ValidationResult`, `LintResult`, etc.

### 4. Test framework
- **Framework**: pytest
- **Structure**: `tests/unit/`, `tests/integration/`, `tests/fixtures/`
- Integration tests skip if `kicad-cli` not available (via `pytest.mark.requires_kicad`)

### 5. Version control
- Repo is at `/home/ubo/work/openclaw_kicad_pcb`
- All skill code lives under `kicad-pcb/` in that repo
- Commit discipline: small, frequent, conventional commit messages (`tidy:`, `feat:`, `fix:`)

### 6. Phase order
- Phase 0 (baseline+fixtures) → Phase 1 (reliability fixes) → Phase 2 (testability refactor)
- Phase 3 (kiutils-based AST parsing) → Phase 4 (doc wrappers) → Phase 5 (validation pipeline)

---

## Bugs Fixed (prior sessions)

All four bugs were in `kicad_pcb.py` and caused `kicad-cli` to reject generated schematics:

| # | Location | Bug | Fix |
|---|----------|-----|-----|
| 1 | `_find_symbol_def` | All sub-symbols renamed `"R_0_1"` → `"Device:R_0_1"` | Added `count=1` to root symbol rename regex |
| 2 | `_find_symbol_def` | Old `(id N)` property format (v20211014 lib) rejected by kicad-cli 9 | `re.sub(r'\s*\(id \d+\)', '', block)` |
| 3 | `_embed_lib_symbol` | `(lib_symbols)` closed with `)` at col 0, collapsing `(kicad_sch)` | Fixed indentation: content=4sp, close=`"  )"` |
| 4 | `cmd_add_component` | Missing `(instances ...)` block → kicad-cli shows `<components/>` empty | Added full `(instances (project ...))` block |

---

## Verification (post-fix)

```
python3 kicad_pcb.py new SmokeTest
python3 kicad_pcb.py add-component Device:R R1 --value 10k
python3 kicad_pcb.py add-component Device:C C1 --value 100nF
kicad-cli sch export netlist --format kicadsexpr ...  → exit:0
python3 kicad_pcb.py export-bom  → 2 component lines: R1 (10k), C1 (100nF)
```

---

## Known Environment

- `pcbnew` Python module: NOT available standalone
- Freerouting: not installed (auto-route gracefully stubs)
- OpenClaw binary: `~/.nvm/versions/node/v24.13.0/bin/openclaw`
- Discord user ID: `685037232998187065`

---

## 2026-02-25T04:24:21Z — Phase 0 Baseline Complete

### Python environment
- **Python**: 3.11.2 (system package)
- **Venv**: `/home/ubo/work/openclaw_kicad_pcb/.venv` (Python 3.11)
- Historical note: this reflects initial Phase 0 setup; later sessions reuse the existing environment.
- Packages installed: kiutils 1.4.8, pydantic 2.12.5, pytest 9.0.2, pytest-cov 7.0.0, ruff 0.15.2
- Latest available kiutils is **1.4.8** (not 1.5+ which doesn't exist yet)

### Key discovery: kicad-cli is Flatpak
- `/home/ubo/.local/bin/kicad-cli` is a Flatpak wrapper: `exec flatpak run --command=kicad-cli org.kicad.KiCad "$@"`
- Flatpak sandbox: kicad-cli can **only access paths under `~`** (user's home)
- pytest's `tmp_path` = `/tmp/pytest-*` is **outside Flatpak sandbox** → kicad-cli exits 3 "Schematic file does not exist or is not accessible"
- Fix: `home_tmp` fixture creates temp dirs under `~/tmp/kicad-tests/{uuid}/` — accessible to Flatpak
- Real `/usr/bin/kicad-cli` does NOT exist; only the Flatpak wrapper at `/home/ubo/.local/bin/kicad-cli`

### Test results (Phase 0)
- 23 tests: 15 unit + 8 integration — all pass
- Commit: `b78e3a5` — "feat: Phase 0 baseline — Python 3.11 venv, pytest scaffold, regression fixtures"

---


High-priority next phases:
1. **Phase 0 — Baseline fixtures** ✅ (captured in `tests/fixtures/`)
2. **Phase 1 — Reliability** ✅ (see below)
3. **Phase 2 — Testability**: module split, Pydantic models, injectable adapters  
4. **Phase 3 — kiutils parser**: replace all regex/string S-expr manipulation ✅
5. **Phase 4 — Doc wrappers**: `SchematicDoc`/`PcbDoc` AST editing API ✅
6. **Phase 5 — Validation pipeline**: lint → validate → transactional write

---

## 2026-02-25T04:48:28Z — Phase 1 Complete (commit 3f09549)

### Changes made to `kicad-pcb/scripts/kicad_pcb.py`

**1.1 SKILL.md doc fixes**
- `connect` command: corrected syntax from `connect <ref1.pin> <ref2.pin>` → `connect --from X,Y --to X,Y`
- `add-net` command: corrected syntax to `add-net NAME [--x X] [--y Y]`
- Removed duplicate `pcbway-quote` table row
- Removed `--layers` flag from `preview-pcb` example (not implemented)
- Replaced non-existent template section with a "no built-in templates" note
- Fixed Python deps: removed non-used `pillow`, marked `cairosvg` as optional
- Added `doctor` to Project Management command table

**1.2 Added `cmd_doctor()` command**
- Checks: kicad-cli on PATH + version, symbol lib dir, config dir, projects dir writable
- Raises `UserError` on failures (caught by main() handler)
- Registered as `doctor` subparser

**1.3 Atomic writes (`_atomic_write`)** 
- `tempfile.mkstemp` → write → `os.replace` (POSIX atomic)
- Temp file cleaned up if write fails
- Called with `root` arg for sanity-checked writes

**1.4 Typed exceptions**
- `KiCadError(RuntimeError)` base
- `UserError`, `ToolError`, `ParseError` subclasses
- Fixed both `bare except:` → `except (json.JSONDecodeError, OSError)` etc.
- `check_kicad()` now **raises** `ToolError` (was: return bool)
- All `sys.exit(1)` in business logic replaced with `raise UserError/ToolError`
- `main()` catches `KiCadError` → prints ❌ message → `sys.exit(1)`

**1.5 Post-write sanity checks (`_check_sexp`)**
- Balanced-paren counter (ignores parens in `"strings"`)
- Root-node check (`kicad_sch` / `kicad_pcb`)
- Called inside `_atomic_write` before `os.replace` — bad content never hits disk

**1.6 Fixed fake UUID in `cmd_new`**
- Was `datetime.now().strftime('%Y%m%d%H%M%S')` → now `str(uuid_module.uuid4())`

### Test results (Phase 1)
- 45 tests: 37 unit + 8 integration — all pass
- New test file: `tests/unit/test_phase1_reliability.py` (22 tests)

## 2026-02-25T18:27:58Z — Phase 7 test suite (7.1–7.6) complete

### Phase 7 test files (16 total in tests/unit/):
- 7.2: `test_sexpr_tokenizer.py`, `test_sexpr_parser.py`, `test_sexpr_serializer.py`, `test_sexpr_utils.py`
- 7.3: `test_sch_doc.py`, `test_pcb_doc.py`
- 7.4: `test_pipeline.py` (mocked kicad-cli via `_FakeCli` stub)
- 7.5: `test_cli.py` (`_build_parser()` extracted from `cli.py`)
- 7.6: `test_golden.py` (14 tests: round-trip, lint-clean, bug regressions)

### Phase 7.6 golden fixtures (tests/fixtures/golden/):
- `minimal.kicad_sch` — minimal valid schematic (canonical serialized form)
- `sch_with_resistor.kicad_sch` — schematic with embedded Device:R + placed symbol + instances block
- `minimal.kicad_pcb` — minimal valid PCB
- `pcb_with_footprint.kicad_pcb` — PCB with one footprint + 4-sided Edge.Cuts outline

### Key design decisions for Phase 7.6:
- Golden fixtures are stored in canonical serialized form (serializer output); tests compare
  `serialize(parse(content.rstrip("\n"))) == content.rstrip("\n")` (serializer has no trailing newline)
- Broken fixture regressions use `kicad_pcb.sexpr` (not kiutils) so they're orthogonal to test_fixtures.py
- bug1: AST walk for sub-symbol names with "Device:" prefix inside lib_symbols
- bug2: textual regex `\(id\s+\d+\)` + AST structural check for (id N) child nodes
- bug3: count bare ')' lines at column 0 — well-formed files have exactly 1 (root close), bug3 has ≥2
- bug4: find_first(sym, "instances") is None on placed symbols

### Total test count: 661 (up from 647 after 7.5)
- Commit for 7.6: `c0d1768`

## 2026-02-26T00:11:37Z — CODE_REVIEW2 implementation complete (commit e054ecc)

### Issues addressed (P0, P1, P2.1, P3.3 from code_review/CODE_REVIEW2_TODO.md):

**P0.1 + P0.2 — Atomic write hardening:**
- Added `_write_temp_text(directory, suffix, content) -> Path` to `fs.py`
  - Uses `os.fdopen()` context manager: FD always closed, even on exception
  - `f.flush()` + `os.fsync()` before `os.replace()` (directory fsync skipped by design)
- Updated `_atomic_write()` to delegate to `_write_temp_text()`
- Fixed `pipeline._kicad_validate_sch` and `pipeline._kicad_validate_pcb` (same bug)
- Removed unused `import os` and `import tempfile` from `pipeline.py`

**P1.1 — Tokenizer-based `_check_sexp`:**
- Split into `_tokenize_sexp(content)` + `_check_sexp(content, root)`
- `_tokenize_sexp` correctly handles `\\"` (escaped backslash before quote) and `\"` (escaped quote inside string) — old char-scanner was broken on `\\"` sequences
- Balance check now `sum(1 if t=="(" else -1 if t==")" else 0 for t in tokens)`
- Extracted helper reduces `_check_sexp` branch count below ruff PLR0912 limit of 12

**P1.2 — `SUPPORTED_ROOTS` constant:**
- `SUPPORTED_ROOTS: frozenset[str] = frozenset({"kicad_sch", "kicad_pcb"})` in `fs.py`
- Exported via `__init__.py`

**P2.1 — "Basic AST" design decision documented in README:**
- New "## Design notes → Serializer and round-trip formatting" section
- Clear statement: comments dropped, key order/whitespace may change; formatting diffs expected
- Notes future CST path if lossless round-trip needed

**P3.3 — `scripts/validate.sh`:**
- Runs ruff check, ruff format --check, mypy, pytest (with optional --fast flag for no coverage)
- Executable; mentioned in README Development section

### New tests (+12, total now 381 in test_phase1_reliability.py cluster):
- `TestCheckSexpEscapes`: 5 new escape-sequence edge case tests
- `TestSupportedRoots`: 3 tests for the new constant
- `TestWriteTempText`: 6 tests (content, directory, suffix, 2 MiB large content, unicode, FD-leak cleanup)

### Files changed in `e054ecc`:
- `kicad-pcb/src/kicad_pcb/fs.py` — SUPPORTED_ROOTS, _tokenize_sexp, _check_sexp, _write_temp_text, _atomic_write
- `kicad-pcb/src/kicad_pcb/pipeline.py` — both validate functions use _write_temp_text
- `kicad-pcb/src/kicad_pcb/__init__.py` — exports SUPPORTED_ROOTS, _write_temp_text
- `tests/unit/test_phase1_reliability.py` — +12 tests
- `scripts/validate.sh` — new
- `README.md` — design notes + validate.sh mention
- `code_review/CODE_REVIEW2.md` — first committed
- `code_review/CODE_REVIEW2_TODO.md` — 37 items marked [x]


---

## 2026-02-26T00:56:24Z — P3.2 richer exception hierarchy complete

### Completed: P3.2 — Richer Exception Hierarchy
- Commit to be made: `feat(p3.2): richer exception hierarchy`
- All 3 P3.2 checkboxes marked [x] in CODE_REVIEW2_TODO.md

### 5 new exception types (kicad-pcb/src/kicad_pcb/errors.py):
- `SExprTokenizeError(ParseError)` — `line: int`, `col: int`, hint, `__str__` → `"line:col: msg"`
- `SExprParseError(ParseError)` — `line: int | None`, `col: int | None`, hint
- `DocSyntaxError(ParseError)` — `path: Path | None`, hint
- `DocLintError(KiCadError)` — `path: Path | None`, `issue_count: int`, dynamic hint property
- `KicadCliValidationError(ToolError)` — `path: Path | None`, `issue_count: int`, hint

### Raise site migrations:
- `tokenizer.py`: 2 sites → `SExprTokenizeError(msg, line=..., col=...)`
- `parser.py`: 6 structural sites → `SExprParseError`; `parse_file` I/O → `DocSyntaxError`
- `fs.py`: `_check_sexp` 2 sites + `_atomic_write` re-raise → `DocSyntaxError`
- `__init__.py`: all 5 new types exported in `__all__`

### Tests: `tests/unit/test_p32_exception_hierarchy.py` — 59 tests, all passing
- Backward compat preserved: all new subtypes caught by existing `except ParseError`

### Test counts after P3.2:
- test_p32_exception_hierarchy.py: 59
- Cumulative: 133 existing affected tests still pass (no regressions)

### Remaining CODE_REVIEW2 items:
- P5.1: Structured logging
- P5.2: Dry-run diff output

---

## 2026-02-26T01:12:56Z — P5.1 structured logging complete

### Completed: P5.1 — Structured Logging Hooks for Pipeline Operations

**kicad-pcb/src/kicad_pcb/pipeline.py — MODIFIED**
- Added `import logging`, `import time` at top
- Added `logger = logging.getLogger(__name__)` module-level logger
- Added `_log_stage(stage, *, path, mode, operation, t0)` helper
  - Emits `logger.debug(...)` with `extra={"kicad": {...}}`
  - Formats message as `[stage] filename  mode=NAME  op=OP  elapsed_ms=X.XXX`
- Instrumented both pipeline functions:
  - `read`, `mutate`, `serialize` — always logged
  - `parse` — logged when `mode >= SYNTAX`
  - `validate.lint` — logged when `mode >= LINT`
  - `validate.kicad` — logged when `mode >= KICAD` and cli provided
  - `write` — logged when not `dry_run`

**Structured context in `record.kicad`:**
```json
{"stage": "read", "path": "/path/to/file.kicad_sch",
 "mode": "LINT", "operation": "add-net", "elapsed_ms": 3.14}
```

**tests/unit/test_p51_structured_logging.py — NEW (28 tests)**
- TestLogStageHelper (8 tests) — direct unit tests of _log_stage
- TestMutateValidateSchLogging (10 tests) — sch pipeline stages
- TestMutateValidatePcbLogging (5 tests) — pcb pipeline stages
- TestStructuredContextCompleteness (5 tests) — required keys / types

### Remaining CODE_REVIEW2 items:
- P5.2: Dry-run diff output

---

## 2026-02-26T01:23:22Z — P5.2 dry-run diff output complete

### Completed: P5.2 — Dry-run diff output

**kicad-pcb/src/kicad_pcb/pipeline.py — MODIFIED**
- Added `import difflib` and `from typing import IO`
- `diff_output: IO[str] | None = None` parameter on both pipeline functions
- Original file text captured at start when `diff_output is not None`
- After all validation: calls `_show_diff(original_text, content, path, diff_output)`
- Added `_show_diff(original, updated, path, out)` helper using `difflib.unified_diff`
  - Uses `fromfile="{name} (before)"` / `tofile="{name} (after)"` labels
  - No-op when before==after (empty diff)
  - Works for both `dry_run=True` and `dry_run=False`

**Usage:**
```python
import sys
mutate_and_validate_sch(path, mutator, dry_run=True, diff_output=sys.stdout)
```

**tests/unit/test_p52_dry_run_diff.py — NEW (23 tests)**
- TestShowDiff (8 tests) — unit tests for _show_diff helper
- TestDryRunDiffSch (8 tests) — sch pipeline diff_output integration
- TestDryRunDiffPcb (5 tests) — pcb pipeline diff_output integration

### All CODE_REVIEW2 items now complete:
- P0.1, P0.2, P1.1, P1.2, P2.1, P2.2, P3.1, P3.2, P3.3, P4.1, P5.1, P5.2 all done
