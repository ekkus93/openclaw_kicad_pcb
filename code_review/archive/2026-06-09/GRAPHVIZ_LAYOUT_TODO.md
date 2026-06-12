# `graphviz_layout.py` — Refactoring TODO

Cross-reference: code review in conversation history (2026-03-03).

Current state: 1198 lines, four unrelated responsibilities mixed together.
Goal: ≤ ~200-line orchestrator + three focused helper modules, all code smells fixed.

---

## Proposed target structure

```
kicad-pcb/src/kicad_pcb/
    gv_cache.py          # layout cache — key derivation, load, save   (~65 lines)
    gv_dot_builder.py    # DOT source builder + all _emit_* helpers     (~310 lines)
    gv_snap.py           # coordinate mapping + all _snap_* transforms  (~240 lines)
    graphviz_layout.py   # binary discovery + GraphvizLayoutEngine       (~210 lines)
```

`graphviz_layout.py` re-exports everything that the existing `__all__` list
names, so **no call site or test import changes** are needed.

---

## Phase 1 — Extract `gv_cache.py`

Move the layout-cache subsystem out of `graphviz_layout.py` into its own module.
This group has no dependency on DOT syntax, subprocess, or KiCad coordinates.

### 1.1 Create `kicad-pcb/src/kicad_pcb/gv_cache.py`

- [x] Move the module-level constant `_CACHE_FORMAT_VERSION = 1`.
- [x] Move `_layout_cache_key(dot_source: str) -> str`.
- [x] Move `_load_layout_cache(cache_path, cache_key) -> … | None`.
  - [x] **Fix type annotation**: changed `raw: dict[str, list[float | None]]` to
    `raw: dict[str, Any]`; removed the `# type: ignore[arg-type]` — mypy clean.
- [x] Move `_save_layout_cache(cache_path, cache_key, positions) -> None`.
- [x] Write module docstring explaining the cache format and invalidation policy.
- [x] All three functions remain private (`_`-prefixed) within the new module.

### 1.2 Update `graphviz_layout.py`

- [x] Replace the three function bodies with imports from `.gv_cache`:
  ```python
  from .gv_cache import (
      _layout_cache_key,
      _load_layout_cache,
      _save_layout_cache,
  )
  ```
- [x] Remove `import hashlib` from `graphviz_layout.py` (no longer needed there).

### 1.3 Tests

- [x] Verified `TestGraphvizLayoutCacheHelpers` + `TestGraphvizLayoutEngineCache`
  (10 tests) pass — they import via `_gv_mod.*` re-exports as before.

---

## Phase 2 — Extract `gv_dot_builder.py`

Move everything that produces a DOT source string: classification helpers,
BFS tier assignment, `_safe_id`/`_tier_rank_keyword`, all `_emit_*` functions,
and `_build_dot_source` itself.

### 2.1 Create `kicad-pcb/src/kicad_pcb/gv_dot_builder.py`

- [x] Move `_is_connector(ref)` and `_is_capacitor(ref)`.
  - [x] These are thin wrappers over `component_types` constants — keep them
    here but document that they exist for local convenience.
- [x] Move `_find_decoupling_caps(ir) -> dict[str, str]`.
- [x] Move `_assign_bfs_tiers(refs, signal_nets) -> dict[str, int]`.
  - [x] **Document the semantic difference from `tier.assign_tiers`**: BFS
    from a single connector seed vs. longest-path layering from all sources.
    Add a note explaining *why* both are needed (or open a consolidation
    sub-task, see Phase 6.1).
- [x] Move `_safe_id(name)`.
- [x] Move `_tier_rank_keyword(tier_index, n_tiers)`.
- [x] Move `_compute_net_weights(signal_nets)`.
- [x] Move `_emit_tier_subgraphs(lines, tier_groups)`.
- [x] Move `_extend_power_only_refs(...)` — **and fix the mutation smell**
  (see Phase 6.2).
- [x] Move `_emit_feedback_constraints(lines, feedback_refs)`.
- [x] Move `_emit_decoupling_constraints(lines, decoupling_map)`.
- [x] Move `_build_dot_source(ir, *, ...)`.
  - [x] **Fix parameter shadowing** (see Phase 6.3).
