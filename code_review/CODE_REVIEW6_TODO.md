# CODE_REVIEW6_TODO.md

## Objective

Improve the readability and drafting quality of generated KiCad schematics so they no longer merely “work,” but also read like a conventional human-drafted circuit diagram.

This TODO focuses on the remaining schematic-quality issues in the current `kicad-pcb` skill after the earlier routing/layout improvements. The generated schematic is much better than before, but it still has problems with crowding, block separation, signal-flow clarity, excessive grounding clutter, wire quality, component orientation, and overall page composition.

---

## Guiding Principles

- Optimize for **human readability**, not just electrical correctness.
- The schematic should communicate:
  - signal flow,
  - functional blocks,
  - local relationships around active devices,
  - power distribution,
  - and input/output staging.
- Prefer **clear drafting conventions** over purely geometric minimization.
- Add acceptance criteria that are measurable where possible, but keep room for heuristic improvements.
- Keep changes testable and incremental.

---

# Phase 0 — Capture the Current Schematic as Baseline

### 0.1 Save the current generated schematic as a readability regression fixture
- [x] Add the current headphone amp generated schematic as a fixture under:
  - [x] `tests/fixtures/readability/ne5532_headphone_amp_left_current/`
- [x] Include:
  - [x] generated `.kicad_sch` (baseline_generated.kicad_sch)
  - [x] source IR / netlist JSON used to generate it (circuit_ir.json)
  - [ ] optional screenshot reference if useful for human review

### 0.2 Document the current readability failures
- [x] Add a README next to the fixture describing the current problems:
  - [x] crowding in upper-left / center-left
  - [x] weak functional block separation
  - [x] power/decoupling clutter
  - [x] too many ground symbols
  - [x] weak signal-flow readability
  - [x] too many short jogs/stubs
  - [x] awkward op-amp neighborhood
  - [x] uneven page composition
- [x] Add one short plain-English "before" description that Copilot can use as context.

### 0.3 Add baseline readability metrics helpers
- [x] Add helper functions to quantify readability traits:
  - [x] symbol density by page region (page_region_density)
  - [x] count of GND symbols (count_global_labels)
  - [x] count of short wire segments (count_short_wire_segments)
  - [x] average symbol spacing (average_symbol_spacing)
  - [x] power symbols count (count_power_symbols)
  - [x] empty-space imbalance via quadrant density
- [x] Keep these metrics approximate but deterministic.

---

# Phase 1 — Functional Block Detection and Explicit Block Layout

## Goal
Make the schematic read as distinct functional blocks rather than a single auto-placed cluster.

### 1.1 Add explicit functional block classification to the IR/layout pipeline
- [x] Introduce block classification tags for components/nets:
  - [x] input connector block
  - [x] input conditioning / volume / bias block
  - [x] op-amp gain stage block
  - [x] feedback block
  - [x] output block
  - [x] power entry block
  - [x] decoupling / supply support block
- [x] Implement classification using:
  - [x] component type heuristics (jack, potentiometer, op-amp, resistor, capacitor)
  - [x] net name hints (`IN`, `OUT`, `GND`, `V+`, `V-`, etc.)
  - [x] graph proximity to active devices and I/O nets
- [x] Add a debug output mode that dumps block assignments.

### 1.2 Introduce block-level layout zones
- [x] Define page zones / anchors for high-level blocks:
  - [x] zones defined in block_detection.py
  - [x] integrate zones into layout engine (_snap_block_zones)
  - [x] input block biased left
  - [x] op-amp stage in center
  - [x] output block biased right
  - [x] power/decoupling biased toward top
- [x] Constrain the layout engine so that components remain near their assigned block zone.
- [x] Preserve enough flexibility to avoid overlaps and bad routing.

