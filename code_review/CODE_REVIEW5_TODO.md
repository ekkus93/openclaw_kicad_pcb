# CODE_REVIEW1_TODO.md

## Objective

Harden and improve the `kicad-pcb` OpenClaw skill so it:

1) Generates **syntactically valid** KiCad S-expression files using structured parsing/editing (no regex structural edits).
2) Prevents broken output via **transactional writes + linting + validation**.
3) Produces **human-usable schematics** (a circuit diagram graph), not a list of isolated symbols with label stubs.
4) Adds a **Graphviz-based schematic layout engine** (bundled binary for now) with clear fallbacks and tests.

---

## Guiding Principles / Non-Negotiables

- **No structural regex edits** of `.kicad_sch` / `.kicad_pcb`.
- All mutating operations must go through a **single transactional pipeline**:
  - parse -> mutate -> serialize temp -> re-parse -> lint -> optional `kicad-cli` validate -> atomic commit
- **Never overwrite** existing project files on validation failure.
- Keep CLI thin; business logic returns structured results; avoid `sys.exit()` in core logic.
- Schematic generation must aim for **readable topology**:
  - connected graph wiring where appropriate
  - limited net label duplication
  - power nets handled as power symbols/global labels where feasible
- Graphviz can be **bundled** for now, but include a **license/notice TODO** and keep the integration modular enough to refactor later if needed.

---

# Phase 0 — Capture Known-Bad Cases and Baselines

### 0.1 Add regression fixtures from real failures
- [x] Create `tests/fixtures/regressions/` and add the known broken cases:
  - [x] `(id N)` embedded symbol issue fixture(s) — `tests/fixtures/broken/bug2_id_property.kicad_sch`
  - [x] sub-symbol naming issue fixture(s) — `tests/fixtures/broken/bug1_subname_rename.kicad_sch`
  - [x] "indentation" / formatting-dependent failure fixture(s) — `tests/fixtures/broken/bug3_paren_indent.kicad_sch`
- [x] Add a README per fixture describing:
  - [x] which command/flow produced it
  - [x] expected failure before fix (parse/lint/validate error)
  - [x] expected behavior after fix (either corrected output or explicit lint rejection)
  - see `tests/fixtures/broken/README.md` and `tests/fixtures/regressions/README.md`

### 0.2 Baseline schematic readability snapshots
- [x] Add at least one "unusable schematic layout" fixture produced by current generator — `tests/fixtures/regressions/headphone_amp_current_layout.kicad_sch` (13-component amp, 16 label nodes, no power symbols, no junctions; committed `b0afc76`).
- [ ] Add a small "intended readable layout" target (golden) for the same circuit — deferred until Phase 4 Graphviz output is accepted.

---

# Phase 1 — Documentation and Command Surface Accuracy

### 1.1 Fix `SKILL.md` to match the real CLI
- [x] Enumerate implemented commands/options from code (single source of truth).
- [x] Update `SKILL.md`:
  - [x] correct command names and signatures — added lint-sch, lint-pcb, validate-sch, validate-pcb, format-sch, format-pcb, apply-pattern; fixed info-sch signature
  - [x] remove unsupported flags/examples — removed `[--json]` from info-sch table, removed `add-component` loop suggestion from Common Templates
  - [x] add examples that pass on a real project — existing examples verified against cli.py
- [x] Add a "Validation policy" section:
  - [x] describe `--mode internal|kicad` levels and default behavior
  - [x] explain rollback/backup behavior on failure — "Write safety" subsection added

### 1.2 Add `doctor`/preflight command (if not already)
- [x] Report:
  - [x] KiCad CLI presence and version — already existed
  - [x] Graphviz `dot` presence/version — added in this phase (checks `GRAPHVIZ_DOT` env var + PATH)
  - [x] symbol/footprint library lookup paths — already existed
  - [x] current project status and writable directories — already existed
- [x] Return non-zero if critical dependencies are missing — already implemented (overall_ok gate)

---

# Phase 2 — Reliability Foundation: Transactional Writes + Validation

