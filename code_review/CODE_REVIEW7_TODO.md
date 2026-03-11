# CODE_REVIEW7_TODO.md

## Objective

Fix the schematic-layout regression in the latest `kicad-pcb` code where many components have drifted back into a narrow column with the op-amp, making the schematic less readable than the previous iteration.

This TODO focuses on:
- diagnosing why the layout regressed,
- reducing over-aggressive same-column constraints,
- preventing hidden fallback-style behaviors from collapsing the layout,
- and restoring a more human-readable block-oriented composition.

---

## Guiding Principles

- The Graphviz pipeline should remain the primary layout engine.
- Do **not** reintroduce a separate layout fallback engine.
- Remove or weaken internal fallback-style behaviors when they damage readability.
- Prefer block-aware placement and local affinity over naive same-column forcing.
- Add regression tests so this exact “op-amp column stack” failure does not return.

---

# Phase 0 — Capture and Measure the Regression

### 0.1 Save the latest bad output as a regression fixture
- [x] Add the current generated schematic to:
  - [x] `tests/fixtures/readability/ne5532_headphone_amp_left_regressed/`
- [x] Include:
  - [x] generated `.kicad_sch`
  - [x] source IR / netlist JSON
  - [x] optional screenshot/SVG reference

### 0.2 Add regression README
- [x] Document the regression symptoms:
  - [x] many parts lined up with U1 in one vertical column
  - [x] weaker block separation than previous iteration
  - [x] op-amp neighborhood too tall and narrow
  - [x] composition worse than previous version

### 0.3 Add layout metrics specific to this regression
- [x] Add helper metric(s):
  - [x] count how many non-power components share the same X column as the op-amp
  - [x] count how many feedback/support components are forced into the exact op-amp column
  - [x] measure spread of components by role/block
- [x] Add a baseline metrics JSON for the regressed output.

---

# Phase 1 — Confirm the Actual Regression Path in Code

## Goal
Prove exactly where the column regression comes from before changing behavior.

### 1.1 Instrument layout decisions in debug mode
- [x] Add debug logging or dump files for:
  - [x] computed tiers
  - [x] connector roles
  - [x] SDS columns
  - [x] halo map
  - [x] decoupling map
  - [x] raw Graphviz positions
  - [x] final post-snap positions
- [x] Make this available behind a debug/config flag to avoid noisy normal runs.

### 1.2 Confirm whether SDS/BFS fallback is being hit
**File:** `src/kicad_pcb/layout.py`

- [x] Add explicit debug output indicating whether:
  - [x] SDS recursive-halving mode is used
  - [x] BFS fallback mode is used
- [x] Add a test that exercises the headphone amp fixture and records which path is used.
- [x] If fallback is hit, identify exactly why connector-role information is considered incomplete.

Confirmed from the live Phase 1 dump for the regressed NE5532 left-channel fixture:
- only `J1` is classified as `input`
- `J2` and `J3` are classified as `unknown`
- no connector is classified as `output`
- the legacy SDS path would therefore degrade to BFS fallback for this fixture

### 1.3 Confirm whether halo forcing is collapsing the op-amp neighborhood
**Files:**
- `src/kicad_pcb/layout.py`
- `src/kicad_pcb/graphviz_layout/dot_builder.py`

- [x] Add debug output listing:
  - [x] halo member -> anchor mappings
  - [x] components forced into anchor column
- [x] Add a test showing how many refs are forced into U1’s column.

Confirmed from the live Phase 1 dump for the regressed NE5532 left-channel fixture:
- halo map anchored to `U1`: `C6 -> U1`, `R2 -> U1`
- these halo refs are already in the same raw Graphviz column as `U1`
- post-snap `U1` column still contains 9 non-power refs total

---

# Phase 2 — Reduce Over-Aggressive Same-Column Halo Forcing

## Goal
Keep related parts near the op-amp without stacking them all in the same column.

### 2.1 Audit same-column forcing in `layout.py`
- [x] Find the logic that sets:
  - [x] halo member column = anchor column
- [x] Replace hard same-column forcing with a softer rule:
  - [x] same block / adjacent column allowed
  - [x] nearest-side placement preferred
  - [x] only the most critical feedback parts may remain same-column if truly necessary

### 2.2 Audit halo constraints in DOT generation
**File:** `src/kicad_pcb/graphviz_layout/dot_builder.py`

- [x] Review `_emit_halo_constraints(...)`
- [x] Reduce constraint strength so halo members are:
  - [x] near the op-amp,
  - [x] but not necessarily in the exact same rank/column
- [x] Prefer invisible affinity edges or weighted attraction over hard column locking where possible.

### 2.3 Add regression tests for op-amp column crowding
- [x] Add a test that asserts:
  - [x] only a limited number of parts may share U1’s exact X column