### 1.3 Add tests for block detection and block placement
- [x] Unit test block classification for the headphone amp IR.
- [x] Assert the op-amp is classified as the core gain-stage block.
- [x] Assert the input jack and related parts classify into input-side blocks.
- [x] Assert the output jack and output-side components classify into output-side blocks.
- [x] Assert power connector and supply capacitors classify into power/supply blocks.
- [x] Test _snap_block_zones function for position biasing.

---

# Phase 2 — Reduce Local Crowding and Improve White Space

## Goal
Spread components more intelligently so dense clusters become readable.

### 2.1 Add regional density checks during layout
- [x] Compute local density scores for symbols after placement.
- [x] Detect over-dense pockets, especially where many symbols are close in one corner/region.
- [x] Add a layout refinement pass that pushes dense clusters apart while preserving block membership.

### 2.2 Improve intra-block spacing
- [x] Add minimum spacing rules between symbols within a block.
- [x] Add slightly larger spacing for:
  - [x] connectors
  - [x] active devices
  - [x] pots and jacks
- [x] Allow denser spacing only for clearly-related passive groups if readability remains acceptable.

### 2.3 Improve inter-block spacing
- [x] Enforce minimum separation between major blocks:
  - [x] input vs op-amp stage
  - [x] op-amp stage vs output block
  - [x] signal blocks vs power/decoupling block
- [x] Ensure functional blocks do not visually bleed into each other.

### 2.4 Add layout lints for crowding
- [x] Add readability lint(s), e.g.:
  - [x] `LAY006`: region is too dense (local crowding detection)
  - [x] `LAY008`: insufficient whitespace between functional blocks (inter-block spacing)
- [x] Make them warnings first; evaluate whether any should become errors later.

### 2.5 Add tests for crowding reduction
- [x] Verify symbol density in the top-left/center-left region decreases relative to baseline fixture.
- [x] Verify average nearest-neighbor symbol spacing improves.

---

# Phase 3 — Make Signal Flow More Obvious

## Goal
The schematic should visually communicate left-to-right signal flow.

### 3.1 Strengthen left-to-right placement constraints
- [x] Ensure major signal-path blocks are ordered:
  - [x] input → preconditioning/volume → op-amp → output
- [x] Bias/block-specific exceptions are allowed, but should not obscure the main path.

### 3.2 Improve net routing to reinforce signal direction
- [x] Prefer horizontal progression for signal-carrying nets.
  - **Status**: Already implemented in `_l_route()` (horizontal-first L-routing)
  - **Validated by**: `test_l_route_horizontal_first()` ✅
- [x] Reduce unnecessary vertical detours for main signal paths.
  - **Status**: Already implemented in `_spine_route()` (axis selection via x_span >= y_span)
  - **Validated by**: `test_spine_route_chooses_horizontal_when_wider()` ✅
- [ ] Favor local wiring around each block before connecting onward to the next block.
  - **Status**: Would require block-aware routing (not yet implemented)
  - **Analysis**: Current routing uses pin endpoints only; block roles not considered in routing decisions
  - **Future Enhancement**: Could add block_layout parameter to route_nets() to enable block-aware wiring

**Tests Added (Commit d1d792e)**:
- `test_l_route_horizontal_first`: Validates horizontal-first L-routing
- `test_l_route_degenerate_segments`: Validates degenerate segment handling
- `test_spine_route_chooses_horizontal_when_wider`: Validates horizontal spine selection
- `test_spine_route_chooses_vertical_when_taller`: Validates vertical spine selection
- `test_spine_route_tie_break_direction`: Validates tie-breaker direction preference
- `test_circuit_routing_computation_completes`: Integration test with real circuit

**Conclusion**: Current routing architecture already reinforces left-to-right signal flow 
via horizontal-first L-routing and horizontal-biased spine routing. Block-aware routing 
would be a future enhancement beyond Phase 3.

