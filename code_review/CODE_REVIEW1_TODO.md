# CODE_REVIEW1_TODO.md

## Objective

Refactor and harden the `kicad-pcb` OpenClaw skill so it generates valid, reliable KiCad schematic/PCB files using proper S-expression parsing and structured document editing, with strong linting/validation and a unit/integration test suite.

---

## Guiding Principles

- **No regex-based structural edits** for `.kicad_sch` / `.kicad_pcb` files.
- **All mutating operations must be transactional** (temp write -> validate -> atomic replace).
- **Validation is mandatory** before overwriting files.
- **CLI layer is thin**; business logic is testable without subprocesses/stdout/sys.exit.
- **Use `kicad-cli` as the external validation oracle** (ERC/DRC/exports), but not as a substitute for syntax/structural linting.
- **Skill docs (`SKILL.md`) must exactly match implemented CLI behavior**.

---

## Phase 0 — Baseline and Inventory (Before Refactor)

### 0.1 Capture current behavior
- [ ] Create a branch for the refactor/hardening work.
- [ ] Run the current script manually on a few known examples and save outputs/logs.
- [ ] Collect examples of broken KiCad files currently produced.
- [ ] Save a small corpus of inputs/outputs as regression fixtures (even if broken) for later comparison.

### 0.2 Inventory current command surface
- [ ] Enumerate all implemented CLI commands and options from `scripts/kicad_pcb.py`.
- [ ] Compare implemented commands/options to `SKILL.md`.
- [ ] Produce a mismatch list (missing commands, wrong signatures, wrong examples, unsupported flags).

### 0.3 Establish supported environment/version baseline
- [ ] Decide minimum supported KiCad version(s) (e.g. KiCad 8+).
- [ ] Record expected `kicad-cli` commands/options for those versions.
- [ ] Record expected symbol library discovery paths per platform (Linux first, others optional).

---

## Phase 1 — Immediate Reliability Fixes (Stop the Bleeding)

### 1.1 Fix `SKILL.md` / command documentation mismatch (High Priority)
- [x] Update command signatures in `SKILL.md` to match actual CLI exactly.
  - [x] Fix `connect` docs to reflect `--from X,Y --to X,Y` (matches implementation).
  - [x] Fix `add-net` docs to reflect actual args/options (`NAME [--x X] [--y Y]`).
  - [x] Remove unsupported examples/options (e.g. `preview-pcb --layers` not shown).
  - [x] Clarify behavior of `import-netlist` — updated to "Export netlist from schematic and report components ready for PCB layout".
- [x] Add examples that actually work with the current CLI syntax (Quick Start section).
- [x] Add a note that strict validation may reject writes on malformed output — added "File Safety" section to SKILL.md.

### 1.2 Add a `doctor` command (or equivalent preflight check)
- [x] Implement `doctor` command to print environment checks:
  - [x] `kicad-cli` availability/version
  - [x] symbol library directory discovery status
  - [x] optional tools (e.g., Java/freerouting if relevant) — added Java + Freerouting JAR checks (informational, not hard failures)
  - [x] current project path validity
  - [x] writable output directories (projects dir write probe)
- [x] Return non-zero exit code if critical dependencies are missing (raises `UserError`).

### 1.3 Add safe writes for all mutating commands
- [x] Introduce temp-write + atomic replace utility (`_atomic_write`).
- [x] Add optional backup creation (`.bak`) before overwriting original files — `_atomic_write` now accepts `backup=True`; copies original to `<path>.bak` before rename.
- [x] Ensure partial writes never corrupt originals on crash/failure.
- [x] Use the safe write utility in every mutation command — `cmd_new` now uses `_atomic_write` for `.kicad_sch` (root-validated) and `.kicad_pcb` (root-validated) and `.kicad_pro` (atomic write, no sexp needed).

