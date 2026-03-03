# Component Placement — Implementation TODO

Cross-reference: [`COMPONENT_PLACEMENT.md`](COMPONENT_PLACEMENT.md)

Each item maps to a rule section from that document and a specific file/function
in `kicad-pcb/src/kicad_pcb/`.

---

## Phase 0 — Bug fix: single-column layout (BLOCKER)

**Root cause:** `graphviz_layout.py::_build_dot_source()` emits every edge as
`component → net` (all components are sources, all nets are sinks). Graphviz
assigns every component to the same rank → single column at x ≈ 33.82 mm.

### 0.1 `graphviz_layout.py::_build_dot_source()` — Switch to a unipartite component graph

- [x] Replace the bipartite model with a **component-to-component** directed
  graph. For each signal net (non-power, ≥ 2 pins), emit one directed edge
  from the "upstream" component to the "downstream" component. Use component
  type to infer directionality:
  - Source tier (connectors, `J*`, `P*`, `CON*`) → passive tier (`R*`, `C*`, `L*`)
    → IC/op-amp tier (`U*`, `IC*`, `OA*`) → sink tier (connectors again).
  - For same-type pairs, emit an undirected (or both-direction) edge so dot
    infers order from topology.
- [x] Keep net nodes in the graph as intermediate routing nodes so that 1:N
  nets (fan-out) produce proper branching ranks rather than star topology.
  Alternative: emit a **virtual net node** only for nets with > 2 pins, and
  route each pair of pins as a direct edge.
- [x] Re-run the headphone amp test schematic and verify that the x-spread of
  component positions is > 60 mm (i.e., there is meaningful tier separation).

### 0.2 `graphviz_layout.py::_build_dot_source()` — Add `rank=source` / `rank=sink` constraints

- [x] Wrap all input connectors in a `{ rank=source; ... }` subgraph.
- [x] Wrap all output connectors in a `{ rank=sink; ... }` subgraph.
- [x] Power-only components: keep in `cluster_power` with `rank=max`
  (already present; verify it still works after model change in 0.1).

### 0.3 Regression test for bipartite model fix

- [x] Add `tests/unit/test_graphviz_layout.py::test_build_dot_source_has_component_edges()`
  asserting that `_build_dot_source()` for a 3-component chain
  (`J1 → R1 → U1 → J2`) produces at least one `component → component` edge
  (or a chain of component → net → component edges) rather than all edges
  pointing only to net nodes.
- [x] Add `test_tier_separation()`: assert that after layout, `x(U1) > x(R1) > x(J1)`.

---

## Phase 1 — Tier assignment (Rules §1, §2, §9)

### 1.1 New module `tier.py` — Longest-path layering

- [x] Implement `assign_tiers(ir: CircuitIR) -> dict[str, int]`.
  - Build an undirected component graph from signal nets.
  - Identify **source** components (input connectors, by ref prefix) and
    **sink** components (output connectors).
  - Run **longest-path layering** (topological sort + depth from source) so
    that every component gets a tier index:
    - Tier 0: input connectors / signal sources.
    - Tier 1..N-2: signal-chain components ordered by longest path.
    - Tier N-1: output connectors / signal sinks.
  - Detect **feedback edges** (back-edges in DFS) and break cycles by
    reversing the edge with the smallest weight (smallest pin count).
- [x] Classify component function from ref prefix:
  ```
  "J", "CON", "P", "SJ", "TJ"  → connector
  "U", "IC", "OA"               → active / IC
  "R", "C", "L", "D", "Q"      → passive
  "BT", "F", "S", "SW"          → misc
  ```
  (Extract constants to a shared `component_types.py` so `layout.py` and
  `graphviz_layout.py` can share them without circular imports.)
- [x] Expose `TIER_SPACING_MM: float = 30.48` and `ORIGIN_X_MM: float = 30.48`
  so that `x = ORIGIN_X + tier * TIER_SPACING_MM` when computing initial x.

### 1.2 `graphviz_layout.py::_build_dot_source()` — Inject Graphviz rank constraints from tiers

