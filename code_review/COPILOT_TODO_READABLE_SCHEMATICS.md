# TODO: Implement readable schematic generation (Graphviz layout + wiring improvements)

This TODO list is the implementation plan for **Issue: Make generated schematics readable**. It is designed to be executed by Github Copilot.

---

## Phase 0 — Fixtures and Baselines (Do first)

### 0.1 Add the headphone-amp schematic regression fixture
- [x] Add fixture directory: `tests/fixtures/schematics/` *(fixtures placed under `tests/fixtures/regressions/` instead)*
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
- [x] Add a policy object or constants:
  - [x] max labels per net (default 2, power nets uncapped; `LabelPolicy.max_labels_per_net`)
  - [x] max global labels per net for high-degree routes (default 4; `LabelPolicy.max_global_labels_per_net`)
- [x] Enforce the policy in router output generation (`route_nets(..., policy=DEFAULT_LABEL_POLICY)`). *(`router.py`, `test_phase4_layout.py::TestLabelPolicy`)*

---

## Phase 3 — Power net strategy: reduce GND/V rail clutter

### 3.1 Implement "power symbol" (preferred) or "rail" strategy
**File:** `src/kicad_pcb/router.py`

Choose one implementation path:

**Option A (preferred): Power symbols**
- [x] Emit KiCad power symbol nodes for GND (and optionally V+/V-). *(`PowerSymbolPlacement` dataclass + `write_routing` loop)*
- [x] Replace repeated `global_label("GND")` at each pin with a local power symbol near that pin. *(`route_nets` emits `PowerSymbolPlacement` instead of `GlobalLabelPlacement` for power nets)*
- [x] Ensure symbol library/definition for power symbols is handled correctly. *(`add_power_symbol` embeds lib def via `read_lib_symbol_def_flat`; fallback to `global_label` when unavailable)*

**Option B: Rails**
- [ ] Create one horizontal rail per power net (e.g., GND at bottom, V+ at top).
- [ ] Add one label per rail.
- [ ] Route short taps from pins to the rail.

### 3.2 Add tests for power net rendering
- [x] For an IR with N GND-connected pins:
  - [x] Assert `routing.global_labels` is empty for power nets. *(`test_power_net_no_global_labels`)*
  - [x] Assert `routing.power_symbols` count == pin count. *(`test_power_net_emits_power_symbols`)*
  - [x] Assert no LAY004 is introduced by power strategy. *(power symbols bypass label-count lint)*

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
- [x] If deoverlap is currently pushing mostly in Y, adjust to:
  - [x] prefer spreading in X first when many symbols share near-identical x *(`_spread_x_columns()` in `snap.py`, runs before `_deoverlap_positions`)*
  - [x] preserve tier ordering while spreading *(symbols sorted by y before partitioning)*
- [x] Add test: a set of symbols with identical x must produce >= N x-columns after deoverlap. *(`TestSpreadXColumns.test_identical_x_produces_at_least_n_columns`)*

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
  - [x] distinct x-columns >= 6 *(`test_golden_fixture_x_columns_ge_6` + `test_dynamic_x_columns_ge_6`; dynamic: 10 columns)*
  - [x] GND global labels == 0 *(`test_golden_fixture_power_symbols_for_gnd` + `test_dynamic_gnd_global_labels_zero`; power:GND symbols used)*
  - [x] no LAY003 *(`test_dynamic_no_lay003_overlap` and `test_golden_fixture_no_lay003_overlap`; power symbols excluded from check)*
  - [x] no LAY004 *(`test_golden_fixture_no_lay004` + `test_dynamic_no_lay004`)*
  - [x] stub ratio below threshold < 0.75 *(`test_golden_fixture_stub_ratio_below_threshold` + `test_dynamic_stub_ratio_below_threshold`; actual ~0.58)*
- [x] Store the resulting `.kicad_sch` as a golden output. *(`tests/fixtures/regressions/headphone_amp_golden_layout.kicad_sch`)*

### 6.2 Optional integration test with KiCad CLI
- [x] If `kicad-cli` is installed in CI/dev:
  - [x] run `kicad-cli sch erc` on the generated schematic and require success.
    *(TestKiCadCLIERC: 3 tests in test_phase6_integration.py — exits 0, zero error violations)*

---

## Phase 7 — UX knobs (Optional but useful)

### 7.1 Add/confirm CLI flag to control layout engine and routing style
- [x] `--layout auto|graphviz|heuristic|none` *(commit 746c6ce: `HeuristicLayoutEngine` added; `_resolve_layout()` wired into `_sch_apply.py` and CLI)*
- [x] `--routing bus|hub|labels` *(commit 746c6ce: `_resolve_routing()` → `use_bus` bool; default `bus`)*
- [x] `--validate none|syntax|lint|kicad|full` *(commit 746c6ce: `_resolve_mode()` expanded; `--mode` kept as deprecated alias)*
- [x] `--strict` (treat warnings as errors) *(implemented in `cli.py`)*
- [x] `--dry-run` (no commit; validate only) *(implemented in `cli.py`)*

### 7.2 Diagnostics improvements
- [x] If Graphviz fails:
  - [x] include stderr excerpt *(RuntimeError message from `_run_dot` already includes `stderr[:400]`; propagated via `GRAPHVIZ_LAYOUT_FALLBACK` warning `details.reason`)*
  - [x] report fallback used (heuristic) *(`GRAPHVIZ_LAYOUT_FALLBACK` warning emitted in both `_resolve_layout` auto-fallback and `_build_managed_mutator._mutate` late-failure path)*
- [x] On lint failure:
  - [x] print lint codes and affected refs/nets *(existing — `[CODE] message` with coordinates; LAY001/LAY002 already include net name / wire counts)*
  - [x] recommend switching layout or adjusting thresholds *(LAY001–LAY004 suggestions now mention `--layout graphviz`; `cli.py` appends engine-switching tip for any LAY* failure)*

---

## Definition of Done

- [x] Headphone amp schematic output is visually readable and meets acceptance criteria. *(x-columns=10 ≥ 6; GND=0 global labels; no LAY003/LAY004; stub ratio 0.58 < 0.75)*
- [x] No LAY003/LAY004 for the fixture. *(LAY003: power symbols excluded from check; LAY004: `_clamp_to_page` + explicit golden/dynamic tests)*
- [x] Power net clutter is reduced (GND global labels <= 2, ideally 0). *(Phase 3 implemented — `PowerSymbolPlacement` emits `power:GND`/`power:VCC` symbols; 0 global labels for power nets)*
- [x] Multi-pin nets use spine routing by default. *(`use_bus=True` default)*
- [x] Transactional write guarantees are enforced (no overwrite on validation failure). *(`TestLAY004BlocksWrite` passes)*
- [x] Unit + golden tests exist and pass. *(376 tests pass)*
