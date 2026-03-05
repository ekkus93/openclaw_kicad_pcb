# TODO: Implement readable schematic generation (Graphviz layout + wiring improvements)

This TODO list is the implementation plan for **Issue: Make generated schematics readable**. It is designed to be executed by Github Copilot.

---

## Phase 0 — Fixtures and Baselines (Do first)

### 0.1 Add the headphone-amp schematic regression fixture
- [ ] Add fixture directory: `tests/fixtures/schematics/`
- [x] Add the current broken/unusable schematic output:
  - [x] `tests/fixtures/regressions/headphone_amp_current_layout.kicad_sch` *(placed under `regressions/` rather than the proposed `schematics/` path)*
- [x] Add a small README describing why it is "unusable":
  - [x] `tests/fixtures/regressions/README.md` exists and documents the baseline issues
- [x] Add helper code to load this fixture and compute stats (x-columns, label count, lint results). *(`schematic_metrics.py` + 28 unit tests, commit `892dbfe`)*

### 0.2 Add acceptance-metrics helpers (used by tests)
- [x] Implement `schematic_metrics.py` with functions:
  - [x] `count_distinct_x_columns(doc, tolerance_mm=0.5) -> int`
  - [x] `count_global_labels(doc, text="GND") -> int`
  - [x] `run_layout_lints(doc) -> list[LintIssue]`
  - [x] `wire_stub_ratio(doc, stub_len_mm=5.08, tol=0.2) -> float`
- [x] Add unit tests for these helpers using small synthetic schematics. *(28 tests in `tests/unit/test_schematic_metrics.py`)*

---

## Phase 1 — Router Inputs: Pass tiers + positions + enable bus routing

### 1.1 Extend `_write_symbols()` to return routing-relevant placement info
**File:** `src/kicad_pcb/commands/_sch_apply.py`

- [x] Ensure `_write_symbols()` returns:
  - [x] `symbol_positions_xy` (existing)
  - [x] `raw_layout` including rotation: `{ref: (x, y, rot)}` — added as 4th tuple element
  - [x] (optional) `tiers` if layout engine already computed them; else computed separately

### 1.2 Compute or reuse tiers for routing
- [x] Import and call `assign_tiers(ir)` (from `src/kicad_pcb/tier.py`) once per schematic generation.
- [x] Ensure tiers are stable/deterministic for a given IR.

### 1.3 Pass tiers + positions into `route_nets()`
- [x] Replace current call with:

```py
routing = route_nets(
    ir=ir,
    pin_endpoints=pin_endpoints,
    tiers=tiers,
    positions=raw_layout,
    use_bus=True,
)
```

### 1.4 Add unit tests proving routing changes take effect
- [x] Create a small IR fixture with a 4-pin net.
- [x] Assert the router produces a spine/taps pattern (not per-pin label stubs). *(TestBusStyleSpineRoute)*
- [x] Create a small IR fixture with a 2-pin net and assert it produces a direct wire if within distance threshold. *(TestPhase8WireRouting)*

---

## Phase 2 — Improve router defaults: spine routing and label fallback policy

### 2.1 Make spine routing the default for multi-pin nets
**File:** `src/kicad_pcb/router.py`

- [x] Change default behavior so that for `degree >= 3` (up to a reasonable max), routing uses spine/hub wiring by default. *(`use_bus=True` is now the default)*
- [x] Only fall back to "stub + label per pin" when:
  - [x] endpoints are unknown, OR
  - [x] net degree is extremely large and net is non-power, OR
  - [x] the spine route cannot be constructed safely

### 2.2 Use tiers and positions to avoid ugly cross-sheet trunks
- [x] If `tiers` and `positions` are provided:
  - [x] prefer routing inside/between adjacent tiers *(tier-distance ≤ 1 guard on 2-pin direct routing)*
  - [x] avoid crossing symbol bodies (use existing body-crossing detection where available) *(`detect_body_crossings` called in `route_nets` when positions provided)*
- [x] Add unit tests: with tiers/positions present, router avoids routing lines through symbol bounding boxes. *(`test_no_body_crossings_after_routing`)*

### 2.3 Add label duplication limits
- [ ] Add a policy object or constants:
  - [ ] max labels per net (default 1, power nets handled separately)
  - [ ] max duplicate label count per sheet for "semantic signals" (optional)
- [ ] Enforce the policy in router output generation.

---

## Phase 3 — Power net strategy: reduce GND/V rail clutter

### 3.1 Implement "power symbol" (preferred) or "rail" strategy
**File:** `src/kicad_pcb/router.py`

Choose one implementation path:

**Option A (preferred): Power symbols**
- [ ] Emit KiCad power symbol nodes for GND (and optionally V+/V-).
- [ ] Replace repeated `global_label("GND")` at each pin with a local power symbol near that pin.
- [ ] Ensure symbol library/definition for power symbols is handled correctly.

**Option B: Rails**
- [ ] Create one horizontal rail per power net (e.g., GND at bottom, V+ at top).
- [ ] Add one label per rail.
- [ ] Route short taps from pins to the rail.