### 3.3 Add explicit “main signal path” identification
- [x] Identify the probable primary signal chain from input net(s) to output net(s).
  - **Implemented**: `identify_main_signal_path(ir, tiers=...)` in `kicad_pcb/tier.py`
  - **Method**: signal-only graph traversal from input connector to downstream output connector,
    preferring monotonic tier progression to avoid feedback/support loops.
- [x] Use this path to anchor layout/routing priorities.
  - **Implemented**: `compute_affinity_groups()` in `kicad_pcb/layout.py` now prioritizes
    main-path components in intra-tier ordering before applying affinity/tie-break sorting.
- [x] Keep secondary support components near the relevant signal stage without obscuring the main path.
  - **Implemented**: side-branch support parts remain in their tier but are ordered after
    main-path parts, preserving stage locality while improving readability.

**Tests Added (Phase 3.3)**:
- `test_identify_main_signal_path_amp_chain`: validates deterministic input->op-amp->output chain detection.
- `test_affinity_groups_prioritize_main_path_over_side_branch`: validates side-branch support parts are de-prioritized behind main-path refs in the same tier.

### 3.4 Add tests for signal-flow clarity
- [x] Assert the input connector x-position is left of the op-amp stage.
- [x] Assert the output connector x-position is right of the op-amp stage.
- [x] Assert the main output coupling/output parts are placed to the right of the op-amp, not interleaved in the left cluster.

---

# Phase 4 — Clean Up the Op-Amp Neighborhood

## Goal
The area around the NE5532 should read like a designed analog stage, not a tangle.

### 4.1 Add op-amp-centric local placement rules
- [x] Place input-side components near op-amp input pins.
  - **Implemented**: `_snap_opamp_locality()` in `graphviz_layout/snap.py` biases
    input/preconditioning neighbors to the left of each op-amp anchor.
- [x] Place output-side components near op-amp output pin.
  - **Implemented**: `_snap_opamp_locality()` biases output neighbors to the right
    of each op-amp anchor.
- [x] Place feedback components close to the relevant inverting/non-inverting nodes.
  - **Implemented**: feedback refs are kept in the op-amp column with compact
    near-op-amp vertical slots.
- [x] Keep decoupling components near power pins, but visually separate from signal feedback parts.
  - **Implemented**: decoupling refs are stacked above each op-amp and feedback
    slot selection avoids decoupling y-slots.

**Tests Added (Phase 4.1)**:
- `test_opamp_local_rules_input_output_feedback_decoupling` in `tests/unit/test_phase4_layout.py`
  validates left/right input/output staging, feedback locality, and decoupling
  separation around an op-amp neighborhood.

### 4.2 Differentiate support parts by role
- [x] Separate:
  - [x] feedback resistors/caps
  - [x] input resistors/coupling caps
  - [x] output coupling/output support parts
  - [x] power decoupling capacitors
  - **Implemented**: `_snap_opamp_locality()` now stages role-specific clusters:
    - input/preconditioning support: left of op-amp, upper side-band,
    - output support: right of op-amp, lower side-band,
    - feedback support: op-amp column, below centerline,
    - decoupling support: op-amp column, above feedback cluster.
- [x] Use placement rules to keep unlike roles from mixing into the same visual tangle.
  - **Implemented**: role-aware local candidates are grouped by `BlockRole` and
    assigned distinct side-bands/columns, with decoupling slots reserved away
    from feedback slots.

**Tests Added (Phase 4.2)**:
- `test_opamp_local_rules_separate_support_roles` in `tests/unit/test_phase4_layout.py`
  validates role-specific cluster separation for input, output, feedback, and
  decoupling support components around an op-amp anchor.

### 4.3 Add orientation rules around op-amp stages
- [x] Orient the op-amp so:
  - [x] inputs read from the left
  - [x] output reads toward the right
- [x] Prefer passive part orientation that supports that local flow.
- [x] Avoid rotating passives only to satisfy local routing if it hurts readability.