### 1.4 Normalize error handling
- [x] Define typed exceptions (`KiCadError`, `UserError`, `ToolError`, `ParseError`).
- [x] Replace broad/bare `except:` blocks with explicit exceptions.
- [x] Stop using `sys.exit()` inside business logic; reserved for CLI entrypoint only.
- [x] Standardize error messages and stderr reporting for subprocess failures.
- [x] Ensure failures include actionable hints (which file, which command, what to try next).

### 1.5 Add post-write sanity checks (temporary, before full AST refactor)
- [x] Add a minimal balanced-parentheses check for generated KiCad files (`_check_sexp`).
- [x] Add root-node sanity checks (`kicad_sch` / `kicad_pcb`) via `_check_sexp` root parameter.
- [x] Fail and rollback if sanity checks fail (integrated into `_atomic_write`).
- [ ] Log validation failure details with file path and operation name — `ParseError` is raised but does not include operation context or file path.

---

## Phase 2 — Refactor for Testability and Maintainability

### 2.1 Split the monolithic script into modules
- [ ] Create a package structure (example):
  - [ ] `kicad_pcb/cli.py` (argparse + dispatch only)
  - [ ] `kicad_pcb/errors.py`
  - [ ] `kicad_pcb/models.py`
  - [ ] `kicad_pcb/config.py`
  - [ ] `kicad_pcb/fs.py`
  - [ ] `kicad_pcb/runner.py`
  - [ ] `kicad_pcb/sexpr/*`
  - [ ] `kicad_pcb/sch_doc.py`
  - [ ] `kicad_pcb/pcb_doc.py`
  - [ ] `kicad_pcb/lint/*`
  - [ ] `kicad_pcb/validate/*`
  - [ ] `kicad_pcb/services/*`
- [ ] Keep `scripts/kicad_pcb.py` as a thin entrypoint wrapper (temporary compatibility).

### 2.2 Introduce typed domain models
- [ ] Create dataclasses (or Pydantic models if preferred) for domain objects:
  - [ ] `ProjectRef`
  - [ ] `ComponentSpec`
  - [ ] `WireSegment`
  - [ ] `NetLabelSpec`
  - [ ] `BoardOutlineRect`
  - [ ] `FootprintMoveSpec`
  - [ ] `LintIssue`
  - [ ] `ValidationResult`
- [ ] Move argument normalization/parsing into model constructors or parser helpers.

### 2.3 Isolate side effects behind injectable adapters
- [ ] Introduce filesystem helper interface/utilities for read/write/list operations.
- [ ] Introduce subprocess runner wrapper (`Runner`) with typed results.
- [ ] Introduce `KicadCliAdapter` with typed methods (ERC/DRC/exports/etc.).
- [ ] Inject runner/fs adapters into services for unit testing.

### 2.4 Separate CLI presentation from business logic
- [ ] Refactor command handlers so they return structured results instead of printing directly.
- [ ] Move formatting/printing to CLI layer.
- [ ] Ensure services raise typed exceptions rather than exiting.

---

## Phase 3 — Implement Proper S-expression Parsing/Serialization (Core Fix)

### 3.1 Build S-expression tokenizer
- [ ] Implement tokenizer that handles:
  - [ ] parentheses
  - [ ] atoms
  - [ ] quoted strings
  - [ ] escape sequences in strings
  - [ ] whitespace/newlines
  - [ ] comments (if present in encountered inputs)
- [ ] Produce tokens with position information (line/column) for precise parse errors.

### 3.2 Build S-expression parser
- [ ] Parse tokens into AST node types:
  - [ ] `ListNode`
  - [ ] `AtomNode`
  - [ ] `StringNode`
- [ ] Include parse errors with source location.
- [ ] Validate end-of-input and balanced nesting.
- [ ] Add convenience parse entrypoints for file/string.

### 3.3 Build serializer (round-trip safe)
- [ ] Implement serializer that outputs deterministic formatting.
- [ ] Preserve string escaping correctly.
- [ ] Ensure `parse -> serialize -> parse` stability.
- [ ] Optionally support a “pretty” vs “compact” mode (pretty is enough initially).