### 2.1 Enforce the transactional mutate-and-validate pipeline everywhere
- [x] Identify all commands that mutate `.kicad_sch` or `.kicad_pcb` — audited all 8 call sites in sch.py (3), patterns.py (1), pcb.py (2), netlist.py (2).
- [x] Ensure every mutator uses exactly one pipeline entrypoint (no ad-hoc writes) — confirmed all 8 use `mutate_and_validate_sch`/`mutate_and_validate_pcb`.
- [x] Pipeline steps must include (already fully implemented in `pipeline.py`):
  - [x] parse original
  - [x] mutate AST
  - [x] serialize to temp
  - [x] re-parse temp (round-trip sanity)
  - [x] run lints
  - [x] optional KiCad CLI validation (policy-driven)
  - [x] atomic commit on success
  - [x] no overwrite on failure
- [x] Add optional `.bak` backups on commit — `--backup` global CLI flag added; wired to all 8 `mutate_and_validate_*` call sites and `_ApplyNetlistRequest`.

### 2.2 Standardize exceptions and error reporting
- [x] Create typed errors: `UserError`, `ParseError`, `LintError`, `ValidationError`, `ExternalToolError` — fully implemented in `errors.py` (`KicadCliValidationError`, `DocSyntaxError`, `DocLintError` also present).
- [x] Ensure CLI catches these and prints:
  - [x] operation name — included in error messages via `❌ {exc}`
  - [x] file paths — included contextually in individual error messages
  - [x] lint codes / tool stderr excerpts — handled by `LintError` catch block and `ToolError` messages
  - [x] suggested remediation steps — `details["hint"]` now printed as `💡 {hint}` in `KiCadError` catch block

### 2.3 Improve netlist import robustness (if applicable)
- [x] Replace any regex-based XML parsing with `xml.etree.ElementTree` — replaced `re.findall` in `cmd_import_netlist` with ElementTree; handles KiCad `<comp ref="...">` attribute format and legacy `<ref>` child-element fallback; malformed XML raises `ToolError`.
- [x] Add unit tests for netlist files with whitespace/newline variance — 22 new tests in `tests/unit/test_phase2_reliability.py` covering all parsing paths, malformed XML, whitespace stripping, deduplication, backup flag wiring, and hint display.

---

# Phase 3 — S-expression and KiCad Document Correctness

### 3.1 Confirm parser strategy (“borrow” vs “own”) and simplify
- [x] Decide and implement one of:
  - [x] **Preferred:** Use the existing in-repo S-expression stack as the single source for parse/serialize, and keep any external library only for optional verification/tests.
  - [ ] Or: Replace in-repo parser with a borrowed library and remove/disable duplicate parser code.
- [x] Update dependencies accordingly (avoid dual-parser drift).
  - `kiutils>=1.4` moved to `[project.optional-dependencies.dev]`; strategy documented in `sexpr/__init__.py`.

### 3.2 Make minimal project template generation AST-based
- [x] Replace hand-written template strings for `.kicad_sch` / `.kicad_pcb` skeletons with AST builders.
  - `_build_sch_skeleton()` and `_build_pcb_skeleton()` in `commands/project.py`.
- [x] Add golden tests for skeleton files.
  - `TestSchSkeleton` and `TestPcbSkeleton` in `tests/unit/test_phase3_correctness.py`.
- [x] Add integration "smoke load" test (parse + optional `kicad-cli` check).
  - `_check_sexp` + round-trip parse→serialize→re-parse verified in tests.

### 3.3 Expand structural lint rules (syntax is not enough)
- [x] Ensure lints exist and are enforced for:
  - [x] duplicate refs (SCH003/PCB002)
  - [x] duplicate UUIDs (SCH002/PCB002)
  - [x] missing required symbol properties (Reference/Value) (SCH004/SCH005)
  - [x] malformed coords/at (SCH006/SCH007/PCB003/PCB004)
  - [x] missing outline / invalid outline (pcb) (PCB005/PCB006/PCB007/PCB008)
  - [x] layer declarations on generated primitives: SCH010 (label missing at), PCB010 (gr_* missing layer), PCB011 (pad missing layers)
- [x] Add a clear mapping from lint code -> fix suggestion.
  - `LINT_SUGGESTIONS` updated with entries for SCH010, PCB010, PCB011.

---

# Phase 4 — Schematic Readability: Layout + Wiring Engine (Major Feature)

## Goal
Replace the current schematic style “place one-by-one and label stubs” with a real **graph layout** and **graph wiring** so the schematic reads like a conventional circuit diagram.