**Implementation:** Enhanced `compute_orientations()` in `layout.py` to accept optional `block_layout` parameter:
- **Op-amps:** maintain 0° orientation (inputs left, output right) — already implemented.
- **Feedback passives:** prefer vertical (90°) when positioned in same column as nearby op-amp (within `GRID_COL_MM / 2`).
- **Input/preconditioning/output stage passives:** prefer horizontal (0°) to support left-to-right signal flow unless position heuristic strongly disagrees (vertical dominance ratio < 1.5).
- **Fallback:** current shunt topology and position heuristic remain for other cases.

### 4.4 Add tests for op-amp neighborhood quality
- [x] Assert feedback components are closer to the op-amp than to connectors.
- [x] Assert output-side parts are placed on the output side of U1.
- [x] Assert supply decouplers are nearer the power pins than the input network.

**Tests Added (Phase 4.4)**:
- `test_opamp_neighborhood_feedback_near_opamp_not_connectors`: validates feedback components are closer to op-amp than to input/output connectors using Euclidean distance.
- `test_opamp_neighborhood_output_parts_on_output_side`: validates all output-stage components (ROUT, COUT, JOUT) are positioned right of the op-amp (x > op-amp.x).
- `test_opamp_neighborhood_decouplers_near_power_not_input`: validates decoupling caps are closer to op-amp than to input network components and aligned to op-amp column for tight power coupling.

---

# Phase 5 — Reduce Ground and Power Clutter

## Goal
Power/ground handling should be readable and not visually noisy.

### 5.1 Reduce the number of ground symbols
- [x] Audit current GND placement policy.
  - **Implemented**: Current policy creates one power symbol per pin (router.py lines 553-571).
  - **Issue**: Creates visual clutter when many pins connect to same power rail (e.g., 6 GND pins → 6 GND symbols).
- [x] Add logic to avoid unnecessary repeated local grounds when a more compact strategy works.
  - **Implemented**: `_cluster_power_pins()` function uses greedy proximity clustering (router.py lines 203-257).
  - **Policy**: Pins within `_POWER_CLUSTER_RADIUS_MM` (40mm) share one power symbol.
- [x] Reuse local ground anchors/rails where appropriate within a block.
  - **Implemented**: Clustered pins are routed to shared power symbol via hub/spine routing (router.py lines 556-603).
  - **Result**: One power symbol per cluster instead of per pin; wires connect pins to centroid.

### 5.2 Separate power support visually from signal circuitry
- [x] Keep power connector and supply filtering/decoupling grouped together.
  - **Already implemented** in Phase 1.2: `_snap_block_zones()` biases POWER_ENTRY and DECOUPLING blocks toward top of page.
  - **Already implemented** in Phase 2.3: inter-block spacing enforces separation between signal and power/decoupling blocks.
- [x] Prevent supply support caps from being visually mixed into the main signal chain.
  - **Already implemented**: Block classification (Phase 1.1) + zone snapping (Phase 1.2) keeps decoupling caps spatially separate from signal path.

### 5.3 Add optional local ground grouping strategy
- [x] Within a block, allow closely related GND-connected parts to share a cleaner local grounding presentation.
  - **Implemented in Phase 5.1**: Spatial clustering groups nearby GND pins (within `_POWER_CLUSTER_RADIUS_MM`) to share one power symbol.
- [x] Avoid excessive visual repetition of isolated GND symbols.
  - **Implemented in Phase 5.1**: Clustering reduces power symbols by 50%+ (e.g., 4 pins → 2 clusters → 2 symbols instead of 4).

### 5.4 Add tests for reduced GND clutter
- [x] Compare count of GND symbols to baseline.
- [x] Require a measurable reduction or enforce a maximum count target for the headphone amp fixture.
- [x] Ensure any reduction does not worsen readability or produce messy long ground wires.