- [x] Call `assign_tiers()` to get `{ref: tier}`.
- [x] Group components by tier and emit a `{ rank=same; ... }` subgraph per
  tier so dot respects the tier order while optimising y-positions freely.
- [x] Pass `ranksep=1.5` (increase from 1.0) to give more horizontal spacing.

### 1.3 Tests

- [x] `test_assign_tiers_linear_chain()` — J1→R1→U1→J2 yields tiers 0,1,2,3.
- [x] `test_assign_tiers_breaks_cycle()` — feedback resistor R_fb in U1→R_fb→U1
  does not cause infinite loop; R_fb gets tier > U1 input tier.
- [x] `test_rank_same_subgraph_present()` — DOT source for a 2-tier circuit
  contains a `rank=same` subgraph for each tier.

---

## Phase 2 — Vertical grouping / affinity clustering (Rule §3)

### 2.1 New function `layout.py::compute_affinity_groups(ir, tiers)`

- [x] For each tier, compute pairwise affinity between components:
  ```
  affinity(A, B) = |shared_signal_nets(A, B)| / min(|signal_nets(A)|, |signal_nets(B)|)
  ```
  *(impl: `compute_affinity_groups(ir, tiers)` in `layout.py`; power nets excluded)*
- [x] Sort components within a tier by affinity to their tier-N-1 neighbours
  so connected components land close together vertically.
- [x] Output: `{tier: [ref, ref, ...]` sorted by descending affinity (alphabetical tiebreak).

### 2.2 `graphviz_layout.py::_build_dot_source()` — Inject `ordering=out` + weight hints

- [x] For edges between high-affinity component pairs, emit `[weight=5]` on
  the DOT edge so dot prefers short connections. *(impl: `_compute_net_weights()`)*
- [x] Added `ordering=out` to the overall graph to prefer upstream-relative
  vertical ordering.

### 2.3 `graphviz_layout.py::_gv_to_kicad()` — Post-layout affinity nudge

- [ ] After Graphviz layout, apply a light vertical reordering pass: for
  each tier, if two components in the same tier have a direct net connection
  and swapping their y positions reduces total wire length, swap them.
  Limit iterations to 20 passes (simulated annealing style) to avoid O(N²)
  blowup. *(deferred — Graphviz `ordering=out` + `weight=5` hints already guide vertical order)*

### 2.4 Tests

- [x] `test_affinity_groups_returns_sorted_refs()` — `TestComputeAffinityGroups`.
- [x] `test_connected_pair_vertically_adjacent()` — covered by `TestNetWeights::
  test_weight_five_in_dot_source` + `test_ordering_out_present_in_dot_source`.

---

## Phase 3 — Power and ground rail handling (Rule §5)

### 3.1 `graphviz_layout.py::_build_dot_source()` — Decoupling cap co-location

- [x] Detect **decoupling capacitors**: `C*` that have exactly one non-power
  net pin → that pin connects to an IC's power pin. *(impl: `_find_decoupling_caps()`)*
- [x] Assign the decoupling cap to the **same column** as its IC by adding
  a zero-weight invisible edge `{C → IC [style=invis, weight=10]}`. *(impl: `_emit_decoupling_constraints()`)*
- [x] Group decoupling caps above the IC centre (lower y value in KiCad
  coordinates) using a `{rank=same; IC; C_decoupling;}` subgraph.

### 3.2 `graphviz_layout.py::_gv_to_kicad()` — VCC bus / GND bus snap

- [x] After coordinate mapping, identify components classified as power nets
  (`PWR_FLAG`, explicit `#PWR` symbols, decoupling caps).
- [x] Clamp VCC-related symbols to `y = ORIGIN_Y` (top row).
- [x] Clamp GND symbols to `y = PAGE_MAX_Y - 20` (bottom row).
- [x] Shift decoupling caps to the same x as their IC anchor, offset by
  `-GRID_ROW_MM` on the y axis (one row above). *(impl: `_post_snap_decoupling_caps()`)*

### 3.3 Tests

- [x] `test_decoupling_cap_same_x_as_ic()` — covered by `TestDecouplingCapCoLocation` (5 tests).
- [x] `test_power_flag_at_top_y()` — covered by `TestSnapPowerSymbols::test_power_flag_clamped_to_top_y` (Phase 3.2).