### 3.4 AST utility helpers
- [ ] Implement helpers:
  - [ ] `find_first(root, key)`
  - [ ] `find_all(root, key)`
  - [ ] `replace_section(root, key, new_section)`
  - [ ] `append_to_section(root, key, item)`
  - [ ] `walk(root)`
- [ ] Add path-like utilities for debug/lint messages (node path reporting).

---

## Phase 4 — KiCad-Specific Document Wrappers (AST-Based Editing)

### 4.1 `SchematicDoc` wrapper (`.kicad_sch`)
- [ ] Implement parser/loader for schematic files into AST.
- [ ] Validate root node is `kicad_sch`.
- [ ] Implement safe mutation methods:
  - [ ] `add_symbol(...)`
  - [ ] `add_wire(...)`
  - [ ] `add_label(...)`
  - [ ] `embed_lib_symbol(...)`
  - [ ] `ensure_lib_symbols_section()`
- [ ] Replace regex/string insertion helpers with AST-based insertion.
- [ ] Replace `_find_symbol_def` / `_find_symbol_pins` heuristic parsing with structured parsing where feasible.
  - [ ] Remove fixed-window scan heuristic (`12k` chars style logic).
  - [ ] Parse library symbol S-expr properly.
- [ ] Normalize UUID creation (real UUIDs everywhere, no timestamp pseudo-UUIDs).

### 4.2 `PcbDoc` wrapper (`.kicad_pcb`)
- [ ] Implement parser/loader for PCB files into AST.
- [ ] Validate root node is `kicad_pcb`.
- [ ] Implement safe mutation methods:
  - [ ] `set_rect_outline(...)`
  - [ ] `clear_generated_outline(...)`
  - [ ] `move_footprint(...)`
  - [ ] `find_footprint_by_ref(...)`
- [ ] Replace regex-based `Edge.Cuts` editing with AST edits.
- [ ] Replace regex-based footprint position updates with AST edits.
- [ ] Preserve rotation and footprint child content when moving footprints.

### 4.3 Internal intermediate representation (IR) for generation (Recommended)
- [ ] Define a small IR for generated content:
  - [ ] components
  - [ ] placements
  - [ ] wires
  - [ ] labels/nets
  - [ ] board outline
- [ ] Build AST emitters from IR instead of hand-concatenating strings.
- [ ] Use IR in higher-level commands for clarity and validation.

---

## Phase 5 — Linting and Validation Pipeline (Mandatory for Mutations)

### 5.1 Syntax validation (always-on)
- [ ] Parse generated output after mutation and before write commit.
- [ ] Confirm expected root node type.
- [ ] Confirm mandatory top-level sections exist (at least for generated/minimal files).
- [ ] Fail on parse errors with line/column context.

### 5.2 Structural linting (custom, always-on)
- [ ] Create lint framework:
  - [ ] severity (`error`, `warning`)
  - [ ] code (`SCH001`, `PCB001`, etc.)
  - [ ] message
  - [ ] optional node path / source location
- [ ] Implement schematic lints:
  - [ ] `SCH001` invalid root
  - [ ] `SCH002` duplicate UUID
  - [ ] `SCH003` duplicate reference designator
  - [ ] `SCH004` symbol missing `Reference`
  - [ ] `SCH005` symbol missing `Value`
  - [ ] `SCH006` malformed coordinates / `at`
  - [ ] `SCH007` malformed wire points
  - [ ] `SCH008` missing/empty `lib_symbols` when symbols exist
  - [ ] `SCH009` symbol instance references nonexistent embedded lib symbol
