# Code Review 6 — Layout Rules TODO

Covers six concrete layout improvement rules derived from schematic analysis of
`ne5532_headphone_amp_left.kicad_sch` / `OpenClaw_Managed.kicad_sch`.  Each rule
is broken into actionable tasks with the owning file and the affected function(s)
noted inline.

---

## Rule 0 — I/O Connector Role Detection

**Problem:** Output connectors (e.g. headphone jack) land on the left because the
current seed-selection only picks the connector with the most hops to an IC, but
doesn't classify all connectors as input vs. output and enforce page-edge
constraints for each type.

### Tasks

- [ ] **R0-1** Add `_classify_connector_roles()` to `tier.py`
  - Input: `refs`, `signal_nets` (same signature as `_choose_seed_connector`)
  - Build undirected signal adjacency graph
  - BFS from every connector; record each connector's hop-distance to every IC
  - BFS from every IC in the *directed* DAG produced by `assign_tiers()`; a
    connector that only appears *after* every IC in the DAG is an **output**
    connector; those appearing *before* or at tier 0 are **input** connectors
  - Return `dict[str, Literal["input", "output", "unknown"]]`

- [ ] **R0-2** Integrate connector roles into `assign_tiers()` in `tier.py`
  - After `_longest_path_dp()`, call `_classify_connector_roles()`
  - Force all output connectors to `tier = max_tier`
  - Force all input connectors to `tier = 0`
  - Log each forced reassignment at `DEBUG` level

- [ ] **R0-3** Enforce page-edge X constraints in `graphviz_layout/snap.py`
  - Add `_enforce_connector_x_bounds()` post-layout snap pass
  - Input connectors: clamp `x ≤ ORIGIN_X + 0.25 × (PAGE_MAX_X − ORIGIN_X)`
  - Output connectors: clamp `x ≥ ORIGIN_X + 0.75 × (PAGE_MAX_X − ORIGIN_X)`
  - Snap clamped x to nearest KiCad grid (1.27 mm)
  - Run this pass immediately after `_snap_connectors_to_ic_y()` in
    `GraphvizLayoutEngine.compute_symbol_positions()`

- [ ] **R0-4** Update connector orientation in `layout.py → compute_orientations()`
  - Currently uses `tiers.get(ref, 0) == _max_tier` to decide 180°
  - Replace with the role classification from R0-1 so output connectors
    always get 180° regardless of exact tier number
  - Keep fallback to tier-based logic when role data is unavailable

- [ ] **R0-5** Update `graphviz_layout/dot_builder.py → _emit_tier_subgraphs()`
  - Ensure output connectors are emitted in `{ rank=sink }` subgraph
  - Ensure input connectors are emitted in `{ rank=source }` subgraph
  - Verify this is consistent with the tier assignments from R0-2

- [ ] **R0-6** Add unit tests for `_classify_connector_roles()`
  - Test: NE5532 amp circuit → J_headphone classified as output, J_audio_in as input
  - Test: circuit with no ICs → all connectors classified as unknown
  - Test: circuit with single connector → classified as input
  - File: `kicad-pcb/tests/test_tier.py`

---

## Rule 1 — Signal-Distance Score (SDS)

**Problem:** Components are currently placed by BFS tier alone, which gives equal
column weight to every hop regardless of whether a component is closer to the input
or the output end of the chain.  SDS captures this as a continuous 0–1 value.

### Tasks

- [ ] **R1-1** Implement `compute_signal_distance_scores()` in `layout.py`
  - Signature: `(ir: CircuitIR, roles: dict[str, str]) -> dict[str, float]`
  - For each component `c` compute:
    - `d_in(c)` = BFS hop count from `c` to the nearest input connector
      (signal nets only, power nets excluded)
    - `d_out(c)` = BFS hop count from `c` to the nearest output connector
  - `SDS(c) = d_in(c) / (d_in(c) + d_out(c))`
  - Handle division-by-zero: if `d_in + d_out == 0` set `SDS = 0.5`
  - Handle unreachable connectors: treat as `d = large_sentinel` (e.g. 1000)
  - Export as public function (`__all__` entry in `layout.py`)

