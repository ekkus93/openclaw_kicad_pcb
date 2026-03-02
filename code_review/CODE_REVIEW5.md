# CODE_REVIEW1.md

## Context and Goal

The `kicad-pcb` OpenClaw skill currently succeeds at:
- producing a correct netlist JSON / IR, and
- generating a KiCad schematic file that loads without syntax errors,

…but the produced schematic is **not usable for humans** because the layout is essentially:
- place symbols sequentially in a grid/list,
- attach tiny “stub” wires to each pin,
- put a net label on each stub,
- repeat labels instead of drawing a connected circuit graph.

The goal of this work is to make the skill:
1) **Rock-solid** for file correctness (never write broken KiCad files).
2) **Readable** in schematic output (a real circuit diagram graph).

---

## Summary of Code Review (What’s Good)

### Structured S-expression handling
We agreed that KiCad files are S-expression-based and must be edited structurally.
The refactor direction is:
- parse -> mutate AST -> serialize -> validate -> commit
No regex-based structural editing.

### Transactional write pipeline
We agreed that it’s unacceptable for the tool to corrupt projects with broken outputs.
All mutating operations must be transactional:
- temp write
- re-parse
- lint
- optional `kicad-cli` ERC/DRC
- atomic commit only on success

### Linting + external validation
We agreed to enforce:
- syntax validation (parse)
- structural lints (custom rules for common mistakes)
- external validation via `kicad-cli` (ERC/DRC) when available
Broken output must be rejected before overwrite.

### Testability
We agreed the architecture must be unit-testable:
- thin CLI layer
- injected adapters for filesystem/subprocess
- unit tests for parser/doc/lint/pipeline
- golden/regression fixtures for known failures
- integration tests with KiCad tools when available

---

## The Biggest Current Problem: Unusable Schematic Layout

### What the current generator does
- Places components one-by-one (grid/list placement).
- Represents connectivity primarily by **duplicated net labels**.
- Creates mostly short stub wires (pin -> label) rather than drawing the circuit.

This produces a schematic that is electrically correct but visually unreadable. Humans expect a circuit diagram:
- connected wires,
- junctions,
- meaningful grouping (stages/blocks),
- power rails handled cleanly,
- minimal net labels used only where they help.

---

## Agreed Solution: Add a Layout + Wiring Engine

We agreed schematic generation needs two major subsystems:

### 1) Placement (Layout Engine)
Given a netlist IR, compute `(x, y, rot)` for each symbol in a way that produces a readable diagram.

Options discussed:
- heuristic placement (fast, domain-informed; especially good for analog/audio)
- Graphviz-based graph layout (generic, strong baseline for arbitrary circuits)

We decided to use **Graphviz** (bundled binary for now) as the primary layout engine, with a heuristic fallback.

### 2) Wiring (Wiring Engine)
Replace “label stubs everywhere” with actual wires/junctions:
- 2-pin nets -> direct wire
- medium degree nets -> junction hub/spine
- high-degree/power nets -> power symbols/global labels to avoid spaghetti
Net labels should be used sparingly.

---

## Graphviz Decision and Licensing Note

We decided:
- Use Graphviz (`dot`) as an **external tool** invoked by the skill.
- For now, bundling the Graphviz binaries is acceptable for project simplicity.
- Add explicit **third-party licensing notices** documenting Graphviz license and redistribution requirements.
- Keep the code modular enough to later switch to system-installed Graphviz or a different layout backend if needed.

---

## Regression Fixtures (Important Point from Copilot)

We agreed to capture real broken examples as regression fixtures:
- known `(id N)` format issue
- sub-symbol naming issue
- formatting/indentation-dependent failures

These fixtures ensure that once fixed, these failures never return silently.

---

## Relationship to `CODE_REVIEW1_TODO.md`

`CODE_REVIEW1_TODO.md` is the execution checklist derived from this discussion. It includes:
- finishing the correctness/validation foundation
- adding Graphviz layout integration
- implementing wiring rules for readability
- adding layout-specific lints
- unit + golden + integration tests
- licensing/notice items for bundled Graphviz

This file (`CODE_REVIEW1.md`) exists to give implementers (Github Copilot) the rationale and background so they understand why the TODO items matter and how to prioritize them.
