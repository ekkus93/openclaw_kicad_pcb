# Graphviz Pipeline Improvements — TODO

Three concrete improvements port algorithm ideas that were embedded in the now-deleted `HeuristicLayoutEngine` into the live Graphviz snap pipeline.  Each is independent and can be implemented separately.

---

## Improvement 1 — IC Centering in Columns

**Problem:** After Graphviz lays out columns, ICs (op-amps, jellybean chips) can
land at the top or bottom of a column rather than in the middle with passive
components arranged around them.  This looks cluttered and makes the schematic
harder to read.

**What the old heuristic did:** `compute_signal_flow_layout()` (in `layout.py`,
lines ~725–780) interleaved components within each column so passives flank the
ICs:

```
plain_other[:mid] + halo_other[:mid] + ic_refs + halo_other[mid:] + plain_other[mid:]
```

**Goal:** Add a new snap pass `_center_ics_in_columns()` in `snap.py` that
re-sorts each grid column after Graphviz placement so ICs sit at the vertical
midpoint with passive components above and below.

---

### Subtasks

#### 1.1 — Study the existing column grouping code

- [x] Read `compute_signal_flow_layout()` in
  [`kicad-pcb/src/kicad_pcb/layout.py`](../kicad-pcb/src/kicad_pcb/layout.py)
  lines ~725–780 to understand the exact interleaving logic for
  `ic_refs`, `halo_other`, and `plain_other`.
- [x] Read `_post_stereo_barycentric()` in
  [`kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py`](../kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py)
  to understand how the snap passes work with `(x, y, rot)` tuples.
- [x] Read `_deoverlap_positions()` in `snap.py` (lines ~745–803) to understand
  the final pass that the new pass must run before.
- [x] Confirm `_OP_AMP_PREFIXES` — currently defined in `layout.py` as the list
  of IC reference-designator prefixes (`U`, `IC`, etc.).  Decide whether to
  re-export it or duplicate it in `snap.py`. **Decision: `snap.py` already imports
  `IC_PREFIXES as _IC_PREFIXES_CT` from `component_types` and uses `_is_ic_ref()`;
  no duplication needed.**
- [x] Confirm the `halo` dict signature: `dict[str, set[str]]` or `dict[str,
  str]`? Grep `_compute_opamp_halo` in `layout.py` and how `halo` is threaded
  through `_apply_post_layout_snaps()` in `snap.py`. **Result: `halo` is
  `dict[str, str]` (halo_ref → anchor_ic_ref); already threaded through
  `_apply_post_layout_snaps()` as `Mapping[str, str] | None`.**

#### 1.2 — Implement `_center_ics_in_columns()` in `snap.py`

- [x] Add the function signature:
  ```python
  def _center_ics_in_columns(
      positions: dict[str, tuple[float, float, float | None]],
      *,
      halo: Mapping[str, str] | None = None,
  ) -> dict[str, tuple[float, float, float | None]]:
  ```
- [x] Group components by their rounded x-coordinate (one group per column).
- [x] Within each column, split refs into three buckets:
  - `ic_refs` — refs whose prefix matches `_OP_AMP_PREFIXES` (e.g. `U`, `IC`)
  - `halo_other` — non-IC refs that are in the halo set
  - `plain_other` — everything else
- [x] Sort each bucket by current y-coordinate to preserve relative ordering.
- [x] Build `ordered = plain_other[:mid] + halo_other[:mid] + ic_refs +
  halo_other[mid:] + plain_other[mid:]` where `mid` is `len(bucket) // 2`.
- [x] Reassign y-coordinates: existing sorted y-slots in the column are
  redistributed in the new interleaved order — no new coordinates introduced.
  **Note: the TODO said "evenly spaced snapped to GRID_ROW_MM" but the
  implementation reuses the column's existing Graphviz-derived y-slots, which
  is preferable since it avoids artificially compressing or expanding spacing.**
- [x] Return the updated positions dict without mutating the input.

#### 1.3 — Wire `_center_ics_in_columns()` into the pipeline