### 3.2 Add tests for power net rendering
- [ ] For an IR with N GND-connected pins:
  - [ ] Assert `global_label("GND")` count is <= 2 (ideally 0 for Option A).
  - [ ] Assert no LAY004 is introduced by power strategy.

---

## Phase 4 — Graphviz post-layout: enforce page bounds and deoverlap correctness

### 4.1 Final "fit to page" after post-snaps
**File:** `src/kicad_pcb/graphviz_layout/snap.py`

- [x] Ensure a final clamp/fit step runs after `_apply_post_layout_snaps()`:
  - [x] page width/height respected (`PAGE_MAX_X`, `PAGE_MAX_Y`) *(`_clamp_to_page()` added as final step)*
  - [x] margins respected (`ORIGIN_X`, `ORIGIN_Y` as lower bounds)
- [x] Prevent any final position from exceeding page bounds.

### 4.2 Add tests for bounds enforcement
- [x] Build a synthetic layout with points intentionally out of bounds.
- [x] Run snap pipeline and assert all points are within bounds. *(`TestClampToPage`, 8 tests)*

### 4.3 Improve deoverlap to spread across X (avoid column collapse)
- [ ] If deoverlap is currently pushing mostly in Y, adjust to:
  - [ ] prefer spreading in X first when many symbols share near-identical x
  - [ ] preserve tier ordering while spreading
- [ ] Add test: a set of symbols with identical x must produce >= N x-columns after deoverlap.

---

## Phase 5 — Make layout lints block output (transactional contract)

### 5.1 Confirm lint severity and propagation
- [x] Ensure LAY004 is treated as **error**, not warning. *(`_ERR` severity in `lint/sch.py`)*
- [x] Ensure pipeline aborts on layout lint errors in `--validate lint|full`. *(`ValidationMode.LINT` is the default; `_ERR` issues raise `LintError`)*

### 5.2 Regression test: "must not write on LAY004"
- [x] Create a test where layout intentionally goes out of bounds.
- [x] Assert:
  - [x] command fails (raises `LintError`) *(`TestLAY004BlocksWrite.test_lay004_raises_lint_error`)*
  - [x] original file is unchanged (transactional no-overwrite) *(`TestLAY004BlocksWrite.test_lay004_does_not_overwrite_original`)*

---

## Phase 6 — Golden test enforcing readability acceptance criteria

### 6.1 Add golden test for headphone amp fixture
- [x] Generate schematic from the headphone amp IR fixture. *(`TestGoldenHeadphoneAmp` in `test_phase6_coverage.py`)*
- [x] Validate:
  - [ ] distinct x-columns >= 6 *(not yet tested; requires `count_distinct_x_columns` helper from Phase 0.2)*
  - [ ] GND global labels <= 2 *(tested as > 0 only; upper bound not yet enforced)*
  - [x] no LAY003 *(`test_dynamic_no_lay003_overlap` and `test_golden_fixture_no_lay003_overlap`)*
  - [ ] no LAY004 *(prevented at runtime by `_clamp_to_page`; golden fixture not explicitly lint-checked for LAY004)*
  - [ ] stub ratio below threshold (e.g. < 0.35) *(requires `wire_stub_ratio` helper from Phase 0.2)*
- [x] Store the resulting `.kicad_sch` as a golden output. *(`tests/fixtures/regressions/headphone_amp_golden_layout.kicad_sch`)*

### 6.2 Optional integration test with KiCad CLI
- [ ] If `kicad-cli` is installed in CI/dev:
  - [ ] run `kicad-cli sch erc` on the generated schematic and require success.

---

## Phase 7 — UX knobs (Optional but useful)

### 7.1 Add/confirm CLI flag to control layout engine and routing style
- [ ] `--layout auto|graphviz|heuristic|none`
- [ ] `--routing bus|hub|labels` (optional; default bus)
- [ ] `--validate none|syntax|lint|kicad|full`
- [x] `--strict` (treat warnings as errors) *(implemented in `cli.py`)*
- [x] `--dry-run` (no commit; validate only) *(implemented in `cli.py`)*

### 7.2 Diagnostics improvements
- [ ] If Graphviz fails:
  - [ ] include stderr excerpt
  - [ ] report fallback used (heuristic)
- [ ] On lint failure:
  - [ ] print lint codes and affected refs/nets
  - [ ] recommend switching layout or adjusting thresholds

---

## Definition of Done

- [ ] Headphone amp schematic output is visually readable and meets acceptance criteria. *(golden tests pass; x-columns and stub-ratio checks still pending)*
- [x] No LAY003/LAY004 for the fixture. *(LAY003 explicitly tested; LAY004 prevented by `_clamp_to_page` and blocked as `_ERR`)*
- [ ] Power net clutter is reduced (GND global labels <= 2, ideally 0). *(Phase 3 not implemented)*
- [x] Multi-pin nets use spine routing by default. *(`use_bus=True` default)*
- [x] Transactional write guarantees are enforced (no overwrite on validation failure). *(`TestLAY004BlocksWrite` passes)*
- [x] Unit + golden tests exist and pass. *(376 tests pass)*
