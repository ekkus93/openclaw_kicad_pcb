import zipfile
from pathlib import Path

review = """# CODE_REVIEW7.md

## Context

This review covers the latest `kicad-pcb` codebase in
`openclaw_kicad_pcb-master.zip` and the generated schematic in
`NE5532_Headphone_Amp_Left_schematic.zip`.

The newest output is a regression from the previous readability pass. The
generated schematic has drifted back toward a **column-oriented placement**
where many parts line up vertically with the op-amp, and the page composition
again looks auto-generated rather than intentionally drafted.

This is not a total return to the original worst-case “pure netlist dump,” because:
- Graphviz routing is still active,
- bus/spine routing is still present,
- power is using symbols instead of repeated `GND` labels in this output,

but the layout has clearly regressed and is no longer using space or block structure well.

---

## What is wrong with the generated schematic

### 1) Too many parts are stacked in the same op-amp column
The output still has multiple X columns overall, but too many important parts
are clustered into the same main X column as the op-amp.

Concrete signs from the generated schematic:
- U1 is placed in the same visual column as many resistors/capacitors.
- The result looks like a vertical “pillar” of parts around the op-amp rather
  than a staged analog circuit.

This is the strongest visible regression.

### 2) Feedback/halo logic is over-constraining placement
The current code appears to force too many related parts into the same column
as the op-amp instead of merely keeping them nearby.

That makes the op-amp neighborhood too narrow and tall.

### 3) Functional block separation is still not actually implemented
The code review docs discuss:
- input block
- output block
- power/decoupling block
- op-amp stage block

But the current code does not yet appear to have a real block-detection /
block-zoning layer wired into layout. So the layout engine is still mostly
working from tiers, connector roles, net structure, and Graphviz constraints
alone.

This means the schematic is still missing explicit:
- input-zone anchoring,
- output-zone anchoring,
- power-zone anchoring,
- local grouping around active stages.

### 4) Internal fallback behavior still exists inside the layout logic
Even if the external fallback engine was removed, there are still fallback-style
behaviors inside the layout pipeline.

Notable examples:
- `layout.py` still contains SDS/BFS fallback logic when connector-role
  information is missing or incomplete.
- Graph-level tiering/column assignment can still fall back to simpler
  behavior if the expected connector role structure is not available.

So while there may no longer be a separate “fallback engine,” there are still
fallback **algorithms** inside the pipeline.

### 5) Halo/feedback constraints are likely too aggressive
The code currently has multiple mechanisms that co-locate components near the anchor IC:
- Graphviz halo constraints in `graphviz_layout/dot_builder.py`
- explicit same-column forcing in `layout.py`

These are likely over-applied and are a strong candidate for the “everything
lines up with U1” regression.

### 6) Power-only / support-part clustering is still too simplistic
The DOT builder still puts power-only refs into a dedicated cluster/rank. That
may be acceptable for pure supply support, but in practice some support parts
still need better local placement relative to the signal path and op-amp.

### 7) The routing is not the main problem now
This is an important distinction:
- earlier, routing and label-stub fallback were the dominant issue
- now, the bigger problem is **placement constraints and composition**

The current regression is more about **where parts are placed** than whether nets are drawn at all.

### 8) The schematic still lacks intentional page composition
The page still does not read as:
- input stage on the left,
- op-amp/gain stage in the center,
- output stage on the right,
- power/decoupling clearly above or aside.

Instead, it reads as a graph whose constraints are being satisfied without a true drafting plan.

---

## Code-level findings

### `_sch_apply.py`
This file is no longer missing the `tiers` / `positions` / `use_bus` wiring.
That earlier bug appears fixed.

This means the regression is **not** due to simply forgetting to pass routing
context into `route_nets()`.

### `router.py`
`route_nets(..., use_bus=True)` is now the default and is being called with tier/position info.

So router fallback is not the primary cause of the column regression.

### `commands/_sch_apply.py::_resolve_layout()`
The command layer only allows `"graphviz"`. So there is no obvious
command-level fallback engine path here.

### `layout.py`
This file still contains fallback behavior internally:
- SDS recursive halving when roles exist
- BFS fallback when connector roles are incomplete
- explicit column forcing for halo members

This is a likely contributor to the regression.

### `graphviz_layout/dot_builder.py`
This file still emits:
- rank groupings
- connector rank constraints
- halo constraints
- cluster_power

These constraints may now be over-constraining the layout into narrow columns.

### `graphviz_layout/__init__.py`
The Graphviz engine is still the active engine, but the code comment explicitly
says no bundled binary is currently present and discovery falls through to
env/PATH. That is not necessarily causing this regression, but it is a mismatch
with earlier assumptions about bundled Graphviz.

### Missing implementation relative to prior plan
The code review/TODO docs talk about:
- block detection
- block-level zoning
- density reduction
- readability-aware refinement

Those do not appear to be fully implemented yet, which explains why Graphviz
alone is still producing awkward results.

---

## High-confidence likely causes of the regression

### Most likely cause #1
**Halo / same-column forcing around the op-amp is too aggressive.**

This is likely the biggest direct cause of the op-amp-centered column stack.

### Most likely cause #2
**The layout still lacks explicit block-aware zoning.**

Without real block placement, Graphviz plus tiers will still tend to produce
compact graph-centric rather than human-centric placement.

### Most likely cause #3
**The SDS/BFS internal fallback logic is still influencing placement.**

Even if there is no external fallback engine, internal fallback column
assignment can still produce degraded layouts if connector-role information is
incomplete or not strong enough.

---

## What should happen next

The fix should focus first on:
1. reducing/opting down same-column halo forcing,
2. proving whether connector-role/SDS fallback is being hit,
3. adding explicit block zoning before or during Graphviz layout,
4. adding regression tests for “too many parts share the op-amp column.”

The next TODO file is designed around those priorities.
"""