- [ ] **R1-2** Handle power-only components (decoupling caps, bypass caps)
  - Components with no signal-net pins inherit the SDS of their associated IC
  - Use `_find_decoupling_caps()` from `graphviz_layout/dot_builder.py` to
    identify `{cap_ref: ic_ref}` mappings
  - After computing SDS for all signal-connected components, copy IC SDS to
    its decoupling caps

- [ ] **R1-3** Store SDS in `ComponentAnnotation` dataclass in `layout.py`
  - Add field `sds: float = 0.5` to `ComponentAnnotation`
  - Populate it from `compute_signal_distance_scores()` inside
    `find_feedback_paths()` (that function already returns a
    `dict[str, ComponentAnnotation]`)

- [ ] **R1-4** Add unit tests for SDS computation
  - Test: linear chain `J_in → R1 → U1 → R2 → J_out`
    - J_in → SDS ≈ 0.0
    - R1 → SDS ≈ 0.25
    - U1 → SDS ≈ 0.5
    - R2 → SDS ≈ 0.75
    - J_out → SDS ≈ 1.0
  - Test: power-only cap inherits its IC's SDS
  - File: `kicad-pcb/tests/test_layout.py`

---

## Rule 2 — Recursive Halving (Hierarchical Column Assignment)

**Problem:** The current flat BFS column assignment puts the entire passive network
around a single op-amp into one or two adjacent columns, creating a dense vertical
stack that partially covers the op-amp symbol.  Recursive halving distributes
components proportionally across the page width based on their SDS.

### Tasks

- [ ] **R2-1** Implement `_recursive_halving()` in `layout.py`
  - Signature:
    ```python
    def _recursive_halving(
        refs: list[str],
        sds: dict[str, float],
        x_lo: float,
        x_hi: float,
        *,
        max_per_col: int = MAX_ROWS_PER_COL,
        grid_col_mm: float = GRID_COL_MM,
    ) -> dict[str, int]:  # ref → column index
    ```
  - Sort `refs` by SDS (ascending)
  - Split at the median SDS into `S_left` and `S_right`
  - Assign `S_left → [x_lo, x_mid]`, `S_right → [x_mid, x_hi]`
  - Recurse on each half
  - Stop recursion when `len(refs) ≤ max_per_col` OR band width ≤ `grid_col_mm`
  - Return a flat `{ref: col_index}` mapping where `col_index` is the count of
    `GRID_COL_MM`-wide slots from the left edge

- [ ] **R2-2** Replace `_bfs_columns()` usage in `compute_signal_flow_layout()`
  - After computing SDS (R1-1) and connector roles (R0-1), call
    `_recursive_halving()` instead of `_bfs_columns()`
  - Keep `_bfs_columns()` as a private fallback for the
    `HeuristicLayoutEngine` (no Graphviz) path
  - Map column indices from `_recursive_halving()` to `x_mm` coordinates
    using the standard formula `ORIGIN_X + col × GRID_COL_MM`

- [ ] **R2-3** Integrate SDS-derived tiers into `gv_dot_builder.py → _build_dot_source()`
  - Convert column indices from `_recursive_halving()` into Graphviz
    `rank=same` subgroup assignments
  - Components with the same column index go into the same `{ rank=same }` subgraph
  - This replaces the current longest-path tier directly as the rank source

- [ ] **R2-4** Preserve `assign_tiers()` as the fallback when SDS cannot be computed
  - SDS computation requires ≥1 input connector and ≥1 output connector
  - When neither can be found (e.g. circuit has only one connector), fall
    back gracefully to the existing `assign_tiers()` longest-path algorithm
  - Log a `WARNING` when the fallback is triggered

- [ ] **R2-5** Add unit tests for recursive halving
  - Test: 8 components in a linear chain → 8 distinct columns, monotone SDS order
  - Test: 2 components → each goes into one half
  - Test: all components at SDS = 0.5 (degenerate) → stable sort, no crash
  - Test: `max_per_col` limit triggers column wrapping correctly
  - File: `kicad-pcb/tests/test_layout.py`

---

## Rule 3 — Two-Pass Barycentric Vertical Sort

**Problem:** The current `_avg_nbr_col()` sort is a single left-to-right pass and
only considers column neighbours, resulting in unnecessary vertical crossings inside
and between adjacent columns.

### Tasks