- [ ] Implement PCB lints:
  - [ ] `PCB001` invalid root
  - [ ] `PCB002` duplicate UUID
  - [ ] `PCB003` footprint missing `at`
  - [ ] `PCB004` malformed `at` / rotation
  - [ ] `PCB005` no `Edge.Cuts` geometry
  - [ ] `PCB006` outline not closed (for generated rectangle mode)
  - [ ] `PCB007` impossible dimensions (<=0 or absurd size)
  - [ ] `PCB008` malformed layer declarations on generated geometry
  - [ ] `PCB009` coordinates out of sane/configured range
- [ ] Add cross-file/project lints (later phase):
  - [ ] `X001` refs mismatch between schematic and PCB
  - [ ] `X002` missing footprint assignments
  - [ ] `X003` basic net naming consistency checks

### 5.3 KiCad CLI validation (authoritative external validation)
- [ ] Implement `KicadCliAdapter` methods:
  - [ ] version detection (`kicad-cli --version`)
  - [ ] ERC
  - [ ] DRC
  - [ ] exports used by the skill
- [ ] Parse and normalize CLI result outputs into structured results.
- [ ] Add version capability detection and compatibility handling.
- [ ] Fail mutations when KiCad validation fails (configurable strictness).

### 5.4 Validation policy / strictness modes
- [ ] Add validation mode controls (CLI flags and/or config):
  - [ ] `none`
  - [ ] `syntax`
  - [ ] `lint`
  - [ ] `kicad`
  - [ ] `full`
- [ ] Set safe default for mutating commands (`full` preferred, `lint` fallback if performance becomes an issue).
- [ ] Optional `--strict` mode to fail on warnings as well as errors.

### 5.5 Transactional mutate-and-validate pipeline
- [ ] Implement a single helper used by all mutating commands:
  - [ ] load/parse current file
  - [ ] apply mutator function
  - [ ] serialize to temp
  - [ ] re-parse temp (round-trip sanity)
  - [ ] run lints
  - [ ] run KiCad validation (per policy)
  - [ ] atomic commit on success
  - [ ] rollback/no-overwrite on failure
- [ ] Make all write commands call this helper (no exceptions).

---

## Phase 6 — CLI and Skill UX Improvements (Reliability + Usability)

### 6.1 Command surface cleanup
- [ ] Ensure command names and semantics are consistent and obvious.
- [ ] Rename commands that over-promise (if needed) or improve docs to set expectations.
- [ ] Add `--dry-run` for mutating commands (validate and show diff/summary without commit).
- [ ] Add `--json` output mode for machine-friendly responses (useful for OpenClaw automation).

### 6.2 Improve user-facing diagnostics
- [ ] Print lint/validation errors in a clear, structured way.
- [ ] Include file path, operation, and issue codes.
- [ ] Suggest likely fixes for common failures (missing symbol library, duplicate refs, malformed coords).

### 6.3 Add “format/lint/validate” explicit commands
- [ ] `lint-sch`, `lint-pcb` commands (or unified `lint`) for existing files.
- [ ] `validate-sch`, `validate-pcb` (syntax + lint + optional KiCad CLI checks).
- [ ] Optional formatter command for canonical S-expression formatting.

---

## Phase 7 — Testing Strategy (Unit + Integration + Regression)

### 7.1 Test framework setup
- [x] Add `pytest` configuration and test layout:
  - [x] `tests/unit`
  - [x] `tests/integration`
  - [x] `tests/fixtures`
- [x] Add coverage reporting (`pyproject.toml` `[tool.coverage]` configured).
- [x] Add markers (`integration`, `requires_kicad`, `unit` all configured in `pyproject.toml`).

### 7.2 Unit tests for S-expression core (highest priority)
- [ ] Tokenizer tests
  - [ ] atoms
  - [ ] quoted strings
  - [ ] escaped quotes
  - [ ] nested parentheses
  - [ ] comments (if supported)
  - [ ] position tracking
- [ ] Parser tests
  - [ ] valid nested lists
  - [ ] malformed input (unexpected EOF, bad string)
  - [ ] precise error locations
