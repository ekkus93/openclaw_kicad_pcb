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
- [ ] Identify all commands that mutate `.kicad_sch` or `.kicad_pcb`.
- [ ] Ensure every mutator uses exactly one pipeline entrypoint (no ad-hoc writes).
- [ ] Pipeline steps must include:
  - [ ] parse original
  - [ ] mutate AST
  - [ ] serialize to temp
  - [ ] re-parse temp (round-trip sanity)
  - [ ] run lints
  - [ ] optional KiCad CLI validation (policy-driven)
  - [ ] atomic commit on success
  - [ ] no overwrite on failure
- [ ] Add optional `.bak` backups on commit.

### 2.2 Standardize exceptions and error reporting
- [ ] Create typed errors: `UserError`, `ParseError`, `LintError`, `ValidationError`, `ExternalToolError`.
- [ ] Ensure CLI catches these and prints:
  - [ ] operation name
  - [ ] file paths
  - [ ] lint codes / tool stderr excerpts
  - [ ] suggested remediation steps

### 2.3 Improve netlist import robustness (if applicable)
- [ ] Replace any regex-based XML parsing with `xml.etree.ElementTree`.
- [ ] Add unit tests for netlist files with whitespace/newline variance.

---

# Phase 3 — S-expression and KiCad Document Correctness

### 3.1 Confirm parser strategy (“borrow” vs “own”) and simplify
- [ ] Decide and implement one of:
  - [ ] **Preferred:** Use the existing in-repo S-expression stack as the single source for parse/serialize, and keep any external library only for optional verification/tests.
  - [ ] Or: Replace in-repo parser with a borrowed library and remove/disable duplicate parser code.
- [ ] Update dependencies accordingly (avoid dual-parser drift).

### 3.2 Make minimal project template generation AST-based
- [ ] Replace hand-written template strings for `.kicad_sch` / `.kicad_pcb` skeletons with AST builders.
- [ ] Add golden tests for skeleton files.
- [ ] Add integration “smoke load” test (parse + optional `kicad-cli` check).

### 3.3 Expand structural lint rules (syntax is not enough)
- [ ] Ensure lints exist and are enforced for:
  - [ ] duplicate refs
  - [ ] duplicate UUIDs
  - [ ] missing required symbol properties (Reference/Value)
  - [ ] malformed coords/at
  - [ ] missing outline / invalid outline (pcb)
  - [ ] layer declarations on generated primitives
- [ ] Add a clear mapping from lint code -> fix suggestion.

---

# Phase 4 — Schematic Readability: Layout + Wiring Engine (Major Feature)

## Goal
Replace the current schematic style “place one-by-one and label stubs” with a real **graph layout** and **graph wiring** so the schematic reads like a conventional circuit diagram.

### 4.1 Introduce a schematic layout subsystem
- [ ] Define a `LayoutEngine` interface:
  - [ ] `compute_symbol_positions(ir) -> dict[ref, (x, y, rot?)]`
  - [ ] optional: `compute_net_routes(...)` (or handled separately)
- [ ] Add config/CLI option:
  - [ ] `--layout auto|graphviz|heuristic|none`
  - [ ] default `auto` (Graphviz if available, else heuristic)
- [ ] Keep layout independent of KiCad AST serialization logic.

### 4.2 Graphviz integration (bundled binary)
- [ ] Add `graphviz/` (or similar) module for calling `dot`.
- [ ] Support a configured/bundled `dot` path:
  - [ ] default to a bundled binary within the repo/distribution
  - [ ] allow override via env var or config (e.g., `GRAPHVIZ_DOT`)
- [ ] Convert circuit IR into a Graphviz DOT graph:
  - [ ] nodes: symbols (refdes) and/or nets (see 4.3)
  - [ ] edges: connectivity (symbol-pin to net, or symbol-symbol simplified)
  - [ ] use `rankdir=LR` to encourage left-to-right flow
  - [ ] use clusters for functional blocks (optional initial: per channel L/R)
- [ ] Parse Graphviz output positions (`dot -Tplain` recommended) into coordinates.
- [ ] Map Graphviz coordinate space to KiCad coordinate space:
  - [ ] apply scaling factor
  - [ ] add margins
  - [ ] snap to KiCad grid if desired
- [ ] Add caching:
  - [ ] save computed layout to a JSON file under managed outputs (optional)
  - [ ] deterministic layout seeds if possible

### 4.3 Decide the graph model for layout (important)
Implement one of these models (or both, with a config):

- [ ] **Bipartite model (recommended initially):**
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
- [ ] Treat power nets specially:
  - [ ] omit from Graphviz graph or compress them
  - [ ] place power symbols separately (top/bottom rails)