- [ ] **R3-1** Implement `_barycentric_sort()` in `layout.py`
  - Signature:
    ```python
    def _barycentric_sort(
        by_col: dict[int, list[str]],
        positions_x: dict[str, float],
        adjacency: dict[str, set[str]],
        *,
        passes: int = 2,
    ) -> dict[int, list[str]]:
    ```
  - **Pass 1 (left-to-right):** for each column `k > 0`, sort by the average
    y-row of connected components in column `k-1`
  - **Pass 2 (right-to-left):** for each column `k < max_col`, sort by the
    average y-row of connected components in column `k+1`
  - Use signal-net adjacency only (power nets excluded)
  - Tiebreak: alphabetical by ref for determinism

- [ ] **R3-2** Replace single-pass sort in `compute_signal_flow_layout()`
  - Call `_barycentric_sort()` in place of the current `members.sort(key=_avg_nbr_col)`
  - Pass the adjacency dict already built in that function

- [ ] **R3-3** Apply barycentric sort after `_apply_stereo_split()` in
  `graphviz_layout/snap.py`
  - Stereo split compresses L/R channels vertically; a post-split barycentric
    pass within each channel half further reduces intra-channel crossings
  - Add a `_post_stereo_barycentric()` helper that operates on each half
    independently (top half = L channel coords, bottom half = R channel)

- [ ] **R3-4** Add unit tests
  - Test: two adjacent columns where the naive sort produces a crossing →
    barycentric sort eliminates it
  - Test: second pass further reduces crossings relative to one pass
  - File: `kicad-pcb/tests/test_layout.py`

---

## Rule 4 — Op-Amp Halo (Feedback Network Colocation)

**Problem:** Feedback resistors, gain-setting resistors, and stability capacitors
(the "halo") currently land in their own BFS columns left or right of the op-amp,
producing long diagonal wires that cross the main signal path.  They should be
placed in the same column as the op-amp, stacked above and below it.

### Tasks

- [x] **R4-1** Implement `_compute_opamp_halo()` in `layout.py`
  - Signature:
    ```python
    def _compute_opamp_halo(
        ir: CircuitIR,
        annotations: dict[str, ComponentAnnotation],
        tiers: dict[str, int],
    ) -> dict[str, str]:  # halo_ref → anchor_ic_ref
    ```
  - A component is a **halo member** when ALL of the following hold:
    1. It is a passive (`R`, `C`, `L`)
    2. `annotations[ref].feedback == True` (back-edge topology), OR its only
       signal-net connections are to pins of a single IC (no other component
       appears in any of its signal nets)
    3. It is not already classified as a shunt/bypass component
    (i.e. it does not connect to any power net)
  - For each halo member, record the IC it is tightly coupled to as its anchor

- [x] **R4-2** Override column assignment for halo members in `_recursive_halving()`
  - After the recursive halving produces initial column assignments, iterate
    over halo members from `_compute_opamp_halo()`
  - Force each halo member into the same column as its anchor IC
  - Log each forced column assignment at `DEBUG` level

- [x] **R4-3** Assign halo rows in `compute_signal_flow_layout()`
  - Within the op-amp's column, place the op-amp at the vertical centre
  - Distribute halo members alternately above and below the op-amp:
    - Prefer placing input-side halo (connected to inverting/non-inverting
      input pins) **above** the op-amp
    - Prefer placing output-side halo (connected to output pin) **below**
  - Use the existing IC-centring logic in the column layout loop as a template

- [x] **R4-4** Add a `_snap_opamp_halo()` post-layout snap pass in
  `graphviz_layout/snap.py`
  - After `_snap_feedback_components()`, run a second pass that verifies
    halo members did not drift away from their anchor IC due to other snaps
  - If any halo member's column differs from its anchor IC after snapping,
    move it back to `anchor_x` and place it `± GRID_ROW_MM` from `anchor_y`
  - Insert this pass between `_snap_feedback_components()` and
    `_apply_stereo_split()` in `GraphvizLayoutEngine.compute_symbol_positions()`

- [x] **R4-5** Update `gv_dot_builder.py → _emit_feedback_constraints()`
  - Extend the existing feedback cluster to include all halo members (not just
    feedback-flagged components)
  - Use invisible edges (`style=invis`) to co-locate halo members at the same
    rank as their anchor IC in the DOT graph