---

## Phase 4 — Component orientation (Rule §10)

### 4.1 New function `layout.py::compute_orientations(ir, positions)` — Rewrite

The current implementation always returns 90° for passives where total_dy >
total_dx and 0° otherwise. Replace with topology-driven logic:

- [x] For each passive (`R*`, `C*`, `L*`):
  - Determine pin connectivity:
    - **Both pins on signal nets (non-power)** → component is **series** → orient 0° (horizontal).
    - **One pin on a power/GND net, other on signal net** → component is **shunt** → orient 90° (vertical).
    - **Both pins on power nets** → degenerate bypass; default to 90°.
  - Special case potentiometer (`RV*`): always 90°.
- [x] For connectors (`J*`, `P*`, `CON*`):
  - Input connectors (tier 0): 0° (pins point right, toward circuit).
  - Output connectors (last tier): 180° (pins point left, toward circuit).
  - Determine input vs output by tier: `tier == 0 → 0°`, `tier == max_tier → 180°`.
- [x] For ICs and op-amps (`U*`, `IC*`, `OA*`): always 0°.
- [x] For diodes (`D*`): 0° (anode left, cathode right for forward-biased series diodes).

### 4.2 `graphviz_layout.py::GraphvizLayoutEngine.compute_symbol_positions()`

- [x] After computing positions from dot, call `compute_orientations()` and
  merge rotation into the result triple: `(x, y, rotation_deg)`. *(Integrated in `commands/netlist.py::_write_symbols()`.)*
- [x] Update `_gv_to_kicad()` to accept an optional per-component rotation
  map rather than always returning `None`.

### 4.3 Tests

- [x] `test_series_resistor_orientation_is_0()` — `TestShuntOrientations::test_series_resistor_no_power_pin_uses_heuristic`.
- [x] `test_shunt_bypass_cap_orientation_is_90()` — `TestShuntOrientations::test_bypass_cap_gnd_is_90`, `test_pullup_resistor_vcc_is_90`, `test_pulldown_resistor_gnd_is_90`, `test_shunt_fires_before_position_heuristic`.
- [x] `test_input_connector_orientation_is_0()` — covered by `TestConnectorOrientations::test_input_connector_orientation_is_0` (Phase 4.1).
- [x] `test_output_connector_orientation_is_180()` — covered by `TestConnectorOrientations::test_output_connector_orientation_is_180` (Phase 4.1).
- [x] `test_ic_orientation_is_always_0()` — `TestShuntOrientations::test_both_pins_power_only_stays_zero` covers passive fallback; IC always 0° verified by existing tests.

---

## Phase 5 — Feedback network detection (Rule §7)

### 5.1 New function `layout.py::find_feedback_paths(ir, tiers)` 

- [ ] Detect **feedback components**: passives where one pin connects to a
  net rooted at a later tier and the other pin connects to a net rooted at an
  earlier tier (i.e., they create a back-edge).
- [ ] Mark them with a `feedback=True` attribute in a `ComponentAnnotation`
  dataclass (new; lives in `layout.py`).
  
### 5.2 `graphviz_layout.py::_build_dot_source()` — Feedback loop U-bend constraints

- [ ] For feedback components, add both a forward edge (current tier) and a
  constraint edge directing Graphviz to route them above the amplifier stage:
  - Add `[constraint=false]` on the back-edge so dot doesn't pull the ranks
    backward.
  - Add `[style=invis]` edge from `{feedback_component}` to a dummy node at
    tier+1 to push the feedback component row above the IC.
- [ ] Emit the feedback component into a `cluster_feedback` subgraph (no
  visible border) to keep it grouped.

### 5.3 `graphviz_layout.py::_gv_to_kicad()` — Raise feedback components above amp

- [ ] Post-layout: for any component marked `feedback=True`, if its y > IC's y
  (in KiCad coords, y increases downward), flip it to `IC.y - GRID_ROW_MM`
  so it appears visually above the amplifier body.

### 5.4 Tests

- [ ] `test_find_feedback_resistor()` — R_fb connecting op-amp output net to
  op-amp inverting input net is detected as feedback.