todo = """# CODE_REVIEW7_TODO.md

## Objective

Fix the schematic-layout regression in the latest `kicad-pcb` code where many
components have drifted back into a narrow column with the op-amp, making the
schematic less readable than the previous iteration.

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
- [ ] Add the current generated schematic to:
  - [ ] `tests/fixtures/readability/ne5532_headphone_amp_left_regressed/`
- [ ] Include:
  - [ ] generated `.kicad_sch`
  - [ ] source IR / netlist JSON
  - [ ] optional screenshot/SVG reference

### 0.2 Add regression README
- [ ] Document the regression symptoms:
  - [ ] many parts lined up with U1 in one vertical column
  - [ ] weaker block separation than previous iteration
  - [ ] op-amp neighborhood too tall and narrow
  - [ ] composition worse than previous version

### 0.3 Add layout metrics specific to this regression
- [ ] Add helper metric(s):
  - [ ] count how many non-power components share the same X column as the op-amp
  - [ ] count how many feedback/support components are forced into the exact op-amp column
  - [ ] measure spread of components by role/block
- [ ] Add a baseline metrics JSON for the regressed output.

---

# Phase 1 — Confirm the Actual Regression Path in Code

## Goal
Prove exactly where the column regression comes from before changing behavior.

### 1.1 Instrument layout decisions in debug mode
- [ ] Add debug logging or dump files for:
  - [ ] computed tiers
  - [ ] connector roles
  - [ ] SDS columns
  - [ ] halo map
  - [ ] decoupling map
  - [ ] raw Graphviz positions
  - [ ] final post-snap positions
- [ ] Make this available behind a debug/config flag to avoid noisy normal runs.

### 1.2 Confirm whether SDS/BFS fallback is being hit
**File:** `src/kicad_pcb/layout.py`

- [ ] Add explicit debug output indicating whether:
  - [ ] SDS recursive-halving mode is used
  - [ ] BFS fallback mode is used
- [ ] Add a test that exercises the headphone amp fixture and records which path is used.
- [ ] If fallback is hit, identify exactly why connector-role information is considered incomplete.

### 1.3 Confirm whether halo forcing is collapsing the op-amp neighborhood
**Files:**
- `src/kicad_pcb/layout.py`
- `src/kicad_pcb/graphviz_layout/dot_builder.py`

- [ ] Add debug output listing:
  - [ ] halo member -> anchor mappings
  - [ ] components forced into anchor column
- [ ] Add a test showing how many refs are forced into U1’s column.

---

# Phase 2 — Reduce Over-Aggressive Same-Column Halo Forcing

## Goal
Keep related parts near the op-amp without stacking them all in the same column.

### 2.1 Audit same-column forcing in `layout.py`
- [ ] Find the logic that sets:
  - halo member column = anchor column
- [ ] Replace hard same-column forcing with a softer rule:
  - [ ] same block / adjacent column allowed
  - [ ] nearest-side placement preferred
  - [ ] only the most critical feedback parts may remain same-column if truly necessary

### 2.2 Audit halo constraints in DOT generation
**File:** `src/kicad_pcb/graphviz_layout/dot_builder.py`

- [ ] Review `_emit_halo_constraints(...)`
- [ ] Reduce constraint strength so halo members are:
  - [ ] near the op-amp,
  - [ ] but not necessarily in the exact same rank/column
- [ ] Prefer invisible affinity edges or weighted attraction over hard column
  locking where possible.

### 2.3 Add regression tests for op-amp column crowding
- [ ] Add a test that asserts:
  - [ ] only a limited number of parts may share U1’s exact X column
- [ ] Example target:
  - [ ] `non_power_parts_in_u1_column <= threshold`
- [ ] Tune threshold based on a cleaner reference layout.

---

# Phase 3 — Strengthen Block-Aware Placement Instead of Column Locking

## Goal
Restore human-readable block layout so parts do not collapse into a graph-theoretic pillar.

### 3.1 Implement real block detection if still missing
- [ ] Add block classification for the headphone amp fixture:
  - [ ] input block
  - [ ] op-amp stage
  - [ ] output block
  - [ ] power/decoupling block
  - [ ] feedback/support sub-block
- [ ] Base it on:
  - [ ] component types
  - [ ] net names
  - [ ] graph proximity to U1, connectors, and power nets

### 3.2 Add block-aware Graphviz zoning
- [ ] Extend Graphviz constraints so blocks prefer zones:
  - [ ] input left
  - [ ] op-amp center
  - [ ] output right
  - [ ] power/decoupling above/top-left/top-center
- [ ] Use softer constraints than same-column locks.

### 3.3 Add post-Graphviz block refinement
- [ ] After Graphviz placement:
  - [ ] nudge blocks apart
  - [ ] keep support parts near their stage
  - [ ] prevent feedback/power parts from collapsing into one narrow vertical stack

### 3.4 Add tests for block spread
- [ ] Assert that:
  - [ ] input block is left of U1
  - [ ] output block is right of U1
  - [ ] power block is not merged into the main op-amp column
  - [ ] feedback parts are near U1 but not all same-column

---

# Phase 4 — Tighten Connector Role Detection So Layout Does Not Degrade

## Goal
Prevent internal fallback-style behavior from silently degrading placement.

### 4.1 Review connector role classification
**File:** `src/kicad_pcb/tier.py`

- [ ] Audit `classify_connector_roles(...)`
- [ ] Verify the headphone amp fixture correctly identifies:
  - [ ] audio input connector
  - [ ] audio output connector
  - [ ] power connector
- [ ] Improve heuristics for stereo jacks / generic power connectors if needed.

### 4.2 Eliminate accidental BFS degradation for this fixture
**File:** `src/kicad_pcb/layout.py`

- [ ] If BFS fallback is still reachable for the headphone amp fixture:
  - [ ] fix role detection
  - [ ] or hard-fail in strict mode rather than silently degrading
- [ ] Add a test that the headphone amp fixture uses the intended
  signal-distance / connector-aware path.

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
- [ ] Confirm whether any support parts that should stay near U1 are being
  over-clustered as “power only”
- [ ] Refine classification so power/support grouping does not distort the signal-stage layout.

### 5.2 Reposition decoupling/support parts more intentionally
- [ ] Keep true decouplers near op-amp power pins.
- [ ] Keep output/input support parts near their signal role, not grouped just
  because they touch power/ground.
- [ ] Add tests distinguishing:
  - [ ] decoupling caps
  - [ ] signal coupling caps
  - [ ] power connector support parts

---

# Phase 6 — Improve Readability of the Op-Amp Neighborhood

## Goal
Make the U1 area read like an analog stage instead of a stacked trunk.

### 6.1 Create local placement rules around U1
- [ ] Input-side parts on input side of U1
- [ ] Output-side parts on output side of U1
- [ ] Feedback parts adjacent to relevant op-amp pins
- [ ] Decouplers close to power pins but visually separated from feedback loop

### 6.2 Limit vertical stacking around U1
- [ ] Add a local spread rule so the neighborhood around U1 uses multiple
  nearby columns/rows when helpful.
- [ ] Avoid placing too many passives directly above/below U1 in the exact same x-coordinate band.

### 6.3 Add op-amp neighborhood tests
- [ ] Assert feedback components are near U1.
- [ ] Assert output-side parts are right-biased relative to U1.
- [ ] Assert support parts do not all share one x-position with U1.

---

# Phase 7 — Add Regression Tests for “Looks Worse Than Before”

## Goal
Stop future commits from silently reintroducing this regression.

### 7.1 Compare against previous better layout
- [ ] Add a previous “better” schematic fixture as reference if available.
- [ ] Use approximate metrics, not exact coordinate diff.

### 7.2 Add metrics thresholds
- [ ] Distinct x-columns should not regress below threshold.
- [ ] Components in U1 column should not exceed threshold.
- [ ] Block spread should not collapse.
- [ ] Short-wire ratio should not increase dramatically.
- [ ] Layout lints should not worsen.

### 7.3 Add human-review checklist for this regression
- [ ] Does the schematic again look like a column dump around U1?
- [ ] Are input/output/power blocks distinguishable?
- [ ] Is the op-amp neighborhood still too narrow/tall?
- [ ] Does this look worse than the previous iteration?

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

1. [ ] Phase 0 — capture regression fixture and metrics
2. [ ] Phase 1 — instrument and prove actual regression path
3. [ ] Phase 2 — weaken same-column halo forcing
4. [ ] Phase 4 — confirm/fix connector role path and fallback behavior
5. [ ] Phase 3 — add block-aware zoning and post-Graphviz refinement
6. [ ] Phase 5 — refine power/support clustering
7. [ ] Phase 6 — improve op-amp neighborhood layout
8. [ ] Phase 7 — lock in regression tests
9. [ ] Phase 8 — clean up docs/comments and debug support

---

## Definition of Done

The headphone amp schematic should:

- [ ] no longer line up a large fraction of parts in the op-amp column
- [ ] visibly separate input, op-amp, output, and power/support regions
- [ ] keep feedback/support parts near U1 without stacking them all vertically with it
- [ ] avoid internal fallback-style degradation for the canonical headphone amp fixture
- [ ] include regression tests that catch this exact layout collapse in future
"""

base = Path("./")
review_path = base / "CODE_REVIEW7.md"
todo_path = base / "CODE_REVIEW7_TODO.md"
review_path.write_text(review, encoding="utf-8")
todo_path.write_text(todo, encoding="utf-8")
zip_path = base / "CODE_REVIEW7_docs.zip"
with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
    zf.write(review_path, arcname=review_path.name)
    zf.write(todo_path, arcname=todo_path.name)

print(review_path)
print(todo_path)
print(zip_path)