- [x] **R4-6** Add unit tests
  - Test: NE5532 inverting amp — R_gain and R_fb are identified as halo, placed
    in U1's column
  - Test: component with dual power+signal nets is NOT classified as halo
  - File: `kicad-pcb/tests/test_layout.py`

---

## Rule 5 — GND / 0 V Net Normalisation

**Problem:** Many nets in the generated schematic carry labels like `0V` instead
of the standard `GND` power symbol.  This makes the schematic confusing because
`0V` appears as a plain net label rather than a recognisable power symbol, and ERC
tools do not link the `0V` nets to the global GND rail.

### Tasks

- [ ] **R5-1** Add a `GND_ALIASES` constant to `component_types.py`
  ```python
  GND_ALIASES: frozenset[str] = frozenset({
      "0V", "0V0", "0", "GROUND", "EARTH",
      "GND", "AGND", "PGND", "DGND", "SGND", "VSS",
  })
  ```
  - Aliases are matched case-insensitively (strip + upper before lookup)

- [ ] **R5-2** Add `normalize_gnd_net_name()` to `component_types.py`
  - Signature: `(name: str) -> str`
  - If `name.strip().upper() in GND_ALIASES` → return `"GND"`
  - Otherwise return `name` unchanged
  - Export in `__all__`

- [ ] **R5-3** Apply normalisation at IR ingestion in `circuit_ir.py`
  - In the net-construction code (wherever `NetIR` objects are created or
    their `name` field is set), call `normalize_gnd_net_name()` on the raw
    name before storing it
  - This ensures that every downstream consumer (tier assignment, layout,
    dot builder, schematic writer) sees `"GND"` instead of `"0V"`

- [ ] **R5-4** Apply normalisation in `preflight.py → collect_existing_net_names()`
  - Normalise GND aliases in the returned frozenset so that existing-net
    deduplication does not treat `GND` and `0V` as distinct nets

- [ ] **R5-5** Update `component_types.py → POWER_NET_PREFIXES`
  - Verify `"0V"` is explicitly listed (it currently is in `POWER_NET_PATTERN`
    but absent from `POWER_NET_PREFIXES`); add if missing
  - Run existing `is_power_net()` tests to confirm no regression

- [ ] **R5-6** Update schematic writer to emit `GND` power symbol
  - In `sch_doc/` (writer module), when rendering a net whose normalised name
    is `"GND"`, emit a KiCad `PWR:GND` global power symbol and its
    corresponding `#PWR` implicit power pin instead of a plain net label
  - Use the same code path already used for `VCC`/`VDD` rendering (if any)

- [ ] **R5-7** Add regression tests
  - Test: `normalize_gnd_net_name("0V")` → `"GND"`
  - Test: `normalize_gnd_net_name("GROUND")` → `"GND"`
  - Test: `normalize_gnd_net_name("net_audio_in")` → unchanged
  - Test: IR built from a netlist with `0V` nets → all `NetIR.name == "GND"`
  - File: `kicad-pcb/tests/test_component_types.py` and
    `kicad-pcb/tests/test_circuit_ir.py`

---

## Rule 6 — Wire Crossing Budget

**Problem:** Even with rules 2 and 3 applied, some crossing may remain (especially
on feedback and cross-channel wires).  Rule 6 adds a measurement pass that can
trigger an extra optimisation sweep when crossings are excessive, and exposes
crossing count as a lintable metric.

### Tasks

- [ ] **R6-1** Implement `count_wire_crossings()` in `layout.py`
  - Signature:
    ```python
    def count_wire_crossings(
        positions: dict[str, tuple[float, float]],
        adjacency: dict[str, set[str]],
    ) -> int:
    ```
  - Two wires `(A, B)` and `(C, D)` **cross** when:
    - `col(A) < col(C)` and `row(A) > row(C)`, OR
    - `col(A) < col(C)` and `row(B) > row(D)`
    (simple column-row order inversion, excludes same-column neighbours)
  - Return the total crossing count
  - Export as a public function