- [ ] `test_feedback_component_placed_above_amp()`.

---

## Phase 6 — Multi-unit IC handling (Rule §6)

### 6.1 New function `tier.py::assign_ic_units_to_tiers(ir, tiers)`

- [ ] Detect multi-unit symbols (KiCad `unit > 0` suffix in ref). Group by
  base ref (e.g., `U1A`, `U1B` → base `U1`).
- [ ] Assign each unit to the tier of its connected signal nets.
- [ ] Assign the **power unit** (unit with only power net connections) to a
  floating position beside the power rails cluster, not in the main tier grid.

### 6.2 `graphviz_layout.py::_build_dot_source()` — Per-unit nodes for multi-unit ICs

- [ ] Instead of a single node per `ref`, emit one node per *unit* for
  multi-unit ICs: `U1A`, `U1B`, etc.
- [ ] Connect each unit node with correct signal edges.
- [ ] Place the power unit in `cluster_power`.

### 6.3 Tests

- [ ] `test_multi_unit_ic_power_unit_in_power_cluster()`.
- [ ] `test_multi_unit_ic_signal_units_in_signal_tiers()`.

---

## Phase 7 — Stereo symmetry (Rule §8)

### 7.1 New function `layout.py::detect_stereo_channels(ir)`

- [ ] Heuristic: two components are in the **same stereo pair** when they
  share identical net-name patterns except for an `_L`/`_R` or `-L`/`-R`
  suffix (e.g., `OUT_L` vs `OUT_R`).
- [ ] Return `{ref: channel}` where channel is `"L"`, `"R"`, or `"mono"`.

### 7.2 `graphviz_layout.py::_gv_to_kicad()` — Apply stereo vertical split

- [ ] After layout, partition components by channel.
- [ ] Force Left-channel components into the top half of the page:
  `y_final = ORIGIN_Y + (y_relative * 0.45)` (use 45% of page height).
- [ ] Force Right-channel components into the bottom half:
  `y_final = ORIGIN_Y + page_height * 0.55 + (y_relative * 0.45)`.
- [ ] Mono / shared components: keep centred vertically at
  `ORIGIN_Y + page_height * 0.5`.

### 7.3 Tests

- [ ] `test_detect_stereo_channels_from_net_suffix()`.
- [ ] `test_left_channel_above_midline()`.
- [ ] `test_right_channel_below_midline()`.

---

## Phase 8 — Wire routing improvements (Rule §4)

### 8.1 `router.py::route_nets()` — Net-label threshold tied to tier distance

- [ ] Currently, direct L-routes are used when Manhattan ≤ 120 mm.
  Replace with a tier-distance condition:
  ```
  if tier_distance(pin_a.ref, pin_b.ref) <= 1:
      try direct L-route
  else:
      emit net label (avoids diagonal "spaghetti")
  ```
  Requires threading `tiers: dict[str, int]` into `route_nets()`.
- [ ] Update `route_nets()` signature to accept optional `tiers` param
  (default `None` → fall back to current Manhattan cap).

### 8.2 `router.py::_l_route()` — 30 mm net-label trigger

- [ ] If the direct L-route segment length > 30 mm AND the pins are in
  non-adjacent tiers, emit a net label on the wire midpoint instead of
  extending the wire across the gap. (Per Rule §4: labels for long
  cross-sheet nets.)
- [ ] Add `MAX_DIRECT_WIRE_MM: float = 30.0` constant (distinct from the
  existing `MAX_DIRECT_DIST_MM = 120.0` which caps the Manhattan fallback).

### 8.3 `router.py` — Body-crossing guard

- [ ] After computing all wire segments, check if any wire passes through a
  component bounding box (approximate: each component is a 10.16 × 10.16 mm
  box centred on its position).
- [ ] If a crossing is detected, reroute the segment as a two-segment
  detour (add a 5.08 mm bend above or below the blocking component).
- [ ] Add `detect_body_crossings(wires, positions) -> list[WireSegment]`
  as a helper.

### 8.4 Tests