### 4.4 Add a heuristic fallback layout engine (must exist)
Even if Graphviz is bundled, keep a heuristic engine for:
- [ ] minimal dependencies / troubleshooting
- [ ] predictable staging for certain circuit types (audio/analog)

Heuristic engine tasks:
- [ ] Identify “stages” from IR using:
  - [ ] net name patterns (IN/OUT, V+/V-, GND)
  - [ ] graph distance from input nets to output nets
- [ ] Place stages left-to-right:
  - [ ] inputs left, outputs right
  - [ ] op-amp centered per stage
  - [ ] feedback parts close to op-amp pins
  - [ ] decoupling caps near IC
  - [ ] mirrored left/right channels (if IR indicates L/R)
- [ ] Ensure no symbol bounding-box overlaps (approx bounding boxes are fine).

### 4.5 Replace “label stubs everywhere” with real wires and junctions
Implement a **wiring engine** that draws actual wires for readability.

- [ ] Create rules per net degree:
  - [ ] degree == 2: draw a direct orthogonal wire between the two pins
  - [ ] degree 3–6: create a hub/junction spine and connect pins to it
  - [ ] high-degree nets (GND, VCC, etc.): use power symbols/global labels; avoid spaghetti
- [ ] Reduce label duplication:
  - [ ] at most one label per net within a local region by default
  - [ ] only label nets that cross blocks or are semantically important
- [ ] Add junctions explicitly where wires meet (KiCad `(junction ...)` nodes as needed).
- [ ] Add optional “bus” style later (out of scope for initial implementation).

### 4.6 Add schematic-layout lints (readability gates)
Add new lint codes to detect “unusable” layout patterns.

- [ ] `LAY001`: net label appears more than N times (configurable)
- [ ] `LAY002`: >X% of wires shorter than stub threshold (indicates label-stub style)
- [ ] `LAY003`: overlapping symbols (approx bounding boxes)
- [ ] `LAY004`: symbols placed outside page bounds
- [ ] `LAY005`: too many disconnected visual islands (heuristic using wire graph)
- [ ] Enforce these lints at `--validate lint|full` for schematic generation commands.

### 4.7 Add rotation/orientation rules (optional but improves readability)
- [ ] Rotate resistors/caps based on wire direction (horizontal vs vertical).
- [ ] Keep op-amps oriented consistently (inputs left, outputs right).
- [ ] Keep connectors at edges (inputs left, outputs right, power top/bottom).

---

# Phase 5 — Graphviz Licensing and Distribution Notes

### 5.1 Add licensing documentation for bundled Graphviz
- [ ] Add `THIRD_PARTY_NOTICES.md` (or update existing) to include:
  - [ ] Graphviz name/version
  - [ ] license name and link
  - [ ] redistribution notice requirements (as applicable)
- [ ] Add a short note in README:
  - [ ] Graphviz is bundled as an external tool for schematic layout
  - [ ] how to replace it with a system-installed Graphviz later
  - [ ] how to override `dot` path

### 5.2 Make bundling strategy explicit in code
- [ ] Centralize discovery of `dot` binary:
  - [ ] bundled path first
  - [ ] env override
  - [ ] PATH lookup as last resort
- [ ] Provide `doctor` output with which path is used.

---

# Phase 6 — Tests: Unit, Golden, Integration

### 6.1 Unit tests for layout engine(s)
- [ ] For a small IR fixture:
  - [ ] Graphviz engine produces non-overlapping positions
  - [ ] positions are stable (deterministic enough for tests; use fixed seeds/options)
- [ ] Heuristic engine:
  - [ ] places input-left/output-right
  - [ ] places feedback components near op-amp
  - [ ] mirrors L/R channels when present

### 6.2 Unit tests for wiring engine
- [ ] degree-2 net -> produces a single wire connecting pins
- [ ] degree-3+ net -> hub/junction wiring created
- [ ] power net -> power symbol/global label path used
- [ ] label duplication capped by policy

### 6.3 Golden schematic tests (critical)
- [ ] Add golden expected `.kicad_sch` outputs for:
  - [ ] simple resistor divider
  - [ ] op-amp inverting/non-inverting stage
  - [ ] small audio block (subset of headphone amp)
- [ ] Compare canonical serialized output (or AST equivalence) to catch regressions.

### 6.4 Integration tests (skip if tools missing)
- [ ] If `kicad-cli` installed:
  - [ ] generated schematic passes ERC
- [ ] If bundled `dot` is present:
  - [ ] Graphviz engine runs end-to-end
- [ ] Add a single end-to-end “generate from IR -> schematic -> ERC” test for CI.

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