- [ ] **R6-2** Trigger remediation pass in `compute_signal_flow_layout()`
  - After the initial `_barycentric_sort()` (R3), compute
    `count_wire_crossings()`
  - **Crossing ratio threshold:** if `crossings / total_wires ≥ 0.30`,
    run one additional barycentric sweep (full two-pass `_barycentric_sort()`
    again on the updated row assignments)
  - Cap at 3 total sweep attempts to avoid an infinite loop
  - Log the crossing count and whether a remediation sweep was triggered at
    `DEBUG` level

- [ ] **R6-3** Expose crossing count in layout analytics / lint
  - Add `crossing_count: int` to the return value or a side-channel dict
    in `HeuristicLayoutEngine.compute_symbol_positions()` (store on the
    engine instance as `self.last_crossing_count`)
  - In `lint/` (LAY series rules), add **LAY007** rule:
    `crossing_count > 0.5 × total_wires` → `WARNING "high wire crossing ratio"`
  - Threshold is lenient (50 %) to fire only on severely tangled layouts

- [ ] **R6-4** Add unit tests
  - Test: crossing-free layout → `count_wire_crossings()` returns 0
  - Test: two deliberately crossed wires → returns 1
  - Test: remediation sweep is triggered when ratio ≥ 0.30
  - Test: LAY007 fires when crossing ratio > 0.50
  - File: `kicad-pcb/tests/test_layout.py` and `kicad-pcb/tests/test_lint.py`

---

## Cross-Rule Integration Tasks

- [ ] **INT-1** Update `GraphvizLayoutEngine.compute_symbol_positions()` snap order
  - New canonical snap order after all rules are implemented:
    1. `snap_positions()` (grid)
    2. `_snap_power_symbols()`
    3. `_enforce_connector_x_bounds()` ← new (R0-3)
    4. `_snap_connectors_to_ic_y()`
    5. `_snap_feedback_components()`
    6. `_snap_opamp_halo()` ← new (R4-4)
    7. `_apply_stereo_split()`
    8. `_post_stereo_barycentric()` ← new (R3-3)
    9. `_compact_y_gap()`
    10. `_post_snap_decoupling_caps()`
    11. `_deoverlap_positions()`
  - Update the `layout_engine.py` docstring pipeline overview to match

- [ ] **INT-2** Update `layout_engine.py` docstring to describe all six rules

- [ ] **INT-3** Wire SDS and connector roles into `GraphvizLayoutEngine`
  - `GraphvizLayoutEngine.__init__()` already accepts `tiers`; add optional
    `sds: dict[str, float] | None = None` and
    `roles: dict[str, str] | None = None` parameters
  - `make_layout_engine_with_ir()` should pre-compute and pass all three

- [ ] **INT-4** End-to-end integration test with NE5532 headphone amp IR
  - File: `kicad-pcb/tests/test_integration_ne5532.py`
  - Build the circuit IR from the attached schematic fixture
  - Run `GraphvizLayoutEngine.compute_symbol_positions()`
  - Assert: output connector (J_headphone) has `x ≥ 0.70 × PAGE_MAX_X`
  - Assert: input connector (J_audio_in) has `x ≤ 0.30 × PAGE_MAX_X`
  - Assert: U1A and halo components share the same x column (within 1.27 mm)
  - Assert: no GND-alias net labels remain (all `== "GND"`)
  - Assert: `count_wire_crossings()` for the produced layout < 10

- [ ] **INT-5** Update `memory.md` with new rules and implementation status
  - Add an entry summarising which rules have been implemented and any
    design decisions made during implementation

---

## Priority Order (Recommended Implementation Sequence)

| Priority | Rule | Rationale |
|----------|------|-----------|
| 1 | **R0** — I/O connector role | Single function + enforcement; immediately fixes headphone-jack position |
| 2 | **R5** — GND normalisation | Purely additive; no layout changes; eliminates noisy `0V` labels immediately |
| 3 | **R4** — Op-amp halo | Collapses the dense passive column; feedback path already partially detected |
| 4 | **R1** + **R2** — SDS + recursive halving | Structural change; requires R0 roles and replaces column algorithm |
| 5 | **R3** — Two-pass barycentric | Tuning on top of R2; small isolated change |
| 6 | **R6** — Crossing budget | Measurement + trigger; depends on R3 being in place |
| 7 | **INT-1…5** — Integration | Connect all rules; end-to-end test |