- [ ] Serializer tests
  - [ ] round-trip parse/serialize/parse equivalence
  - [ ] deterministic formatting
  - [ ] escaping correctness

### 7.3 Unit tests for KiCad document wrappers
- [ ] `SchematicDoc` tests
  - [ ] add symbol into empty/non-empty sections
  - [ ] add wire/label without breaking existing content
  - [ ] embed symbol once (no duplicate embed)
  - [ ] duplicate ref detection lint
  - [ ] malformed coordinates rejected
- [ ] `PcbDoc` tests
  - [ ] set rectangle outline creates expected geometry
  - [ ] outline replacement only removes generated outline
  - [ ] move footprint updates `at` and preserves rotation
  - [ ] malformed footprint `at` linted/rejected

### 7.4 Unit tests for validation pipeline
- [x] Failed syntax validation prevents overwrite (`test_no_temp_file_left_on_parse_error`, `test_with_root_check_bad_content_no_clobber`).
- [ ] Failed lint prevents overwrite — no lint framework yet.
- [ ] Failed mocked `kicad-cli` validation prevents overwrite — not implemented.
- [x] Successful validation commits atomically (`test_writes_content`, `test_with_root_check_valid`).
- [ ] Backup creation behavior works as configured — not implemented.

### 7.5 Unit tests for CLI parsing/dispatch
- [ ] Command argument parsing matches documented signatures.
- [ ] Invalid args produce useful messages and non-zero exit.
- [ ] `--json` output mode returns structured responses.

### 7.6 Golden file tests (critical for regression prevention)
- [x] Regression fixtures for known-bad cases added (`tests/fixtures/broken/`: bug1–bug4 `.kicad_sch` files).
- [x] Working fixture for smoke comparison (`tests/fixtures/working/SmokeTest_R1.kicad_sch`).
- [ ] Create fixture inputs/expected outputs:
  - [ ] minimal schematic
  - [ ] schematic with one/two symbols
  - [ ] minimal PCB
  - [ ] PCB with a few footprints
- [ ] Assert AST equality and/or canonical serialized equality.
- [ ] Add regressions for every previously broken file case found in Phase 0.

### 7.7 Integration tests with real KiCad (skip if unavailable)
- [x] Create project → schematic files created and loadable by kicad-cli (`test_schematic_loadable_by_kicad_cli`).
- [x] Add component(s) → schematic remains loadable and exportable (netlist/BOM export tests).
- [ ] Set board size → PCB DRC/export commands run — not covered.
- [ ] Full mini flow (e.g., simple divider/LED + resistor) passes validation and exports package — not covered.
- [x] Ensure tests are skipped cleanly if `kicad-cli` is not installed (`requires_kicad` marker + `skipif`).

---

## Phase 8 — Version Compatibility and Platform Hardening

### 8.1 KiCad CLI version compatibility layer
- [ ] Detect `kicad-cli` version at runtime.
- [ ] Maintain capability map for commands/options by version.
- [ ] Gracefully degrade or error with clear guidance if unsupported options are requested.

### 8.2 Symbol library path handling
- [ ] Replace hardcoded `/usr/share/kicad/symbols` assumptions with discovery logic/config override.
- [ ] Support explicit CLI/config path for symbol libraries.
- [ ] Validate discovered paths and report in `doctor`.

### 8.3 Runtime environment resolution
- [ ] Avoid resolving critical paths/tool binaries at import time if it reduces flexibility/testability.
- [ ] Move discovery into adapters/services with caching if needed.
- [ ] Ensure deterministic behavior in tests through injected environment/config.

---

## Phase 9 — Improve Generation Quality (After Reliability Foundation)

### 9.1 Align skill claims with actual capabilities
- [ ] Update `SKILL.md` to accurately represent current functionality.
- [ ] Remove or clearly mark aspirational NL-to-PCB features if not implemented.
- [ ] Add examples that demonstrate supported flows reliably.