### 4.1 Introduce a schematic layout subsystem
- [x] Define a `LayoutEngine` interface:
  - [x] `compute_symbol_positions(ir) -> dict[ref, (x, y, rot?)]`
  - [x] optional: `compute_net_routes(...)` (or handled separately)
- [x] Add config/CLI option:
  - [x] `--layout auto|graphviz|heuristic|none`
  - [x] default `auto` (Graphviz if available, else heuristic)
- [x] Keep layout independent of KiCad AST serialization logic.

### 4.2 Graphviz integration (bundled binary)
- [x] Add `graphviz/` (or similar) module for calling `dot`.
- [x] Support a configured/bundled `dot` path:
  - [x] default to a bundled binary within the repo/distribution
  - [x] allow override via env var or config (e.g., `GRAPHVIZ_DOT`)
- [x] Convert circuit IR into a Graphviz DOT graph:
  - [x] nodes: symbols (refdes) and/or nets (see 4.3)
  - [x] edges: connectivity (symbol-pin to net, or symbol-symbol simplified)
  - [x] use `rankdir=LR` to encourage left-to-right flow
  - [x] use clusters for functional blocks (optional initial: per channel L/R)
- [x] Parse Graphviz output positions (`dot -Tplain` recommended) into coordinates.
- [x] Map Graphviz coordinate space to KiCad coordinate space:
  - [x] apply scaling factor
  - [x] add margins
  - [x] snap to KiCad grid if desired
- [x] Add caching:
  - [x] save computed layout to a JSON file under managed outputs (optional)
  - [x] deterministic layout seeds if possible

### 4.3 Decide the graph model for layout (important)
Implement one of these models (or both, with a config):

- [x] **Bipartite model (recommended initially):**
  - node types: `component` and `net`
  - edges: component ↔ net membership
  - benefit: captures multi-pin nets cleanly
  - risk: power nets become huge hubs (handle with special rules)
- [ ] **Collapsed model:**
  - nodes: components only
  - edges: connect components that share a net
  - benefit: smaller graph
  - risk: loses clarity about multi-drop nets

Add rules:
- [x] Treat power nets specially:
  - [x] omit from Graphviz graph or compress them
  - [x] place power symbols separately (top/bottom rails)

### 4.4 Add a heuristic fallback layout engine (must exist)
Even if Graphviz is bundled, keep a heuristic engine for:
- [x] minimal dependencies / troubleshooting
- [x] predictable staging for certain circuit types (audio/analog)

Heuristic engine tasks:
- [x] Identify "stages" from IR using:
  - [x] net name patterns (IN/OUT, V+/V-, GND)
  - [x] graph distance from input nets to output nets
- [x] Place stages left-to-right:
  - [x] inputs left, outputs right
  - [ ] op-amp centered per stage (deferred — not applicable to all circuits)
  - [ ] feedback parts close to op-amp pins (deferred)
  - [ ] decoupling caps near IC (deferred)
  - [ ] mirrored left/right channels (if IR indicates L/R) (deferred)
- [x] Ensure no symbol bounding-box overlaps (detected by LAY003; layout does a best-effort spread).

### 4.5 Replace “label stubs everywhere” with real wires and junctions
Implement a **wiring engine** that draws actual wires for readability.

- [x] Create rules per net degree:
  - [x] degree == 2: draw a direct orthogonal wire between the two pins
  - [x] degree 3–6: create a hub/junction spine and connect pins to it
  - [x] high-degree nets (GND, VCC, etc.): use power symbols/global labels; avoid spaghetti
- [x] Reduce label duplication:
  - [x] at most one label per net within a local region by default
  - [x] only label nets that cross blocks or are semantically important
- [x] Add junctions explicitly where wires meet (KiCad `(junction ...)` nodes as needed).
- [ ] Add optional “bus” style later (out of scope for initial implementation).

### 4.6 Add schematic-layout lints (readability gates)
Add new lint codes to detect “unusable” layout patterns.

- [x] `LAY001`: net label appears more than N times (configurable)
- [x] `LAY002`: >X% of wires shorter than stub threshold (indicates label-stub style)
- [x] `LAY003`: overlapping symbols (approx bounding boxes)
- [x] `LAY004`: symbols placed outside page bounds
- [x] `LAY005`: too many disconnected visual islands (heuristic using wire graph)
- [ ] Enforce these lints at `--validate lint|full` for schematic generation commands. (deferred to Phase 5)

