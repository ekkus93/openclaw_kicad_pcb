# kicad-pcb Skill — Memory File

_Last updated: 2026-02-25T16:45:00Z_

---

## 2026-02-25T16:45:00Z — Lint clean pass (ruff + mypy)

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
- **Script**: `kicad-pcb/scripts/kicad_pcb.py` (monolith, ~1401 lines, being refactored)
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

## 2026-02-25T04:27:38Z — Phase 0 Baseline Complete

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
4. **Phase 3 — kiutils parser**: replace all regex/string S-expr manipulation
5. **Phase 4 — Doc wrappers**: `SchematicDoc`/`PcbDoc` AST editing API
6. **Phase 5 — Validation pipeline**: lint → validate → transactional write

---

## 2026-02-25T — Phase 1 Complete (commit 3f09549)

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