- [x] Example target:
  - [x] `non_power_parts_in_u1_column <= threshold`
- [x] Tune threshold based on a cleaner reference layout.

Completed on 2026-03-11:
- softened same-column halo behavior in `layout.py`, `graphviz_layout/dot_builder.py`, and `graphviz_layout/snap.py`
- fixed the remaining re-collapse in `_snap_opamp_locality(...)` so halo members stay adjacent to the anchor IC instead of being forced back onto `ic_x`
- added unit/integration coverage for the canonical NE5532 regression fixture

---

# Phase 3 — Strengthen Block-Aware Placement Instead of Column Locking

## Goal
Restore human-readable block layout so parts do not collapse into a graph-theoretic pillar.

### 3.1 Implement real block detection if still missing
- [x] Add block classification for the headphone amp fixture:
  - [x] input block
  - [x] op-amp stage
  - [x] output block
  - [x] power/decoupling block
  - [x] feedback/support sub-block
- [x] Base it on:
  - [x] component types
  - [x] net names
  - [x] graph proximity to U1, connectors, and power nets

### 3.2 Add block-aware Graphviz zoning
- [x] Extend Graphviz constraints so blocks prefer zones:
  - [x] input left
  - [x] op-amp center
  - [x] output right
  - [x] power/decoupling above/top-left/top-center
- [x] Use softer constraints than same-column locks.

### 3.3 Add post-Graphviz block refinement
- [x] After Graphviz placement:
  - [x] nudge blocks apart
  - [x] keep support parts near their stage
  - [x] prevent feedback/power parts from collapsing into one narrow vertical stack

### 3.4 Add tests for block spread
- [x] Assert that:
  - [x] input block is left of U1
  - [x] output block is right of U1
  - [x] power block is not merged into the main op-amp column
  - [x] feedback parts are near U1 but not all same-column

Completed on 2026-03-11:
- added path/proximity-based block classification in `kicad-pcb/src/kicad_pcb/block_detection.py`
- threaded block-aware zoning through the Graphviz/snap pipeline in `layout.py` and `graphviz_layout/snap.py`
- added post-Graphviz refinement for block zones, stage cohesion, page balance, and central composition
- added regression coverage in `tests/unit/test_block_detection.py` for block roles, block-zone snapping, and left/right stage placement

---

# Phase 4 — Tighten Connector Role Detection So Layout Does Not Degrade

## Goal
Prevent internal fallback-style behavior from silently degrading placement.

### 4.1 Review connector role classification
**File:** `src/kicad_pcb/tier.py`

- [x] Audit `classify_connector_roles(...)`
- [x] Verify the headphone amp fixture correctly identifies:
  - [x] audio input connector
  - [x] audio output connector
  - [x] power connector
- [x] Improve heuristics for stereo jacks / generic power connectors if needed.

### 4.2 Eliminate accidental BFS degradation for this fixture
**File:** `src/kicad_pcb/layout.py`

- [x] If BFS fallback is still reachable for the headphone amp fixture:
  - [x] fix role detection
  - [x] or hard-fail in strict mode rather than silently degrading
- [x] Add a test that the headphone amp fixture uses the intended signal-distance / connector-aware path.

Note:
- The legacy warning-plus-BFS degradation branch in `layout.py` was removed on 2026-03-11.
- Remaining work here is limited to stronger diagnostics when internal degradation-like behavior is encountered.

Completed on 2026-03-11:
- `classify_connector_roles(...)` now accepts optional IR context and uses connector metadata / net-name hints to distinguish `input`, `output`, and `power` connectors when topology alone is ambiguous
- the canonical NE5532 regression fixture now classifies `J1=input`, `J2=output`, `J3=power`
- Phase 1 debug dumps now expose `power_refs` separately, and the canonical fixture no longer reports missing connector roles or would-trigger-BFS degradation

### 4.3 Improve diagnostics
- [ ] When the layout path falls back internally:
  - [ ] surface a warning or debug note
  - [ ] explain why
- [ ] Avoid silent quality regressions.

---

# Phase 5 — Revisit Power-Only and Support-Part Clustering

## Goal
Prevent support parts from being dumped into simplistic clusters that hurt readability.

### 5.1 Audit `cluster_power` usage in DOT output
**File:** `src/kicad_pcb/graphviz_layout/dot_builder.py`

- [ ] Review which refs are classified as `power_only_refs`
- [ ] Confirm whether any support parts that should stay near U1 are being over-clustered as “power only”
- [ ] Refine classification so power/support grouping does not distort the signal-stage layout.

### 5.2 Reposition decoupling/support parts more intentionally
- [ ] Keep true decouplers near op-amp power pins.
- [ ] Keep output/input support parts near their signal role, not grouped just because they touch power/ground.
- [ ] Add tests distinguishing:
  - [ ] decoupling caps
  - [ ] signal coupling caps
  - [ ] power connector support parts

