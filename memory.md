# kicad-pcb Skill — Memory File

_Last updated: 2026-02-25T21:15:00Z_

---

## 2026-02-25T21:15:00Z — Phase 8.2: Symbol library path discovery (commit 00c4689)

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

## 2026-02-25T20:25:00Z — Phase 8.1: KiCad CLI version compatibility layer (commit ca50af3)

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
