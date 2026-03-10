# Copilot Clarifications for CODE_REVIEW6

## 1. Baseline fixture — which schematic?

Use a **fresh generated schematic from the existing headphone amp IR/netlist test case**, not a hand-picked file from the repo.

### Decision
- Primary baseline = **generate from the current canonical headphone amp IR/netlist fixture**
- Save that generated output as the **before/failing readability fixture**
- Keep the source IR/netlist as the real source of truth

### Reason
- avoids stale checked-in outputs drifting from code
- makes the test reproducible
- keeps the workflow aligned with the actual generator path

### Concrete instruction
- If there is already a stable headphone-amp IR/netlist fixture in tests, use that.
- If not, create one from the existing left-channel headphone amp case we’ve been discussing.
- Generate the schematic with the current code, store it under the readability fixture directory, and document that it is the **baseline auto-generated output before readability improvements**.

---

## 2. Component role/block information in current IR

Assume **functional classification is heuristic** unless the current IR already explicitly contains role tags.

### Working assumption
- No required block membership tags
- No required semantic roles like `feedback`, `decoupling`, `input`
- Infer from:
  - component type / symbol family
  - refdes patterns
  - net names
  - graph topology around active devices, connectors, and power nets

If the IR already has useful metadata, use it opportunistically, but do **not** make the new readability system depend on it.

### Reason
- the readability layer should work on current IR without requiring an IR schema migration first

---

## 3. Integration point with existing layout engine

Do this in **three layers**, in this order:

### First: extend Graphviz inputs/constraints
This should be the primary integration point.

Add:
- block-aware constraints
- stronger left-to-right zoning
- preferred page zones for input / op-amp / output / power
- any clustering hints that improve macro placement

### Second: add a post-Graphviz refinement pass
Use this for:
- de-crowding
- whitespace balancing
- op-amp neighborhood cleanup
- page-bounds and composition balancing
- minor block separation fixes

### Third: do not reintroduce a separate heuristic fallback engine
Do not add or rely on a separate heuristic layout engine for this work. Keep the implementation focused on the existing Graphviz-based layout pipeline.

### Final answer
- **primary:** extend Graphviz layout constraints
- **secondary:** add post-Graphviz refinement
- **do not add:** a separate heuristic fallback engine

---

## 4. Scope priority

Do **not** implement all 10 phases blindly in one pass.

### Priority order

#### Highest priority
- Phase 0: baseline fixture + metrics helpers
- Phase 1: functional block detection + block layout zones
- Phase 2: reduce crowding / improve whitespace
- Phase 3: strengthen signal flow
- Phase 4: clean up op-amp neighborhood

#### Next
- Phase 6: reduce short joggy wires
- Phase 5: reduce ground/power clutter

#### Later polish
- Phase 7: improve input/output staging
- Phase 8: page composition
- Phase 9: orientation consistency
- Phase 10: golden tests + human review loop

### Practical instruction
- Start with a proof-of-concept covering **Phases 0–4**
- Stop there and regenerate the headphone amp schematic
- Re-evaluate visually and metrically before continuing

That gives the biggest readability improvement fastest.

---

## 5. Visual validation

Use **both** metric-based thresholds and manual visual review.

### Required automated validation
Use metrics in tests for:
- local density reduction
- block separation
- x-ordering of input/op-amp/output
- GND symbol count
- short wire count / jog count
- page balance heuristics
- no overlap / no out-of-bounds

### Required manual validation
Also regenerate the headphone amp schematic and inspect it visually.

### Reason
- readability is not fully capturable by metrics
- metrics prevent regressions
- manual review confirms the schematic actually feels more human-drafted

### Final answer
- automated tests are the regression gate
- manual screenshot review is the quality check during iteration

---

## Extra instruction for Copilot

Please do **not** start by inventing a large new IR schema.

Instead:
1. build the baseline fixture and metrics
2. add heuristic block classification on top of the current IR
3. thread that classification into Graphviz zoning/constraints
4. add a refinement pass after Graphviz
5. regenerate and compare against the baseline

That keeps the work incremental and lowers risk.

---

## Pasteable reply to Copilot

Use the existing headphone amp IR/netlist test case as the canonical source. Generate a fresh schematic from it and save that as the baseline readability fixture. Do not rely on a manually curated checked-in schematic as the primary source of truth.

Assume functional classification is heuristic unless the current IR already has tags. Do not require IR schema changes to start this work.

Integrate the readability improvements primarily by extending Graphviz layout constraints, then add a post-Graphviz refinement pass for de-crowding, whitespace, op-amp-local cleanup, and page composition. Keep the heuristic engine as fallback, not the primary implementation target.

Do not try to implement all 10 phases in one pass. Start with Phases 0–4 only, regenerate the headphone amp schematic, and reassess before continuing.

Use both automated metrics and manual visual review. Metrics should be the regression gate; regenerated schematic screenshots should be used for human readability review.