---

# Phase 6 — Improve Readability of the Op-Amp Neighborhood

## Goal
Make the U1 area read like an analog stage instead of a stacked trunk.

### 6.1 Create local placement rules around U1
- [x] Input-side parts on input side of U1
- [x] Output-side parts on output side of U1
- [x] Feedback parts adjacent to relevant op-amp pins
- [x] Decouplers close to power pins but visually separated from feedback loop

### 6.2 Limit vertical stacking around U1
- [x] Add a local spread rule so the neighborhood around U1 uses multiple nearby columns/rows when helpful.
- [ ] Avoid placing too many passives directly above/below U1 in the exact same x-coordinate band.

### 6.3 Add op-amp neighborhood tests
- [x] Assert feedback components are near U1.
- [x] Assert output-side parts are right-biased relative to U1.
- [x] Assert support parts do not all share one x-position with U1.

Partially completed on 2026-03-11:
- added op-amp locality, input-stage cohesion, output-stage cohesion, and late deoverlap protections in `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py`
- added targeted coverage in `tests/unit/test_phase4_layout.py` for locality, stage separation, left-to-right flow, and no-IC output alignment
- remaining work is mostly heuristic hardening to further reduce tall/narrow op-amp stacks in difficult fixtures

---

# Phase 7 — Add Regression Tests for “Looks Worse Than Before”

## Goal
Stop future commits from silently reintroducing this regression.

### 7.1 Compare against previous better layout
- [x] Add a previous “better” schematic fixture as reference if available.
- [x] Use approximate metrics, not exact coordinate diff.

Completed on 2026-03-11:
- no earlier saved NE5532 "better" fixture was available, so Phase 7 guardrails compare the current generator output for the canonical regression IR against the captured bad snapshot in `tests/fixtures/readability/ne5532_headphone_amp_left_regressed/`
- added approximate metric comparisons in `tests/unit/test_phase7_regression_guardrails.py` instead of exact coordinate matching

### 7.2 Add metrics thresholds
- [x] Distinct x-columns should not regress below threshold.
- [x] Components in U1 column should not exceed threshold.
- [x] Block spread should not collapse.
- [x] Short-wire ratio should not increase dramatically.
- [x] Layout lints should not worsen.

### 7.3 Add human-review checklist for this regression
- [x] Does the schematic again look like a column dump around U1?
- [x] Are input/output/power blocks distinguishable?
- [x] Is the op-amp neighborhood still too narrow/tall?
- [x] Does this look worse than the previous iteration?

Completed on 2026-03-11:
- added `tests/fixtures/readability/ne5532_headphone_amp_left_regressed/PHASE7_HUMAN_REVIEW_CHECKLIST.md` for manual regression review of the canonical NE5532 fixture

---

# Phase 8 — Code Hygiene / Architectural Cleanup

## Goal
Remove mismatches between docs, assumptions, and implementation.

### 8.1 Remove stale “fallback engine” assumptions from docs/comments
- [ ] Update docs/comments to clearly distinguish:
  - [ ] no separate layout fallback engine
  - [ ] internal fallback algorithms may still exist
- [ ] Document where fallback-like behavior still lives inside the Graphviz pipeline.

### 8.2 Make Graphviz binary strategy explicit
**File:** `src/kicad_pcb/graphviz_layout/__init__.py`

- [ ] If Graphviz is supposed to be bundled, actually bundle it or correct the docs/comments.
- [ ] If PATH/env is the intended strategy, document it clearly.

### 8.3 Add debug artifacts for future regressions
- [ ] Save optional debug JSON for:
  - [ ] tiers
  - [ ] connector roles
  - [ ] block classification
  - [ ] raw positions
  - [ ] final positions
- [ ] This should make future layout regressions much easier to diagnose.

---

## Suggested Implementation Order

1. [x] Phase 0 — capture regression fixture and metrics
2. [x] Phase 1 — instrument and prove actual regression path
3. [x] Phase 2 — weaken same-column halo forcing
4. [x] Phase 4 — confirm/fix connector role path and fallback behavior
5. [x] Phase 3 — add block-aware zoning and post-Graphviz refinement
6. [ ] Phase 5 — refine power/support clustering
7. [ ] Phase 6 — improve op-amp neighborhood layout
8. [x] Phase 7 — lock in regression tests
9. [ ] Phase 8 — clean up docs/comments and debug support

---

## Definition of Done

The headphone amp schematic should:

- [x] no longer line up a large fraction of parts in the op-amp column
- [x] visibly separate input, op-amp, output, and power/support regions
- [x] keep feedback/support parts near U1 without stacking them all vertically with it
- [ ] avoid internal fallback-style degradation for the canonical headphone amp fixture
- [x] include regression tests that catch this exact layout collapse in future