### 4.7 Add rotation/orientation rules (optional but improves readability)
- [x] Rotate resistors/caps based on wire direction (horizontal vs vertical).
- [x] Keep op-amps oriented consistently (inputs left, outputs right).
- [x] Keep connectors at edges (inputs left, outputs right, power top/bottom).

---

# Phase 5 — Graphviz Licensing and Distribution Notes

### 5.1 Add licensing documentation for bundled Graphviz
- [x] Add `THIRD_PARTY_NOTICES.md` (or update existing) to include:
  - [x] Graphviz name/version
  - [x] license name and link
  - [x] redistribution notice requirements (as applicable)
- [x] Add a short note in README:
  - [x] Graphviz is bundled as an external tool for schematic layout
  - [x] how to replace it with a system-installed Graphviz later
  - [x] how to override `dot` path

### 5.2 Make bundling strategy explicit in code
- [x] Centralize discovery of `dot` binary:
  - [x] bundled path first
  - [x] env override
  - [x] PATH lookup as last resort
- [x] Provide `doctor` output with which path is used.

---

# Phase 6 — Tests: Unit, Golden, Integration

### 6.1 Unit tests for layout engine(s)
- [x] For a small IR fixture:
  - [x] Graphviz engine produces non-overlapping positions (`TestGraphvizPositionStability` — skipped if dot absent)
  - [x] positions are stable (deterministic enough for tests; use fixed seeds/options)
- [x] Heuristic engine:
  - [x] places input-left/output-right (`TestHeuristicInputPlacement`)
  - [ ] places feedback components near op-amp
  - [ ] mirrors L/R channels when present

### 6.2 Unit tests for wiring engine
- [x] degree-2 net -> produces a single wire connecting pins (`TestLabelDuplicationPolicy`, `TestRouteNetsDirect`)
- [x] degree-3+ net -> hub/junction wiring created (`TestRouteNetsHub`)
- [x] power net -> power symbol/global label path used (`TestRouteNetsPower`)
- [x] label duplication capped by policy (`TestLabelDuplicationPolicy`, `TestRouteNetsHighFanout`)

### 6.3 Golden schematic tests (critical)
- [x] Add golden expected `.kicad_sch` outputs for:
  - [x] simple resistor divider (`TestGoldenResistorDivider`)
  - [x] op-amp inverting/non-inverting stage (`TestGoldenOpAmpStage`)
  - [ ] small audio block (subset of headphone amp)
- [x] Compare canonical serialized output (or AST equivalence) to catch regressions.

### 6.4 Integration tests (skip if tools missing)
- [x] If `kicad-cli` installed:
  - [x] generated schematic parseable/exportable via kicad-cli (`TestKiCadCLINetlistExport`)
- [x] If bundled `dot` is present:
  - [x] Graphviz engine runs end-to-end (`TestGraphvizEndToEnd` — skipped if dot absent)
- [x] Add a single end-to-end "generate from IR -> schematic -> kicad-cli" test for CI. (`test_divider_schematic_exportable`)

---

# Phase 7 — UX / Controls (Optional but Recommended)

### 7.1 CLI flags and configuration
- [ ] Add/confirm:
  - [ ] `--layout ...`
  - [ ] `--validate ...`
  - [ ] `--strict` (warnings -> errors)
  - [ ] `--dry-run` (validate but do not commit)
  - [ ] `--json` output for structured responses

### 7.2 Improve diagnostics for layout failures
- [ ] If Graphviz fails, print:
  - [ ] command invoked
  - [ ] stderr excerpt
  - [ ] fallback used (heuristic)
- [ ] On layout lint failure, include:
  - [ ] which lint codes fired
  - [ ] which nets/symbols triggered them
  - [ ] suggestion to relax thresholds or switch layout engine

---

## Definition of Done (for this milestone)

- [ ] The skill generates `.kicad_sch` files that are:
  - [ ] syntactically valid S-expressions
  - [ ] pass structural lints
  - [ ] do not overwrite originals on failure
  - [ ] visually readable (connected graph wiring, not label stubs everywhere)
- [ ] Graphviz layout engine exists and is the default under `--layout auto`.
- [ ] Graphviz bundling is documented with licensing notes.
- [ ] Unit and golden tests cover layout and wiring behavior.
- [ ] Integration tests (where tools exist) confirm ERC passes on key fixtures.
