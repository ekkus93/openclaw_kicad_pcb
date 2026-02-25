# kicad-pcb Skill — Memory File

_Last updated: 2026-02-25_

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

## Architecture Decisions (2026-02-25)

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

## Refactor Plan (CODE_REVIEW1_TODO.md)

High-priority next phases:
1. **Phase 0 — Baseline fixtures** (captured in `tests/fixtures/`)
2. **Phase 1 — Reliability**: `doctor` command, atomic writes, typed exceptions
3. **Phase 2 — Testability**: module split, Pydantic models, injectable adapters  
4. **Phase 3 — kiutils parser**: replace all regex/string S-expr manipulation
5. **Phase 4 — Doc wrappers**: `SchematicDoc`/`PcbDoc` AST editing API
6. **Phase 5 — Validation pipeline**: lint → validate → transactional write