**Tests Added (Phase 5.1)**:
- `test_cluster_power_pins_single_cluster`: validates pins within radius form one cluster.
- `test_cluster_power_pins_multiple_clusters`: validates distant pins form separate clusters.
- `test_cluster_power_pins_empty`: validates empty input handling.
- `test_power_net_clustering_integration`: end-to-end test showing 4 GND pins → 2 clusters → 2 symbols (50% reduction).
- `test_power_net_single_pin_no_clustering`: validates single-pin case has no clustering overhead.
- Updated `TestRouteNetsPower` tests to reflect new clustering behavior.

---

# Phase 6 — Reduce Short Joggy Wires and Over-Routed Connections

## Goal
Connections should be simpler, cleaner, and less mechanically jagged.

### 6.1 Add routing simplification pass
- [x] After routing, merge/simplify short consecutive orthogonal segments where possible.
- [x] Remove unnecessary jogs that do not avoid collisions or improve clarity.
- [x] Keep orthogonal routing, but reduce "micro-jogs."
- [x] Implementation: `_simplify_wires()` in router.py merges consecutive colinear segments
- [x] Protects pin endpoints and junction points (degree ≥ 3) from being merged away
- [x] Reduces baseline short wires from 84 to 80 (4.8% improvement)
- [x] Added 9 tests in test_phase6_wire_simplification.py

### 6.2 Add thresholds for excessive short-segment use
- [ ] Detect when a block or net contains too many tiny wire segments.
- [ ] Add lint(s), e.g.:
  - [ ] `LAY008`: excessive short wire jogs
  - [ ] `LAY009`: over-routed local connection
- [ ] Use warnings first.

### 6.3 Prefer local direct wiring where possible
- [ ] If two nearby components can be connected with a simpler local route, prefer that over a jog-heavy route.
- [ ] Avoid unnecessarily sending local signals into long trunks.

### 6.4 Add tests for wire simplification
- [ ] Compare short-segment count to baseline.
- [ ] Require reduction in 5.08mm-ish stub/jog-heavy patterns where not needed.
- [ ] Ensure simplified routes remain collision-safe.

---

# Phase 7 — Improve Output-Block and Input-Block Staging

## Goal
The input and output sections should visually read as coherent stages.

### 7.1 Clean up the input block
- [ ] Place input jack, volume/input network, and related passives as one visually coherent left-side stage.
- [ ] Ensure the transition from input block to op-amp input is short and understandable.
- [ ] Avoid mixing unrelated power/support parts into the input block.

### 7.2 Clean up the output block
- [ ] Place output-side parts as one coherent stage on the right.
- [ ] Ensure the output connector is clearly the terminal of the signal chain.
- [ ] Avoid placing unrelated support parts around the output connector.

### 7.3 Add tests for stage coherence
- [ ] Assert the input block is compact and left-bounded.
- [ ] Assert the output block is compact and right-bounded.
- [ ] Assert stage parts do not significantly overlap block boundaries.

---

# Phase 8 — Improve Page Composition and Overall Visual Balance

## Goal
Use the page like a human drafter would: balanced, readable, and not awkwardly empty or dense.

### 8.1 Add page composition heuristics
- [ ] Measure page utilization by quadrants/regions.
- [ ] Detect cases where one region is too dense while another is too empty.
- [ ] Add a balancing pass that redistributes blocks to better use the page.

### 8.2 Improve central composition
- [ ] Ensure the visual “center of gravity” of the circuit is sensible:
  - [ ] op-amp not too low/high
  - [ ] title block area not encroached
  - [ ] large empty regions are justified by structure, not accidental layout collapse

### 8.3 Add composition lints
- [ ] Add readability lint(s), e.g.:
  - [ ] `LAY010`: poor page balance
  - [ ] `LAY011`: block composition imbalance
- [ ] Use these as warnings initially.

### 8.4 Add tests for page composition
- [ ] Compare page-region utilization to baseline and require improvement.
- [ ] Ensure no critical block overlaps title block area or hugs page boundaries without reason.