- [x] In `snap.py`, add `_center_ics_in_columns` to the `_apply_post_layout_snaps`
  call sequence — inserted **before** `_post_snap_decoupling_caps()` (so caps
  re-anchor to the ICs' centred y-values) and **before** `_deoverlap_positions()`.
  **Note: placed before decoupling caps, not after as originally planned, which
  is correct because caps must snap to the IC's final centred position.**
- [x] Thread the `halo` argument through `_apply_post_layout_snaps()` — already
  present in the signature; confirmed it now passes through to `_center_ics_in_columns`.
- [x] No changes needed to `graphviz_layout/__init__.py` — `_center_ics_in_columns`
  is called entirely within `_apply_post_layout_snaps()` inside `snap.py`.

#### 1.4 — Write tests

- [x] Added `TestCenterICsInColumns` class (12 tests) in
  [`tests/unit/test_phase4_layout.py`](../tests/unit/test_phase4_layout.py).
- [x] Test: single IC in a 3-ref column → IC lands at position index 1 (middle).
- [x] Test: two ICs in a 4-ref column → both ICs land in the middle pair.
- [x] Test: column with only passives (no ICs) → original order is preserved.
- [x] Test: column with halo members → halo members appear adjacent to IC.
- [x] Test: empty positions dict → returns empty dict without error.
- [x] Test: single-component column → unchanged.
- [x] Additional tests added: `#PWR`/`#FLG` exclusion; input dict not mutated;
  multiple columns (only IC columns reordered); x and rotation preserved;
  `halo=None` vs no kwarg produces identical results. All 12 pass.

#### 1.5 — Verify with existing golden / regression tests

- [x] Run `pytest tests/unit/test_golden.py` — 14 tests, all passed, exit 0.
- [x] No golden fixture updates were needed — existing fixtures still match.
- [x] Run the full test suite: 102 kicad-pcb unit tests pass; 212 tests in
  `test_phase4_layout.py` pass; ruff clean on `snap.py`.

---

## Improvement 2 — Crossing Remediation Sweeps

**Problem:** Graphviz's `dot` minimises edge crossings globally, but the
subsequent snap passes (connector y-snap, stereo split, deoverlap) can
re-introduce crossings.  There is currently no post-snap pass that measures
and corrects wire crossings.

**What the old heuristic did:** `compute_signal_flow_layout()` measured the
crossing ratio after each barycentric sort and looped for up to
`_MAX_REMEDIATION_SWEEPS` (= 10) iterations if the ratio was ≥ 0.30.

**Goal:** Add a new snap pass `_remediate_crossings()` in `snap.py` that, after
all other snaps have run, measures wire crossings and iteratively re-sorts
columns to reduce them.

---

### Subtasks

#### 2.1 — Understand the measurement and sort algorithm

- [x] Read `count_wire_crossings()` in `layout.py` — understand the return type
  (int = total crossing count) and its `sig_adj` argument.
  **Return type: `int`. Signature: `(positions: dict[str, tuple[float, float]],
  adjacency: dict[str, set[str]]) -> int`.
  IMPORTANT: takes 2-tuples `(x, y)`, NOT 3-tuples. Snap.py positions are
  `(x, y, rot)` — must strip `rot` before calling (e.g. `{r: (x, y) for r, (x, y, _) in positions.items()}`).**
- [x] Read `_barycentric_sort()` in `layout.py` — understand the return type
  (`dict[int, list[str]]` = `{col: [ref, …]}`) and how it re-sorts columns
  using the barycentres of connected neighbours.
  **Signature: `(by_col: dict[int, list[str]], adjacency: dict[str, set[str]], *, passes: int = 2) -> dict[int, list[str]]`.
  Keys are integer column INDICES, not x-mm values.
  Snap.py will need to bucket refs by column index (not raw x) before calling.
  Two-pass sweep: L→R then R→L per pass. Returns a fresh dict (input not mutated).**
- [x] Read `_MAX_REMEDIATION_SWEEPS` constant near the top of `layout.py` —
  confirm value (currently 10).
  **Actual value: `_MAX_REMEDIATION_SWEEPS = 3` (not 10 — the TODO was wrong). The `max_sweeps` parameter in the new snap pass should default to 3.**
- [x] Understand the `sig_adj` structure: `dict[str, set[str]]` built by
  `build_signal_adjacency()`.
  **`build_signal_adjacency(ir)` is the public wrapper for `_build_signal_adjacency()`.
  Returns `{ref: {neighbour_refs}}` over signal nets only (power nets excluded).
  Already exported from `layout.py`; tested in `test_layout.py`.**
- [x] Determine what "crossing ratio" threshold to use: the heuristic used
  `_crossings / _total_sig_wires ≥ 0.30`.  Decide whether to keep this
  threshold or make it configurable.
  **Decision: make it configurable via `crossing_ratio_threshold: float = 0.30` parameter
  (matching the existing heuristic default). The denominator is
  `sum(len(v) for v in sig_adj.values()) // 2`; guard against zero.**

#### 2.2 — Export the needed helpers from `layout.py`

- [x] Check whether `_barycentric_sort()` is currently private (underscore
  prefix).  If so, decide: rename to `barycentric_sort()` (public) or keep
  private and import with the underscore name in `snap.py`.
  **Decision: rename to `barycentric_sort` (public) — done as part of Cleanup 4.3.
  Call sites in `layout.py` updated; tests in `test_layout.py` updated; ruff clean.
  See 4.3 below which is now partially complete.**
- [x] Add the following to the import block in `snap.py`:
  ```python
  from ..layout import GRID_COL_MM as _GRID_COL_MM
  from ..layout import barycentric_sort as _barycentric_sort
  from ..layout import build_signal_adjacency as _build_signal_adjacency
  from ..layout import count_wire_crossings as _count_wire_crossings
  ```
  **Deferred to 2.3: these imports are unused until `_remediate_crossings` is
  implemented; adding them forward would require `# noqa: F401` suppressions
  which violate the anti-paperclip rules. They will be added in 2.3 alongside
  the function body.**
- [x] Add these to the re-export list in `graphviz_layout/__init__.py` imports
  from snap if needed (only if they need to be externally visible — probably
  not).
  **Confirmed: not needed externally — `_remediate_crossings` is called from
  within `_apply_post_layout_snaps()` in `snap.py` only.**

#### 2.3 — Implement `_remediate_crossings()` in `snap.py`

- [x] Add the function signature:
  ```python
  def _remediate_crossings(
      positions: dict[str, tuple[float, float, float | None]],
      ir: CircuitIR,
      *,
      max_sweeps: int = 3,
      crossing_ratio_threshold: float = 0.30,
      skip_pairs: frozenset[tuple[str, str]] = frozenset(),
  ) -> dict[str, tuple[float, float, float | None]]:
  ```
  **Note: `grid` param dropped (y-slots are reused from existing snapped positions—no re-snap needed). `skip_pairs` added to thread decoupling-cap co-location pairs through to the inner `_deoverlap_positions` call.**
- [x] Build `sig_adj = build_signal_adjacency(ir)`.
- [x] Count total signal wire count (denominator): number of edges in `sig_adj`
  divided by 2 (undirected).  Use `sum(len(v) for v in sig_adj.values()) // 2`.
  Guard against division by zero (return positions unchanged if zero wires).
- [x] Group `positions` into `by_col: dict[int, list[str]]` using **integer column
  indices** (not raw x-mm): `col_idx = round((x - ORIGIN_X) / GRID_COL_MM)`.
  Import `ORIGIN_X` and `GRID_COL_MM` from `..layout`.
  **Power symbols (`#PWR`/`#FLG`) excluded from bucketing and never moved.**
  **Added 4 imports to `snap.py`: `GRID_COL_MM as _GRID_COL_MM`,
  `barycentric_sort as _barycentric_sort`, `build_signal_adjacency as _build_signal_adjacency`,
  `count_wire_crossings as _count_wire_crossings`.**
- [x] Loop up to `max_sweeps` times:
  1. Build 2-tuple positions for crossing count:
     `pos2 = {r: (x, y) for r, (x, y, _) in positions.items()}`.
     (`count_wire_crossings` takes `dict[str, tuple[float, float]]` — no rotation.)
  2. Compute `crossing_count = count_wire_crossings(pos2, sig_adj)`.
  3. Compute `ratio = crossing_count / total_sig_wires`.
  4. If `ratio < crossing_ratio_threshold` or on the last iteration, break.
  5. `by_col = _barycentric_sort(dict(by_col), sig_adj)`. (`_barycentric_sort` is the snap.py alias for the public `barycentric_sort` imported from `layout.py`.)
  6. Rebuild `positions` from `by_col`: each column's members get the same
     x as before; y-slots are redistributed by row index in new sorted order;
     rotation is preserved from the current positions dict.
     **No grid-snapping step: existing y-values are already snapped.**
- [x] After the loop, re-run `_deoverlap_positions(skip_pairs=skip_pairs)` since reordering may have
  introduced new overlaps.
- [x] Return the updated positions dict.

#### 2.4 — Wire `_remediate_crossings()` into the pipeline

- [x] In `snap.py` `_apply_post_layout_snaps()`, add the call **as the final
  step**, after `_deoverlap_positions()`.
  **Done: `result = _remediate_crossings(result, ir, skip_pairs=decouple_skip)`
  added as the final line. `decouple_skip` threads the decoupling-cap pair set
  through so the inner deoverlap respects intentional co-locations.**
  Canonical pipeline order after this change:
  1. `snap_positions` (grid snap)
  2. `_snap_power_symbols`
  2b. `_enforce_connector_x_bounds` (conditional on `roles`)
  2c. `_snap_connectors_to_ic_y`
  3. `_snap_feedback_components` (conditional)
  3b. `_snap_opamp_halo` (conditional)
  4. `_apply_stereo_split` (conditional)
  4b. `_post_stereo_barycentric` (conditional)
  5. `_compact_y_gap`
  6. `_center_ics_in_columns` ← from Improvement 1
  7. `_post_snap_decoupling_caps` (conditional)
  8. `_deoverlap_positions`
  9. `_remediate_crossings` ← new, includes its own inner deoverlap
- [x] Add `ir: CircuitIR` to `_apply_post_layout_snaps()` signature if it is not
  already present — needed to build `sig_adj`.  Check the current signature in
  `snap.py` line ~945.
  **Already present: `ir: CircuitIR` is the second positional parameter.**
- [x] Update the call site in `graphviz_layout/__init__.py` to pass `ir`.
  **`ir` is already passed to `_apply_post_layout_snaps()` — no change needed.**

#### 2.5 — Write tests

- [x] Add `TestRemediateCrossings` class in
  [`tests/unit/test_phase4_layout.py`](../tests/unit/test_phase4_layout.py).
  **13 tests added (details below).**
- [x] Test: two-column circuit with obvious crossing (components in wrong column
  order) → crossings reduced after one sweep.
  **Note: `count_wire_crossings` excludes same-column-start edges by design, so
  a 3-column layout is required. `R1(col0)→R2(col2)` crosses `R3(col1)→R4(col2)`,
  detected because left endpoints are at different x (30.48 < 60.96).**
- [x] Test: circuit with ratio < threshold → returned unchanged (no sweep runs).
  **Used `crossing_ratio_threshold=1.0`; ratio=0.5 < 1.0 → immediate return.**
- [x] Test: circuit with zero signal wires → returned unchanged without error.
  **Used single-component nets (no signal adjacency) → total_sig_wires=0 guard.**
- [x] Test: `max_sweeps=1` → exits on first iteration without sorting (break fires
  before the sort because `sweep == max_sweeps - 1 == 0`). Also added
  `max_sweeps=2` test which allows one sort pass and does fix the crossing.
- [x] Test: already-optimal layout → positions unchanged after `_remediate_crossings`.
- [x] Add a `count_wire_crossings` unit test in
  [`kicad-pcb/tests/unit/test_layout.py`](../kicad-pcb/tests/unit/test_layout.py)
  that constructs a known 2×2 crossing case and asserts the count is 1.
  **Pre-existing from R6 work; requirement was already satisfied when this TODO was written.**

#### 2.6 — Verify with existing golden / regression tests

- [x] Run `pytest tests/unit/test_golden.py` — confirm no regressions and update
  goldens if crossing reduction changes expected positions.
  **All 14 golden tests pass; no fixture updates required.**
- [x] Run the full test suite: all unit tests pass (exit 0).

---

## Improvement 3 — Affinity-Ordered Nodes in DOT Source

**Problem:** When Graphviz processes `{rank=same; A; B; C}` subgraphs, the
left-to-right order of nodes within the same tier is determined by Graphviz's
internal barycentric heuristic initialised from declaration order.  Currently
nodes are emitted in sorted-by-ref-name order (e.g. `C1`, `C2`, `R1`, `U1`)
which is arbitrary from a signal-flow standpoint.  Providing Graphviz with an
affinity-ordered list gives it a better starting point and produces fewer
crossings before any snap pass runs.

**What `compute_affinity_groups()` does (in `layout.py`, line 784):**  For
each tier it sorts refs so components most strongly coupled (by shared signal
nets / min pin-count) to the **previous** tier appear first.  This is dead code
— it has never been called from the Graphviz pipeline.

**Goal:** Call `compute_affinity_groups()` in `__init__.py` and pass the
affinity-ordered ref lists to `_build_dot_source()` so that `_emit_tier_subgraphs()`
emits nodes in affinity order rather than alphabetical order.

---

### Subtasks

#### 3.1 — Study `compute_affinity_groups()` and DOT emission code

- [x] Read `compute_affinity_groups()` in `layout.py` lines 784–860.  Note:
  - Input: `ir: CircuitIR`, `tiers: dict[str, int]` (ref → tier index).
  - Output: `dict[int, list[str]]` (tier index → refs in affinity order).
  - Tier 0 is sorted alphabetically as a stable base.
- [ ] Read `_emit_tier_subgraphs()` in
  [`kicad-pcb/src/kicad_pcb/graphviz_layout/dot_builder.py`](../kicad-pcb/src/kicad_pcb/graphviz_layout/dot_builder.py)
  lines 271–313 — understand how it currently emits `{rank=same; ref1; ref2;
  …}` blocks and what arguments it takes.
- [ ] Read `_build_dot_source()` in `dot_builder.py` lines 429–570 — understand
  the full function signature and where `_emit_tier_subgraphs` is called.
- [ ] Determine the current ref declaration order inside each `{rank=same}` —
  find the `sorted(...)` call that produces alphabetical order and note its
  line number.

#### 3.2 — Add `affinity_order` parameter to `_emit_tier_subgraphs()`

- [ ] Add an optional parameter:
  ```python
  affinity_order: dict[int, list[str]] | None = None,
  ```
  to `_emit_tier_subgraphs()`.
- [ ] Inside `_emit_tier_subgraphs()`, when emitting the `{rank=same; …}` block
  for a tier, use `affinity_order[tier_index]` as the ref list if present,
  otherwise fall back to the existing sorted order.
- [ ] Preserve the existing fallback so callers that don't pass `affinity_order`
  are unaffected.

#### 3.3 — Add `affinity_order` parameter to `_build_dot_source()`

- [ ] Add:
  ```python
  affinity_order: dict[int, list[str]] | None = None,
  ```
  to `_build_dot_source()`.
- [ ] Thread the value through to `_emit_tier_subgraphs()`.

#### 3.4 — Compute affinity order in `__init__.py` and pass it to DOT builder

- [ ] In `graphviz_layout/__init__.py`, import `compute_affinity_groups`:
  ```python
  from ..layout import compute_affinity_groups as _compute_affinity_groups
  ```
- [ ] After computing `_tiers` (already done early in
  `compute_symbol_positions`), call:
  ```python
  affinity_order = _compute_affinity_groups(ir, _tiers)
  ```
- [ ] Pass `affinity_order=affinity_order` to `_build_dot_source()`.
- [ ] Confirm the cache key still captures `affinity_order` implicitly — the
  cache key is `sha256(dot_source)` so as long as the DOT source changes when
  affinity order changes, the cache is automatically invalidated.

#### 3.5 — Remove "dead code" marker / add docstring note

- [ ] `compute_affinity_groups()` in `layout.py` was previously dead.  Now that
  it is used, remove any dead-code comments and ensure the docstring accurately
  says it is used by the Graphviz pipeline.
- [ ] The ruff/mypy lint suite should still pass — run `ruff check` and
  `mypy kicad-pcb/src` after changes.

#### 3.6 — Write tests

- [x] Add `TestComputeAffinityGroups` class in
  [`tests/unit/test_phase4_layout.py`](../tests/unit/test_phase4_layout.py)
  (it likely already has some coverage from prior history — check first).
  **Added: 3 tests (`test_affinity_groups_returns_sorted_refs`,
  `test_first_tier_alphabetical`, `test_isolated_component_gets_stable_position`).**
- [x] Test: two-tier circuit where tier 1 has two components, one with high
  affinity to tier 0 → high-affinity component is listed first.
- [x] Test: tier 0 → alphabetical sort regardless of signal nets.
- [x] Test: component with no signal nets → affinity = 0; still included in
  output.
- [ ] Test: `_build_dot_source()` with `affinity_order` set → DOT string emits
  refs in specified order within each `{rank=same}` block.
- [ ] Test: `_build_dot_source()` without `affinity_order` → DOT string emits
  refs in alphabetical order (backward-compatibility).

#### 3.7 — Verify with existing golden / regression tests

- [ ] Run `pytest tests/unit/test_golden.py` — confirm that affinity ordering
  changes the DOT source, which invalidates the layout cache, but that the
  schematic output is still correct.
- [ ] Run the full test suite: `cd kicad-pcb && python -m pytest && cd ..`
- [ ] Run `ruff check kicad-pcb/src` and `mypy kicad-pcb/src` — zero new errors.

---

## Cleanup — Remove Dead Code Orphaned by HeuristicLayoutEngine Deletion

When `HeuristicLayoutEngine` was deleted, several functions in `layout.py`
became unreachable from production code.  They are only called from tests or
from `compute_signal_flow_layout()` which is itself only called from tests.
These should be evaluated and either promoted to first-class pipeline utilities
or deleted.

### Subtasks

#### 4.1 — Audit the remaining orphaned functions in `layout.py`

- [ ] For each function listed below, confirm whether it is called from anything
  other than tests or `compute_signal_flow_layout()`:
  - `compute_signal_flow_layout()` itself — only called from test files
  - `_recursive_halving()` — called only from `compute_sds_columns()` (still
    used ✓) and `compute_signal_flow_layout()` — verify
  - `_rh_recurse()` — called only from `_recursive_halving()` — verify
  - `_barycentric_sort()` — called only from `compute_signal_flow_layout()`
    (will be promoted in Improvement 2)
  - `count_wire_crossings()` — used in tests; promoted in Improvement 2
  - `build_signal_adjacency()` — used in tests; promoted in Improvement 2

#### 4.2 — Decide fate of `compute_signal_flow_layout()`

- [ ] Option A: Keep it in `layout.py` as a reference / testing utility and add
  a docstring note: "Not used by the production pipeline; kept for algorithm
  reference and test coverage."
- [ ] Option B: Delete it and remove the tests that directly invoke it.
- [ ] Make the decision, document it in a brief code comment, and apply.

#### 4.3 — Promote `_barycentric_sort()` (needed for Improvement 2)

- [x] Rename `_barycentric_sort` → `barycentric_sort` (remove underscore) if it
  will be imported from outside `layout.py`.
  **Done in step 2.2: renamed in `layout.py` definition and both internal call
  sites in `compute_signal_flow_layout`.**
- [x] Update all call sites (in `compute_signal_flow_layout` and new `snap.py`
  code) to use the new name.
  **`layout.py` internal calls updated. `snap.py` imports `barycentric_sort as _barycentric_sort`
  (added in 2.3 alongside `_remediate_crossings` implementation).**
- [x] Update tests that reference the old private name.
  **`kicad-pcb/tests/unit/test_layout.py`: import and all 8 call sites updated
  from `_barycentric_sort` → `barycentric_sort`; ruff import-sort fixed; tests pass.**

#### 4.4 — Final lint/type-check pass

- [ ] `ruff check kicad-pcb/src` — zero errors.
- [ ] `mypy kicad-pcb/src` — zero new errors.
- [ ] `cd kicad-pcb && python -m pytest` — all tests pass.

---

## Implementation Order Recommendation

1. **Improvement 3** (affinity ordering in DOT) first — it improves Graphviz
   input quality before any snap pass runs.  Low risk: pure additive change to
   `_build_dot_source()`.

2. **Improvement 1** (IC centering in columns) second — simple deterministic
   y-reordering pass with no measurement loop.

3. **Cleanup 4.3** (promote `barycentric_sort`) in parallel with or just before
   Improvement 2, since Improvement 2 depends on it.

4. **Improvement 2** (crossing remediation sweeps) last — most complex because
   it involves a measurement loop and re-runs `_deoverlap_positions()`.

5. **Cleanup 4.1–4.2** (orphaned code audit) at the end once Improvements 1–3
   are stable.

---

## Files Touched Summary

| File | Change |
|---|---|
| `kicad-pcb/src/kicad_pcb/layout.py` | Export `barycentric_sort` (rename); add note to `compute_affinity_groups`; optionally keep/delete `compute_signal_flow_layout` |
| `kicad-pcb/src/kicad_pcb/graphviz_layout/__init__.py` | Import `compute_affinity_groups`; call it; pass to `_build_dot_source`; pass `ir` to `_apply_post_layout_snaps` |
| `kicad-pcb/src/kicad_pcb/graphviz_layout/dot_builder.py` | Add `affinity_order` param to `_emit_tier_subgraphs` and `_build_dot_source` |
| `kicad-pcb/src/kicad_pcb/graphviz_layout/snap.py` | Add `_center_ics_in_columns()`; add `_remediate_crossings()`; update `_apply_post_layout_snaps()` |
| `tests/unit/test_phase4_layout.py` | Add `TestCenterICsInColumns`, `TestRemediateCrossings`, check/add `TestComputeAffinityGroups` |
| `kicad-pcb/tests/unit/test_layout.py` | Add crossing-count unit test; update `_barycentric_sort` references if renamed |