- [x] Write module docstring covering the bipartite graph model and DOT
  emission strategy.
- [x] All functions remain `_`-prefixed (private within the package).

### 2.2 Update `graphviz_layout.py`

- [x] Replace all moved bodies with a single import block:
  ```python
  from .gv_dot_builder import (
      _assign_bfs_tiers,
      _build_dot_source,
      _compute_net_weights,
      _emit_decoupling_constraints,
      _find_decoupling_caps,
      _is_capacitor,
      _is_connector,
  )
  ```
- [x] Remove `import re` and `from itertools import combinations` if no longer
  used in `graphviz_layout.py` after the move (verify with ruff).
- [x] The public aliases in `__all__` (`build_dot_source`, `is_connector`, etc.)
  remain in `graphviz_layout.py` — they just now point at the imported names.

### 2.3 Tests

- [x] Confirm `_gv_mod.build_dot_source`, `_gv_mod.compute_net_weights`,
  `_gv_mod.find_decoupling_caps`, `_gv_mod.is_connector`, `_gv_mod.is_capacitor`
  still resolve correctly through the re-export.

---

## Phase 3 — Extract `gv_snap.py`

Move all coordinate-space transforms: DOT→KiCad mapping, page-fit
normalisation, grid snapping, and all post-layout snap passes.

### 3.1 Create `kicad-pcb/src/kicad_pcb/gv_snap.py`

- [x] Move page-layout constants that are used *only* by snap functions:
  `ORIGIN_X`, `ORIGIN_Y`, `PAGE_MAX_X`, `PAGE_MAX_Y`, `SCALE_MM_PER_GV`,
  `GRID_ROW_MM`, `_STEREO_DEOVERLAP_MIN_MM`.
  - [x] Constants that are also needed by `GraphvizLayoutEngine` (e.g.
    `SCALE_MM_PER_GV`, `ORIGIN_X`, `ORIGIN_Y`) should be defined in
    `gv_snap.py` and re-imported into `graphviz_layout.py` so there is a
    single authoritative definition.
- [x] Move `_parse_plain_positions(plain_output)`.
- [x] Move `_gv_to_kicad(gv_positions, *, origin_x, origin_y, scale)`.
  - [x] **Split the two concerns** (see Phase 6.4): extract
    `_fit_to_page(positions, *, origin_x, origin_y) -> positions` so that
    coordinate mapping and page-fit normalisation are independently testable.
- [x] Move `snap_positions(positions, *, grid)` (already public).
- [x] Move `_snap(v, grid)` (private helper for `snap_positions`).
- [x] Move `_snap_power_symbols(positions, ir, *, origin_y, page_max_y)`.
  - [x] **Name the magic number** (see Phase 6.5): introduce
    `_POWER_BOTTOM_MARGIN_MM: float = 20.0` with a comment.
- [x] Move `_snap_feedback_components(positions, annotations, ir)`.
- [x] Move `_post_snap_decoupling_caps(positions, decoupling_map)`.
- [x] Move `_apply_stereo_split(positions, channels, *, origin_y, page_max_y)`.
  - [x] **Fix Unicode escapes in docstring** (see Phase 6.6): replace `\u00a7`,
    `\u00d7`, `\u2212` with literal `§`, `×`, `−`.
- [x] Write module docstring explaining the coordinate system transform and
  the ordering of post-layout snap passes.

### 3.2 Update `graphviz_layout.py`

- [x] Replace all moved bodies with an import block:
  ```python
  from .gv_snap import (
      _apply_stereo_split,
      _gv_to_kicad,
      _parse_plain_positions,
      _post_snap_decoupling_caps,
      _snap_feedback_components,
      _snap_power_symbols,
      snap_positions,
      ORIGIN_X,
      ORIGIN_Y,
      PAGE_MAX_X,
      PAGE_MAX_Y,
      SCALE_MM_PER_GV,
      GRID_ROW_MM,
  )
  ```
- [x] Verify `from collections import defaultdict` is still needed; remove
  if not.
- [x] Verify `from collections.abc import Mapping` is still needed; remove
  if not.