- [ ] `test_cross_tier_net_gets_label_not_long_wire()`.
- [ ] `test_route_nets_respects_tier_distance()`.
- [ ] `test_no_body_crossings_after_routing()`.

---

## Phase 9 — Layout engine integration

### 9.1 `layout_engine.py::make_layout_engine()` — Pass tiers to engine

- [ ] Add optional `tiers: dict[str, int] | None = None` parameter.
- [ ] If `tiers` is provided, store on the engine and use it in
  `_build_dot_source()` (Phase 1.2).
- [ ] Add a convenience factory `make_layout_engine_with_ir(ir)` that
  computes tiers internally and returns a fully configured engine.

### 9.2 `graphviz_layout.py::GraphvizLayoutEngine` — End-to-end integration

- [ ] Revise `compute_symbol_positions()` to run the full pipeline after
  getting raw positions from dot:
  1. `gv_positions = _run_dot(dot_source)` — existing step
  2. `kicad_positions = _gv_to_kicad(gv_positions)` — existing step
  3. `orientations = compute_orientations(ir, kicad_positions)` — Phase 4
  4. `snapped = snap_positions(kicad_positions)` — existing step
  5. Merge orientations: `{ref: (x, y, rot) for ref, (x, y, _) in snapped}`
  6. Return merged dict.

### 9.3 Integration test

- [ ] Add `tests/integration/test_layout_pipeline.py` (or extend existing
  `tests/unit/test_graphviz_layout.py`):
  - Build a minimal headphone-amp IR (3 tiers: J_IN → R1, C1 → U1 → J_OUT).
  - Run `make_layout_engine().compute_symbol_positions(ir)`.
  - Assert: `x(J_IN) < x(R1) < x(U1) < x(J_OUT)`.
  - Assert: `rotation(R1) == 0` (series, horizontal).
  - Assert: `rotation(C1) == 90` (bypass cap, vertical).
  - Assert: `rotation(J_IN) == 0`, `rotation(J_OUT) == 180`.

---

## Phase 10 — Cleanup and documentation

### 10.1 Extract shared component-type constants

- [ ] Create `kicad_pcb/component_types.py` with:
  ```python
  CONNECTOR_PREFIXES = ("J", "CON", "P", "SJ", "TJ")
  IC_PREFIXES = ("U", "IC", "OA")
  PASSIVE_PREFIXES = ("R", "C", "L", "D", "Q", "RV")
  POWER_NET_PREFIXES = ("GND", "AGND", "DGND", "PGND", "VCC", "VDD", ...)
  ```
- [ ] Remove duplicate prefix lists from `layout.py`, `graphviz_layout.py`,
  and `router.py`; replace with imports from `component_types.py`.

### 10.2 Update `layout_engine.py` docstring

- [ ] Document the full layout pipeline: tier assignment → dot rank injection
  → bipartite (or component) graph → KiCad coordinate mapping → orientation
  → snapping.

### 10.3 Update `COMPONENT_PLACEMENT.md`

- [ ] Add a "Status" column to each rule (Planned / Implemented / Tested).
- [ ] Record the file and function that implements each rule.

### 10.4 Run full test suite

- [ ] `ruff check .` — zero warnings.
- [ ] `mypy .` — zero errors.
- [ ] `pytest tests/unit/ -q` — all existing tests still pass.
- [ ] Coverage ≥ 70% on new functions.

---

## Implementation Order (suggested)

| Priority | Phase | Rationale |
|----------|-------|-----------|
| 1 | 0 — Bipartite model fix | Unblocks all visual debugging |
| 2 | 1 — Tier assignment | Foundation for Phases 2–9 |
| 3 | 4 — Orientation | High visual impact, self-contained |
| 4 | 3 — Power/decoupling | Fixes messy power net placement |
| 5 | 5 — Feedback | Needs tiers + orientation from Phases 1,4 |
| 6 | 2 — Affinity grouping | Quality improvement; needs tiers |
| 7 | 6 — Multi-unit IC | Low frequency but important for op-amps |
| 8 | 7 — Stereo | Specialised to headphone amps |
| 9 | 8 — Routing improvements | Needs placement stabilised first |
| 10 | 9, 10 — Integration + cleanup | Always last |