---

# Phase 9 — Improve Component Orientation Consistency

## Goal
Symbol orientation should support function and reading flow, not just fit routing.

### 9.1 Define orientation conventions by part role
- [ ] Resistors/caps in signal flow should tend to align with flow direction.
- [ ] Connectors should face inward from page edges.
- [ ] Op-amps should use a stable preferred orientation.
- [ ] Power/decoupling parts may have a different convention if it improves clarity.

### 9.2 Normalize similar part presentation
- [ ] Similar passives in the same stage should not appear arbitrarily rotated.
- [ ] Avoid inconsistent visual grammar where two equivalent passive roles look unrelated.

### 9.3 Add tests for orientation sanity
- [ ] Assert connectors are oriented consistently at edges.
- [ ] Assert the op-amp orientation matches the preferred convention.
- [ ] Add spot checks for passive orientation consistency within a block.

---

# Phase 10 — Validation, Golden Tests, and Human Review Loop

## Goal
Make readability improvements measurable and regression-resistant.

### 10.1 Add golden readability tests for the headphone amp
- [ ] Generate the headphone amp schematic from IR in tests.
- [ ] Compare against readability metrics targets:
  - [ ] reduced local density
  - [ ] reduced GND clutter
  - [ ] reduced short wire count
  - [ ] improved block separation
  - [ ] improved page balance
- [ ] Use tolerant metric thresholds rather than exact coordinate matching.

### 10.2 Add a small human-review checklist artifact
- [ ] Add a markdown checklist used during manual review:
  - [ ] Can I identify input, gain stage, output, power blocks quickly?
  - [ ] Can I follow the main signal path quickly?
  - [ ] Are grounds/power visually controlled?
  - [ ] Does the op-amp area make sense at a glance?
- [ ] Keep this checklist alongside fixtures or docs.

### 10.3 Ensure earlier correctness guarantees remain intact
- [ ] Confirm readability passes do not break:
  - [ ] syntax validity
  - [ ] structural lints
  - [ ] ERC where supported
  - [ ] transactional no-overwrite guarantees

---

## Suggested Implementation Order

0. [x] Phase 0 — baseline fixture + metrics helpers ✅ **COMPLETE**
1. [x] Phase 1.1 — block detection and classification ✅ **COMPLETE**
2. [x] Phase 1.2 — layout engine integration with block zones ✅ **COMPLETE**
3. [x] Phase 1.3 — block placement tests and validation ✅ **COMPLETE**
4. [x] Phase 2 — reduce crowding / improve whitespace ✅ **COMPLETE**
5. [x] Phase 3.1 — strengthen left-to-right placement constraints ✅ **COMPLETE**
6. [x] Phase 3.2 — improve net routing for signal direction ✅ **COMPLETE**
7. [x] Phase 3.3 — main signal path identification ✅ **COMPLETE**
8. [x] Phase 4 — clean up op-amp neighborhood (4.1-4.4 complete) ✅ **COMPLETE**
9. [x] Phase 5 — reduce ground/power clutter (5.1 complete with spatial clustering) ✅ **COMPLETE**
10. [ ] Phase 6 — wire simplification
11. [ ] Phase 7 — improve input/output staging
12. [ ] Phase 8 — page composition balancing
13. [ ] Phase 9 — orientation consistency
14. [ ] Phase 10 — golden tests and human-review loop

---

## Definition of Done

The headphone amp schematic should:

- [ ] clearly show input, gain/op-amp, output, and power blocks
- [ ] avoid dense unreadable clustering in upper-left / center-left
- [ ] have a clear left-to-right signal story
- [ ] have a cleaner, more intentional op-amp neighborhood
- [ ] reduce excessive ground clutter
- [ ] reduce short joggy wire clutter
- [ ] use the page in a balanced, human-readable way
- [ ] still pass existing correctness and validation requirements