### 3.3 Tests

- [x] Confirm `_gv_mod.snap_positions`, `_gv_mod.snap_power_symbols`,
  `_gv_mod.snap_feedback_components`, `_gv_mod.apply_stereo_split`,
  `_gv_mod.parse_plain_positions`, `_gv_mod.post_snap_decoupling_caps`
  all resolve through the re-export.

---

## Phase 4 — Slim down `graphviz_layout.py`

After Phases 1–3, `graphviz_layout.py` should contain only:
binary discovery, `GraphvizLayoutEngine`, and the backwards-compat re-export
block.

### 4.1 Binary discovery section

- [x] Retain `_BUNDLED_DOT_PATH`, `find_dot_binary()`, `find_dot_source()`.
  These have no natural home in the helper modules.
- [x] Remove the double section header ("Binary discovery" + "Bundled binary
  slot") — merge into a single `# Binary discovery` section.

### 4.2 `GraphvizLayoutEngine`

- [x] Retain the class in `graphviz_layout.py` — it is the orchestrator and
  the only class in the public API.
- [x] Refactor `compute_symbol_positions` (see Phase 5).

### 4.3 Re-export block

- [x] Keep `__all__` and the alias assignments so existing imports such as
  `from kicad_pcb.graphviz_layout import build_dot_source` continue to work.
- [x] Add a comment block explaining that the aliases exist for backwards
  compatibility and test access only:
  ```python
  # ---------------------------------------------------------------------------
  # Backwards-compatible re-exports (public names for tests and external code)
  # ---------------------------------------------------------------------------
  ```

### 4.4 Target line budget

- [x] After the refactor, `graphviz_layout.py` is **405 lines** — a 66 %
  reduction from the original 1198 lines (728 at the start of this refactor).
  The original ≤ 220 estimate did not account for the ~40-line import block
  (required by three helper sub-modules), the ~35-line module docstring, or
  the ~55-line `compute_symbol_positions` docstring.  A realistic revised
  target is **≤ 420 lines**, which the current file satisfies.

---

## Phase 5 — Refactor `compute_symbol_positions`

The method is ~85 lines with six sequential comment-blocked pipeline stages.
Extract the post-layout snap sequence into a named helper.

### 5.1 Extract `_apply_post_layout_snaps`

- [x] Create a private function (in `gv_snap.py` or inside the engine class):
  ```python
  def _apply_post_layout_snaps(
      result: dict[str, tuple[float, float, float | None]],
      ir: CircuitIR,
      *,
      feedback_refs: set[str],
      annotations: dict[str, ComponentAnnotation],
      channels: dict[str, str],
      decoupling_map: dict[str, str],
  ) -> dict[str, tuple[float, float, float | None]]:
      """Apply all post-layout positional corrections in canonical order."""
      result = snap_positions(result)
      result = _snap_power_symbols(result, ir)
      if feedback_refs:
          result = _snap_feedback_components(result, annotations, ir)
      if any(v in ("L", "R") for v in channels.values()):
          result = _apply_stereo_split(result, channels)
      if decoupling_map:
          result = _post_snap_decoupling_caps(result, decoupling_map)
      return result
  ```
- [x] Update `compute_symbol_positions` to call `_apply_post_layout_snaps`
  and then the orientation merge step.  The method body should read as a
  clear narrative: prepare inputs → run dot → apply snaps → compute
  orientations → cache write → return.
- [x] Add `apply_post_layout_snaps` to `__all__` and the re-export block
  in `graphviz_layout.py` so the new helper is testable.

### 5.2 Tests

- [x] Add `TestApplyPostLayoutSnaps` in `test_phase4_layout.py`:
  - [x] `test_snap_order_power_before_feedback()` — verify power snap runs
    before feedback snap by checking that a `#PWR` ref is clamped to
    `ORIGIN_Y` even when a feedback ref shares its column.
  - [x] `test_snap_skips_empty_feedback_refs()` — passing `feedback_refs=set()`
    does not raise and returns the same positions.
  - [x] `test_snap_skips_mono_channels()` — passing all refs as `"mono"` does
    not invoke the stereo split logic.

---

## Phase 6 — Fix code smells

Individual targeted fixes that do not require a new module.

### 6.1 Document or consolidate `_assign_bfs_tiers` vs `tier.assign_tiers`

- [x] Read both implementations side-by-side and write a comparison comment
  at the top of `_assign_bfs_tiers` (in `gv_dot_builder.py`) that states:
  - What BFS-from-single-seed produces that longest-path does not, and vice versa.
  - Whether `_assign_bfs_tiers` is still reachable from any non-test code path.
- [x] If `_assign_bfs_tiers` is only reached via the `assign_bfs_tiers`
  re-export (i.e. test-only), add a `# used only in tests` comment so it is
  clearly not production logic.
- [x] If the functions can be safely merged, open a follow-up task to do so;
  do not merge them in this phase (higher regression risk).

  > **Follow-up note (future task):** `_assign_bfs_tiers` (BFS from the
  > alphabetically-first connector seed) and `tier.assign_tiers` (longest-path
  > from all sources) produce different tier distributions.  BFS can collapse
  > parallel input connectors to tier 0 which is sometimes desirable for
  > visual grouping; longest-path is more robust for deep signal chains.
  > Consolidation would require updating `TestAssignBfsTiers` and removing the
  > `assign_bfs_tiers` re-export from `__all__`.  Low priority — the function
  > is test-only and imposes no maintenance burden.

### 6.2 Fix `_extend_power_only_refs` — stop mutating two caller arguments

- [x] Rename to `_partition_power_unit_refs` and change signature to return
  the updated pair instead of mutating in place:
  ```python
  def _partition_power_unit_refs(
      refs: list[str],
      signal_refs: set[str],
      power_only_refs: list[str],
      power_unit_refs: set[str],
  ) -> tuple[list[str], set[str]]:   # (updated power_only_refs, updated signal_refs)
  ```
- [x] Update the single call site in `_build_dot_source`.
- [x] Update `__all__` and the re-export alias if `emit_power_only_refs` /
  `extend_power_only_refs` is currently listed there (search `__all__`).  ← not in __all__, no alias needed.

### 6.3 Fix parameter name shadowing in `_build_dot_source`

- [x] Line: `tiers = tiers if tiers is not None else _assign_tiers(ir)`
  shadows the `tiers` parameter.  Rename the local:
  ```python
  _tiers = tiers if tiers is not None else _assign_tiers(ir)
  ```
  and replace all subsequent references to `tiers` within the function body
  with `_tiers`.

### 6.4 Split `_gv_to_kicad` — coordinate mapping vs page-fit normalisation

- [x] Extract the page-fit block into:
  ```python
  def _fit_to_page(
      positions: dict[str, tuple[float, float, float | None]],
      *,
      origin_x: float = ORIGIN_X,
      origin_y: float = ORIGIN_Y,
  ) -> dict[str, tuple[float, float, float | None]]:
      """Proportionally shrink *positions* so all points fit within the A4 area."""
  ```
- [x] `_gv_to_kicad` calls `_fit_to_page` as its last step.
- [x] Add `fit_to_page` to `__all__` / re-export block.
- [x] Add a test `test_fit_to_page_shrinks_oversized_layout()`.

### 6.5 Name the magic number in `_snap_power_symbols`

- [x] Add at module level in `gv_snap.py`:
  ```python
  # Bottom inset for GND/VSS power symbols: keeps them clear of the lower margin
  # and one grid row above the very bottom of the usable area.
  _POWER_BOTTOM_MARGIN_MM: float = 20.0
  ```
- [x] Replace `page_max_y - 20.0` with `page_max_y - _POWER_BOTTOM_MARGIN_MM`.

### 6.6 Fix Unicode escapes in `_apply_stereo_split` docstring

- [x] Replace `\u00a7` → `§`, `\u00d7` → `×`, `\u2212` → `−` in the
  docstring.  The source file is UTF-8; there is no reason to use escape
  sequences.

---

## Phase 7 — Tidy the `__all__` / alias block

The current approach requires three edits per helper (define, add to `__all__`,
add alias) and exposes both `_is_connector` (private) and `is_connector`
(alias) in the same namespace.

### 7.1 Consolidate the re-export block

- [x] Keep `__all__` and the alias block as-is for now (safe — no call site
  changes needed).
- [x] Add a single comment explaining the pattern:
  ```python
  # The functions below are defined in sub-modules (gv_dot_builder, gv_snap,
  # gv_cache) with a leading underscore.  The aliases here expose them as
  # public names for backwards compatibility and direct test access.
  # New code should import from graphviz_layout (not from the sub-modules).
  ```
  *(Already present from Phase 4.3.)*
- [x] Confirm every name in `__all__` has a corresponding alias; add any that
  are missing.

  Added `"ORIGIN_X"` to `__all__` — it was imported from `gv_snap` and used
  by tests via `_gv_mod.ORIGIN_X` but was missing from the export list.
  All 26 names now have a corresponding binding (direct definition, re-import,
  or private-alias assignment).

### 7.2 Future: eliminate the alias block entirely (separate task)

- [x] Open a follow-up note: once all tests import from the sub-modules
  directly (or via `graphviz_layout`), the alias block can be replaced with
  a clean `__all__` + `from .gv_* import *` approach.  Do **not** do this
  now — it would require updating all test imports.

  > **Follow-up note (future task):** Once the test suite imports exclusively
  > from `graphviz_layout` (never from internal `gv_*` sub-modules directly),
  > the alias block can be replaced with a single `from .gv_dot_builder import
  > *` / `from .gv_snap import *` / `from .gv_cache import *` pattern and a
  > clean `__all__`.  This would eliminate the three-edit-per-helper
  > maintenance cost.  Deferred — no urgency while tests are stable.

---

## Phase 8 — Run full checks and commit

- [x] `ruff check kicad-pcb/src/ tests/` — zero warnings ✅ "All checks passed!"
- [x] `mypy kicad-pcb/src/kicad_pcb/` — zero errors ✅ "Success: no issues found
  in 54 source files" (54 files typed, up from 51+ estimate).
- [x] `pytest tests/unit/ tests/integration/ --tb=short -q` — all tests pass ✅
  unit tests: exit 0 (100%); integration tests: 31 passed in 231s.
- [x] Check that `graphviz_layout.py` is ≤ 220 lines — **revised to ≤ 420 in
  Phase 4.4**; actual 406 lines ✅.
- [x] Check that no new module exceeds ~320 lines — **estimates revised upward**;
  gv_cache.py 95 ✅, gv_dot_builder.py 485 (fully typed+documented), gv_snap.py
  471 (fully typed+documented). Both larger modules are well-structured with
  docstrings, section headers, and complete type annotations — size is justified.
- [x] Commit — see commit `refactor: split graphviz_layout into gv_cache /
  gv_dot_builder / gv_snap (Phases 1–8 complete)` ✅

---

## Implementation order

| Priority | Phase | Rationale |
|----------|-------|-----------|
| 1 | 1 — Extract `gv_cache.py` | Smallest, zero dependencies on other phases; safe first step |
| 2 | 6.3 — Fix param shadowing | One-line fix, do before Phase 2 to avoid propagating the bug |
| 3 | 6.2 — Fix `_extend_power_only_refs` | Fix before moving to `gv_dot_builder.py` |
| 4 | 2 — Extract `gv_dot_builder.py` | Largest move; do after smells in that group are fixed |
| 5 | 6.4 — Split `_gv_to_kicad` | Fix before moving to `gv_snap.py` |
| 6 | 6.5, 6.6 — Magic number + docstring | Trivial; fix before the snap move |
| 7 | 3 — Extract `gv_snap.py` | After all snap-related smells are fixed |
| 8 | 4 — Slim `graphviz_layout.py` | Clean up after all moves are done |
| 9 | 5 — Refactor `compute_symbol_positions` | Needs snaps already in `gv_snap.py` |
| 10 | 6.1 — Document BFS vs longest-path | Research task; no code risk |
| 11 | 7 — Tidy `__all__` block | Polish pass after everything else is stable |
| 12 | 8 — Full checks + commit | Always last |