### 9.2 Add validated generation patterns/templates
- [ ] Add a small library of known-good patterns:
  - [ ] resistor divider
  - [ ] LED + resistor
  - [ ] connector breakout
  - [ ] decoupling capacitor pattern
- [ ] Emit from IR + validate before commit.
- [ ] Use templates as “known good” building blocks for LLM-driven workflows.

### 9.3 Preflight semantic checks before generation
- [ ] Detect duplicate references before emitting.
- [ ] Require/validate footprints for PCB-targeted outputs.
- [ ] Validate pin references and symbol existence before wiring.
- [ ] Catch common net naming mistakes early.

---

## Phase 10 — CI / Quality Gates (Recommended)

### 10.1 Add static quality checks
- [x] Add Python formatting (`ruff` — clean pass, committed `b7645fc`).
- [x] Add import sorting (`ruff` `I001` rule — clean pass).
- [x] Add linting (`ruff` — 0 violations, configured in `pyproject.toml`).
- [x] Add type checking (`mypy` — clean pass, exit 0).

### 10.2 Add CI test pipeline
- [ ] Run unit tests on every PR.
- [ ] Run integration tests in a KiCad-capable environment (or nightly if setup is heavy).
- [ ] Enforce coverage threshold for parser/doc/lint modules.
- [ ] Publish test artifacts/logs for failed KiCad validation runs.

---

## Deliverables Checklist (Definition of Done)

### Must-have for “usable and not horrible”
- [ ] No regex-based structural edits for `.kicad_sch` / `.kicad_pcb` mutation paths — current schematic mutations still use string/regex operations.
- [ ] All mutating commands use transactional write + validation pipeline — `cmd_new` now uses `_atomic_write` (done); full transactional pipeline (Phase 5) not yet implemented.
- [ ] Syntax + structural linting implemented and enabled by default — only basic sexp balance/root check exists; no structural lint framework.
- [ ] `kicad-cli` validation integrated for ERC/DRC (where applicable) — not integrated into mutation pipeline.
- [x] `SKILL.md` matches actual command behavior (all commands, `import-netlist` description, File Safety note).
- [ ] Unit tests cover parser/serializer and core mutations — no S-expr parser yet.
- [x] Regression fixtures for known-bad cases (`tests/fixtures/broken/` has bug1–bug4).

### “Rock solid” target
- [ ] AST-based editing for all mutation operations.
- [ ] Strong lint rules with clear diagnostics.
- [ ] Integration tests passing on supported KiCad versions.
- [ ] Version compatibility handling and `doctor` diagnostics.
- [ ] Canonical serializer/formatter for stable output and diffs.

---

## Suggested Implementation Order (Copilot-Friendly)

1. [x] Fix docs mismatch (`SKILL.md`) and add `doctor` — fully done.
2. [x] Add typed exceptions + safe atomic writes + minimal sanity checks — fully done (`.bak` backup added, `cmd_new` uses `_atomic_write`).
3. [ ] Split CLI from services and introduce `Runner` / `KicadCliAdapter`.
4. [ ] Implement S-expression tokenizer/parser/serializer + tests.
5. [ ] Implement `SchematicDoc` and `PcbDoc` AST wrappers for highest-risk ops.
6. [ ] Add lint framework + key schematic/PCB lints.
7. [ ] Implement transactional `mutate_and_validate()` pipeline and wire into all mutating commands.
8. [ ] Add golden tests + integration tests.
9. [ ] Add version compatibility layer + symbol library discovery improvements.
10. [ ] Improve generation quality via IR/templates and semantic preflight checks.

---

## Notes for Github Copilot / Implementer

- Prioritize correctness over speed or minimal code changes.
- Avoid “quick fixes” with regex for nested S-expression structures.
- Keep all mutation logic pure/testable where possible.
- Treat `kicad-cli` validation failures as first-class errors, not warnings hidden in logs.
- Every time a bug produces a broken KiCad file, add a regression fixture/test before fixing.
